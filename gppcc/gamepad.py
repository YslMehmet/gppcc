"""Gamepad input via SDL2 (pygame-ce).

Uses SDL's GameController API so Xbox, PlayStation, Switch Pro and most other
pads share one standard layout. Unknown devices fall back to the raw joystick
API, exposing buttons as B0, B1, ...
"""

from __future__ import annotations

import os
import sys
import time

# Must be set before SDL initialises: keep receiving input while another app has focus.
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
# SDL otherwise swallows SIGINT/SIGTERM (turns them into SDL_QUIT), so Ctrl+C would not quit.
os.environ.setdefault("SDL_NO_SIGNAL_HANDLERS", "1")
# macOS: run as a menu-bar utility (no Dock icon, never steals focus).
os.environ.setdefault("SDL_MAC_BACKGROUND_APP", "1")

import pygame  # noqa: E402
from pygame._sdl2 import controller as sdl_controller  # noqa: E402

_BUTTON_MAP = {
    "A": pygame.CONTROLLER_BUTTON_A,
    "B": pygame.CONTROLLER_BUTTON_B,
    "X": pygame.CONTROLLER_BUTTON_X,
    "Y": pygame.CONTROLLER_BUTTON_Y,
    "Back": pygame.CONTROLLER_BUTTON_BACK,
    "Guide": pygame.CONTROLLER_BUTTON_GUIDE,
    "Start": pygame.CONTROLLER_BUTTON_START,
    "LS": pygame.CONTROLLER_BUTTON_LEFTSTICK,
    "RS": pygame.CONTROLLER_BUTTON_RIGHTSTICK,
    "LB": pygame.CONTROLLER_BUTTON_LEFTSHOULDER,
    "RB": pygame.CONTROLLER_BUTTON_RIGHTSHOULDER,
    "Up": pygame.CONTROLLER_BUTTON_DPAD_UP,
    "Down": pygame.CONTROLLER_BUTTON_DPAD_DOWN,
    "Left": pygame.CONTROLLER_BUTTON_DPAD_LEFT,
    "Right": pygame.CONTROLLER_BUTTON_DPAD_RIGHT,
    # SDL >= 2.0.14 extras (no pygame constants yet)
    "Misc": 15, "Paddle1": 16, "Paddle2": 17, "Paddle3": 18, "Paddle4": 19, "Touchpad": 20,
}
_MAX_BUTTON = getattr(pygame, "CONTROLLER_BUTTON_MAX", 21)
_BUTTON_MAP = {k: v for k, v in _BUTTON_MAP.items() if v < _MAX_BUTTON}


class PadState:
    """Normalised snapshot: sticks in [-1, 1] (y down = +), triggers in [0, 1]."""

    __slots__ = ("buttons", "lx", "ly", "rx", "ry", "lt", "rt")

    def __init__(self):
        self.buttons: set[str] = set()
        self.lx = self.ly = self.rx = self.ry = 0.0
        self.lt = self.rt = 0.0

    def to_dict(self) -> dict:
        return {
            "buttons": sorted(self.buttons),
            "lx": round(self.lx, 3), "ly": round(self.ly, 3),
            "rx": round(self.rx, 3), "ry": round(self.ry, 3),
            "lt": round(self.lt, 3), "rt": round(self.rt, 3),
        }


def _axis(v: float) -> float:
    return max(-1.0, min(1.0, v / 32767.0))


class Gamepads:
    def __init__(self):
        pygame.init()
        pygame.joystick.init()
        sdl_controller.init()
        self.device = None     # Controller or Joystick
        self.is_standard = False
        self.name = None
        self._last_scan = 0.0
        self.quit_requested = False
        # macOS fallback for HID pads SDL ignores (e.g. GuliKit / 8BitDo in Android mode).
        self.hid = None
        if sys.platform == "darwin":
            try:
                from .machid import HidGamepads

                self.hid = HidGamepads()
                self.hid.refresh()
            except Exception as e:  # never let the fallback break normal SDL input
                print(f"HID fallback unavailable: {e}")
        self.rescan()

    def rescan(self) -> None:
        self._last_scan = time.monotonic()
        if self.device is not None and not self._detached():
            return
        self.device = None
        self.name = None
        count = pygame.joystick.get_count()
        # Prefer a recognised controller; otherwise take the first raw joystick.
        for i in range(count):
            if sdl_controller.is_controller(i):
                c = sdl_controller.Controller(i)
                self.device, self.is_standard, self.name = c, True, c.name
                return
        if count:
            j = pygame.joystick.Joystick(0)
            j.init()
            self.device, self.is_standard, self.name = j, False, j.get_name()

    def poll(self) -> PadState:
        rescan = False
        for ev in pygame.event.get():
            if ev.type in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED):
                rescan = True
            elif ev.type == pygame.QUIT:
                self.quit_requested = True
        if rescan:
            self.rescan()
        elif self.device is None and time.monotonic() - self._last_scan > 2.0:
            self.rescan()

        if self.hid is not None:
            self.hid.pump()

        st = PadState()
        d = self.device
        if d is None:
            if self.hid is not None and self.hid.device is not None:
                self.hid.fill(st)
            return st
        try:
            if self.is_standard:
                for name, idx in _BUTTON_MAP.items():
                    if d.get_button(idx):
                        st.buttons.add(name)
                st.lx = _axis(d.get_axis(pygame.CONTROLLER_AXIS_LEFTX))
                st.ly = _axis(d.get_axis(pygame.CONTROLLER_AXIS_LEFTY))
                st.rx = _axis(d.get_axis(pygame.CONTROLLER_AXIS_RIGHTX))
                st.ry = _axis(d.get_axis(pygame.CONTROLLER_AXIS_RIGHTY))
                st.lt = max(0.0, _axis(d.get_axis(pygame.CONTROLLER_AXIS_TRIGGERLEFT)))
                st.rt = max(0.0, _axis(d.get_axis(pygame.CONTROLLER_AXIS_TRIGGERRIGHT)))
            else:
                self._poll_raw(d, st)
        except pygame.error:
            self.device = None
            self.name = None
        return st

    def _detached(self) -> bool:
        try:
            return not (self.device.attached() if self.is_standard else self.device.get_init())
        except Exception:
            return True

    @staticmethod
    def _poll_raw(j, st: PadState) -> None:
        for i in range(j.get_numbuttons()):
            if j.get_button(i):
                st.buttons.add(f"B{i}")
        n = j.get_numaxes()
        ax = [j.get_axis(i) for i in range(n)]  # already in [-1, 1]
        if n >= 2:
            st.lx, st.ly = ax[0], ax[1]
        if n >= 4:
            st.rx, st.ry = ax[2], ax[3]
        if j.get_numhats():
            hx, hy = j.get_hat(0)
            if hy > 0:
                st.buttons.add("Up")
            if hy < 0:
                st.buttons.add("Down")
            if hx < 0:
                st.buttons.add("Left")
            if hx > 0:
                st.buttons.add("Right")

    @property
    def active_name(self):
        if self.device is not None:
            return self.name
        if self.hid is not None and self.hid.device is not None:
            return self.hid.name
        return None

    def info(self) -> dict:
        hid = self.device is None and self.hid is not None and self.hid.device is not None
        return {
            "connected": self.device is not None or hid,
            "name": self.active_name,
            "standard": self.is_standard or hid,
            "backend": "sdl" if self.device is not None else "hid" if hid else None,
            "count": pygame.joystick.get_count(),
        }

    def close(self) -> None:
        try:
            sdl_controller.quit()
            pygame.quit()
        except Exception:
            pass
