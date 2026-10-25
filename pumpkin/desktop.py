"""A plain app window around the web UI (GTK + WebKit). Closing it leaves Pumpkin running."""
import os

from . import APP_NAME, __version__

ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "pumpkin.svg")


def own_page(uri):
    """Pumpkin's own pages (this computer only). A prefix check would also keep look-alikes such as
    http://127.0.0.1.example.com in the window."""
    from urllib.parse import urlparse
    if not uri:
        return False
    if uri.startswith("about:"):
        return True
    try:
        u = urlparse(uri)
        return u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost")
    except ValueError:
        return False


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
    st.set_user_agent_with_application_details("PumpkinApp", __version__)   # Settings > Signed-in devices
    try:
        st.set_media_playback_requires_user_gesture(False)
    except AttributeError:
        pass

    def on_permission(view, req):
        # the microphone for voice chat, only for our own page
        if own_page(view.get_uri()):
            req.allow()
        else:
            req.deny()
        return True
    web.connect("permission-request", on_permission)

    def on_policy(_view, decision, kind):
        # files sent as downloads (the Files page) are saved instead of ignored
        if kind == WebKit2.PolicyDecisionType.RESPONSE:
            resp = decision.get_response()
            headers = resp.get_http_headers() if resp else None
            disp = (headers.get_one("Content-Disposition") or "") if headers else ""
            if disp.lower().startswith("attachment") or not decision.is_mime_type_supported():
                decision.download()
                return True
            return False
        # links to other sites open in the normal browser
        if kind == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION or kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri and not own_page(uri):
                Gio.AppInfo.launch_default_for_uri(uri, None)
                decision.ignore()
                return True
        return False
    web.connect("decide-policy", on_policy)
    def on_download(_ctx, download):
        # files from the Files page go to ~/Downloads (never overwriting), then the folder opens
        from gi.repository import GLib
        folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.expanduser("~/Downloads")

        def decide(dl, suggested):
            os.makedirs(folder, exist_ok=True)
            name = os.path.basename(suggested or "download")
            stem, ext = os.path.splitext(name)
            path, n = os.path.join(folder, name), 2
            while os.path.exists(path):
                path, n = os.path.join(folder, f"{stem} ({n}){ext}"), n + 1
            dl.set_destination(GLib.filename_to_uri(path, None))
            return True

        download.connect("decide-destination", decide)
        download.connect("finished", lambda _dl: Gio.AppInfo.launch_default_for_uri(
            GLib.filename_to_uri(folder, None), None))
    try:
        ctx.connect("download-started", on_download)
    except TypeError:
        pass
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
