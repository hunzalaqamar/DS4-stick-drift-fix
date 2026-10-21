"""Settings and per-controller calibration, saved under %APPDATA%\\StickDriftFix."""

import json
import os
import threading

DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "StickDriftFix")

DEFAULTS = {
    "fix_enabled": True,       # create corrected virtual controllers
    "output": "xbox360",       # "xbox360" or "ds4"
    "deadzone": 0.06,
    "outer": 0.97,
    "smart_recenter": True,    # re-centre when a stick is let go and settles off-centre
    "smart_settle": 0.4,
    "auto_putdown": True,      # re-centre when the gyro says the controller is lying still
    "putdown_sec": 1.5,
    "gyro_still": 0.015,       # rad/s - desk ~0.002, in hands 0.05+
    "idle_recenter": True,     # controllers without gyro: re-centre after a long idle
    "idle_sec": 5.0,
    "combo": True,             # hold BACK + START to re-centre
    "combo_sec": 1.0,
    "feedback": True,          # buzz / light bar flash on re-centre
    "hide_original": False,    # use HidHide so games only see the fixed controller
    "theme": "system",         # "system", "light" or "dark"
    "games_only": True,        # send nothing while Windows menus/apps are in front
}
CHOICES = {"output": ("xbox360", "ds4"), "theme": ("system", "light", "dark")}

LIMITS = {
    "deadzone": (0.0, 0.3), "outer": (0.7, 1.0), "smart_settle": (0.2, 1.5),
    "putdown_sec": (0.5, 5.0), "gyro_still": (0.003, 0.1), "idle_sec": (2.0, 30.0),
    "combo_sec": (0.3, 3.0),
}


def _load(name):
    try:
        with open(os.path.join(DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(name, data):
    try:
        os.makedirs(DIR, exist_ok=True)
        tmp = os.path.join(DIR, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, os.path.join(DIR, name))
    except OSError:
        pass


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        saved = _load("settings.json")
        self.settings = dict(DEFAULTS)
        self.settings.update({k: v for k, v in saved.items() if k in DEFAULTS})
        self.profiles = _load("profiles.json")

    def update(self, changes):
        with self.lock:
            for k, v in changes.items():
                if k not in DEFAULTS:
                    continue
                kind = type(DEFAULTS[k])
                if kind is bool:
                    v = bool(v)
                elif kind is float:
                    lo, hi = LIMITS.get(k, (-1e9, 1e9))
                    v = min(hi, max(lo, float(v)))
                elif k in CHOICES and v not in CHOICES[k]:
                    continue
                self.settings[k] = v
            _save("settings.json", self.settings)
        return dict(self.settings)

    def save_profiles(self):
        with self.lock:
            _save("profiles.json", self.profiles)
