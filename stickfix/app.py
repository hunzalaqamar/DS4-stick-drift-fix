"""Desktop window: pywebview (Edge WebView2) showing ui/, backed by the engine."""

import os
import pathlib
import sys
import webbrowser

from . import winutil

THEME_BG = {"dark": (18, 22, 51), "light": (236, 239, 249)}

LINKS = {
    "vigem": "https://github.com/nefarius/ViGEmBus/releases",
    "hidhide": "https://github.com/nefarius/HidHide/releases",
    "repo": "https://github.com/hunzalaqamar/DS4-stick-drift-fix",
}


def resource(*parts):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


class Api:
    """Methods callable from JavaScript. Attributes start with _ so pywebview
    doesn't expose them."""

    def __init__(self, engine, store):
        self._engine = engine
        self._store = store
        self._hwnd = None

    def title_theme(self, theme):
        if self._hwnd and theme in THEME_BG:
            winutil.title_bar(self._hwnd, theme == "dark", THEME_BG[theme])

    def state(self):
        return self._engine.snapshot

    def set_fix(self, on):
        return self._engine.call(self._engine.set_fix, bool(on))

    def set_output(self, kind):
        if kind in ("xbox360", "ds4"):
            self._engine.call(self._engine.set_output, kind)

    def set_hiding(self, on):
        return self._engine.call(self._engine.set_hiding, bool(on), timeout=15)

    def update_settings(self, changes):
        changes = {k: v for k, v in dict(changes).items() if k not in ("fix_enabled", "output", "hide_original")}
        return self._store.update(changes)

    def recentre(self, dev_id, which="both"):
        return self._engine.call(self._engine.recentre, int(dev_id), str(which))

    def calibration(self, dev_id, action):
        if action in ("start", "finish", "cancel"):
            return self._engine.call(self._engine.calibration, int(dev_id), action)

    def reset_profile(self, dev_id):
        return self._engine.call(self._engine.reset_profile, int(dev_id))

    def rumble(self, dev_id, low, high, ms):
        self._engine.call(self._engine.test_rumble, int(dev_id), int(low), int(high), min(int(ms), 2000))

    def led(self, dev_id, r, g, b):
        self._engine.call(self._engine.test_led, int(dev_id), int(r), int(g), int(b))

    def open_link(self, name):
        if name in LINKS:
            webbrowser.open(LINKS[name])


def setup_logging():
    """Errors go to %APPDATA%\\StickDriftFix\\log.txt - the exe has no console."""
    import logging
    import threading
    from logging.handlers import RotatingFileHandler
    from .store import DIR
    os.makedirs(DIR, exist_ok=True)
    handler = RotatingFileHandler(os.path.join(DIR, "log.txt"), maxBytes=256_000, backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("stickfix")
    sys.excepthook = lambda *exc: log.error("Unhandled error", exc_info=exc)
    threading.excepthook = lambda a: log.error("Error in thread %s", a.thread.name,
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    return log


def main():
    if not winutil.single_instance():
        winutil.message_box("Stick Drift Fix is already running. Check your taskbar.")
        return 0

    from . import __version__
    log = setup_logging()
    log.info("Starting Stick Drift Fix %s", __version__)

    import webview
    from .engine import Engine
    from .store import Store

    store = Store()
    engine = Engine(store)
    engine.start()

    pref = store.settings["theme"]
    theme = pref if pref != "system" else ("dark" if winutil.system_dark() else "light")
    api = Api(engine, store)
    url = pathlib.Path(resource("ui", "index.html")).as_uri() + "?theme=" + pref
    window = webview.create_window(
        "Stick Drift Fix", url, js_api=api, width=1240, height=820, min_size=(900, 620),
        background_color="#%02x%02x%02x" % THEME_BG[theme])

    def on_shown():
        try:
            api._hwnd = window.native.Handle.ToInt32()
            api.title_theme(theme)
        except Exception:
            pass

    window.events.shown += on_shown
    try:
        webview.start(gui="edgechromium", debug="--debug" in sys.argv)
    finally:
        engine.stop()
        engine.join(3)
    return 0
