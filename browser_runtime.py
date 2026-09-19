"""提供共享的 HTTP 请求、代理处理和 Chromium 启动参数。"""
import json
import os
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

from DrissionPage import ChromiumOptions
from curl_cffi import requests
from cpa_xai.proxyutil import (
    LocalAuthProxyBridge,
    prepare_chromium_proxy,
    proxy_for_chromium,
)

_config = {}
_extension_path = ""

# Managed Xvfb (for browser_hide_mode=xvfb) — process-local, not headless Chromium.
_xvfb_proc = None
_xvfb_display = None

BROWSER_HIDE_MODES = ("normal", "minimize", "xvfb")


def configure_runtime(config_ref, extension_path=""):
    global _config, _extension_path
    _config = config_ref if config_ref is not None else {}
    _extension_path = str(extension_path or "")


def get_browser_hide_mode(config=None):
    """Return normal|minimize|xvfb. Never returns headless here (use cpa_headless for that).

    Priority:
      1) explicit config dict arg
      2) env GROK_BROWSER_HIDE_MODE (one-shot job override)
      3) runtime-configured config (configure_runtime)
      4) project config.json
      5) normal
    """
    # 1 explicit
    if isinstance(config, dict) and config.get("browser_hide_mode") is not None:
        mode = str(config.get("browser_hide_mode") or "normal").strip().lower()
    else:
        # 2 env override for dashboard/CLI one-shot jobs
        env_mode = str(os.environ.get("GROK_BROWSER_HIDE_MODE") or "").strip().lower()
        if env_mode:
            mode = env_mode
        else:
            cfg = _config if isinstance(_config, dict) else {}
            mode = str(cfg.get("browser_hide_mode") or "").strip().lower()
            if not mode:
                # 4 load config.json
                try:
                    cfg_path = Path(__file__).resolve().parent / "config.json"
                    if cfg_path.is_file():
                        with open(cfg_path, "r", encoding="utf-8") as handle:
                            file_cfg = json.load(handle)
                        mode = str((file_cfg or {}).get("browser_hide_mode") or "normal").strip().lower()
                    else:
                        mode = "normal"
                except Exception:
                    mode = "normal"

    if mode in ("min", "minimized", "minimise"):
        mode = "minimize"
    if mode in ("virtual", "virtual-display", "xvfb-run"):
        mode = "xvfb"
    if mode not in BROWSER_HIDE_MODES:
        mode = "normal"
    return mode


def _find_free_x_display(start=90, end=130):
    for num in range(int(start), int(end)):
        sock = "/tmp/.X11-unix/X%d" % num
        if not os.path.exists(sock):
            return num
    return None


def ensure_xvfb_display(log_callback=None):
    """Start a managed Xvfb and point DISPLAY at it. Real Chromium, hidden from the user desktop."""
    global _xvfb_proc, _xvfb_display
    logger = log_callback or (lambda _m: None)

    if _xvfb_proc is not None and _xvfb_proc.poll() is None and _xvfb_display:
        os.environ["DISPLAY"] = _xvfb_display
        logger("[*] browser hide=xvfb reuse DISPLAY=%s" % _xvfb_display)
        return _xvfb_display

    if not shutil.which("Xvfb"):
        raise RuntimeError(
            "browser_hide_mode=xvfb but Xvfb not found. Install: sudo apt install xvfb"
        )

    display_num = _find_free_x_display()
    if display_num is None:
        raise RuntimeError("No free X display for Xvfb (tried :90-:129)")

    display = ":%d" % display_num
    cmd = [
        "Xvfb",
        display,
        "-screen",
        "0",
        "1920x1080x24",
        "-nolisten",
        "tcp",
        "-ac",
    ]
    _xvfb_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    # Wait briefly for the socket
    sock = "/tmp/.X11-unix/X%d" % display_num
    for _ in range(30):
        if os.path.exists(sock) and _xvfb_proc.poll() is None:
            break
        time.sleep(0.1)
    if _xvfb_proc.poll() is not None or not os.path.exists(sock):
        code = _xvfb_proc.poll()
        _xvfb_proc = None
        raise RuntimeError("Xvfb failed to start on %s (exit=%s)" % (display, code))

    os.environ["DISPLAY"] = display
    _xvfb_display = display
    logger("[*] browser hide=xvfb started DISPLAY=%s (pid=%s)" % (display, _xvfb_proc.pid))
    return display


def stop_managed_xvfb(log_callback=None):
    """Stop process-local Xvfb if we started it."""
    global _xvfb_proc, _xvfb_display
    logger = log_callback or (lambda _m: None)
    if _xvfb_proc is None:
        return
    try:
        if _xvfb_proc.poll() is None:
            _xvfb_proc.terminate()
            try:
                _xvfb_proc.wait(timeout=3)
            except Exception:
                _xvfb_proc.kill()
        logger("[*] browser hide=xvfb stopped DISPLAY=%s" % (_xvfb_display or "?"))
    finally:
        _xvfb_proc = None
        _xvfb_display = None


def prepare_browser_display(config=None, log_callback=None):
    """Apply hide-mode display setup before launching Chromium."""
    mode = get_browser_hide_mode(config)
    logger = log_callback or (lambda _m: None)
    if mode == "xvfb":
        ensure_xvfb_display(log_callback=logger)
    else:
        logger("[*] browser hide_mode=%s DISPLAY=%s" % (mode, os.environ.get("DISPLAY") or "(unset)"))
    return mode


def apply_browser_hide_options(options, mode=None, log_callback=None):
    """Mutate ChromiumOptions for hide mode. Does NOT enable headless unless mode forces it (it doesn't)."""
    logger = log_callback or (lambda _m: None)
    mode = (mode or get_browser_hide_mode()).strip().lower()
    if mode not in BROWSER_HIDE_MODES:
        mode = "normal"

    def _arg(flag, value=None):
        if not hasattr(options, "set_argument"):
            return
        try:
            if value is None:
                options.set_argument(flag)
            else:
                # Prefer single-token form Chrome accepts: --flag=value
                token = "%s=%s" % (flag, value) if not flag.endswith("=") else "%s%s" % (flag, value)
                if not token.startswith("--"):
                    token = "--" + token
                try:
                    options.set_argument(token)
                except TypeError:
                    options.set_argument(flag, value)
        except Exception:
            pass

    def _remove(*flags):
        if hasattr(options, "remove_argument"):
            for f in flags:
                try:
                    options.remove_argument(f)
                except Exception:
                    pass

    # Always keep a real GUI browser for Grok/CF stability (no --headless here).
    try:
        if hasattr(options, "headless"):
            options.headless(False)
    except Exception:
        pass

    # Common quiet flags
    for flag in (
        "--mute-audio",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-features=TranslateUI",
    ):
        _arg(flag)

    if mode == "minimize":
        # Real window, but minimized / parked off-screen.
        # Force X11: on Wayland, --start-minimized is often ignored and window pops full-size.
        _remove("--start-maximized", "--start-fullscreen", "--window-position", "--window-size")
        _arg("--ozone-platform=x11")
        _arg("--start-minimized")
        _arg("--window-position=-32000,-32000")
        _arg("--window-size=800,600")
        logger("[*] browser hide=minimize (X11 + start-minimized + off-screen)")
    elif mode == "xvfb":
        # Full-size window on virtual framebuffer — user desktop stays clean.
        # CRITICAL on Wayland hosts: without --ozone-platform=x11 Chrome may ignore
        # DISPLAY=:N Xvfb and still open a visible Wayland window at 1920x1080.
        _remove("--start-maximized", "--start-fullscreen")
        _arg("--ozone-platform=x11")
        _arg("--window-size=1280,900")
        _arg("--window-position=0,0")
        logger(
            "[*] browser hide=xvfb (X11 on virtual display %s)"
            % (os.environ.get("DISPLAY") or "?")
        )
    else:
        _arg("--window-size=1280,900")
        logger("[*] browser hide=normal")

    return mode


def force_minimize_browser(browser, log_callback=None):
    """Best-effort minimize after launch (CDP). Helps when --start-minimized is ignored."""
    logger = log_callback or (lambda _m: None)
    try:
        # Prefer active tab target
        tab = None
        try:
            tab = browser.latest_tab
        except Exception:
            tabs = getattr(browser, "get_tabs", lambda: [])()
            tab = tabs[0] if tabs else None
        if tab is None:
            return False
        # CDP: Browser.getWindowForTarget → setWindowBounds minimized
        try:
            target_id = getattr(tab, "tab_id", None) or getattr(tab, "id", None)
            if hasattr(tab, "run_cdp"):
                win = tab.run_cdp("Browser.getWindowForTarget", targetId=target_id) if target_id else tab.run_cdp("Browser.getWindowForTarget")
            elif hasattr(browser, "run_cdp"):
                win = browser.run_cdp("Browser.getWindowForTarget")
            else:
                return False
            window_id = None
            if isinstance(win, dict):
                window_id = win.get("windowId") or (win.get("window") or {}).get("id")
            if window_id is None and isinstance(win, dict):
                # some versions return bounds only
                window_id = win.get("windowId")
            if window_id is not None:
                runner = tab.run_cdp if hasattr(tab, "run_cdp") else browser.run_cdp
                runner(
                    "Browser.setWindowBounds",
                    windowId=window_id,
                    bounds={"windowState": "minimized"},
                )
                logger("[*] CDP minimize ok windowId=%s" % window_id)
                return True
        except Exception as exc:
            logger("[!] CDP minimize failed: %s" % exc)
        # Fallback: JS blur (weak)
        try:
            tab.run_js("window.blur();")
        except Exception:
            pass
        return False
    except Exception as exc:
        logger("[!] force_minimize_browser error: %s" % exc)
        return False


def create_browser_options(browser_proxy="", extension_path=None, hide_mode=None, log_callback=None):
    """Build ChromiumOptions for registration/mint.

    hide_mode: normal|minimize|xvfb (from config browser_hide_mode when None).
    For xvfb, call prepare_browser_display() before Chromium() so DISPLAY is set.
    """
    mode = hide_mode if hide_mode is not None else get_browser_hide_mode()
    # Ensure virtual display exists before Chromium attaches (xvfb mode).
    # Pass full mode via config dict so env/config resolution is consistent.
    prepare_browser_display(
        config={"browser_hide_mode": mode},
        log_callback=log_callback,
    )

    options = ChromiumOptions()
    options.auto_port()
    options.set_timeouts(base=1)

    # Resource & Memory Optimization Flags to prevent leaks
    options.set_argument("--renderer-process-limit=2")
    options.set_argument("--js-flags=--max-old-space-size=256")
    options.set_argument("--disable-dev-shm-usage")
    options.set_argument("--disable-background-networking")
    options.set_argument("--disable-gpu")
    options.set_argument("--disable-software-rasterizer")
    options.set_argument("--mute-audio")
    options.set_argument("--no-first-run")

    apply_browser_proxy_option(options, browser_proxy)
    apply_browser_hide_options(options, mode=mode, log_callback=log_callback)
    effective_extension = _extension_path if extension_path is None else str(extension_path or "")
    if effective_extension and os.path.exists(effective_extension):
        options.add_extension(effective_extension)

    # Final safety: if xvfb, DISPLAY must not be the user session :0
    if mode == "xvfb":
        disp = os.environ.get("DISPLAY") or ""
        if disp in ("", ":0", ":0.0"):
            # re-ensure
            ensure_xvfb_display(log_callback=log_callback)
            disp = os.environ.get("DISPLAY") or ""
        if log_callback:
            log_callback("[*] xvfb pre-launch DISPLAY=%s" % disp)

    return options


def get_configured_proxy():
    return str((_config or {}).get("proxy", "") or "").strip()


def get_proxies():
    proxy = get_configured_proxy()
    return {"http": proxy, "https": proxy} if proxy else {}


def _parse_proxy_url(proxy):
    raw = str(proxy or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    try:
        return urllib.parse.urlsplit(raw)
    except Exception:
        return None


def _safe_proxy_port(parsed):
    try:
        return parsed.port
    except Exception:
        return None


def _proxy_has_auth(proxy):
    parsed = _parse_proxy_url(proxy)
    return bool(parsed and parsed.hostname and (parsed.username is not None or parsed.password is not None))


def _strip_proxy_auth(proxy):
    raw = str(proxy or "").strip()
    parsed = _parse_proxy_url(raw)
    if not parsed or not parsed.hostname:
        return raw
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = "[%s]" % host
    port = _safe_proxy_port(parsed)
    netloc = "%s:%s" % (host, port) if port else host
    stripped = urllib.parse.urlunsplit((parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment))
    return stripped.split("://", 1)[1] if "://" not in raw else stripped


def _proxy_endpoint_terms(proxy=None):
    parsed = _parse_proxy_url(proxy or get_configured_proxy())
    if not parsed or not parsed.hostname:
        return []
    terms = [parsed.hostname]
    port = _safe_proxy_port(parsed)
    if port:
        terms.extend(["%s:%s" % (parsed.hostname, port), "port %s" % port])
    return [item.lower() for item in terms if item]


def is_proxy_connection_error(exc):
    if not get_configured_proxy():
        return False
    err = str(exc or "").lower()
    if not err:
        return False
    if any(item in err for item in ("proxy", "tunnel", "socks")):
        return True
    markers = (
        "could not connect", "failed to connect", "connection refused",
        "connection reset", "connect error", "timed out", "timeout",
    )
    if any(item in err for item in markers):
        terms = _proxy_endpoint_terms()
        return not terms or any(term in err for term in terms)
    return False


def page_has_proxy_error(page_obj):
    try:
        url = str(getattr(page_obj, "url", "") or "")
        title = str(page_obj.run_js("return document.title || ''") or "")
        body = str(page_obj.run_js("return document.body ? document.body.innerText.slice(0, 2000) : ''") or "")
    except Exception:
        return False
    text = "%s\n%s\n%s" % (url, title, body)
    text = text.lower()
    return any(marker in text for marker in (
        "err_proxy", "proxy connection failed", "proxy server",
        "proxy authentication", "tunnel connection failed",
        "cannot connect to proxy server", "proxy server",
    ))


def prepare_browser_proxy(use_proxy=True, log_callback=None):
    proxy = get_configured_proxy()
    if not use_proxy or not proxy:
        return "", None
    parsed = _parse_proxy_url(proxy)
    if _proxy_has_auth(proxy) and parsed and (parsed.scheme or "http").lower() not in ("http", "https"):
        stripped = _strip_proxy_auth(proxy)
        if log_callback:
            log_callback("[!] Chromium does not directly support this authenticated proxy protocol, using de-authenticated proxy address, will fall back to direct connection on failure")
        return stripped, None
    logger = None
    if log_callback:
        logger = lambda message: log_callback("[*] Started local authenticated proxy bridge for Chromium: %s" % message.split(": ", 1)[-1]) if "started authenticated proxy bridge" in message else log_callback(message)
    return prepare_chromium_proxy(proxy, log=logger)


def apply_browser_proxy_option(options, proxy):
    if not proxy:
        return
    if hasattr(options, "set_proxy"):
        try:
            options.set_proxy(proxy)
            return
        except Exception:
            pass
    if not hasattr(options, "set_argument"):
        raise AttributeError("Current DrissionPage ChromiumOptions does not support setting browser proxy")
    try:
        options.set_argument("--proxy-server=%s" % proxy)
    except TypeError:
        options.set_argument("--proxy-server", proxy)


def _build_request_kwargs(**kwargs):
    request_kwargs = dict(kwargs)
    proxies = request_kwargs.pop("proxies", None)
    if proxies is None:
        proxies = get_proxies()
    if proxies:
        request_kwargs["proxies"] = proxies
    request_kwargs.setdefault("timeout", 15)
    return request_kwargs


def http_get(url, **kwargs):
    request_kwargs = _build_request_kwargs(**kwargs)
    try:
        return requests.get(url, **request_kwargs)
    except Exception as exc:
        if is_proxy_connection_error(exc):
            direct = dict(request_kwargs)
            direct.pop("proxies", None)
            return requests.get(url, **direct)
        raise


def http_post(url, **kwargs):
    request_kwargs = _build_request_kwargs(**kwargs)
    try:
        return requests.post(url, **request_kwargs)
    except Exception as exc:
        if is_proxy_connection_error(exc):
            direct = dict(request_kwargs)
            direct.pop("proxies", None)
            return requests.post(url, **direct)
        raise
