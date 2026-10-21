"""Drift correction for one controller: re-centring, range learning and the
rules that decide *when* it is safe to re-centre without fighting real input.

All stick values are normalised floats in -1..1 with +Y pointing down (SDL
convention). Nothing in here talks to hardware, so it can be unit-tested.
"""

import math
from collections import deque

STEADY_WINDOW = 0.4     # s of history used to decide a stick is resting
STEADY_RANGE = 0.02     # max wobble (about 2.5 steps on an 8-bit stick) while resting
RELEASE_WINDOW = 0.06   # s - a spring snap-back is faster than a thumb
RELEASE_DROP = 0.30     # deflection drop inside RELEASE_WINDOW that counts as "let go"
RELEASE_VALID = 2.0     # s after a release in which a new rest point is accepted
FINE_RADIUS = 0.10      # slow drift near centre is followed continuously
FINE_ALPHA = 0.05
EDGE_MARGIN = 0.08      # a rest point this close to the end of travel loses a direction
MIN_SPAN = 0.06
CALIB_MIN_TRAVEL = 0.25  # range calibration ignores sides the stick barely moved to


def axis(v, c, lo, hi):
    """Map raw v to -1..1 around centre c, scaled per side against the travel
    the axis really has, so a worn or off-centre stick still reaches 100%."""
    span = (hi - c) if v >= c else (c - lo)
    return max(-1.0, min(1.0, (v - c) / max(span, MIN_SPAN)))


def radial(x, y, deadzone, outer):
    m = math.hypot(x, y)
    if m <= deadzone:
        return 0.0, 0.0
    out = min(1.0, (m - deadzone) / max(outer - deadzone, 0.01))
    k = out / m
    return max(-1.0, min(1.0, x * k)), max(-1.0, min(1.0, y * k))


class Steady:
    __slots__ = ("ok", "mx", "my")

    def __init__(self, ok, mx=0.0, my=0.0):
        self.ok, self.mx, self.my = ok, mx, my


class Stick:
    def __init__(self, name, saved=None):
        self.name = name
        self.cx = self.cy = 0.0
        self.lo = [-1.0, -1.0]
        self.hi = [1.0, 1.0]
        if saved:
            self.lo = list(saved.get("lo", self.lo))
            self.hi = list(saved.get("hi", self.hi))
            self.cx, self.cy = saved.get("centre", (0.0, 0.0))
        self.samples = deque()    # (t, x, y)
        self.recent = deque()     # (t, deflection) for release detection
        self.released_at = None
        self.calib = None         # [min x, max x, min y, max y] during range calibration
        self.dirty = False

    # -- data in -----------------------------------------------------------
    def add(self, t, x, y):
        s = self.samples
        if s:
            _, px, py = s[-1]
            self._learn(0, x, px)
            self._learn(1, y, py)
        s.append((t, x, y))
        while s[0][0] < t - STEADY_WINDOW:
            s.popleft()

        if self.calib is not None:
            c = self.calib
            c[0], c[1] = min(c[0], x), max(c[1], x)
            c[2], c[3] = min(c[2], y), max(c[3], y)

        d = self.deflection(x, y)
        r = self.recent
        r.append((t, d))
        while r[0][0] < t - RELEASE_WINDOW:
            r.popleft()
        if max(v for _, v in r) - d >= RELEASE_DROP:
            self.released_at = t

    def _learn(self, i, v, prev):
        # Two samples in a row past the known edge, so one glitch can't stretch it.
        if v < self.lo[i] and prev < self.lo[i]:
            self.lo[i] = max(v, prev)
            self.dirty = True
        elif v > self.hi[i] and prev > self.hi[i]:
            self.hi[i] = min(v, prev)
            self.dirty = True

    # -- queries -----------------------------------------------------------
    def steady(self, now):
        s = self.samples
        if len(s) < 3 or s[0][0] > now - STEADY_WINDOW * 0.85:
            return Steady(False)
        xs = [p[1] for p in s]
        ys = [p[2] for p in s]
        if max(xs) - min(xs) > STEADY_RANGE or max(ys) - min(ys) > STEADY_RANGE:
            return Steady(False)
        return Steady(True, sum(xs) / len(xs), sum(ys) / len(ys))

    def deflection(self, x, y):
        return math.hypot(axis(x, self.cx, self.lo[0], self.hi[0]),
                          axis(y, self.cy, self.lo[1], self.hi[1]))

    def output(self, x, y, deadzone, outer):
        return radial(axis(x, self.cx, self.lo[0], self.hi[0]),
                      axis(y, self.cy, self.lo[1], self.hi[1]), deadzone, outer)

    def at_edge(self):
        return (self.cx - self.lo[0] < EDGE_MARGIN or self.hi[0] - self.cx < EDGE_MARGIN
                or self.cy - self.lo[1] < EDGE_MARGIN or self.hi[1] - self.cy < EDGE_MARGIN)

    # -- changes -----------------------------------------------------------
    def set_centre(self, x, y):
        moved = math.hypot(x - self.cx, y - self.cy)
        self.cx, self.cy = x, y
        self.released_at = None
        self.dirty = True
        return moved

    def nudge(self, x, y):
        self.cx += FINE_ALPHA * (x - self.cx)
        self.cy += FINE_ALPHA * (y - self.cy)

    def start_calibration(self):
        x, y = (self.samples[-1][1], self.samples[-1][2]) if self.samples else (self.cx, self.cy)
        self.calib = [x, x, y, y]

    def finish_calibration(self, apply=True):
        c, self.calib = self.calib, None
        if not apply or c is None:
            return False
        changed = False
        for i, (lo, hi, centre) in enumerate(((c[0], c[1], self.cx), (c[2], c[3], self.cy))):
            if centre - lo >= CALIB_MIN_TRAVEL:
                self.lo[i] = lo
                changed = True
            if hi - centre >= CALIB_MIN_TRAVEL:
                self.hi[i] = hi
                changed = True
        self.dirty = self.dirty or changed
        return changed

    def reset_range(self):
        self.lo, self.hi = [-1.0, -1.0], [1.0, 1.0]
        self.dirty = True

    def saved(self):
        return {"centre": [round(self.cx, 4), round(self.cy, 4)],
                "lo": [round(v, 4) for v in self.lo], "hi": [round(v, 4) for v in self.hi]}


class DriftGuard:
    """Decides when each stick may be re-centred. check() returns a list of
    (kind, message) events describing what it did."""

    def __init__(self, left, right, settings, has_gyro):
        self.left, self.right = left, right
        self.settings = settings          # dict, read live
        self.has_gyro = has_gyro
        self.gyro = deque()               # (t, gx, gy, gz)
        self.last_busy = -1e9
        self.started = None
        self.calibrated = False
        self.putdown_since = None
        self.idle_since = None
        self.combo_since = None
        self.combo_done = False
        self.gyro_noise = None

    def feed_gyro(self, t, gx, gy, gz):
        g = self.gyro
        g.append((t, gx, gy, gz))
        while g[0][0] < t - 0.5:
            g.popleft()

    def feed_inputs(self, t, busy, combo):
        if busy:
            self.last_busy = t
        if combo:
            if self.combo_since is None:
                self.combo_since = t
        else:
            self.combo_since, self.combo_done = None, False

    def _gyro_noise(self, now):
        g = self.gyro
        if len(g) < 10 or g[-1][0] < now - 0.25:
            return None
        noise = 0.0
        for i in (1, 2, 3):
            vals = [s[i] for s in g]
            m = sum(vals) / len(vals)
            noise = max(noise, math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)))
        return noise

    def recentre(self, which="both", now=None):
        """Re-centre resting sticks now. Returns names that were re-centred."""
        done = []
        for stick in self._pick(which):
            st = stick.steady(now) if now is not None else Steady(False)
            if st.ok:
                stick.set_centre(st.mx, st.my)
                done.append(stick.name)
        return done

    def _pick(self, which):
        return {"left": (self.left,), "right": (self.right,)}.get(which, (self.left, self.right))

    def check(self, now):
        s = self.settings
        ev = []
        ls, rs = self.left.steady(now), self.right.steady(now)
        self.gyro_noise = self._gyro_noise(now) if self.has_gyro else None

        if not self.calibrated:
            if self.started is None:
                self.started = now
            if (ls.ok and rs.ok) or now - self.started > 3:
                for stick, st in ((self.left, ls), (self.right, rs)):
                    if st.ok:
                        stick.set_centre(st.mx, st.my)
                    elif stick.samples:
                        xs = [p[1] for p in stick.samples]
                        ys = [p[2] for p in stick.samples]
                        stick.set_centre(sum(xs) / len(xs), sum(ys) / len(ys))
                self.calibrated = True
                ev.append(("calibrated", "Calibrated" if (ls.ok and rs.ok)
                           else "Calibrated while a stick was moving - re-centre when it's resting"))
            return ev

        # Hold BACK + START (SHARE + OPTIONS / View + Menu).
        if (s["combo"] and self.combo_since is not None and not self.combo_done
                and now - self.combo_since >= s["combo_sec"]):
            self.combo_done = True
            done = self.recentre("both", now)
            ev.append(("combo", "Re-centred " + " & ".join(done) + " stick" if done
                       else "Button combo held, but both sticks were moving"))

        idle = now - self.last_busy > STEADY_WINDOW
        both_rest = ls.ok and rs.ok and idle

        # Put down on a table: gyro says nobody is holding it.
        if self.has_gyro and s["auto_putdown"]:
            still = both_rest and self.gyro_noise is not None and self.gyro_noise < s["gyro_still"]
            if still:
                self.putdown_since = self.putdown_since or now
                if now - self.putdown_since >= s["putdown_sec"]:
                    moved = [st.name for st, r in ((self.left, ls), (self.right, rs))
                             if st.set_centre(r.mx, r.my) > 0.012]
                    if moved:
                        ev.append(("putdown", "Controller put down - re-centred " + " & ".join(moved)))
                    return ev
            else:
                self.putdown_since = None

        # No gyro: fall back to a long idle period with no input at all.
        if not self.has_gyro and s["idle_recenter"]:
            if both_rest:
                self.idle_since = self.idle_since or now
                if now - self.idle_since >= s["idle_sec"]:
                    moved = [st.name for st, r in ((self.left, ls), (self.right, rs))
                             if st.set_centre(r.mx, r.my) > 0.012]
                    if moved:
                        ev.append(("idle", "Idle - re-centred " + " & ".join(moved)))
                    return ev
            else:
                self.idle_since = None

        for stick, st in ((self.left, ls), (self.right, rs)):
            if not st.ok:
                continue
            d = stick.deflection(st.mx, st.my)
            # Smart in-game re-centre: the stick snapped back after being let go
            # and settled somewhere off-centre - that's its new rest point.
            if (s["smart_recenter"] and stick.released_at is not None
                    and now - stick.released_at <= RELEASE_VALID
                    and now - stick.released_at >= s["smart_settle"]):
                if s["deadzone"] < d < 0.92:
                    stick.set_centre(st.mx, st.my)
                    ev.append(("smart", f"Stick let go off-centre - re-centred {stick.name} stick"))
                    continue
                if d <= s["deadzone"]:
                    stick.released_at = None
            if d < FINE_RADIUS:
                stick.nudge(st.mx, st.my)
        return ev
