"""HidHide integration: hide the real controller from games while the fix runs,
and give it back when the fix stops. Needs HidHide installed; no admin needed."""

import os
import subprocess

CLI_PATHS = [
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                 r"Nefarius Software Solutions\HidHide\x64\HidHideCLI.exe"),
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                 r"Nefarius Software Solutions\HidHide\x86\HidHideCLI.exe"),
]


class HidHide:
    def __init__(self):
        self.cli = next((p for p in CLI_PATHS if os.path.exists(p)), None)
        self.cloaked_by_us = False
        self.error = None

    @property
    def installed(self):
        return self.cli is not None

    def _run(self, *args):
        try:
            r = subprocess.run([self.cli, *args], capture_output=True, text=True, timeout=10,
                               creationflags=0x08000000)
            if r.returncode != 0:
                self.error = (r.stderr or r.stdout).strip() or f"HidHide exit code {r.returncode}"
            return r.stdout
        except (OSError, subprocess.SubprocessError) as e:
            self.error = str(e)
            return ""

    def cloaked(self):
        return self.installed and "--cloak-on" in self._run("--cloak-state")

    def hide(self, instances, app_paths):
        """Allow our own process through, add the devices, switch cloaking on."""
        if not self.installed:
            return False
        self.error = None
        current = self._run("--app-list", "--dev-list").lower()
        args = []
        for app in app_paths:
            if f'"{app.lower()}"' not in current:
                args += ["--app-reg", app]
        for inst in instances:
            if f'"{inst.lower()}"' not in current:
                args += ["--dev-hide", inst]
        if args:
            self._run(*args)
        if not self.cloaked():
            self._run("--cloak-on")
            self.cloaked_by_us = True
        return self.error is None

    def restore(self):
        """Switch cloaking off again if we were the ones who switched it on."""
        if self.installed and self.cloaked_by_us:
            self._run("--cloak-off")
            self.cloaked_by_us = False
