"""Engine logic tests with a fake gamepad and a recording output (no real input injected)."""

import os
import tempfile
import time
import unittest

os.environ["GPPCC_HOME"] = tempfile.mkdtemp()

from gppcc import config, engine as engine_mod  # noqa: E402
from gppcc.gamepad import PadState  # noqa: E402


class RecOut:
    def __init__(self):
        self.ev = []
        self.moved = [0.0, 0.0]
        self.scrolled = [0.0, 0.0]

    def __getattr__(self, name):
        return lambda *a: self.ev.append((name, *a))

    def move(self, dx, dy):
        self.moved[0] += dx
        self.moved[1] += dy

    def scroll(self, dx, dy):
        self.scrolled[0] += dx
        self.scrolled[1] += dy

    def release_all(self):
        self.ev.append(("release_all",))


class FakePads:
    name = "Fake"
    active_name = "Fake"

    def poll(self):
        return PadState()

    def info(self):
        return {"connected": True, "name": "Fake", "standard": True, "count": 1}

    def close(self):
        pass


def pad(*buttons, lx=0.0, ly=0.0, rx=0.0, ry=0.0, lt=0.0, rt=0.0):
    s = PadState()
    s.buttons = set(buttons)
    s.lx, s.ly, s.rx, s.ry, s.lt, s.rt = lx, ly, rx, ry, lt, rt
    return s


class EngineTest(unittest.TestCase):
    def setUp(self):
        engine_mod.Gamepads = FakePads
        engine_mod.Output = RecOut
        config.ensure_home()
        self.e = engine_mod.Engine("default")
        self.out = self.e.out

    def tick(self, st, dt=0.01):
        self.e._tick(st, dt)

    def names(self):
        return [e[0] for e in self.out.ev]

    def test_hold_mouse(self):
        self.tick(pad("A"))
        self.tick(pad("A"))
        self.tick(pad())
        self.assertEqual(self.out.ev, [("mouse_down", "left"), ("mouse_up", "left")])

    def test_press_key(self):
        self.tick(pad("X"))
        self.tick(pad())
        self.assertEqual(self.out.ev, [("tap", "enter")])

    def test_toggle_drag_lock(self):
        self.tick(pad(rt=1.0)); self.tick(pad())
        self.assertEqual(self.out.ev, [("mouse_down", "left")])
        self.tick(pad(rt=1.0)); self.tick(pad())
        self.assertEqual(self.out.ev[-1], ("mouse_up", "left"))

    def test_combo_overrides_plain_and_suppresses_release(self):
        # Back+Start toggles enabled; plain Start (play/pause) must not fire.
        self.tick(pad("Back"))
        self.tick(pad("Back", "Start"))
        self.assertFalse(self.e.enabled)
        self.assertNotIn(("tap", "media_play_pause"), self.out.ev)
        self.tick(pad())
        # While disabled only toggle_enabled works
        self.tick(pad("X")); self.tick(pad())
        self.assertNotIn(("tap", "enter"), self.out.ev)
        self.tick(pad("Back")); self.tick(pad("Back", "Start")); self.tick(pad())
        self.assertTrue(self.e.enabled)

    def test_repeat(self):
        p = self.e.profile
        p["repeat_delay_ms"] = 0
        p["repeat_interval_ms"] = 10
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.06:
            self.tick(pad("Up"))
            time.sleep(0.005)
        self.tick(pad())
        n = self.out.ev.count(("tap", "up"))
        self.assertGreaterEqual(n, 3)
        before = len(self.out.ev)
        self.tick(pad()); time.sleep(0.02); self.tick(pad())
        self.assertEqual(len(self.out.ev), before)

    def test_cursor_deadzone_and_precision(self):
        self.tick(pad(lx=0.1))
        self.assertEqual(self.out.moved, [0.0, 0.0])
        self.tick(pad(lx=1.0), dt=0.1)
        full = self.out.moved[0]
        self.assertAlmostEqual(full, self.e.profile["cursor"]["max_speed"] * 0.1, places=3)
        self.out.moved = [0.0, 0.0]
        self.tick(pad(lx=1.0, lt=1.0), dt=0.1)  # LT = precision (hold)
        self.assertAlmostEqual(self.out.moved[0], full * self.e.profile["cursor"]["precision_factor"], places=3)

    def test_scroll_direction(self):
        self.tick(pad(ry=-1.0), dt=0.1)  # stick up
        self.assertGreater(self.out.scrolled[1], 0)

    def test_macro_runs_and_toggle_loops(self):
        self.tick(pad("LS")); self.tick(pad())
        time.sleep(0.2)
        self.assertEqual([e for e in self.out.ev if e[0] == "tap"], [("tap", "mod+a"), ("tap", "mod+c")])
        # LB+Y toggles auto clicker loop
        self.out.ev.clear()
        self.tick(pad("LB")); self.tick(pad("LB", "Y")); self.tick(pad())
        time.sleep(0.3)
        self.tick(pad())
        self.assertIn("Auto clicker", self.e.snapshot()["macros_running"])
        self.tick(pad("LB")); self.tick(pad("LB", "Y")); self.tick(pad())
        time.sleep(0.15)
        n = self.names().count("click")
        self.assertGreaterEqual(n, 2)
        time.sleep(0.2)
        self.assertEqual(self.names().count("click"), n)  # stopped

    def test_macro_cleanup_releases_held(self):
        p = self.e.profile
        p["macros"]["hold"] = {"repeat": 1, "steps": [{"op": "key_down", "keys": "shift"}, {"op": "wait", "ms": 5000}]}
        p["bindings"].append({"id": "h1", "input": "Guide", "with": None, "trigger": "hold",
                              "action": {"type": "macro", "name": "hold"}})
        self.tick(pad("Guide")); time.sleep(0.05); self.tick(pad())
        time.sleep(0.1)
        self.assertEqual([e for e in self.out.ev if e[0].startswith("key")],
                         [("key_down", "shift"), ("key_up", "shift")])

    def test_profile_switch_binding(self):
        p = config.load_profile("default"); p["name"] = "other"; config.save_profile(p)
        self.e.profile["bindings"].append({"id": "np", "input": "Guide", "with": None, "trigger": "press",
                                           "action": {"type": "next_profile"}})
        self.tick(pad("Guide")); self.tick(pad())
        self.assertEqual(self.e.active_name, "other")
        config.delete_profile("other")
        config.set_active_name("default")


    def test_remap_translates_and_disables(self):
        self.e.set_remap("Fake", {"B3": "X", "X": "none"})
        self.tick(pad("B3")); self.tick(pad())
        self.assertIn(("tap", "enter"), self.out.ev)      # B3 now acts as X (Enter)
        self.out.ev.clear()
        self.tick(pad("X")); self.tick(pad())
        self.assertEqual(self.out.ev, [])                 # raw X disabled
        self.assertEqual(self.e.snapshot()["last_raw"]["name"], "X")
        self.e.set_remap("Fake", {})
        self.assertEqual(config.load_remaps(), {})


class FakeOverlay:
    def __init__(self):
        self.on_type = lambda combo: None
        self._open = False
        self.aims = []
        self.commits = 0
        self.closed = 0

    def open(self):
        self._open = True

    def close(self):
        if self._open:
            self.closed += 1
        self._open = False

    def is_open(self):
        return self._open

    def aim(self, lx, ly, rx, ry):
        self.aims.append((lx, ly, rx, ry))

    def commit(self):
        self.commits += 1
        self.on_type("x")  # pretend the aimed key was "x"

    def set_shift(self, on):
        self.shift = on

    def shutdown(self):
        pass


class OverlayTest(unittest.TestCase):
    def setUp(self):
        engine_mod.Gamepads = FakePads
        engine_mod.Output = RecOut
        config.ensure_home()
        self.e = engine_mod.Engine("default")
        self.out = self.e.out
        self.ov = FakeOverlay()
        self.ov.shift = False
        # Short-circuit lazy creation so no real Tk window is spawned.
        from gppcc import overlay as overlay_mod
        self._orig_create = overlay_mod.create
        overlay_mod.create = lambda cfg=None: self.ov

    def tearDown(self):
        from gppcc import overlay as overlay_mod
        overlay_mod.create = self._orig_create

    def tick(self, st, dt=0.01):
        self.e._tick(st, dt)

    def test_open_captures_pad_then_close(self):
        # Back + Y opens the overlay (default binding).
        self.tick(pad("Back"))
        self.tick(pad("Back", "Y"))
        self.assertTrue(self.ov.is_open())

        # While open the cursor must not move, even at full stick deflection.
        self.out.moved = [0.0, 0.0]
        self.tick(pad("Back", "Y", lx=1.0, ry=-1.0), dt=0.1)
        self.assertEqual(self.out.moved, [0.0, 0.0])
        self.assertEqual(self.out.scrolled, [0.0, 0.0])
        self.assertEqual(self.ov.aims[-1], (1.0, 0.0, 0.0, -1.0))

        # A types the aimed key (overlay stays open).
        self.tick(pad("A"))
        self.assertEqual(self.ov.commits, 1)
        self.assertIn(("tap", "x"), self.out.ev)
        self.assertTrue(self.ov.is_open())

        # B closes it; cursor control resumes afterwards.
        self.tick(pad("B"))
        self.assertFalse(self.ov.is_open())
        self.assertGreaterEqual(self.ov.closed, 1)
        self.out.moved = [0.0, 0.0]
        self.tick(pad()); self.tick(pad(lx=1.0), dt=0.1)
        self.assertGreater(self.out.moved[0], 0.0)

    def test_start_also_closes_overlay(self):
        # Start is a universal "close" so a mislabelled face button can't trap you.
        self.tick(pad("Back")); self.tick(pad("Back", "Y"))
        self.assertTrue(self.ov.is_open())
        self.tick(pad()); self.tick(pad("Start"))
        self.assertFalse(self.ov.is_open())

    def test_pause_closes_overlay(self):
        self.tick(pad("Back")); self.tick(pad("Back", "Y"))
        self.assertTrue(self.ov.is_open())
        self.e.set_enabled(False)
        self.assertFalse(self.ov.is_open())

    def test_shift_toggle(self):
        # Default shift is X in toggle mode.
        self.tick(pad("Back")); self.tick(pad("Back", "Y"))
        self.assertFalse(self.ov.shift)
        self.tick(pad()); self.tick(pad("X"))
        self.assertTrue(self.ov.shift)
        self.tick(pad()); self.tick(pad("X"))
        self.assertFalse(self.ov.shift)

    def test_shift_hold(self):
        self.tick(pad("Back")); self.tick(pad("Back", "Y"))  # opens (loads default cfg)
        self.e._overlay_cfg = config.normalize_overlay({"shift": {"input": "X", "mode": "hold"}})
        self.tick(pad("X"))
        self.assertTrue(self.ov.shift)
        self.tick(pad())            # release X -> shift off
        self.assertFalse(self.ov.shift)


class OverlayConfigTest(unittest.TestCase):
    def test_defaults_split_alphabet(self):
        o = config.load_overlay()
        names = [w["name"] for w in o["wheels"]]
        self.assertIn("a–m", names)
        self.assertIn("n–z", names)
        am = o["wheels"][names.index("a–m")]["keys"]
        self.assertEqual([k["key"] for k in am], list("abcdefghijklm"))

    def test_normalize_rejects_junk_and_clamps(self):
        o = config.normalize_overlay({
            "theme": {"key_fill": "not-a-color", "font": "  Comic Sans  "},
            "size": {"width_frac": 5, "height_frac": -1},
            "sound": {"enabled": "yes", "style": "explode"},
            "wheels": [
                {"name": "x" * 50, "keys": [{"label": "LONGLABELHERE!!", "key": "a"}, {"key": ""}]},
                {"name": "empty", "keys": []},          # dropped: no valid keys
                "garbage",
            ],
        })
        self.assertEqual(o["theme"]["key_fill"], config.DEFAULT_OVERLAY["theme"]["key_fill"])
        self.assertEqual(o["theme"]["font"], "Comic Sans")
        self.assertEqual(o["size"]["width_frac"], 1.0)      # clamped
        self.assertEqual(o["size"]["height_frac"], 0.15)    # clamped
        self.assertEqual(o["sound"]["style"], config.DEFAULT_OVERLAY["sound"]["style"])
        self.assertEqual(len(o["wheels"]), 1)               # only the first wheel had a valid key
        self.assertLessEqual(len(o["wheels"][0]["name"]), 16)
        self.assertEqual(len(o["wheels"][0]["keys"]), 1)    # empty-key entry dropped

    def test_empty_falls_back_to_defaults(self):
        o = config.normalize_overlay({"wheels": []})
        self.assertEqual(len(o["wheels"]), len(config.DEFAULT_OVERLAY["wheels"]))

    def test_round_trip(self):
        o = config.load_overlay()
        o["wheels"][0]["name"] = "Letters"
        o["sound"]["style"] = "beep"
        config.save_overlay(o)
        self.assertEqual(config.load_overlay()["wheels"][0]["name"], "Letters")
        self.assertEqual(config.load_overlay()["sound"]["style"], "beep")


class ComboParseTest(unittest.TestCase):
    def test_parse(self):
        from pynput.keyboard import Key, KeyCode
        from gppcc.output import parse_combo, IS_MAC
        self.assertEqual(parse_combo("ctrl+shift+t"), [Key.ctrl, Key.shift, KeyCode.from_char("t")])
        self.assertEqual(parse_combo("mod+c")[0], Key.cmd if IS_MAC else Key.ctrl)
        self.assertEqual(parse_combo("ctrl++"), [Key.ctrl, KeyCode.from_char("+")])
        self.assertEqual(parse_combo("Escape"), [Key.esc])
        with self.assertRaises(ValueError):
            parse_combo("ctrl+nonsense")


if __name__ == "__main__":
    unittest.main()
