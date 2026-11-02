"""macOS fallback: read HID gamepads that SDL ignores.

SDL's IOKit backend only accepts devices whose *primary* HID usage is
Joystick/GamePad. Many Bluetooth pads in "Android" / Fire TV mode (e.g. GuliKit,
8BitDo, Amazon Fire controller) report Consumer Control as primary usage, so SDL
never sees them. Here we talk to IOKit directly and map the standard Android HID
gamepad layout onto gppcc's input names.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import threading
import time

import objc
from Foundation import NSArray, NSDictionary

V = ctypes.c_void_p
_io = ctypes.CDLL(ctypes.util.find_library("IOKit"))
_cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))


def _sig(f, res, *args):
    f.restype = res
    f.argtypes = list(args)


_sig(_io.IOHIDManagerCreate, V, V, ctypes.c_uint32)
_sig(_io.IOHIDManagerSetDeviceMatchingMultiple, None, V, V)
_sig(_io.IOHIDManagerOpen, ctypes.c_int32, V, ctypes.c_uint32)
_sig(_io.IOHIDManagerCopyDevices, V, V)
_sig(_io.IOHIDManagerScheduleWithRunLoop, None, V, V, V)
_sig(_io.IOHIDManagerRegisterInputValueCallback, None, V, V, V)
_sig(_io.IOHIDDeviceGetProperty, V, V, V)
_sig(_io.IOHIDDeviceCopyMatchingElements, V, V, V, ctypes.c_uint32)
_sig(_io.IOHIDValueGetElement, V, V)
_sig(_io.IOHIDValueGetIntegerValue, ctypes.c_long, V)
_sig(_io.IOHIDElementGetDevice, V, V)
for _n in ("Type", "UsagePage", "Usage", "LogicalMin", "LogicalMax"):
    _sig(getattr(_io, "IOHIDElementGet" + _n), ctypes.c_long, V)
_sig(_cf.CFSetGetCount, ctypes.c_long, V)
_sig(_cf.CFSetGetValues, None, V, V)
_sig(_cf.CFArrayGetCount, ctypes.c_long, V)
_sig(_cf.CFArrayGetValueAtIndex, V, V, ctypes.c_long)
_sig(_cf.CFRunLoopGetCurrent, V)
_sig(_cf.CFRunLoopRunInMode, ctypes.c_int32, V, ctypes.c_double, ctypes.c_bool)
_sig(_cf.CFRelease, None, V)
_sig(_cf.CFStringCreateWithCString, V, V, ctypes.c_char_p, ctypes.c_uint32)


def _cfstr(s: str):
    return _cf.CFStringCreateWithCString(None, s.encode(), 0x08000100)  # UTF-8, never freed (few, static)

_VALUE_CB = ctypes.CFUNCTYPE(None, V, ctypes.c_int32, V, V)

# Private run-loop mode so servicing HID callbacks never touches other sources.
_MODE = _cfstr("gppcc.hid")

PAGE_DESKTOP, PAGE_SIM, PAGE_BUTTON, PAGE_CONSUMER = 0x01, 0x02, 0x09, 0x0C

# Android / Linux HID gamepad convention: button usage N -> BTN_SOUTH + N-1
BUTTONS = {
    1: "A", 2: "B", 4: "X", 5: "Y",
    7: "LB", 8: "RB", 9: "LT", 10: "RT",
    11: "Back", 12: "Start", 13: "Guide", 14: "LS", 15: "RS",
}
CONSUMER = {
    0x223: "Guide",       # AC Home
    0x224: "Back",        # AC Back
    0x40: "Start",        # Menu
    0xCD: "Misc",         # Play/Pause
    0xB5: "MediaNext", 0xB6: "MediaPrev", 0xB3: "MediaForward", 0xB4: "MediaRewind",
    0xE9: "VolUp", 0xEA: "VolDown", 0xB8: "MediaEject",
}
HAT_DIRS = {0: ("Up",), 1: ("Up", "Right"), 2: ("Right",), 3: ("Down", "Right"),
            4: ("Down",), 5: ("Down", "Left"), 6: ("Left",), 7: ("Up", "Left")}


def _ptr(obj) -> int:
    return objc.pyobjc_id(obj)


_KEYS: dict = {}


def _prop(dev, key: str):
    k = _KEYS.get(key) or _KEYS.setdefault(key, _cfstr(key))
    p = _io.IOHIDDeviceGetProperty(dev, k)
    return objc.objc_object(c_void_p=p) if p else None


class HidGamepads:
    """Tracks HID joysticks/gamepads; first one found is the active device."""

    def __init__(self):
        self._lock = threading.Lock()
        self._mgr = _io.IOHIDManagerCreate(None, 0)
        match = [
            NSDictionary.dictionaryWithDictionary_({"DeviceUsagePage": 1, "DeviceUsage": u})
            for u in (4, 5)  # Joystick, GamePad
        ]
        self._match_arr = NSArray.arrayWithArray_(match)
        _io.IOHIDManagerSetDeviceMatchingMultiple(self._mgr, _ptr(self._match_arr))
        self._cb = _VALUE_CB(self._on_value)
        _io.IOHIDManagerRegisterInputValueCallback(self._mgr, self._cb, None)
        _io.IOHIDManagerScheduleWithRunLoop(self._mgr, _cf.CFRunLoopGetCurrent(), _MODE)
        _io.IOHIDManagerOpen(self._mgr, 0)
        self.device = None
        self.name = None
        self._layout: dict = {}
        self._values: dict = {}       # (page, usage) -> int
        self._last_check = 0.0

    # ---------------------------------------------------------- devices
    def _devices(self) -> list:
        s = _io.IOHIDManagerCopyDevices(self._mgr)
        if not s:
            return []
        n = _cf.CFSetGetCount(s)
        arr = (V * n)()
        _cf.CFSetGetValues(s, arr)
        _cf.CFRelease(s)
        return [arr[i] for i in range(n)]

    def refresh(self) -> None:
        devs = self._devices()
        if self.device in devs:
            return
        with self._lock:
            self.device, self.name, self._values = None, None, {}
        for d in devs:
            layout = self._read_layout(d)
            if layout["buttons"] and layout["axes"]:
                with self._lock:
                    self.device = d
                    self._layout = layout
                    self.name = str(_prop(d, "Product") or "HID gamepad")
                return

    @staticmethod
    def _read_layout(dev) -> dict:
        els = _io.IOHIDDeviceCopyMatchingElements(dev, None, 0)
        layout = {"buttons": set(), "axes": {}}
        if not els:
            return layout
        for k in range(_cf.CFArrayGetCount(els)):
            e = _cf.CFArrayGetValueAtIndex(els, k)
            page, usage = _io.IOHIDElementGetUsagePage(e), _io.IOHIDElementGetUsage(e)
            if page == PAGE_BUTTON:
                layout["buttons"].add(usage)
            elif page in (PAGE_DESKTOP, PAGE_SIM) and usage:
                layout["axes"][(page, usage)] = (_io.IOHIDElementGetLogicalMin(e),
                                                 _io.IOHIDElementGetLogicalMax(e))
        _cf.CFRelease(els)
        return layout

    # ---------------------------------------------------------- input
    def _on_value(self, ctx, result, sender, value) -> None:
        try:
            el = _io.IOHIDValueGetElement(value)
            if _io.IOHIDElementGetDevice(el) != self.device:
                return
            key = (_io.IOHIDElementGetUsagePage(el), _io.IOHIDElementGetUsage(el))
            self._values[key] = _io.IOHIDValueGetIntegerValue(value)
        except Exception:
            pass

    def pump(self) -> None:
        # Deliver queued HID callbacks (runs only our private mode, returns immediately).
        while _cf.CFRunLoopRunInMode(_MODE, 0, True) == 4:  # kCFRunLoopRunHandledSource
            pass
        now = time.monotonic()
        if now - self._last_check > 1.0:
            self._last_check = now
            self.refresh()

    def fill(self, st) -> None:
        """Write the current state into a gamepad.PadState."""
        v = self._values
        axes = self._layout.get("axes", {})

        def norm(key, center=True):
            if key not in v or key not in axes:
                return 0.0
            lo, hi = axes[key]
            if hi <= lo:
                return 0.0
            x = (v[key] - lo) / (hi - lo)
            return max(-1.0, min(1.0, x * 2 - 1)) if center else max(0.0, min(1.0, x))

        for (page, usage), val in v.items():
            if not val:
                continue
            if page == PAGE_BUTTON:
                st.buttons.add(BUTTONS.get(usage, f"B{usage}"))
            elif page == PAGE_CONSUMER and usage in CONSUMER:
                st.buttons.add(CONSUMER[usage])

        D = PAGE_DESKTOP
        st.lx, st.ly = norm((D, 0x30)), norm((D, 0x31))
        if (D, 0x33) in axes and (D, 0x34) in axes:   # Rx/Ry sticks, Z/Rz triggers
            st.rx, st.ry = norm((D, 0x33)), norm((D, 0x34))
            st.lt, st.rt = norm((D, 0x32), False), norm((D, 0x35), False)
        else:                                          # Android: Z/Rz = right stick
            st.rx, st.ry = norm((D, 0x32)), norm((D, 0x35))
        if (PAGE_SIM, 0xC5) in axes:
            st.lt = norm((PAGE_SIM, 0xC5), False)      # brake
        if (PAGE_SIM, 0xC4) in axes:
            st.rt = norm((PAGE_SIM, 0xC4), False)      # accelerator
        # Digital triggers count as fully pressed
        if "LT" in st.buttons:
            st.lt = 1.0
        if "RT" in st.buttons:
            st.rt = 1.0
        st.buttons.discard("LT")
        st.buttons.discard("RT")

        hat = v.get((D, 0x39))
        if hat is not None and (D, 0x39) in axes:
            lo, _ = axes[(D, 0x39)]
            st.buttons.update(HAT_DIRS.get(hat - lo, ()))
