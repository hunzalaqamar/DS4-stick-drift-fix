# DS4 Stick Fix

**Software fix for DualShock 4 stick drift on Windows - no soldering, no new controller.**

A worn PS4 controller (official or clone) often has an analog stick that rests off-centre, drifts on its own, or can no longer reach the edges. A dead zone in DS4Windows or Steam only hides a small amount of drift. Once the stick rests at 60% to one side, a dead zone that big makes the stick unusable.

DS4 Stick Fix reads the controller directly, works out where each stick *actually* rests and how far it *actually* travels, and sends the corrected input to games as a virtual Xbox 360 controller.

It was built to rescue a clone DS4 whose left stick rested at **-0.67** and could only reach **40%** of its travel to the right. After the fix, that stick rests at 0.00 and reaches a full -1.00 / +1.00.

## Features

- **Re-centres an off-centre stick.** The stick's real rest position becomes zero, however far off it is.
- **Learns each axis's real range.** If a worn sensor tops out at 102 instead of 255, that's treated as full deflection, so you still get 100% in every direction. Ranges are saved, so the fix works from the first second on later runs.
- **Re-centres automatically when you put the controller down.** It uses the gyro to tell "lying on the desk" from "in your hands". It never re-centres while you're holding the controller, so it can't fight your real input.
- **Manual re-centre:** hold **SHARE + OPTIONS** for 1 second. The controller buzzes and flashes green to confirm.
- **Slow drift tracking.** Small creep near the centre is corrected continuously.
- **Full pass-through.** All buttons, triggers and the D-pad pass through unchanged, and game rumble is forwarded to the controller. PS works as the Xbox Guide button.
- **Low latency.** It reads at the controller's native 250 Hz.

## Requirements

- Windows 10 or 11
- A DualShock 4 (v1, v2 or clone) **connected by USB**
- [Python 3.10+](https://www.python.org/downloads/)
- The [ViGEmBus](https://github.com/nefarius/ViGEmBus/releases) driver. If you've used DS4Windows, you already have it.

## Install

```bash
git clone https://github.com/hunzalaqamar/DS4-stick-drift-fix.git
cd DS4-stick-drift-fix
py -m pip install -r requirements.txt
```

## Usage

1. **Close DS4Windows** (tray icon → Exit). Both apps can't run at once, otherwise games see two controllers.
2. Double-click **`Start Stick Fix.bat`**, or run `py ds4_stick_fix.py`.
3. Leave the sticks alone for a second while it calibrates.
4. **First run only:** push each stick all the way round in a few full circles so it learns the real range.
5. Play. Keep the window open. Ctrl+C or closing the window stops it.

Check the result on [gamepad-tester.com](https://gamepad-tester.com) using the **Xbox 360 Controller** entry. The "Wireless Controller" entry is the raw, uncorrected stick.

### Options

| Command | What it does |
|---|---|
| `py ds4_stick_fix.py` | Run the fix |
| `py ds4_stick_fix.py --monitor` | Print live raw readings, centres, ranges and gyro state without creating a virtual controller. Good for diagnosing a stick. |
| `py ds4_stick_fix.py --reset-ranges` | Forget learned ranges (use when switching controllers) |

## Avoiding double input

The real DS4 is still visible to Windows next to the virtual Xbox pad. Most games only use the Xbox pad, but some launchers grab both:

- **Steam:** Settings → Controller → turn off **PlayStation Controller Support**.
- **Everything else:** use [HidHide](https://github.com/nefarius/HidHide) to hide the DS4. Add `python.exe` to its application list so this tool can still see it.

## How it works

```
DS4 (USB HID, 250 Hz) ──► ds4_stick_fix.py ──► ViGEmBus virtual Xbox 360 pad ──► games
                              │
                              ├─ per-axis centre (rest position)
                              ├─ per-axis travel (learned lo/hi, saved)
                              ├─ asymmetric rescale + radial dead zone
                              └─ gyro "is it being held?" check
```

Each axis value is scaled separately on each side of its centre against the travel it actually has. A stick resting at 43 with a range of 0-103 maps 0 → -1.0, 43 → 0.0 and 103 → +1.0.

Re-centring only happens when the data says it's safe:

| Trigger | Condition |
|---|---|
| Startup | Both sticks steady for 0.5 s |
| Put down | Gyro noise below the "lying still" level, no buttons or triggers pressed, sticks steady for 1.5 s |
| Button combo | SHARE + OPTIONS held 1 s. Only sticks that are resting get re-centred. |
| Slow drift | A resting stick within 10% of centre nudges the centre gently |

Measured on the test controller: gyro noise is about 2 on a desk and 50-1700 in hand. The threshold is 15.

## Tuning

All settings are constants at the top of `ds4_stick_fix.py`:

| Setting | Default | Meaning |
|---|---|---|
| `DEADZONE` | 0.06 | Radial dead zone after re-centring |
| `OUTER_ZONE` | 0.97 | Deflection that counts as 100% |
| `GYRO_STILL` | 15.0 | Gyro noise below this = controller is lying still |
| `PUTDOWN_SEC` | 1.5 | How long it must lie still before auto re-centre |
| `COMBO_SEC` | 1.0 | How long to hold SHARE + OPTIONS |

If auto re-centre ever kicks in while you're holding the controller, run `--monitor`, hold the controller still, and set `GYRO_STILL` below the lowest number you see.

## Limitations

- **USB only.** Bluetooth uses a different report format.
- It can only work with the range the sensor still has. A stick that reports nothing at all needs a hardware fix: cleaning with isopropyl alcohol, or replacing the analog module.
- If your clone has no gyro, auto re-centre switches itself off and you use SHARE + OPTIONS instead.
- So far it has been tested on one clone DS4 v2. Reports from other controllers are welcome.

## License

MIT - see [LICENSE](LICENSE).
