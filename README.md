# DS4 Stick Drift Fix

Fix PS4 / DualShock 4 stick drift on Windows in software. No soldering, no new controller.

A dead zone can only hide small drift. This tool finds where each stick **actually** rests and how far it **actually** moves, then sends corrected input to games as a virtual Xbox 360 controller.

Tested on a clone DS4 whose left stick rested at **-0.67** and could only reach 40% of its travel to the right. After the fix it rests at **0.00** and draws a full circle.

## Features

- Re-centres sticks that rest off-centre, however far off
- Learns each axis's real range, so worn sticks still reach 100% (saved between runs)
- Re-centres automatically when you put the controller down (uses the gyro, so it never fires while you're holding it)
- Manual re-centre: hold **SHARE + OPTIONS** for 1 second
- All buttons, triggers and rumble pass through unchanged

## Requirements

- Windows 10/11, DualShock 4 (official or clone) **over USB**
- [Python 3.10+](https://www.python.org/downloads/)
- [ViGEmBus](https://github.com/nefarius/ViGEmBus/releases) driver (already installed if you've used DS4Windows)
- Recommended: [HidHide](https://github.com/nefarius/HidHide), so games only see the fixed controller

## Setup

```bash
git clone https://github.com/hunzalaqamar/DS4-stick-drift-fix.git
cd DS4-stick-drift-fix
py -m pip install -r requirements.txt
```

## Usage

1. Close **DS4Windows** (tray icon → Exit).
2. Run **`Start Stick Fix.bat`**. Don't touch the sticks for a second while it calibrates.
3. First run only: spin both sticks in full circles a few times so it learns their range.
4. Keep the window open while you play.

Check the result on [gamepad-tester.com](https://gamepad-tester.com). Look at the **Xbox 360 Controller** entry.

| Command | Purpose |
|---|---|
| `py ds4_stick_fix.py --monitor` | Show live raw readings (no virtual controller) |
| `py ds4_stick_fix.py --reset-ranges` | Forget learned ranges (new controller) |

## Troubleshooting

**Two controllers show up ("Wireless Controller" + "Xbox 360 Controller")**
The real DS4 is still visible. Open the *HidHide Configuration Client*:
1. **Applications** tab: add your `python.exe` (usually `%LOCALAPPDATA%\Programs\Python\Python3xx\python.exe`).
2. **Devices** tab: tick **Sony Interactive Entertainment Wireless Controller**. Do *not* tick "Controller (XBOX 360 For Windows)", that's the fixed one.
3. Tick **Enable device hiding**.
4. Unplug and replug the controller, then restart the script.

**A stick doesn't respond after starting**
The stick was stuck or moving during startup calibration. Put the controller down for 2 seconds until it says *re-centred*, or hold **SHARE + OPTIONS** for 1 second.

**Script says "Waiting for controller"**
Use a USB cable (Bluetooth isn't supported). If HidHide is on, check that `python.exe` is on its Applications list.

**Script says "DS4Windows is running"**
Exit DS4Windows from the tray. Running both creates two controllers.

**Double input in Steam games**
Steam → Settings → Controller → turn off **PlayStation Controller Support**.

**A stick only reaches part of its range**
Spin it in full circles to teach it the range. Switched controllers? Run with `--reset-ranges`.

**It re-centres while I'm holding the controller**
Run `--monitor` while holding the controller still, then set `GYRO_STILL` at the top of `ds4_stick_fix.py` below the lowest gyro number you see.

**Drift keeps getting worse**
Software can only use the range the sensor still has. Try cleaning the stick: push it to one side, spray compressed air or a drop of 90%+ isopropyl alcohol at its base, rotate it, and let it dry. If that doesn't help, the analog module needs replacing.

## License

MIT
