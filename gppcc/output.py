"""OS input injection (mouse + keyboard) for macOS and Windows.

Built on pynput, with platform-specific smooth scrolling and a registry of
everything currently held down so we can always release it safely.
"""

from __future__ import annotations

import sys
import threading

from pynput import keyboard, mouse

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

_kb = keyboard.Controller()
_mouse = mouse.Controller()
_lock = threading.RLock()

# Names users can type in key combos, e.g. "ctrl+shift+t", "mod+c", "f5".
KEY_ALIASES = {
    "control": "ctrl", "ctl": "ctrl",
    "option": "alt", "opt": "alt",
    "command": "cmd", "win": "cmd", "windows": "cmd", "super": "cmd", "meta": "cmd",
    "return": "enter", "escape": "esc",
    "del": "delete", "bksp": "backspace",
    "pgup": "page_up", "pageup": "page_up", "pgdn": "page_down", "pagedown": "page_down",
    "capslock": "caps_lock",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "play": "media_play_pause", "playpause": "media_play_pause",
    "next": "media_next", "prev": "media_previous", "previous": "media_previous",
    "volup": "media_volume_up", "voldown": "media_volume_down", "mute": "media_volume_mute",
    "plus": "+", "spacebar": "space",
}

MOUSE_BUTTONS = {
    "left": mouse.Button.left,
    "right": mouse.Button.right,
    "middle": mouse.Button.middle,
}

# One wheel "notch" in platform units.
_MAC_PX_PER_NOTCH = 24
_WIN_DELTA_PER_NOTCH = 120


def available_key_names() -> list[str]:
    names = sorted(k.name for k in keyboard.Key)
    return ["mod"] + names


def _resolve_key(token: str):
    t = token.strip().lower()
    t = KEY_ALIASES.get(t, t)
    if t == "mod":  # Cmd on macOS, Ctrl on Windows – handy for cross-platform shortcuts
        return keyboard.Key.cmd if IS_MAC else keyboard.Key.ctrl
    if len(t) == 1:
        return keyboard.KeyCode.from_char(t)
    key = getattr(keyboard.Key, t, None)
    if key is None:
        raise ValueError(f"unknown key: {token!r}")
    return key


def parse_combo(combo: str) -> list:
    """"ctrl+shift+t" -> [Key.ctrl, Key.shift, KeyCode('t')]. A lone '+' is allowed."""
    combo = (combo or "").strip()
    if not combo:
        return []
    if combo == "+":
        return [_resolve_key("+")]
    parts = combo.split("+")
    # "ctrl++" -> ["ctrl", "", ""] -> treat trailing empty pair as '+'
    if combo.endswith("++"):
        parts = parts[:-2] + ["+"]
    return [_resolve_key(p) for p in parts if p != ""]


class Output:
    """Thread-safe injector that remembers what is held so it can be released."""

    def __init__(self):
        self._held_keys: dict = {}     # key -> refcount
        self._held_buttons: dict = {}  # button -> refcount
        self._scroll_acc = [0.0, 0.0]
        self._move_acc = [0.0, 0.0]

    # -- keyboard ----------------------------------------------------------
    def key_down(self, combo: str) -> None:
        keys = parse_combo(combo)
        with _lock:
            for k in keys:
                if self._held_keys.get(k, 0) == 0:
                    _kb.press(k)
                self._held_keys[k] = self._held_keys.get(k, 0) + 1

    def key_up(self, combo: str) -> None:
        keys = parse_combo(combo)
        with _lock:
            for k in reversed(keys):
                n = self._held_keys.get(k, 0)
                if n <= 1:
                    self._held_keys.pop(k, None)
                    _kb.release(k)
                else:
                    self._held_keys[k] = n - 1

    def tap(self, combo: str) -> None:
        keys = parse_combo(combo)
        with _lock:
            for k in keys:
                _kb.press(k)
            for k in reversed(keys):
                if self._held_keys.get(k, 0) == 0:
                    _kb.release(k)

    def type_text(self, text: str) -> None:
        with _lock:
            _kb.type(text)

    # -- mouse -------------------------------------------------------------
    def mouse_down(self, button: str) -> None:
        b = MOUSE_BUTTONS[button]
        with _lock:
            if self._held_buttons.get(b, 0) == 0:
                _mouse.press(b)
            self._held_buttons[b] = self._held_buttons.get(b, 0) + 1

    def mouse_up(self, button: str) -> None:
        b = MOUSE_BUTTONS[button]
        with _lock:
            n = self._held_buttons.get(b, 0)
            if n <= 1:
                self._held_buttons.pop(b, None)
                _mouse.release(b)
            else:
                self._held_buttons[b] = n - 1

    def click(self, button: str, count: int = 1) -> None:
        with _lock:
            _mouse.click(MOUSE_BUTTONS[button], count)

    def move(self, dx: float, dy: float) -> None:
        """Relative move; keeps sub-pixel remainder so slow movement stays smooth."""
        self._move_acc[0] += dx
        self._move_acc[1] += dy
        ix, iy = int(self._move_acc[0]), int(self._move_acc[1])
        if ix == 0 and iy == 0:
            return
        self._move_acc[0] -= ix
        self._move_acc[1] -= iy
        with _lock:
            _mouse.move(ix, iy)

    def move_to(self, x: int, y: int) -> None:
        with _lock:
            _mouse.position = (x, y)

    def position(self):
        return _mouse.position

    def scroll(self, dx: float, dy: float) -> None:
        """Scroll in wheel notches (fractions allowed). +dy = up, +dx = right."""
        self._scroll_acc[0] += dx
        self._scroll_acc[1] += dy
        if IS_MAC:
            self._scroll_mac()
        elif IS_WIN:
            self._scroll_win()
        else:
            ix, iy = int(self._scroll_acc[0]), int(self._scroll_acc[1])
            if ix or iy:
                self._scroll_acc[0] -= ix
                self._scroll_acc[1] -= iy
                with _lock:
                    _mouse.scroll(ix, iy)

    def _scroll_mac(self) -> None:
        import Quartz

        px = int(self._scroll_acc[0] * _MAC_PX_PER_NOTCH)
        py = int(self._scroll_acc[1] * _MAC_PX_PER_NOTCH)
        if not px and not py:
            return
        self._scroll_acc[0] -= px / _MAC_PX_PER_NOTCH
        self._scroll_acc[1] -= py / _MAC_PX_PER_NOTCH
        ev = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitPixel, 2, py, px)
        with _lock:
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

    def _scroll_win(self) -> None:
        import ctypes
        from pynput._util.win32 import INPUT, INPUT_union, MOUSEINPUT, SendInput

        dx = int(self._scroll_acc[0] * _WIN_DELTA_PER_NOTCH)
        dy = int(self._scroll_acc[1] * _WIN_DELTA_PER_NOTCH)
        if not dx and not dy:
            return
        self._scroll_acc[0] -= dx / _WIN_DELTA_PER_NOTCH
        self._scroll_acc[1] -= dy / _WIN_DELTA_PER_NOTCH
        with _lock:
            for flag, amount in ((MOUSEINPUT.WHEEL, dy), (MOUSEINPUT.HWHEEL, dx)):
                if amount:
                    inp = INPUT(type=INPUT.MOUSE, value=INPUT_union(
                        mi=MOUSEINPUT(dwFlags=flag, mouseData=amount)))
                    SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    # -- safety ------------------------------------------------------------
    def release_all(self) -> None:
        with _lock:
            for b in list(self._held_buttons):
                try:
                    _mouse.release(b)
                except Exception:
                    pass
            for k in list(self._held_keys):
                try:
                    _kb.release(k)
                except Exception:
                    pass
            self._held_buttons.clear()
            self._held_keys.clear()


def has_permissions() -> bool:
    """macOS needs Accessibility permission to inject events."""
    if not IS_MAC:
        return True
    try:
        import HIServices

        return bool(HIServices.AXIsProcessTrusted())
    except Exception:
        return True


def request_permissions() -> None:
    """Show the macOS Accessibility prompt for this process."""
    if not IS_MAC:
        return
    try:
        import HIServices

        HIServices.AXIsProcessTrustedWithOptions({HIServices.kAXTrustedCheckOptionPrompt: True})
    except Exception:
        pass


def setup_platform() -> None:
    if IS_WIN:
        # Without DPI awareness Windows scales our cursor coordinates on HiDPI screens.
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
