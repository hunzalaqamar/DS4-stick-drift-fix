"""Small Windows helpers: tell real controllers from virtual ones, spot other
remapping tools, keep a single app instance and theme the title bar."""

import ctypes
import subprocess
from ctypes import wintypes

CR_SUCCESS = 0
_cfg = ctypes.WinDLL("cfgmgr32")
_cfg.CM_Locate_DevNodeW.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.LPCWSTR, wintypes.ULONG]
_cfg.CM_Get_Parent.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.ULONG]
_cfg.CM_Get_Device_IDW.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.ULONG, wintypes.ULONG]

# Other tools that create their own virtual controllers.
REMAPPERS = {
    "ds4windows.exe": "DS4Windows",
    "dsx.exe": "DSX",
    "rewasd.exe": "reWASD",
    "rewasdengine.exe": "reWASD",
    "inputmapper.exe": "InputMapper",
    "x360ce.exe": "x360ce",
    "betterjoy.exe": "BetterJoy",
    "betterjoyforcemu.exe": "BetterJoy",
}


# Windows' own menus and apps move their selection with a controller. While
# one of these is in front, the virtual controller sends nothing.
WINDOWS_UI = {
    "explorer.exe", "startmenuexperiencehost.exe", "searchhost.exe", "searchapp.exe",
    "shellexperiencehost.exe", "shellhost.exe", "systemsettings.exe", "applicationframehost.exe",
    "textinputhost.exe", "lockapp.exe", "snippingtool.exe", "screenclippinghost.exe",
    "screensketch.exe", "taskmgr.exe", "winstore.app.exe", "photos.exe", "microsoft.photos.exe",
    "notepad.exe", "mspaint.exe", "calculatorapp.exe", "windowsterminal.exe", "control.exe",
    "mmc.exe", "rundll32.exe", "openwith.exe", "pickerhost.exe", "credentialuibroker.exe",
}

_user32 = ctypes.WinDLL("user32")
_kernel32 = ctypes.WinDLL("kernel32")


def foreground_process():
    """(pid, exe name in lower case) of the window in front."""
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(_user32.GetForegroundWindow(), ctypes.byref(pid))
    name = ""
    h = _kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            name = buf.value.rsplit("\\", 1)[-1].lower()
        _kernel32.CloseHandle(h)
    return pid.value, name


def instance_from_path(path):
    r"""SDL device path '\\?\HID#VID_x&PID_y#8&abc&0&0000#{guid}' ->
    device instance id 'HID\VID_x&PID_y\8&abc&0&0000'."""
    if not path:
        return None
    p = path.decode(errors="ignore") if isinstance(path, bytes) else path
    if p.startswith("\\\\?\\"):
        p = p[4:]
    parts = p.split("#")
    if len(parts) < 3:
        return None
    return "\\".join(parts[:3]).upper()


def _parent_ids(instance, depth):
    node = wintypes.DWORD()
    if _cfg.CM_Locate_DevNodeW(ctypes.byref(node), instance, 0) != CR_SUCCESS:
        return []
    ids = []
    buf = ctypes.create_unicode_buffer(512)
    for _ in range(depth):
        parent = wintypes.DWORD()
        if _cfg.CM_Get_Parent(ctypes.byref(parent), node, 0) != CR_SUCCESS:
            break
        if _cfg.CM_Get_Device_IDW(parent, buf, 512, 0) != CR_SUCCESS:
            break
        ids.append(buf.value.upper())
        node = parent
    return ids


def is_virtual(instance):
    """Virtual pads (ViGEmBus: ours, DS4Windows', ...) sit up to three levels
    under a ROOT\\ enumerated bus driver. Real pads sit under a USB/BT hub."""
    if not instance:
        return False
    return any(p.startswith("ROOT\\") for p in _parent_ids(instance, 3))


def running_remappers():
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             creationflags=0x08000000).stdout.lower()
    except OSError:
        return []
    found = []
    for exe, name in REMAPPERS.items():
        if f'"{exe}"' in out and name not in found:
            found.append(name)
    return found


def close_remappers():
    """End the remapping tools we detected (the user clicked the button)."""
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                         creationflags=0x08000000).stdout.lower()
    closed = []
    for exe, name in REMAPPERS.items():
        if f'"{exe}"' in out:
            subprocess.run(["taskkill", "/IM", exe, "/F"], capture_output=True, creationflags=0x08000000)
            if name not in closed:
                closed.append(name)
    return closed


_mutex = None


def single_instance(name="StickDriftFix.SingleInstance"):
    global _mutex
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, name)
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def message_box(text, title="Stick Drift Fix"):
    ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)


def system_dark():
    """True when Windows apps are set to dark mode."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return True


def title_bar(hwnd, dark, rgb):
    """Match the window's title bar to the app theme (caption colour needs Windows 11)."""
    try:
        dwm = ctypes.windll.dwmapi
        flag = ctypes.c_int(1 if dark else 0)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(flag), 4)  # immersive dark mode
        colour = ctypes.c_int(rgb[0] | rgb[1] << 8 | rgb[2] << 16)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colour), 4)  # caption colour
    except OSError:
        pass
