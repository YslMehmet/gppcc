"""On-screen radial keyboard overlay, driven entirely by the gamepad.

A binding with the ``keyboard`` action opens a small, always-on-top overlay that
shows a Cupertino-style picker of donut wheels. The left stick aims at a key, the
right stick flicks between wheels (neighbours shrink and fade on each side), **A**
types the aimed key, a configurable button toggles/holds **Shift**, and
**B** / **Start** close it.

The visuals are rendered with Pillow (supersampled, anti-aliased, gradient
shaded) and painted to a layered window with per-pixel alpha via
``UpdateLayeredWindow`` — so edges are smooth and transparent areas are
click-through, with no colour-key fringing.

The overlay runs its own thread for the window + paint loop; the engine (which
owns the main thread) only touches the plain attributes guarded by ``_lock``.
Typing goes through the engine's ``Output`` (via ``on_type``). Wheels, colours,
size, sound and the shift button come from an overlay config dict (see
``config.normalize_overlay``) and update live with :meth:`set_config`.

Windows only for now.
"""

from __future__ import annotations

import math
import sys
import threading
import time

IS_WIN = sys.platform.startswith("win")

_WINDOW_TITLE = "gppcc-keyboard-overlay"

# How far the left stick must leave centre before it aims at a key.
AIM_DEADZONE = 0.45
# Right-stick flick thresholds (must fall back below REARM before re-triggering).
FLICK_ON = 0.6
FLICK_REARM = 0.3


def supported() -> bool:
    return IS_WIN


def create(cfg: dict | None = None) -> "KeyboardOverlay | None":
    """Return an overlay instance for this platform, or None if unsupported."""
    if not supported():
        return None
    if cfg is None:
        from . import config
        cfg = config.load_overlay()
    return KeyboardOverlay(cfg)


class KeyboardOverlay:
    def __init__(self, cfg: dict | None = None, on_type=None):
        self.on_type = on_type or (lambda combo: None)
        self._lock = threading.Lock()
        self._open = False             # authoritative; set by the engine thread
        self._shutdown = False
        self._lx = self._ly = self._rx = self._ry = 0.0
        self._commits = 0              # pending A-presses to type
        self._rs_armed = True
        self._wheel = 0
        self._slot: int | None = None
        self._shift = False
        self._offset = 0.0             # animation: wheel-strip slide
        self._flash_until = 0.0
        self._started = False
        self._thread: threading.Thread | None = None
        self._cfg_version = 0
        self._set_cfg(cfg)

    def _set_cfg(self, cfg: dict | None) -> None:
        from . import config
        self._cfg = config.normalize_overlay(cfg or {})
        self._wheels = self._cfg["wheels"]
        self._theme = self._cfg["theme"]
        self._size = self._cfg["size"]
        self._sound = self._cfg["sound"]

    # ------------------------------------------------------ engine-thread API
    def ensure_started(self) -> None:
        if not self._started:
            self._started = True
            self._thread = threading.Thread(target=self._run_ui, daemon=True, name="overlay")
            self._thread.start()

    def set_config(self, cfg: dict) -> None:
        with self._lock:
            self._set_cfg(cfg)
            self._wheel = min(self._wheel, len(self._wheels) - 1)
            self._slot = None
            self._cfg_version += 1

    def set_shift(self, on: bool) -> None:
        with self._lock:
            self._shift = bool(on)

    def open(self) -> None:
        self.ensure_started()
        with self._lock:
            self._open = True
            self._slot = None
            self._rs_armed = True
            self._offset = 0.0

    def close(self) -> None:
        with self._lock:
            self._open = False
        hwnd = getattr(self, "_hwnd", None)
        if IS_WIN and hwnd:
            try:
                self._user32.ShowWindow(hwnd, 0)  # SW_HIDE
                self._visible = False
            except Exception:
                pass

    def is_open(self) -> bool:
        return self._open

    def aim(self, lx: float, ly: float, rx: float, ry: float) -> None:
        with self._lock:
            self._lx, self._ly, self._rx, self._ry = lx, ly, rx, ry

    def commit(self) -> None:
        with self._lock:
            self._commits += 1

    def shutdown(self) -> None:
        self._shutdown = True
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=1.0)

    # ------------------------------------------------------------- UI thread
    def _run_ui(self) -> None:
        try:
            import tkinter as tk
        except Exception as e:  # pragma: no cover - tkinter missing
            print(f"overlay unavailable: {e}")
            return

        self._tk = tk
        root = tk.Tk()
        root.title(_WINDOW_TITLE)
        root.overrideredirect(True)
        root.withdraw()
        self._root = root
        root.update_idletasks()
        try:
            self._init_window()
        except Exception as e:  # pragma: no cover
            print(f"overlay init failed: {e}")
            return
        self._visible = False
        self._applied_version = self._cfg_version
        self._last_sig = None

        self._schedule()
        root.mainloop()
        self._free_dib()
        self._root = self._tk = None

    def _schedule(self) -> None:
        self._root.after(16, self._tick_ui)

    def _tick_ui(self) -> None:
        if self._shutdown:
            try:
                self._root.quit()
                self._root.destroy()
            except Exception:
                pass
            return

        with self._lock:
            want_open = self._open
            lx, ly, rx, ry = self._lx, self._ly, self._rx, self._ry
            commits = self._commits
            self._commits = 0
            version = self._cfg_version
            shift = self._shift

        try:
            if version != self._applied_version:
                self._applied_version = version
                self._resize()
                self._last_sig = None

            if want_open and not self._visible:
                self._show_window()
                self._visible = True
            elif not want_open and self._visible:
                self._hide_window()
                self._visible = False

            if want_open:
                self._update_selection(lx, ly, rx, ry)
                for _ in range(commits):
                    self._do_commit()
                # Ease the wheel-slide animation toward rest.
                if abs(self._offset) > 0.02:
                    self._offset *= 0.62
                    if abs(self._offset) <= 0.02:
                        self._offset = 0.0
                self._maybe_paint(shift)
        except Exception as e:  # never let the loop die, or the window freezes
            print(f"overlay tick error: {e}")
        finally:
            self._schedule()

    def _update_selection(self, lx, ly, rx, ry) -> None:
        if self._rs_armed and abs(rx) >= FLICK_ON and abs(rx) >= abs(ry):
            step = 1 if rx > 0 else -1
            self._wheel = (self._wheel + step) % len(self._wheels)
            self._slot = None
            self._offset = max(-1.2, min(1.2, self._offset + step))
            self._rs_armed = False
            self._play_sound("switch")
        elif abs(rx) < FLICK_REARM and abs(ry) < FLICK_REARM:
            self._rs_armed = True

        keys = self._wheels[self._wheel]["keys"]
        if math.hypot(lx, ly) >= AIM_DEADZONE:
            n = len(keys)
            ang = math.degrees(math.atan2(ly, lx))
            best, bestd = None, 999.0
            for i in range(n):
                target = -90 + i * (360.0 / n)
                d = abs((ang - target + 180) % 360 - 180)
                if d < bestd:
                    best, bestd = i, d
            self._slot = best

    def _do_commit(self) -> None:
        keys = self._wheels[self._wheel]["keys"]
        if self._slot is None or self._slot >= len(keys):
            return
        combo = keys[self._slot]["key"]
        if self._shift and combo and "+" not in combo:
            combo = "shift+" + combo
        try:
            self.on_type(combo)
        except Exception as e:  # pragma: no cover
            print(f"overlay type error: {e}")
        self._flash_until = time.monotonic() + 0.12
        self._play_sound("type")

    def _play_sound(self, kind: str) -> None:
        if not IS_WIN or not self._sound.get("enabled"):
            return
        style = self._sound.get("style", "tick")
        if style == "off":
            return

        def go():
            try:
                import winsound
                if style == "beep":
                    winsound.MessageBeep(-1)
                else:
                    winsound.Beep(1500 if kind == "type" else 900, 18)
            except Exception:
                pass

        threading.Thread(target=go, daemon=True).start()

    # ----------------------------------------------------------- painting
    def _maybe_paint(self, shift: bool) -> None:
        flashing = time.monotonic() < self._flash_until
        animating = abs(self._offset) > 0.02
        sig = (self._wheel, self._slot, shift, flashing, round(self._offset, 2), self._applied_version)
        if sig == self._last_sig and not flashing:
            return
        self._last_sig = sig
        self._paint(shift, flashing, ss=2 if animating else 3)

    def _paint(self, shift: bool, flashing: bool, ss: int) -> None:
        from . import overlay_render
        img = overlay_render.render_frame(
            self._w, self._h, self._wheels, self._wheel, self._slot,
            shift, flashing, self._theme, offset=self._offset, ss=ss)
        self._blit(img)

    # --------------------------------------------- Windows layered window
    def _init_window(self) -> None:
        if not IS_WIN:
            return
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._user32 = ctypes.windll.user32
        self._gdi32 = ctypes.windll.gdi32

        class SIZE(ctypes.Structure):
            _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                        ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        self._SIZE, self._BLEND, self._BIH = SIZE, BLENDFUNCTION, BITMAPINFOHEADER
        self._POINT = wintypes.POINT

        # Declare handle types: on 64-bit, HWND/HDC/HGDIOBJ exceed c_int (ctypes'
        # default), which raises "int too long to convert" for large handles.
        H = ctypes.c_void_p
        c_int, c_uint = ctypes.c_int, ctypes.c_uint
        P = ctypes.POINTER
        u, g = self._user32, self._gdi32
        u.GetDC.restype = H; u.GetDC.argtypes = [H]
        u.ReleaseDC.argtypes = [H, H]
        u.GetAncestor.restype = H; u.GetAncestor.argtypes = [H, c_uint]
        u.ShowWindow.argtypes = [H, c_int]
        u.SetWindowPos.argtypes = [H, H, c_int, c_int, c_int, c_int, c_uint]
        u.UpdateLayeredWindow.restype = c_int
        u.UpdateLayeredWindow.argtypes = [H, H, P(self._POINT), P(SIZE),
                                          H, P(self._POINT), wintypes.COLORREF, P(BLENDFUNCTION), c_uint]
        g.CreateCompatibleDC.restype = H; g.CreateCompatibleDC.argtypes = [H]
        g.CreateDIBSection.restype = H
        g.CreateDIBSection.argtypes = [H, P(BITMAPINFOHEADER), c_uint, P(ctypes.c_void_p), H, c_uint]
        g.SelectObject.restype = H; g.SelectObject.argtypes = [H, H]
        g.DeleteObject.argtypes = [H]
        g.DeleteDC.argtypes = [H]
        LONG_PTR = ctypes.c_longlong if ctypes.sizeof(H) == 8 else c_int
        get = getattr(u, "GetWindowLongPtrW", u.GetWindowLongW)
        setf = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
        get.restype = LONG_PTR; get.argtypes = [H, c_int]
        setf.restype = LONG_PTR; setf.argtypes = [H, c_int, LONG_PTR]

        child = int(self._root.winfo_id())
        self._hwnd = u.GetAncestor(child, 2) or child  # GA_ROOT
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_TOPMOST = 0x00000008
        ex = get(self._hwnd, GWL_EXSTYLE)
        setf(self._hwnd, GWL_EXSTYLE,
             ex | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)

        self._screen_dc = u.GetDC(None)
        self._mem_dc = g.CreateCompatibleDC(self._screen_dc)
        self._dib = None
        self._dib_bits = None
        self._dib_size = (0, 0)
        self._resize()

    def _screen_size(self):
        return self._user32.GetSystemMetrics(0), self._user32.GetSystemMetrics(1)

    def _resize(self) -> None:
        if not IS_WIN:
            return
        sw, sh = self._screen_size()
        self._w = max(320, int(sw * self._size["width_frac"]))
        self._h = max(240, int(sh * self._size["height_frac"]))
        self._x, self._y = (sw - self._w) // 2, (sh - self._h) // 2
        self._ensure_dib(self._w, self._h)

    def _ensure_dib(self, w, h) -> None:
        if self._dib_size == (w, h) and self._dib:
            return
        self._free_dib()
        ctypes = self._ctypes
        bih = self._BIH()
        bih.biSize = ctypes.sizeof(self._BIH)
        bih.biWidth = w
        bih.biHeight = -h            # top-down
        bih.biPlanes = 1
        bih.biBitCount = 32
        bih.biCompression = 0        # BI_RGB
        bits = ctypes.c_void_p()
        self._dib = self._gdi32.CreateDIBSection(self._screen_dc, ctypes.byref(bih), 0,
                                                 ctypes.byref(bits), None, 0)
        self._dib_bits = bits
        self._dib_size = (w, h)
        self._old_obj = self._gdi32.SelectObject(self._mem_dc, self._dib)

    def _free_dib(self) -> None:
        if getattr(self, "_dib", None):
            try:
                self._gdi32.SelectObject(self._mem_dc, self._old_obj)
                self._gdi32.DeleteObject(self._dib)
            except Exception:
                pass
            self._dib = None

    def _blit(self, img) -> None:
        if not IS_WIN or not self._dib:
            return
        from PIL import Image, ImageChops

        r, g, b, a = img.split()
        # Premultiply by alpha and reorder to BGRA for the DIB.
        pm = Image.merge("RGBA", (ImageChops.multiply(b, a), ImageChops.multiply(g, a),
                                  ImageChops.multiply(r, a), a))
        raw = pm.tobytes("raw", "RGBA")
        ctypes = self._ctypes
        ctypes.memmove(self._dib_bits, raw, len(raw))

        pt_dst = self._POINT(self._x, self._y)
        size = self._SIZE(self._w, self._h)
        pt_src = self._POINT(0, 0)
        blend = self._BLEND(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
        ULW_ALPHA = 2
        self._user32.UpdateLayeredWindow(
            self._hwnd, self._screen_dc, ctypes.byref(pt_dst), ctypes.byref(size),
            self._mem_dc, ctypes.byref(pt_src), 0, ctypes.byref(blend), ULW_ALPHA)

    def _show_window(self) -> None:
        if not IS_WIN:
            return
        self._last_sig = None
        self._paint(self._shift, False, ss=3)  # ensure content before showing
        SW_SHOWNOACTIVATE, HWND_TOPMOST = 4, -1
        SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x2, 0x1, 0x10
        self._user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)
        self._user32.SetWindowPos(self._hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                  SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def _hide_window(self) -> None:
        if IS_WIN:
            self._user32.ShowWindow(self._hwnd, 0)  # SW_HIDE
