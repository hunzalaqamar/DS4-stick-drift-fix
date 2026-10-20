"""
DS4 stick fix - re-centres a drifting DualShock 4 (or clone) and passes it to
games as a virtual Xbox 360 controller. Use it INSTEAD of DS4Windows.

How the sticks get re-centred:
  1. At startup - leave the sticks alone for a second.
  2. Automatically when you put the controller down: if the gyro says nobody
     is holding it, no buttons are pressed and both sticks are resting for
     ~1.5 s, wherever the sticks rest becomes the new centre.
  3. On demand: hold SHARE + OPTIONS for 1 second with your thumbs off the
     sticks. The controller buzzes and flashes green to confirm.
  4. Small, slow drift close to the centre is tracked continuously.

Each axis' real travel is learned as you use the sticks (push each stick
all the way round once on a new controller) and saved in stick_ranges.json,
so worn sticks that can't reach the edges still give full output.

Nothing is re-centred while you are holding the controller and actually
pushing a stick, so it never fights your real input.

Usage:
  python ds4_stick_fix.py            run the fix
  python ds4_stick_fix.py --monitor  only print live readings (no virtual pad)
"""

import argparse
import ctypes
import json
import math
import os
import subprocess
import sys
import threading
import time
from collections import deque

import hid
import vgamepad as vg

# ---- tuning --------------------------------------------------------------
DEADZONE = 0.06        # radial dead zone applied after re-centring
OUTER_ZONE = 0.97      # stick output reaches 1.0 at this deflection
STEADY_COUNTS = 2      # raw jitter (0-255 scale) allowed for a "resting" stick
WINDOW_N = 125         # samples of history the checks look at (0.5 s at 250 Hz)
CHECK_EVERY = 25       # run the checks every N samples (10x per second)
FINE_RADIUS = 0.10     # slow drift tracking only happens this close to centre
FINE_ALPHA = 0.05      # how quickly slow drift tracking follows
GYRO_STILL = 15.0      # gyro noise below this = lying still (measured: desk ~2, in hands 50+)
PUTDOWN_SEC = 1.5      # how long it must lie still before re-centring
COMBO_SEC = 1.0        # hold SHARE + OPTIONS this long to re-centre
TRIGGER_IDLE = 10      # trigger values above this count as "pressed"
START_SPAN = 40        # assumed travel each side of centre until an axis is pushed fully
RANGES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stick_ranges.json")

SONY_VID = 0x054C
DS4_PIDS = {0x05C4, 0x09CC, 0x0BA0}  # DS4 v1, DS4 v2, DS4 USB wireless adapter

B = vg.XUSB_BUTTON
HAT_TO_DPAD = {
    0: B.XUSB_GAMEPAD_DPAD_UP,
    1: B.XUSB_GAMEPAD_DPAD_UP | B.XUSB_GAMEPAD_DPAD_RIGHT,
    2: B.XUSB_GAMEPAD_DPAD_RIGHT,
    3: B.XUSB_GAMEPAD_DPAD_DOWN | B.XUSB_GAMEPAD_DPAD_RIGHT,
    4: B.XUSB_GAMEPAD_DPAD_DOWN,
    5: B.XUSB_GAMEPAD_DPAD_DOWN | B.XUSB_GAMEPAD_DPAD_LEFT,
    6: B.XUSB_GAMEPAD_DPAD_LEFT,
    7: B.XUSB_GAMEPAD_DPAD_UP | B.XUSB_GAMEPAD_DPAD_LEFT,
}
# (report byte, bit, xbox button)
BUTTON_MAP = [
    (5, 0x10, B.XUSB_GAMEPAD_X),               # square
    (5, 0x20, B.XUSB_GAMEPAD_A),               # cross
    (5, 0x40, B.XUSB_GAMEPAD_B),               # circle
    (5, 0x80, B.XUSB_GAMEPAD_Y),               # triangle
    (6, 0x01, B.XUSB_GAMEPAD_LEFT_SHOULDER),   # L1
    (6, 0x02, B.XUSB_GAMEPAD_RIGHT_SHOULDER),  # R1
    (6, 0x10, B.XUSB_GAMEPAD_BACK),            # share
    (6, 0x20, B.XUSB_GAMEPAD_START),           # options
    (6, 0x40, B.XUSB_GAMEPAD_LEFT_THUMB),      # L3
    (6, 0x80, B.XUSB_GAMEPAD_RIGHT_THUMB),     # R3
    (7, 0x01, B.XUSB_GAMEPAD_GUIDE),           # PS
]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def s16(r, i):
    v = r[i] | (r[i + 1] << 8)
    return v - 65536 if v > 32767 else v


def std(values):
    n = len(values)
    m = sum(values) / n
    return math.sqrt(sum((v - m) ** 2 for v in values) / n)


def axis(v, c, lo, hi):
    """Raw 0-255 value -> -1..1 around centre c, scaled per side against the
    axis' real travel (lo..hi), so a worn stick whose range is squashed or
    off-centre still reaches full deflection both ways."""
    span = (hi - c) if v >= c else (c - lo)
    return max(-1.0, min(1.0, (v - c) / max(span, 8)))


def load_ranges():
    try:
        with open(RANGES_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_ranges(ranges):
    try:
        with open(RANGES_FILE, "w") as f:
            json.dump(ranges, f, indent=2)
    except OSError:
        pass


def radial(x, y):
    m = math.hypot(x, y)
    if m <= DEADZONE:
        return 0.0, 0.0
    out = min(1.0, (m - DEADZONE) / (OUTER_ZONE - DEADZONE))
    k = out / m
    return max(-1.0, min(1.0, x * k)), max(-1.0, min(1.0, y * k))


class Stick:
    def __init__(self, name, saved):
        self.name = name
        self.cx = self.cy = 127.5
        self.xs = deque(maxlen=WINDOW_N)
        self.ys = deque(maxlen=WINDOW_N)
        # Real travel of each axis [x, y], learned from use and saved to disk.
        self.lo = list(saved["lo"]) if saved else None
        self.hi = list(saved["hi"]) if saved else None
        self.dirty = False

    def add(self, x, y):
        if self.lo is not None and self.xs:
            self._learn(0, x, self.xs[-1])
            self._learn(1, y, self.ys[-1])
        self.xs.append(x)
        self.ys.append(y)

    def _learn(self, i, v, prev):
        # Two samples in a row past the known edge, so a single glitch can't stretch it.
        if v < self.lo[i] and prev < self.lo[i]:
            self.lo[i] = max(v, prev)
            self.dirty = True
        elif v > self.hi[i] and prev > self.hi[i]:
            self.hi[i] = min(v, prev)
            self.dirty = True

    def init_range(self):
        if self.lo is None:
            self.lo = [max(0, round(c) - START_SPAN) for c in (self.cx, self.cy)]
            self.hi = [min(255, round(c) + START_SPAN) for c in (self.cx, self.cy)]
            self.dirty = True

    def saved(self):
        return {"lo": self.lo, "hi": self.hi}

    def steady(self):
        return (max(self.xs) - min(self.xs) <= STEADY_COUNTS
                and max(self.ys) - min(self.ys) <= STEADY_COUNTS)

    def mean(self):
        return sum(self.xs) / len(self.xs), sum(self.ys) / len(self.ys)

    def deflection(self, x, y):
        return math.hypot(axis(x, self.cx, self.lo[0], self.hi[0]),
                          axis(y, self.cy, self.lo[1], self.hi[1]))

    def set_centre(self, x, y):
        moved = math.hypot(x - self.cx, y - self.cy)
        self.cx, self.cy = x, y
        return moved

    def nudge(self, x, y):
        self.cx += FINE_ALPHA * (x - self.cx)
        self.cy += FINE_ALPHA * (y - self.cy)

    def output(self, x, y):
        return radial(axis(x, self.cx, self.lo[0], self.hi[0]),
                      axis(y, self.cy, self.lo[1], self.hi[1]))

    def describe(self):
        return f"{self.name} centre ({self.cx:.0f}, {self.cy:.0f})"


class Feedback:
    """Rumble + lightbar on the real controller: game rumble plus our buzzes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.dev = None
        self.game = (0, 0)
        self.buzz_until = 0.0
        self.flash_until = 0.0

    def attach(self, dev):
        with self.lock:
            self.dev = dev
        self.send()

    def send(self):
        with self.lock:
            if self.dev is None:
                return
            now = time.monotonic()
            strong, weak = self.game
            if now < self.buzz_until:
                strong, weak = max(strong, 150), max(weak, 150)
            colour = (0, 160, 0) if now < self.flash_until else (0, 0, 64)
            report = bytes([0x05, 0x07, 0x04, 0x00, weak, strong, *colour] + [0] * 23)
            try:
                self.dev.write(report)
            except (OSError, ValueError):
                pass

    def game_rumble(self, large, small):
        self.game = (large, small)
        self.send()

    def confirm(self, buzz):
        now = time.monotonic()
        if buzz:
            self.buzz_until = now + 0.2
            threading.Timer(0.21, self.send).start()
        self.flash_until = now + 0.5
        threading.Timer(0.51, self.send).start()
        self.send()


def find_ds4():
    for d in hid.enumerate(SONY_VID, 0):
        if d["product_id"] in DS4_PIDS and d["usage_page"] == 1 and d["usage"] == 5:
            return d["path"]
    return None


def ds4windows_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq DS4Windows.exe", "/NH"],
                         capture_output=True, text=True).stdout
    return "DS4Windows.exe" in out


def run(dev, pad, fb, monitor):
    ranges = load_ranges()
    left, right = Stick("left", ranges.get("left")), Stick("right", ranges.get("right"))
    last_save = 0.0
    gyro = [deque(maxlen=WINDOW_N) for _ in range(3)]
    busy = deque(maxlen=WINDOW_N)
    started = time.monotonic()
    calibrated = False
    gyro_alive = False
    gyro_warned = False
    putdown_since = None
    combo_since = None
    combo_done = False
    last_state = None
    n = 0
    log("Controller connected - leave the sticks alone for a second...")

    while True:
        r = dev.read(64, 1000)
        if not r or r[0] != 0x01 or len(r) < 25:
            continue
        lx, ly, rx, ry = r[1], r[2], r[3], r[4]
        left.add(lx, ly)
        right.add(rx, ry)
        for d, i in zip(gyro, (13, 15, 17)):
            d.append(s16(r, i))
        b5, b6, b7 = r[5], r[6], r[7]
        hat = b5 & 0x0F
        combo = (b6 & 0x30) == 0x30  # share + options
        busy.append(bool((b5 & 0xF0) or hat != 8 or b6 or (b7 & 0x03)
                         or r[8] > TRIGGER_IDLE or r[9] > TRIGGER_IDLE))
        now = time.monotonic()

        if combo:
            if combo_since is None:
                combo_since = now
        else:
            combo_since, combo_done = None, False

        n += 1
        if n % CHECK_EVERY == 0 and len(left.xs) == WINDOW_N:
            lsteady, rsteady = left.steady(), right.steady()
            gstd = max(std(d) for d in gyro)
            if gstd > 0:
                gyro_alive = True
            elif not gyro_alive and not gyro_warned and now - started > 3:
                gyro_warned = True
                log("No gyro on this controller - auto re-centre is off. "
                    "Use SHARE + OPTIONS to re-centre.")

            if not calibrated:
                if (lsteady and rsteady) or now - started > 3:
                    left.set_centre(*left.mean())
                    right.set_centre(*right.mean())
                    left.init_range()
                    right.init_range()
                    calibrated = True
                    note = "" if (lsteady and rsteady) else " (sticks were moving - press SHARE + OPTIONS to redo)"
                    log(f"Calibrated: {left.describe()}, {right.describe()}{note}")
                    fb.confirm(buzz=False)
            else:
                # Hold SHARE + OPTIONS: re-centre whichever sticks are resting.
                if combo_since is not None and not combo_done and now - combo_since >= COMBO_SEC:
                    combo_done = True
                    done = []
                    for s, ok in ((left, lsteady), (right, rsteady)):
                        if ok:
                            s.set_centre(*s.mean())
                            done.append(s.describe())
                    if done:
                        log("Re-centred (button combo): " + ", ".join(done))
                        fb.confirm(buzz=True)
                    else:
                        log("Combo held but both sticks were moving - take your thumbs off and retry")

                # Controller lying untouched: wherever the sticks rest is centre.
                still = (gyro_alive and gstd < GYRO_STILL and not any(busy)
                         and lsteady and rsteady)
                if still:
                    putdown_since = putdown_since or now
                    if now - putdown_since >= PUTDOWN_SEC:
                        moved = [s.describe() for s in (left, right)
                                 if s.set_centre(*s.mean()) > 1.5]
                        if moved:
                            log("Controller put down - re-centred: " + ", ".join(moved))
                            fb.confirm(buzz=False)
                else:
                    putdown_since = None
                    # Slow drift: a resting stick close to centre nudges the centre.
                    for s, ok in ((left, lsteady), (right, rsteady)):
                        if ok:
                            mx, my = s.mean()
                            if s.deflection(mx, my) < FINE_RADIUS:
                                s.nudge(mx, my)

            if (left.dirty or right.dirty) and now - last_save > 2:
                left.dirty = right.dirty = False
                last_save = now
                save_ranges({"left": left.saved(), "right": right.saved()})

            if monitor and calibrated and n % (CHECK_EVERY * 5) == 0:
                lo = left.output(lx, ly)
                ro = right.output(rx, ry)
                print(f"raw L({lx:3},{ly:3}) R({rx:3},{ry:3}) | "
                      f"centre L({left.cx:5.1f},{left.cy:5.1f}) R({right.cx:5.1f},{right.cy:5.1f}) | "
                      f"range LX {left.lo[0]}-{left.hi[0]} LY {left.lo[1]}-{left.hi[1]} "
                      f"RX {right.lo[0]}-{right.hi[0]} RY {right.lo[1]}-{right.hi[1]} | "
                      f"out L({lo[0]:+.2f},{-lo[1]:+.2f}) R({ro[0]:+.2f},{-ro[1]:+.2f}) | "
                      f"gyro noise {gstd:5.1f} {'HELD' if gstd >= GYRO_STILL else 'still'}"
                      f"{' | resting' if lsteady and rsteady else ''}", flush=True)

        if monitor:
            continue

        mask = HAT_TO_DPAD.get(hat, 0)
        for byte, bit, button in BUTTON_MAP:
            if r[byte] & bit:
                mask |= button
        if combo:  # don't send Back/Start to the game while re-centring
            mask &= ~(B.XUSB_GAMEPAD_BACK | B.XUSB_GAMEPAD_START)

        if calibrated:
            lo = left.output(lx, ly)
            ro = right.output(rx, ry)
        else:
            lo = ro = (0.0, 0.0)

        state = (mask, r[8], r[9], lo, ro)
        if state != last_state:
            last_state = state
            pad.report.wButtons = int(mask)
            pad.left_trigger(value=r[8])
            pad.right_trigger(value=r[9])
            pad.left_joystick_float(x_value_float=lo[0], y_value_float=-lo[1])
            pad.right_joystick_float(x_value_float=ro[0], y_value_float=-ro[1])
            pad.update()


def main():
    ap = argparse.ArgumentParser(description="Re-centre a drifting DS4 and output a virtual Xbox pad.")
    ap.add_argument("--monitor", action="store_true", help="only print live readings, no virtual pad")
    ap.add_argument("--reset-ranges", action="store_true", help="forget learned stick ranges (new controller)")
    args = ap.parse_args()

    if args.reset_ranges and os.path.exists(RANGES_FILE):
        os.remove(RANGES_FILE)
        print("Learned stick ranges cleared - push each stick all the way round once.")

    if not args.monitor and ds4windows_running():
        print("DS4Windows is running. Close it first (right-click its tray icon > Exit),")
        print("otherwise games will see two controllers.")
        return 1

    try:  # lower input latency
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x80)
    except Exception:
        pass

    fb = Feedback()
    pad = None
    if not args.monitor:
        pad = vg.VX360Gamepad()

        def on_rumble(client, target, large_motor, small_motor, led_number, user_data):
            fb.game_rumble(large_motor, small_motor)

        pad.register_notification(callback_function=on_rumble)

    print("DS4 stick fix running. Hold SHARE + OPTIONS for 1 s to re-centre. Ctrl+C to quit.")
    waiting_logged = False
    try:
        while True:
            path = find_ds4()
            if not path:
                if not waiting_logged:
                    log("Waiting for controller (plug it in via USB)...")
                    waiting_logged = True
                time.sleep(1)
                continue
            waiting_logged = False
            dev = hid.device()
            try:
                dev.open_path(path)
            except OSError:
                time.sleep(1)
                continue
            fb.attach(dev)
            try:
                run(dev, pad, fb, args.monitor)
            except OSError:
                log("Controller disconnected.")
            finally:
                fb.attach(None)
                dev.close()
                if pad:
                    pad.reset()
                    pad.update()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
