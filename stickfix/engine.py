"""Input engine: reads every real controller through SDL2, corrects the sticks
and feeds a virtual controller per real one. Runs in its own thread; the UI
talks to it through call() and reads snapshot."""

import ctypes
import logging
import os
import queue
import sys
import threading
import time
from collections import deque

import sdl2 as S

from . import __version__, winutil
from .drift import DriftGuard, Stick
from .hidhide import HidHide
from .virtual import VIGEM_ERROR, VirtualPad

SAMPLE_DT = 0.004      # 250 Hz analysis samples
CHECK_DT = 0.1         # re-centre rules run 10x per second
SNAPSHOT_DT = 1 / 60
PROFILE_SAVE_DT = 2.0
NUM_BUTTONS = 21
BACK, START = 4, 6

TYPE_NAMES = {1: "Xbox 360", 2: "Xbox One / Series", 3: "PS3", 4: "PS4", 5: "Switch Pro",
              7: "PS5", 8: "Amazon Luna", 9: "Stadia", 10: "NVIDIA Shield",
              11: "Joy-Con (L)", 12: "Joy-Con (R)", 13: "Joy-Con pair"}
FAMILY = {1: "xbox", 2: "xbox", 3: "playstation", 4: "playstation", 7: "playstation",
          5: "nintendo", 11: "nintendo", 12: "nintendo", 13: "nintendo"}
POWER = {-1: "unknown", 0: "empty", 1: "low", 2: "medium", 3: "full", 4: "full", 5: "wired"}


def _s(b):
    return b.decode(errors="replace") if b else ""


class Device:
    def __init__(self, engine, index, instance):
        self.engine = engine
        self.gc = S.SDL_GameControllerOpen(index)
        if not self.gc:
            raise RuntimeError(_s(S.SDL_GetError()))
        js = S.SDL_GameControllerGetJoystick(self.gc)
        self.js = js
        self.iid = S.SDL_JoystickInstanceID(js)
        self.instance = instance
        self.name = _s(S.SDL_GameControllerName(self.gc)) or "Controller"
        self.vid = S.SDL_GameControllerGetVendor(self.gc)
        self.pid = S.SDL_GameControllerGetProduct(self.gc)
        self.ctype = S.SDL_GameControllerGetType(self.gc)
        self.serial = _s(S.SDL_GameControllerGetSerial(self.gc))
        guid = ctypes.create_string_buffer(33)
        S.SDL_JoystickGetGUIDString(S.SDL_JoystickGetGUID(js), guid, 33)
        self.key = f"{self.vid:04x}:{self.pid:04x}:{self.serial or guid.value.decode()}"

        self.has_gyro = (bool(S.SDL_GameControllerHasSensor(self.gc, S.SDL_SENSOR_GYRO))
                         and S.SDL_GameControllerSetSensorEnabled(self.gc, S.SDL_SENSOR_GYRO, S.SDL_TRUE) == 0)
        self.has_rumble = bool(S.SDL_GameControllerHasRumble(self.gc))
        self.has_led = bool(S.SDL_GameControllerHasLED(self.gc))

        profile = engine.store.profiles.get(self.key, {})
        self.left = Stick("left", profile.get("left"))
        self.right = Stick("right", profile.get("right"))
        self.guard = DriftGuard(self.left, self.right, engine.settings, self.has_gyro)

        self.axes = [0.0] * 6
        self.out = [0.0] * 4
        self.buttons = [False] * NUM_BUTTONS
        self.dirty = True
        self.pad = None
        self.pad_error = None
        self.calibrating = False
        self.led_reset_at = None
        self.updates = 0
        self.rate = 0
        self.connected_at = time.perf_counter()
        self.read()

    # -- input ---------------------------------------------------------------
    def read(self):
        g = self.gc
        ax = self.axes
        for i in range(4):
            ax[i] = max(-1.0, S.SDL_GameControllerGetAxis(g, i) / 32767)
        ax[4] = max(0, S.SDL_GameControllerGetAxis(g, 4)) / 32767
        ax[5] = max(0, S.SDL_GameControllerGetAxis(g, 5)) / 32767
        b = self.buttons
        for i in range(NUM_BUTTONS):
            b[i] = S.SDL_GameControllerGetButton(g, i) == 1
        self.correct()

    def correct(self):
        s = self.engine.settings
        if self.guard.calibrated:
            ax = self.axes
            self.out[0], self.out[1] = self.left.output(ax[0], ax[1], s["deadzone"], s["outer"])
            self.out[2], self.out[3] = self.right.output(ax[2], ax[3], s["deadzone"], s["outer"])
        else:
            self.out[:] = [0.0] * 4

    def sample(self, now):
        if now - self.connected_at < 0.3:
            # SDL reports zeros until the first input report arrives; don't
            # let those blank values into calibration.
            self.read()
            return
        ax = self.axes
        self.left.add(now, ax[0], ax[1])
        self.right.add(now, ax[2], ax[3])
        combo = self.buttons[BACK] and self.buttons[START]
        busy = any(self.buttons) or ax[4] > 0.05 or ax[5] > 0.05
        self.guard.feed_inputs(now, busy, combo)

    def send(self):
        if not self.pad:
            return
        s = self.engine.settings
        b = self.buttons
        o = self.out
        hide_menu = s["combo"] and b[BACK] and b[START]
        try:
            self.pad.send(b, self.axes[4], self.axes[5], o[0], o[1], o[2], o[3], hide_menu)
        except Exception as e:
            self.pad_error = str(e)

    # -- output devices ------------------------------------------------------
    def start_virtual(self, kind):
        self.stop_virtual()
        try:
            self.pad = VirtualPad(kind, lambda large, small: self.engine.post(("rumble", self.iid, large, small)))
            self.pad_error = None
            self.send()
        except Exception as e:
            self.pad, self.pad_error = None, str(e)

    def stop_virtual(self):
        if self.pad:
            self.pad.close()
            self.pad = None

    def rumble(self, low, high, ms):
        if self.has_rumble:
            S.SDL_GameControllerRumble(self.gc, int(low), int(high), int(ms))

    def flash(self, now, rgb=(0, 200, 90), buzz=False):
        if not self.engine.settings["feedback"]:
            return
        if self.has_led:
            S.SDL_GameControllerSetLED(self.gc, *rgb)
            self.led_reset_at = now + 0.6
        if buzz:
            self.rumble(30000, 30000, 160)

    def tick_led(self, now):
        if self.led_reset_at and now >= self.led_reset_at:
            self.led_reset_at = None
            S.SDL_GameControllerSetLED(self.gc, 0, 60, 220)

    def close(self):
        self.stop_virtual()
        S.SDL_GameControllerClose(self.gc)

    # -- UI ------------------------------------------------------------------
    def snapshot(self):
        s = self.engine.settings
        noise = self.guard.gyro_noise

        def stick(st):
            return {"centre": [st.cx, st.cy], "lo": st.lo, "hi": st.hi, "edge": st.at_edge(),
                    "calib": st.calib}

        return {
            "id": self.iid, "name": self.name, "type": TYPE_NAMES.get(self.ctype, "Controller"),
            "family": FAMILY.get(self.ctype, "generic"), "vid": f"{self.vid:04X}", "pid": f"{self.pid:04X}",
            "serial": self.serial, "power": POWER.get(S.SDL_JoystickCurrentPowerLevel(self.js), "unknown"),
            "gyro": self.has_gyro, "gyroNoise": noise,
            "held": None if noise is None else noise >= s["gyro_still"],
            "rumble": self.has_rumble, "led": self.has_led,
            "raw": self.axes[:4], "out": self.out[:], "triggers": self.axes[4:6],
            "buttons": [1 if b else 0 for b in self.buttons],
            "left": stick(self.left), "right": stick(self.right),
            "calibrated": self.guard.calibrated, "calibrating": self.calibrating,
            "virtual": {"on": self.pad is not None, "kind": self.pad.kind if self.pad else None,
                        "error": self.pad_error},
            "rate": self.rate,
        }


class Engine(threading.Thread):
    def __init__(self, store):
        super().__init__(name="engine", daemon=True)
        self.store = store
        self.settings = store.settings
        self.queue = queue.Queue()
        self.devices = {}
        self.ignored = set()
        self.events = deque(maxlen=120)
        self.event_id = 0
        self.hidhide = HidHide()
        self.remappers = []
        self.snapshot = {"ready": False, "devices": [], "events": []}
        self.sdl_error = None
        self._stopping = False
        self._profiles_dirty_at = None

    # -- thread-safe API -----------------------------------------------------
    def post(self, item):
        self.queue.put(item)

    def call(self, fn, *args, timeout=3.0):
        """Run fn(*args) on the engine thread and return its result."""
        box = {"done": threading.Event()}
        self.queue.put(("call", fn, args, box))
        box["done"].wait(timeout)
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def stop(self):
        self._stopping = True

    def log(self, kind, msg, device=None):
        logging.getLogger("stickfix.engine").info("[%s] %s%s", kind, msg, f" ({device})" if device else "")
        self.event_id += 1
        self.events.append({"id": self.event_id, "time": time.strftime("%H:%M:%S"), "kind": kind,
                            "msg": msg, "device": device})

    # -- engine-thread operations (use via call()) ----------------------------
    def set_fix(self, on):
        self.store.update({"fix_enabled": bool(on)})
        kind = self.settings["output"]
        for d in self.devices.values():
            d.start_virtual(kind) if on else d.stop_virtual()
        self._apply_hiding()
        self.log("fix", "Drift fix on" if on else "Drift fix off - games see the original controller")
        return on

    def set_output(self, kind):
        self.store.update({"output": kind})
        if self.settings["fix_enabled"]:
            for d in self.devices.values():
                d.start_virtual(self.settings["output"])
        self.log("fix", "Output switched to " + ("DualShock 4" if kind == "ds4" else "Xbox 360"))

    def set_hiding(self, on):
        self.store.update({"hide_original": bool(on)})
        self._apply_hiding()
        if on and self.hidhide.error:
            self.log("warn", "HidHide: " + self.hidhide.error)
        return self.settings["hide_original"]

    def recentre(self, dev_id, which):
        d = self.devices.get(dev_id)
        if not d:
            return []
        done = d.guard.recentre(which, time.perf_counter())
        if done:
            self.log("manual", "Re-centred " + " & ".join(done) + " stick", d.name)
            d.flash(time.perf_counter())
        else:
            self.log("warn", "Let go of the sticks, then re-centre", d.name)
        d.correct()
        d.dirty = True
        return done

    def calibration(self, dev_id, action):
        d = self.devices.get(dev_id)
        if not d:
            return False
        if action == "start":
            d.left.start_calibration()
            d.right.start_calibration()
            d.calibrating = True
            return True
        apply = action == "finish"
        changed = [st.name for st in (d.left, d.right) if st.finish_calibration(apply)]
        d.calibrating = False
        if apply:
            self.log("calib", "Range calibrated: " + (" & ".join(changed) + " stick" if changed
                                                       else "no change (move the sticks to their edges)"), d.name)
        self._mark_profiles()
        return bool(changed)

    def reset_profile(self, dev_id):
        d = self.devices.get(dev_id)
        if not d:
            return False
        d.left.reset_range()
        d.right.reset_range()
        d.guard.recentre("both", time.perf_counter())
        self.log("calib", "Calibration reset to defaults", d.name)
        self._mark_profiles()
        return True

    def test_rumble(self, dev_id, low, high, ms):
        d = self.devices.get(dev_id)
        if d:
            d.rumble(low, high, ms)

    def test_led(self, dev_id, r, g, b):
        d = self.devices.get(dev_id)
        if d and d.has_led:
            S.SDL_GameControllerSetLED(d.gc, int(r), int(g), int(b))

    # -- internals -----------------------------------------------------------
    def _mark_profiles(self):
        self._profiles_dirty_at = self._profiles_dirty_at or time.perf_counter()

    def _app_paths(self):
        paths = {os.path.abspath(sys.executable)}
        base = os.path.dirname(sys.executable)
        for exe in ("python.exe", "pythonw.exe"):
            if os.path.exists(os.path.join(base, exe)):
                paths.add(os.path.join(base, exe))
        return sorted(paths)

    def _apply_hiding(self):
        hh = self.hidhide
        want = self.settings["hide_original"] and self.settings["fix_enabled"]
        if not hh.installed:
            return
        if want:
            inst = [d.instance for d in self.devices.values() if d.instance]
            hh.hide(inst, self._app_paths())
        else:
            hh.restore()

    def _add(self, index):
        iid = S.SDL_JoystickGetDeviceInstanceID(index)
        if iid in self.devices or iid in self.ignored:
            return
        instance = winutil.instance_from_path(S.SDL_JoystickPathForIndex(index))
        if winutil.is_virtual(instance):
            self.ignored.add(iid)
            return
        if not S.SDL_IsGameController(index):
            self.ignored.add(iid)
            self.log("warn", f"{_s(S.SDL_JoystickNameForIndex(index))} isn't recognised as a gamepad")
            return
        try:
            d = Device(self, index, instance)
        except Exception as e:
            self.log("warn", f"Couldn't open controller: {e}")
            return
        self.devices[d.iid] = d
        self.log("connect", f"Connected: {d.name}" + ("" if d.has_gyro else " (no gyro - idle re-centre)"), d.name)
        if self.settings["fix_enabled"]:
            d.start_virtual(self.settings["output"])
            if d.pad_error:
                self.log("warn", "Virtual controller failed: " + d.pad_error, d.name)
        self._apply_hiding()

    def _remove(self, iid):
        d = self.devices.pop(iid, None)
        self.ignored.discard(iid)
        if d:
            self._save_device(d)
            d.close()
            self.log("disconnect", f"Disconnected: {d.name}", d.name)

    def _save_device(self, d):
        if d.left.dirty or d.right.dirty:
            d.left.dirty = d.right.dirty = False
            self.store.profiles[d.key] = {"name": d.name, "left": d.left.saved(), "right": d.right.saved()}
            self.store.save_profiles()

    def _handle(self, ev, now):
        t = ev.type
        if t == S.SDL_CONTROLLERDEVICEADDED or t == S.SDL_JOYDEVICEADDED:
            self._add(ev.cdevice.which if t == S.SDL_CONTROLLERDEVICEADDED else ev.jdevice.which)
        elif t == S.SDL_CONTROLLERDEVICEREMOVED:
            self._remove(ev.cdevice.which)
        elif t == S.SDL_CONTROLLERSENSORUPDATE:
            d = self.devices.get(ev.csensor.which)
            if d and ev.csensor.sensor == S.SDL_SENSOR_GYRO:
                data = ev.csensor.data
                d.guard.feed_gyro(now, data[0], data[1], data[2])
                d.updates += 1  # one gyro update per input report
        elif t in (S.SDL_CONTROLLERAXISMOTION, S.SDL_CONTROLLERBUTTONDOWN, S.SDL_CONTROLLERBUTTONUP):
            d = self.devices.get(ev.caxis.which if t == S.SDL_CONTROLLERAXISMOTION else ev.cbutton.which)
            if d:
                d.dirty = True
                if not d.has_gyro:  # gyro pads count reports via sensor updates
                    d.updates += 1

    def _commands(self):
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                return
            if item[0] == "call":
                _, fn, args, box = item
                try:
                    box["result"] = fn(*args)
                except Exception as e:
                    box["error"] = e
                box["done"].set()
            elif item[0] == "rumble":
                _, iid, large, small = item
                d = self.devices.get(iid)
                if d:
                    on = large or small
                    d.rumble(large * 257, small * 257, 10000 if on else 0)

    def _watch_remappers(self):
        # tasklist is slow, so it never runs on the input thread.
        while not self._stopping:
            self.remappers = winutil.running_remappers()
            time.sleep(5)

    def _build_snapshot(self):
        hh = self.hidhide
        self.snapshot = {
            "ready": True,
            "version": __version__,
            "error": self.sdl_error,
            "fix": self.settings["fix_enabled"],
            "settings": dict(self.settings),
            "vigem": {"ok": VIGEM_ERROR is None, "error": VIGEM_ERROR},
            "hidhide": {"installed": hh.installed, "active": hh.cloaked_by_us, "error": hh.error},
            "remappers": self.remappers,
            "devices": [d.snapshot() for d in self.devices.values()],
            "events": list(self.events)[-40:],
        }

    def run(self):
        S.SDL_SetHint(b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", b"1")
        S.SDL_SetHint(b"SDL_JOYSTICK_HIDAPI_PS4_RUMBLE", b"1")
        S.SDL_SetHint(b"SDL_JOYSTICK_HIDAPI_PS5_RUMBLE", b"1")
        if S.SDL_Init(S.SDL_INIT_GAMECONTROLLER | S.SDL_INIT_SENSOR) != 0:
            self.sdl_error = _s(S.SDL_GetError())
            self._build_snapshot()
            return
        ver = S.SDL_version()
        S.SDL_GetVersion(ctypes.byref(ver))
        self.log("info", f"Engine started (SDL {ver.major}.{ver.minor}.{ver.patch})")
        if VIGEM_ERROR:
            self.log("warn", "ViGEmBus driver not found - install it to create fixed controllers")

        ev = S.SDL_Event()
        now = time.perf_counter()
        next_sample = next_check = next_snap = now
        next_second = now + 1
        threading.Thread(target=self._watch_remappers, name="remappers", daemon=True).start()
        try:
            while not self._stopping:
                self._commands()
                if S.SDL_WaitEventTimeout(ctypes.byref(ev), 2):
                    now = time.perf_counter()
                    self._handle(ev, now)
                    while S.SDL_PollEvent(ctypes.byref(ev)):
                        self._handle(ev, now)
                now = time.perf_counter()

                for d in self.devices.values():
                    if d.dirty:
                        d.dirty = False
                        d.read()
                        d.send()

                if now >= next_sample:
                    for d in self.devices.values():
                        d.sample(now)
                    next_sample = now + SAMPLE_DT

                if now >= next_check:
                    next_check = now + CHECK_DT
                    for d in self.devices.values():
                        for kind, msg in d.guard.check(now):
                            self.log(kind, msg, d.name)
                            if kind in ("combo", "smart", "putdown", "idle"):
                                d.flash(now, buzz=kind == "combo")
                            d.dirty = True
                        if d.left.dirty or d.right.dirty:
                            self._mark_profiles()
                        d.tick_led(now)

                if self._profiles_dirty_at and now - self._profiles_dirty_at > PROFILE_SAVE_DT:
                    self._profiles_dirty_at = None
                    for d in self.devices.values():
                        self._save_device(d)

                if now >= next_second:
                    next_second = now + 1
                    for d in self.devices.values():
                        d.rate, d.updates = d.updates, 0

                if now >= next_snap:
                    next_snap = now + SNAPSHOT_DT
                    self._build_snapshot()
        finally:
            for d in list(self.devices.values()):
                self._save_device(d)
                d.close()
            self.hidhide.restore()
            S.SDL_Quit()
