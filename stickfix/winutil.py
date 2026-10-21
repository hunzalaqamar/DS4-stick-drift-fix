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
_cfg.CM_Get_DevNode_PropertyW.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG),
                                          ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.ULONG]

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


class _DEVPROPKEY(ctypes.Structure):
    _fields_ = [("guid", ctypes.c_byte * 16), ("pid", wintypes.ULONG)]


def _devpkey(guid, pid):
    import uuid
    key = _DEVPROPKEY()
    ctypes.memmove(key.guid, uuid.UUID(guid).bytes_le, 16)
    key.pid = pid
    return key


DEVPKEY_DEVICE_STACK = _devpkey("540b947e-8b40-45bc-a8a2-6a0b894cbda2", 14)


def driver_stack(instance):
    r"""Drivers attached to a device, top first, e.g. ['\Driver\HidHide',
    '\Driver\hidgamepad', '\Driver\HidUsb']. None if it can't be read."""
    node = wintypes.DWORD()
    if not instance or _cfg.CM_Locate_DevNodeW(ctypes.byref(node), instance, 0) != CR_SUCCESS:
        return None
    ptype, size = wintypes.ULONG(), wintypes.ULONG(2048)
    buf = ctypes.create_string_buffer(size.value)
    if _cfg.CM_Get_DevNode_PropertyW(node, ctypes.byref(DEVPKEY_DEVICE_STACK), ctypes.byref(ptype),
                                     buf, ctypes.byref(size), 0) != CR_SUCCESS:
        return None
    text = buf.raw[:size.value].decode("utf-16-le", errors="ignore")
    return [x for x in text.split("\0") if x]


def hidhide_attached(instance):
    """True/False when HidHide's filter is (not) in the device's driver stack;
    None when Windows doesn't say. HidHide's settings can list a device as
    hidden while its filter isn't attached, until the device restarts."""
    stack = driver_stack(instance)
    if stack is None:
        return None
    return any(x.lower().endswith("\\hidhide") for x in stack)


def restart_device(instance):
    """Restart a device with pnputil, the same as Disable then Enable in Device
    Manager. Windows asks the user for admin approval first. Returns
    'ok', 'reboot', 'cancelled' or an error message."""
    class SEI(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
                    ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                    ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    import os
    pnputil = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "pnputil.exe")
    sei = SEI(cbSize=ctypes.sizeof(SEI), fMask=0x40, lpVerb="runas", lpFile=pnputil,  # NOCLOSEPROCESS
              lpParameters=f'/restart-device "{instance}"', nShow=0)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        err = ctypes.get_last_error()
        return "cancelled" if err == 1223 else f"Windows error {err}"
    _kernel32.WaitForSingleObject(sei.hProcess, 30000)
    code = wintypes.DWORD()
    _kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
    _kernel32.CloseHandle(sei.hProcess)
    if code.value == 0:
        return "ok"
    if code.value == 3010:  # ERROR_SUCCESS_REBOOT_REQUIRED
        return "reboot"
    return f"pnputil exit code {code.value}"


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
