"""A plain app window around the web UI (GTK + WebKit). Closing it leaves Pumpkin running."""
import os

from . import APP_NAME

ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "pumpkin.svg")


def _load():
    os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
    import gi
    gi.require_version("Gtk", "3.0")
    for v in ("4.1", "4.0"):
        try:
            gi.require_version("WebKit2", v)
            break
        except ValueError:
            continue
    from gi.repository import Gtk, WebKit2  # noqa: E402
    return Gtk, WebKit2


def available():
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        _load()
        return True
    except (ImportError, ValueError):
        return False


def run(url):
    Gtk, WebKit2 = _load()
    from gi.repository import Gdk, Gio
    win = Gtk.Window(title=APP_NAME)
    win.set_default_size(1280, 860)
    if os.path.exists(ICON):
        try:
            win.set_icon_from_file(ICON)
        except Exception:  # noqa: BLE001
            pass
    ctx = WebKit2.WebContext.get_default()
    data = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "pumpkin", "webview")
    try:
        mgr = ctx.get_website_data_manager()
        cookies = mgr.get_cookie_manager() if hasattr(mgr, "get_cookie_manager") else ctx.get_cookie_manager()
    except Exception:  # noqa: BLE001
        cookies = ctx.get_cookie_manager()
    os.makedirs(data, exist_ok=True)
    cookies.set_persistent_storage(os.path.join(data, "cookies.sqlite"), WebKit2.CookiePersistentStorage.SQLITE)
    web = WebKit2.WebView()
    st = web.get_settings()
    st.set_enable_developer_extras(True)
    try:
        st.set_media_playback_requires_user_gesture(False)
    except AttributeError:
        pass

    def on_permission(_view, req):
        # the microphone for voice chat, only for our own page
        req.allow()
        return True
    web.connect("permission-request", on_permission)

    def on_policy(_view, decision, kind):
        # links to other sites open in the normal browser
        if kind == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION or kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri and not uri.startswith(("http://127.0.0.1", "http://localhost", "about:")):
                Gio.AppInfo.launch_default_for_uri(uri, None)
                decision.ignore()
                return True
        return False
    web.connect("decide-policy", on_policy)
    web.load_uri(url)
    win.add(web)
    win.connect("destroy", Gtk.main_quit)
    accel = Gtk.AccelGroup()
    win.add_accel_group(accel)
    key, mod = Gtk.accelerator_parse("<Control>r")
    accel.connect(key, mod, 0, lambda *a: (web.reload(), True)[1])
    del Gdk
    win.show_all()
    Gtk.main()
