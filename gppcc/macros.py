"""Macro playback on background threads (cancellable, always cleans up)."""

from __future__ import annotations

import threading

from .output import Output


class MacroRun(threading.Thread):
    def __init__(self, out: Output, name: str, macro: dict, loop: bool = False,
                 start_delay: float = 0.0, on_error=None):
        super().__init__(daemon=True, name=f"macro:{name}")
        self.out = out
        self.macro_name = name
        self.macro = macro
        self.loop = loop
        self.start_delay = start_delay
        self.on_error = on_error
        self._cancel = threading.Event()
        self._held_keys: list[str] = []
        self._held_buttons: list[str] = []

    def stop(self) -> None:
        self._cancel.set()

    @property
    def stopped(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        try:
            if self.start_delay and self._cancel.wait(self.start_delay):
                return
            steps = self.macro.get("steps", [])
            count = 0
            total = max(1, int(self.macro.get("repeat", 1)))
            while not self._cancel.is_set():
                for step in steps:
                    if self._cancel.is_set():
                        break
                    self._exec(step)
                count += 1
                if not self.loop and count >= total:
                    break
                # Never spin a tight loop if the macro has no waits.
                if self._cancel.wait(0.01):
                    break
        except Exception as e:  # bad key name etc. – report, don't crash the engine
            if self.on_error:
                self.on_error(f"macro '{self.macro_name}': {e}")
        finally:
            for b in reversed(self._held_buttons):
                self.out.mouse_up(b)
            for k in reversed(self._held_keys):
                self.out.key_up(k)

    def _exec(self, s: dict) -> None:
        op = s["op"]
        o = self.out
        if op == "tap":
            o.tap(s["keys"])
        elif op == "key_down":
            o.key_down(s["keys"])
            self._held_keys.append(s["keys"])
        elif op == "key_up":
            if s["keys"] in self._held_keys:
                self._held_keys.remove(s["keys"])
                o.key_up(s["keys"])
        elif op == "text":
            o.type_text(s["text"])
        elif op == "click":
            o.click(s["button"], s.get("count", 1))
        elif op == "mouse_down":
            o.mouse_down(s["button"])
            self._held_buttons.append(s["button"])
        elif op == "mouse_up":
            if s["button"] in self._held_buttons:
                self._held_buttons.remove(s["button"])
                o.mouse_up(s["button"])
        elif op == "move":
            o.move(s["dx"], s["dy"])
        elif op == "move_to":
            o.move_to(s["x"], s["y"])
        elif op == "scroll":
            o.scroll(s["dx"], s["dy"])
        elif op == "wait":
            self._cancel.wait(s["ms"] / 1000.0)
