"""Virtual controller output through ViGEmBus (Xbox 360 or DualShock 4)."""

import gc

try:
    import vgamepad as vg
    VIGEM_ERROR = None
except Exception as e:  # driver missing or not running
    vg = None
    VIGEM_ERROR = str(e) or e.__class__.__name__

# SDL game controller button indices
A, B, X, Y, BACK, GUIDE, START, LSTICK, RSTICK, LB, RB, UP, DOWN, LEFT, RIGHT = range(15)
TOUCHPAD = 20

if vg:
    XB = vg.XUSB_BUTTON
    X360_MAP = [
        (A, XB.XUSB_GAMEPAD_A), (B, XB.XUSB_GAMEPAD_B), (X, XB.XUSB_GAMEPAD_X), (Y, XB.XUSB_GAMEPAD_Y),
        (BACK, XB.XUSB_GAMEPAD_BACK), (GUIDE, XB.XUSB_GAMEPAD_GUIDE), (START, XB.XUSB_GAMEPAD_START),
        (LSTICK, XB.XUSB_GAMEPAD_LEFT_THUMB), (RSTICK, XB.XUSB_GAMEPAD_RIGHT_THUMB),
        (LB, XB.XUSB_GAMEPAD_LEFT_SHOULDER), (RB, XB.XUSB_GAMEPAD_RIGHT_SHOULDER),
        (UP, XB.XUSB_GAMEPAD_DPAD_UP), (DOWN, XB.XUSB_GAMEPAD_DPAD_DOWN),
        (LEFT, XB.XUSB_GAMEPAD_DPAD_LEFT), (RIGHT, XB.XUSB_GAMEPAD_DPAD_RIGHT),
    ]
    DB = vg.DS4_BUTTONS
    DS4_MAP = [
        (A, DB.DS4_BUTTON_CROSS), (B, DB.DS4_BUTTON_CIRCLE), (X, DB.DS4_BUTTON_SQUARE),
        (Y, DB.DS4_BUTTON_TRIANGLE), (BACK, DB.DS4_BUTTON_SHARE), (START, DB.DS4_BUTTON_OPTIONS),
        (LSTICK, DB.DS4_BUTTON_THUMB_LEFT), (RSTICK, DB.DS4_BUTTON_THUMB_RIGHT),
        (LB, DB.DS4_BUTTON_SHOULDER_LEFT), (RB, DB.DS4_BUTTON_SHOULDER_RIGHT),
    ]
    D = vg.DS4_DPAD_DIRECTIONS
    DS4_DPAD = {
        (1, 0, 0, 0): D.DS4_BUTTON_DPAD_NORTH, (1, 0, 0, 1): D.DS4_BUTTON_DPAD_NORTHEAST,
        (0, 0, 0, 1): D.DS4_BUTTON_DPAD_EAST, (0, 1, 0, 1): D.DS4_BUTTON_DPAD_SOUTHEAST,
        (0, 1, 0, 0): D.DS4_BUTTON_DPAD_SOUTH, (0, 1, 1, 0): D.DS4_BUTTON_DPAD_SOUTHWEST,
        (0, 0, 1, 0): D.DS4_BUTTON_DPAD_WEST, (1, 0, 1, 0): D.DS4_BUTTON_DPAD_NORTHWEST,
    }


class VirtualPad:
    """One virtual controller. send() takes SDL-style state: buttons list,
    triggers 0..1, sticks -1..1 with +Y down."""

    def __init__(self, kind, on_rumble):
        if vg is None:
            raise RuntimeError(VIGEM_ERROR)
        self.kind = kind
        self.pad = vg.VDS4Gamepad() if kind == "ds4" else vg.VX360Gamepad()
        self.last = None

        def callback(client, target, large_motor, small_motor, led_number, user_data):
            on_rumble(large_motor, small_motor)

        self._callback = callback  # keep a reference alive for ctypes
        self.pad.register_notification(callback_function=callback)

    def send(self, b, lt, rt, lx, ly, rx, ry, hide_menu=False):
        lt, rt = round(lt * 255), round(rt * 255)
        state = (tuple(b), hide_menu, lt, rt, round(lx, 4), round(ly, 4), round(rx, 4), round(ry, 4))
        if state == self.last:
            return
        self.last = state
        p = self.pad
        if self.kind == "ds4":
            mask = 0
            for i, bit in DS4_MAP:
                if b[i]:
                    mask |= bit
            if hide_menu:
                mask &= ~(DB.DS4_BUTTON_SHARE | DB.DS4_BUTTON_OPTIONS)
            if lt:
                mask |= DB.DS4_BUTTON_TRIGGER_LEFT
            if rt:
                mask |= DB.DS4_BUTTON_TRIGGER_RIGHT
            p.report.wButtons = int(mask)
            p.directional_pad(DS4_DPAD.get((b[UP], b[DOWN], b[LEFT], b[RIGHT]), D.DS4_BUTTON_DPAD_NONE))
            special = (1 if b[GUIDE] else 0) | (2 if b[TOUCHPAD] else 0)
            p.report.bSpecial = special
            p.left_trigger(lt)
            p.right_trigger(rt)
            p.left_joystick_float(x_value_float=lx, y_value_float=ly)
            p.right_joystick_float(x_value_float=rx, y_value_float=ry)
        else:
            mask = 0
            for i, bit in X360_MAP:
                if b[i]:
                    mask |= bit
            if hide_menu:
                mask &= ~(XB.XUSB_GAMEPAD_BACK | XB.XUSB_GAMEPAD_START)
            p.report.wButtons = int(mask)
            p.left_trigger(lt)
            p.right_trigger(rt)
            p.left_joystick_float(x_value_float=lx, y_value_float=-ly)
            p.right_joystick_float(x_value_float=rx, y_value_float=-ry)
        p.update()

    def close(self):
        try:
            self.pad.unregister_notification()
        except Exception:
            pass
        self.pad = None
        gc.collect()  # vgamepad unplugs the device when the object is freed
