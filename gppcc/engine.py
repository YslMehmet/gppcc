"""Main loop: gamepad state -> cursor/scroll movement + bound operations."""

from __future__ import annotations

import math
import threading
import time
from collections import deque

from . import config
from .gamepad import Gamepads, PadState
from .macros import MacroRun
from .output import Output

TICK_HZ = 125

# Actions that are "instant": holding/toggling them just fires once on start.
_INSTANT = {"scroll", "move", "text", "toggle_enabled", "profile", "next_profile",
            "keyboard", "none"}


class Engine:
    def __init__(self, profile_name: str | None = None):
        self.out = Output()
        self.pads = Gamepads()
        self.lock = threading.RLock()
        self.enabled = True
        self.running = True
        self.log: deque = deque(maxlen=50)

        self.profile: dict = {}
        self.active_name = profile_name or config.get_active_name()
        self._load(self.active_name)

        self._prev_pressed: set[str] = set()
        self._state = PadState()
        self._last_input: dict = {"name": None, "t": 0.0}
        # Remapping: raw names the controller reports -> names bindings see.
        self.remaps: dict = config.load_remaps()
        self._raw_pressed: set[str] = set()
        self._last_raw: dict = {"name": None, "t": 0.0}
        self.overlay = None                 # created lazily on first keyboard action
        self._overlay_opener: str | None = None
        self._overlay_cfg: dict | None = None
        self._shift_on = False
        self._reset_runtime()

    # ------------------------------------------------------------------ state
    def _reset_runtime(self) -> None:
        self._active: dict[str, list[dict]] = {}   # input -> bindings activated on press
        self._used_as_mod: set[str] = set()
        self._toggled: set[str] = set()            # binding ids currently toggled on
        self._macros: dict[str, MacroRun] = {}     # binding id -> running macro
        self._repeat_next: dict[str, float] = {}   # binding id -> next fire time
        self._precision_holds = 0
        self._precision_latched = False

    def _stop_all(self) -> None:
        for run in self._macros.values():
            run.stop()
        self._reset_runtime()
        self.out.release_all()
        if self.overlay is not None:
            self.overlay.close()
            self._overlay_opener = None

    def _load(self, name: str) -> None:
        self.profile = config.load_profile(name)
        self.active_name = name

    def _note(self, msg: str) -> None:
        self.log.append({"t": time.time(), "msg": msg})

    # ------------------------------------------------------- public (thread-safe)
    def set_enabled(self, on: bool) -> None:
        with self.lock:
            if self.enabled and not on:
                self._stop_all()
            self.enabled = on
            self._note("control enabled" if on else "control paused")

    def switch_profile(self, name: str) -> None:
        with self.lock:
            self._stop_all()
            self._load(name)
            config.set_active_name(name)
            self._note(f"profile → {name}")

    def profile_saved(self, profile: dict) -> None:
        """Called after the UI saves a profile; hot-reload it if it is active."""
        with self.lock:
            if profile["name"] == self.active_name:
                self._stop_all()
                self.profile = profile

    def test_action(self, action: dict, delay: float = 0.0) -> None:
        action = config._norm_action(action)
        binding = {"id": "__test__", "input": "", "trigger": "press", "action": action}

        def go():
            time.sleep(delay)
            with self.lock:
                self._fire(binding)

        threading.Thread(target=go, daemon=True).start()

    def test_macro(self, name: str, macro: dict, delay: float = 3.0) -> None:
        macro = config.normalize_profile({"macros": {name or "test": macro}})["macros"]
        m = next(iter(macro.values()), None)
        if m:
            MacroRun(self.out, name, m, start_delay=delay, on_error=self._note).start()

    def set_remap(self, device: str, mapping: dict) -> dict:
        with self.lock:
            saved = config.save_remap(device, mapping)
            if saved:
                self.remaps[device] = saved
            else:
                self.remaps.pop(device, None)
            self._stop_all()
            self._prev_pressed = set()
            return saved

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "enabled": self.enabled,
                "profile": self.active_name,
                "device": self.pads.info(),
                "pad": self._state.to_dict(),
                "pressed": sorted(self._prev_pressed),
                "last_input": self._last_input,
                "raw_pressed": sorted(self._raw_pressed),
                "last_raw": self._last_raw,
                "remap": self.remaps.get(self.pads.active_name or "", {}),
                "precision": self._precision_on(),
                "macros_running": sorted({r.macro_name for r in self._macros.values() if r.is_alive()}),
                "toggled": sorted(self._toggled),
                "overlay": self._overlay_active(),
                "log": list(self.log)[-15:],
                "now": time.time(),
            }

    # -------------------------------------------------------------- main loop
    def run(self) -> None:
        period = 1.0 / TICK_HZ
        last = time.perf_counter()
        try:
            while self.running:
                now = time.perf_counter()
                dt = min(now - last, 0.05)
                last = now
                st = self.pads.poll()
                if getattr(self.pads, "quit_requested", False):
                    break
                with self.lock:
                    self._tick(st, dt)
                spare = period - (time.perf_counter() - now)
                if spare > 0:
                    time.sleep(spare)
        finally:
            with self.lock:
                self._stop_all()
            if self.overlay is not None:
                self.overlay.shutdown()
            self.pads.close()

    def _pressed_inputs(self, st: PadState) -> set[str]:
        th = self.profile["trigger_threshold"]
        p = set(st.buttons)
        if st.lt >= th:
            p.add("LT")
        if st.rt >= th:
            p.add("RT")
        for s, x, y in (("LS", st.lx, st.ly), ("RS", st.rx, st.ry)):
            if y <= -th:
                p.add(f"{s}_Up")
            if y >= th:
                p.add(f"{s}_Down")
            if x <= -th:
                p.add(f"{s}_Left")
            if x >= th:
                p.add(f"{s}_Right")
        return p

    def _tick(self, st: PadState, dt: float) -> None:
        self._state = st
        raw = self._pressed_inputs(st)
        for name in raw - self._raw_pressed:
            self._last_raw = {"name": name, "t": time.time()}
        self._raw_pressed = raw
        remap = self.remaps.get(self.pads.active_name or "")
        if remap:
            pressed = {remap.get(n, n) for n in raw}
            pressed.discard("none")
        else:
            pressed = raw
        prev = self._prev_pressed
        self._prev_pressed = pressed
        now = time.monotonic()

        # While the keyboard overlay is open it captures the pad: no cursor,
        # scroll, or ordinary bindings fire — only its own navigation does.
        if self._overlay_active():
            self._drive_overlay(st, pressed, prev)
            return

        for name in sorted(pressed - prev):
            self._last_input = {"name": name, "t": time.time()}
            self._on_press(name, pressed)
        for name in sorted(prev - pressed):
            self._on_release(name)

        self._run_repeats(now)
        # Drop finished macro runs (non-toggle ones), so they can be fired again.
        for bid, run in list(self._macros.items()):
            if not run.is_alive():
                del self._macros[bid]
                self._toggled.discard(bid)

        if self._overlay_active():   # a binding just opened it this tick
            return

        if self.enabled:
            self._move_cursor(st, dt)
            self._scroll(st, dt)

    # ----------------------------------------------------------- bindings
    def _on_press(self, name: str, pressed: set[str]) -> None:
        cands = [b for b in self.profile["bindings"] if b["input"] == name]
        combo = [b for b in cands if b.get("with") and b["with"] in pressed]
        chosen = combo or [b for b in cands if not b.get("with")]
        for b in combo:
            self._used_as_mod.add(b["with"])
        if not self.enabled:
            chosen = [b for b in chosen if b["action"]["type"] == "toggle_enabled"]
        self._active[name] = chosen
        for b in chosen:
            self._safe(self._binding_down, b)

    def _on_release(self, name: str) -> None:
        suppressed = name in self._used_as_mod
        self._used_as_mod.discard(name)
        for b in self._active.pop(name, []):
            self._safe(self._binding_up, b, suppressed)

    def _binding_down(self, b: dict) -> None:
        t = b["trigger"]
        if t == "press":
            self._fire(b)
        elif t == "hold":
            self._start(b)
        elif t == "repeat":
            self._fire(b)
            self._repeat_next[b["id"]] = time.monotonic() + self.profile["repeat_delay_ms"] / 1000
        elif t == "toggle":
            if b["id"] in self._toggled:
                self._toggled.discard(b["id"])
                self._end(b)
            else:
                self._toggled.add(b["id"])
                self._start(b)

    def _binding_up(self, b: dict, suppressed: bool) -> None:
        t = b["trigger"]
        if t == "release" and not suppressed:
            self._fire(b)
        elif t == "hold":
            self._end(b)
        elif t == "repeat":
            self._repeat_next.pop(b["id"], None)

    def _run_repeats(self, now: float) -> None:
        if not self._repeat_next:
            return
        by_id = {b["id"]: b for bs in self._active.values() for b in bs}
        for bid, due in list(self._repeat_next.items()):
            b = by_id.get(bid)
            if b is None:
                self._repeat_next.pop(bid, None)
                continue
            if now >= due:
                self._safe(self._fire, b)
                interval = b.get("repeat_ms") or self.profile["repeat_interval_ms"]
                self._repeat_next[bid] = now + interval / 1000

    def _safe(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as e:
            self._note(f"error: {e}")

    # ------------------------------------------------------------- actions
    def _fire(self, b: dict) -> None:
        """Instant execution of a binding's action."""
        a = b["action"]
        t = a["type"]
        o = self.out
        if t == "key":
            o.tap(a["keys"])
        elif t == "mouse":
            o.click(a["button"], a.get("count", 1))
        elif t == "scroll":
            o.scroll(a["dx"], a["dy"])
        elif t == "move":
            o.move(a["dx"], a["dy"])
        elif t == "text":
            o.type_text(a["text"])
        elif t == "macro":
            run = self._macros.get(b["id"])
            if run is None or not run.is_alive():
                self._start_macro(b, loop=False)
        elif t == "precision":
            self._precision_latched = not self._precision_latched
        elif t == "toggle_enabled":
            self._toggle_enabled()
        elif t == "profile":
            self._switch_from_binding(a["name"])
        elif t == "next_profile":
            names = config.list_profiles()
            if names:
                i = names.index(self.active_name) if self.active_name in names else -1
                self._switch_from_binding(names[(i + 1) % len(names)])
        elif t == "keyboard":
            self._open_overlay(b.get("input") or None)

    def _start(self, b: dict) -> None:
        """Begin a continuous action (hold / toggle-on)."""
        a = b["action"]
        t = a["type"]
        if t == "key":
            self.out.key_down(a["keys"])
        elif t == "mouse":
            self.out.mouse_down(a["button"])
        elif t == "macro":
            self._start_macro(b, loop=True)
        elif t == "precision":
            self._precision_holds += 1
        elif t in _INSTANT:
            self._fire(b)

    def _end(self, b: dict) -> None:
        a = b["action"]
        t = a["type"]
        if t == "key":
            self.out.key_up(a["keys"])
        elif t == "mouse":
            self.out.mouse_up(a["button"])
        elif t == "macro":
            run = self._macros.pop(b["id"], None)
            if run:
                run.stop()
        elif t == "precision":
            self._precision_holds = max(0, self._precision_holds - 1)

    def _start_macro(self, b: dict, loop: bool) -> None:
        name = b["action"].get("name", "")
        macro = self.profile["macros"].get(name)
        if macro is None:
            self._note(f"unknown macro: {name!r}")
            return
        old = self._macros.pop(b["id"], None)
        if old:
            old.stop()
        run = MacroRun(self.out, name, macro, loop=loop, on_error=self._note)
        self._macros[b["id"]] = run
        run.start()

    def _toggle_enabled(self) -> None:
        if self.enabled:
            self._stop_all()
            self.enabled = False
            self._note("control paused (gamepad)")
        else:
            self.enabled = True
            self._note("control enabled (gamepad)")

    def _switch_from_binding(self, name: str) -> None:
        if name not in config.list_profiles():
            self._note(f"unknown profile: {name!r}")
            return
        self.switch_profile(name)

    # --------------------------------------------------- keyboard overlay
    def _ensure_overlay(self):
        if self.overlay is None:
            from . import overlay as overlay_mod

            self._overlay_cfg = config.load_overlay()
            self.overlay = overlay_mod.create(self._overlay_cfg)
            if self.overlay is not None:
                self.overlay.on_type = self.out.tap
        return self.overlay

    def overlay_saved(self, cfg: dict) -> None:
        """Called after the UI saves the overlay config; apply it live if open."""
        with self.lock:
            self._overlay_cfg = cfg
            if self.overlay is not None:
                self.overlay.set_config(cfg)

    def _open_overlay(self, opener: str | None) -> None:
        ov = self._ensure_overlay()
        if ov is None:
            self._note("keyboard overlay is Windows-only for now")
            return
        if ov.is_open():
            return
        self._stop_all()          # release anything held before we start typing
        self._overlay_opener = opener
        self._shift_on = False
        ov.set_shift(False)
        ov.open()
        self._note("keyboard overlay opened")

    def _overlay_active(self) -> bool:
        return self.overlay is not None and self.overlay.is_open()

    def _drive_overlay(self, st: PadState, pressed: set[str], prev: set[str]) -> None:
        ov = self.overlay
        ov.aim(st.lx, st.ly, st.rx, st.ry)

        def edge(name: str) -> bool:
            return name in pressed and name not in prev

        # Shift: a configurable button, either toggle (caps-lock) or hold.
        shift = (self._overlay_cfg or {}).get("shift") or {"input": "X", "mode": "toggle"}
        si, mode = shift["input"], shift["mode"]
        if mode == "hold":
            on = si in pressed
            if on != self._shift_on:
                self._shift_on = on
                ov.set_shift(on)
        elif edge(si):
            self._shift_on = not self._shift_on
            ov.set_shift(self._shift_on)

        if edge("A"):
            ov.commit()
        # Several ways out, so a mislabelled face button (common on Nintendo-style
        # pads) can never trap the user: B, Start, or re-pressing the opener.
        if edge("B") or edge("Start") or (self._overlay_opener and edge(self._overlay_opener)):
            ov.close()
            self._overlay_opener = None
            self._note("keyboard overlay closed")

    # -------------------------------------------------------------- motion
    def _precision_on(self) -> bool:
        return self._precision_holds > 0 or self._precision_latched

    @staticmethod
    def _stick(st: PadState, which: str):
        if which == "left":
            return st.lx, st.ly
        if which == "right":
            return st.rx, st.ry
        return 0.0, 0.0

    def _move_cursor(self, st: PadState, dt: float) -> None:
        c = self.profile["cursor"]
        x, y = self._stick(st, c["stick"])
        mag = math.hypot(x, y)
        dz = c["deadzone"]
        if mag <= dz:
            return
        norm = min(1.0, (mag - dz) / (1.0 - dz))
        speed = (norm ** c["curve"]) * c["max_speed"]
        if self._precision_on():
            speed *= c["precision_factor"]
        vx, vy = x / mag * speed, y / mag * speed
        if c["invert_y"]:
            vy = -vy
        self.out.move(vx * dt, vy * dt)

    def _scroll(self, st: PadState, dt: float) -> None:
        s = self.profile["scroll"]
        x, y = self._stick(st, s["stick"])
        dz, curve = s["deadzone"], s["curve"]

        def shape(v: float) -> float:
            a = abs(v)
            if a <= dz:
                return 0.0
            return math.copysign(min(1.0, (a - dz) / (1.0 - dz)) ** curve, v)

        dy = -shape(y) * s["speed"] * dt  # stick up -> scroll up
        dx = shape(x) * s["speed"] * dt if s["horizontal"] else 0.0
        if s["invert"]:
            dx, dy = -dx, -dy
        if self._precision_on():
            dx *= 0.4
            dy *= 0.4
        if dx or dy:
            self.out.scroll(dx, dy)
