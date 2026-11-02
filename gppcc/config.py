"""Profile storage and schema normalisation.

Profiles live as JSON files in ~/.gppcc/profiles (override with GPPCC_HOME).
"""

from __future__ import annotations

import copy
import json
import os
import re
import uuid
from pathlib import Path

HOME = Path(os.environ.get("GPPCC_HOME", Path.home() / ".gppcc"))
PROFILE_DIR = HOME / "profiles"
STATE_FILE = HOME / "state.json"

# ---------------------------------------------------------------------------
# Schema vocabulary (also served to the web UI via /api/meta)
# ---------------------------------------------------------------------------

BUTTONS = [
    "A", "B", "X", "Y",
    "LB", "RB", "LT", "RT",
    "Back", "Start", "Guide",
    "LS", "RS",
    "Up", "Down", "Left", "Right",
    "Misc", "Paddle1", "Paddle2", "Paddle3", "Paddle4", "Touchpad",
]
# Stick directions exposed as digital inputs so they can be bound too.
STICK_DIRECTIONS = [
    f"{s}_{d}" for s in ("LS", "RS") for d in ("Up", "Down", "Left", "Right")
]
INPUTS = BUTTONS + STICK_DIRECTIONS

TRIGGERS = ["press", "release", "hold", "repeat", "toggle"]

ACTION_TYPES = [
    "none", "key", "mouse", "scroll", "move", "text", "macro",
    "precision", "toggle_enabled", "profile", "next_profile",
]

MOUSE_BUTTONS = ["left", "right", "middle"]

MACRO_OPS = [
    "tap", "key_down", "key_up", "text",
    "click", "mouse_down", "mouse_up",
    "move", "move_to", "scroll", "wait",
]

STICKS = ["left", "right", "none"]

DEFAULT_PROFILE = {
    "name": "default",
    "cursor": {
        "stick": "left",
        "deadzone": 0.15,
        "max_speed": 1500,        # pixels / second at full deflection
        "curve": 2.2,             # response exponent (1 = linear)
        "precision_factor": 0.3,  # speed multiplier while "precision" is held
        "invert_y": False,
    },
    "scroll": {
        "stick": "right",
        "deadzone": 0.2,
        "speed": 18,              # wheel notches / second at full deflection
        "curve": 2.0,
        "horizontal": True,
        "invert": False,
    },
    "trigger_threshold": 0.5,     # LT/RT and stick directions count as pressed above this
    "repeat_delay_ms": 400,
    "repeat_interval_ms": 60,
    "bindings": [
        {"input": "A", "trigger": "hold", "action": {"type": "mouse", "button": "left"}},
        {"input": "B", "trigger": "hold", "action": {"type": "mouse", "button": "right"}},
        {"input": "X", "trigger": "press", "action": {"type": "key", "keys": "enter"}},
        {"input": "Y", "trigger": "press", "action": {"type": "key", "keys": "esc"}},
        {"input": "LB", "trigger": "press", "action": {"type": "key", "keys": "ctrl+shift+tab"}},
        {"input": "RB", "trigger": "press", "action": {"type": "key", "keys": "ctrl+tab"}},
        {"input": "LT", "trigger": "hold", "action": {"type": "precision"}},
        {"input": "RT", "trigger": "toggle", "action": {"type": "mouse", "button": "left"}},
        {"input": "Up", "trigger": "repeat", "action": {"type": "key", "keys": "up"}},
        {"input": "Down", "trigger": "repeat", "action": {"type": "key", "keys": "down"}},
        {"input": "Left", "trigger": "repeat", "action": {"type": "key", "keys": "left"}},
        {"input": "Right", "trigger": "repeat", "action": {"type": "key", "keys": "right"}},
        {"input": "Start", "trigger": "press", "action": {"type": "key", "keys": "media_play_pause"}},
        {"input": "Start", "with": "Back", "trigger": "press", "action": {"type": "toggle_enabled"}},
        {"input": "LS", "trigger": "press", "action": {"type": "macro", "name": "Select all + copy"}},
        {"input": "RS", "trigger": "press", "action": {"type": "key", "keys": "mod+v"}},
        {"input": "Y", "with": "LB", "trigger": "toggle", "action": {"type": "macro", "name": "Auto clicker"}},
    ],
    "macros": {
        "Select all + copy": {
            "repeat": 1,
            "steps": [
                {"op": "tap", "keys": "mod+a"},
                {"op": "wait", "ms": 60},
                {"op": "tap", "keys": "mod+c"},
            ],
        },
        "Auto clicker": {
            "repeat": 1,
            "steps": [
                {"op": "click", "button": "left", "count": 1},
                {"op": "wait", "ms": 80},
            ],
        },
    },
}


# ---------------------------------------------------------------------------
# Normalisation – keeps whatever the UI sends within a sane shape
# ---------------------------------------------------------------------------

def _num(v, default, lo=None, hi=None, cast=float):
    try:
        v = cast(v)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _choice(v, options, default):
    return v if v in options else default


def _norm_action(a) -> dict:
    a = a if isinstance(a, dict) else {}
    t = _choice(a.get("type"), ACTION_TYPES, "none")
    out = {"type": t}
    if t == "key":
        out["keys"] = str(a.get("keys", "")).strip()
    elif t == "mouse":
        out["button"] = _choice(a.get("button"), MOUSE_BUTTONS, "left")
        out["count"] = _num(a.get("count", 1), 1, 1, 3, int)
    elif t == "scroll":
        out["dx"] = _num(a.get("dx", 0), 0, -100, 100)
        out["dy"] = _num(a.get("dy", 0), 0, -100, 100)
    elif t == "move":
        out["dx"] = _num(a.get("dx", 0), 0, -10000, 10000, int)
        out["dy"] = _num(a.get("dy", 0), 0, -10000, 10000, int)
    elif t == "text":
        out["text"] = str(a.get("text", ""))
    elif t in ("macro", "profile"):
        out["name"] = str(a.get("name", ""))
    return out


def _norm_binding(b) -> dict | None:
    if not isinstance(b, dict):
        return None
    inp = b.get("input")
    if not isinstance(inp, str) or not inp:
        return None
    with_ = b.get("with") or None
    if with_ == inp or (with_ is not None and not isinstance(with_, str)):
        with_ = None
    out = {
        "id": str(b.get("id") or uuid.uuid4().hex[:8]),
        "input": inp,
        "with": with_,
        "trigger": _choice(b.get("trigger"), TRIGGERS, "press"),
        "action": _norm_action(b.get("action")),
    }
    if "repeat_ms" in b and b["repeat_ms"] not in (None, ""):
        out["repeat_ms"] = _num(b["repeat_ms"], 60, 10, 10000, int)
    return out


def _norm_step(s) -> dict | None:
    if not isinstance(s, dict):
        return None
    op = _choice(s.get("op"), MACRO_OPS, None)
    if op is None:
        return None
    out = {"op": op}
    if op in ("tap", "key_down", "key_up"):
        out["keys"] = str(s.get("keys", "")).strip()
    elif op == "text":
        out["text"] = str(s.get("text", ""))
    elif op in ("click", "mouse_down", "mouse_up"):
        out["button"] = _choice(s.get("button"), MOUSE_BUTTONS, "left")
        if op == "click":
            out["count"] = _num(s.get("count", 1), 1, 1, 3, int)
    elif op == "move":
        out["dx"] = _num(s.get("dx", 0), 0, -10000, 10000, int)
        out["dy"] = _num(s.get("dy", 0), 0, -10000, 10000, int)
    elif op == "move_to":
        out["x"] = _num(s.get("x", 0), 0, -100000, 100000, int)
        out["y"] = _num(s.get("y", 0), 0, -100000, 100000, int)
    elif op == "scroll":
        out["dx"] = _num(s.get("dx", 0), 0, -100, 100)
        out["dy"] = _num(s.get("dy", 0), 0, -100, 100)
    elif op == "wait":
        out["ms"] = _num(s.get("ms", 100), 100, 0, 600000, int)
    return out


def normalize_profile(p, name: str | None = None) -> dict:
    p = p if isinstance(p, dict) else {}
    d = DEFAULT_PROFILE
    cur_in = p.get("cursor") if isinstance(p.get("cursor"), dict) else {}
    scr_in = p.get("scroll") if isinstance(p.get("scroll"), dict) else {}
    dc, ds = d["cursor"], d["scroll"]

    macros_in = p.get("macros") if isinstance(p.get("macros"), dict) else {}
    macros = {}
    for mname, m in macros_in.items():
        if not isinstance(m, dict) or not str(mname).strip():
            continue
        steps = [s for s in map(_norm_step, m.get("steps") or []) if s]
        macros[str(mname).strip()] = {
            "repeat": _num(m.get("repeat", 1), 1, 1, 100000, int),
            "steps": steps,
        }

    return {
        "name": name or str(p.get("name") or "default"),
        "cursor": {
            "stick": _choice(cur_in.get("stick"), STICKS, dc["stick"]),
            "deadzone": _num(cur_in.get("deadzone"), dc["deadzone"], 0, 0.9),
            "max_speed": _num(cur_in.get("max_speed"), dc["max_speed"], 10, 20000),
            "curve": _num(cur_in.get("curve"), dc["curve"], 0.5, 5),
            "precision_factor": _num(cur_in.get("precision_factor"), dc["precision_factor"], 0.01, 1),
            "invert_y": bool(cur_in.get("invert_y", dc["invert_y"])),
        },
        "scroll": {
            "stick": _choice(scr_in.get("stick"), STICKS, ds["stick"]),
            "deadzone": _num(scr_in.get("deadzone"), ds["deadzone"], 0, 0.9),
            "speed": _num(scr_in.get("speed"), ds["speed"], 0.5, 200),
            "curve": _num(scr_in.get("curve"), ds["curve"], 0.5, 5),
            "horizontal": bool(scr_in.get("horizontal", ds["horizontal"])),
            "invert": bool(scr_in.get("invert", ds["invert"])),
        },
        "trigger_threshold": _num(p.get("trigger_threshold"), d["trigger_threshold"], 0.05, 0.95),
        "repeat_delay_ms": _num(p.get("repeat_delay_ms"), d["repeat_delay_ms"], 0, 5000, int),
        "repeat_interval_ms": _num(p.get("repeat_interval_ms"), d["repeat_interval_ms"], 10, 5000, int),
        "bindings": [b for b in map(_norm_binding, p.get("bindings") or []) if b],
        "macros": macros,
    }


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[\w\- .]{1,64}$")


def valid_name(name: str) -> bool:
    return bool(_NAME_RE.match(name or "")) and name.strip() == name and not name.startswith(".")


def _path(name: str) -> Path:
    if not valid_name(name):
        raise ValueError(f"invalid profile name: {name!r}")
    return PROFILE_DIR / f"{name}.json"


def ensure_home() -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    if not any(PROFILE_DIR.glob("*.json")):
        save_profile(normalize_profile(copy.deepcopy(DEFAULT_PROFILE)))


def list_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILE_DIR.glob("*.json") if valid_name(p.stem))


def load_profile(name: str) -> dict:
    with open(_path(name), encoding="utf-8") as f:
        return normalize_profile(json.load(f), name)


def save_profile(profile: dict) -> dict:
    profile = normalize_profile(profile, profile.get("name"))
    path = _path(profile["name"])
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
    os.replace(tmp, path)
    return profile


def delete_profile(name: str) -> None:
    _path(name).unlink(missing_ok=True)


def get_active_name() -> str:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            name = json.load(f).get("active")
        if name in list_profiles():
            return name
    except (OSError, ValueError, AttributeError):
        pass
    names = list_profiles()
    return "default" if "default" in names else names[0]


def set_active_name(name: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"active": name}, f)


# ---------------------------------------------------------------------------
# Per-controller button remapping (shared by all profiles)
#   {"GuliKit Controller DW": {"B3": "X", "X": "Y", "Guide": "none"}, ...}
# Keys are the names the controller reports, values what gppcc should see.
# ---------------------------------------------------------------------------

DEVICES_FILE = HOME / "devices.json"
REMAP_TARGETS = INPUTS + ["none"]


def _norm_remap(m) -> dict:
    out = {}
    if isinstance(m, dict):
        for k, v in m.items():
            if isinstance(k, str) and 0 < len(k) <= 32 and v in REMAP_TARGETS and k != v:
                out[k] = v
    return out


def load_remaps() -> dict:
    try:
        with open(DEVICES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {str(d): _norm_remap(m) for d, m in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_remap(device: str, mapping) -> dict:
    remaps = load_remaps()
    mapping = _norm_remap(mapping)
    if mapping:
        remaps[device] = mapping
    else:
        remaps.pop(device, None)
    DEVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DEVICES_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(remaps, f, indent=2)
    os.replace(tmp, DEVICES_FILE)
    return mapping
