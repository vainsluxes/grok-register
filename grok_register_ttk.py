#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GUI and CLI main entry point with compatibility shims for split registration modules."""

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext
    TK_AVAILABLE = True
    TK_IMPORT_ERROR = None
except ImportError as exc:
    tk = None
    ttk = None
    messagebox = None
    scrolledtext = None
    TK_AVAILABLE = False
    TK_IMPORT_ERROR = exc
import threading
import datetime
import time
import os
import sys
import gc
import queue
import secrets
import struct
import random
import re
import string
import json
import base64
import select
import socket
import socketserver
import ssl
import urllib.parse
import tempfile
import traceback

os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError
from curl_cffi import requests

import functools
import types
import app_config as _app_config
import account_outputs as _account_outputs
import browser_runtime as _browser_runtime
import mail_service as _mail_service
import registration_browser as _registration_browser
from app_config import (
    DEFAULT_CONFIG, ConfigError, config, load_config, save_config,
    validate_config, validate_config_structure, validate_run_requirements,
)



MEMORY_CLEANUP_INTERVAL = 5

UI_BG = "#242424"
UI_PANEL_BG = "#2b2b2b"
UI_FG = "#f2f2f2"
UI_MUTED_FG = "#b8b8b8"
UI_ENTRY_BG = "#333333"
UI_BUTTON_BG = "#3a3a3a"
UI_ACTIVE_BG = "#4a6078"




class RegistrationCancelled(Exception):
    pass


class AccountRetryNeeded(Exception):
    pass




class RemoteTokenCompatibilityError(RuntimeError):
    pass


class RemoteTokenRequestError(RuntimeError):
    pass


def log_exception(context, exc, log_callback=None):
    message = f"{context}: {exc.__class__.__name__}: {exc}"
    if log_callback:
        log_callback(f"[!] {message}")
    else:
        print(f"[!] {message}", file=sys.stderr)
    return message














def ensure_stable_python_runtime():
    if sys.version_info < (3, 14) or os.environ.get("DPE_REEXEC_DONE") == "1":
        return

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local_app_data, "Programs", "Python", "Python312", "python.exe"),
        os.path.join(local_app_data, "Programs", "Python", "Python313", "python.exe"),
    ]

    current_python = os.path.normcase(os.path.abspath(sys.executable))
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        if os.path.normcase(os.path.abspath(candidate)) == current_python:
            return

        print(
            f"[*] Detected Python {sys.version.split()[0]}, auto-switching to a more stable interpreter: {candidate}"
        )
        env = os.environ.copy()
        env["DPE_REEXEC_DONE"] = "1"
        os.execve(candidate, [candidate, os.path.abspath(__file__), *sys.argv[1:]], env)


def warn_runtime_compatibility():
    if sys.version_info >= (3, 14):
        print(
            "[Note] Current Python is 3.14+; if Mail.tm TLS errors occur, consider using Python 3.12 or 3.13."
        )


ensure_stable_python_runtime()
warn_runtime_compatibility()

EXTENSION_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "turnstilePatch")
)































































































def _make_compat_proxy(module, name, binder=None):
    target = getattr(module, name)
    @functools.wraps(target)
    def proxy(*args, **kwargs):
        if binder is not None:
            binder()
        return getattr(module, name)(*args, **kwargs)
    return proxy


def _bind_browser_runtime():
    _browser_runtime.configure_runtime(config, EXTENSION_PATH)


def _bind_account_outputs():
    _account_outputs.configure_token_runtime(
        config, http_get, http_post, log_exception,
        compatibility_error=RemoteTokenCompatibilityError,
        request_error=RemoteTokenRequestError,
    )


def _bind_mail_service():
    _mail_service.bind_runtime(globals())
    _current = globals().get("generate_username")
    _standard = _MAIL_COMPAT_PROXIES.get("generate_username")
    if _current is not None and _current is not _standard:
        _mail_service.generate_username = _current
    elif _standard is not None:
        _mail_service.generate_username = _MAIL_ORIGINALS["generate_username"]


def _bind_registration_browser():
    _registration_browser.bind_runtime(globals())


LocalAuthProxyBridge = _browser_runtime.LocalAuthProxyBridge
for _name in ['get_configured_proxy', 'get_proxies', '_parse_proxy_url', '_safe_proxy_port', '_proxy_has_auth', '_strip_proxy_auth', '_proxy_endpoint_terms', 'is_proxy_connection_error', 'page_has_proxy_error', '_ReusableThreadingTCPServer', '_proxy_recv_until_headers', '_proxy_relay', '_LocalAuthProxyBridgeHandler', 'LocalAuthProxyBridge', 'prepare_browser_proxy', 'apply_browser_proxy_option', 'create_browser_options', '_build_request_kwargs', 'http_get', 'http_post']:
    if _name.startswith("_") and _name in {"_ReusableThreadingTCPServer", "_LocalAuthProxyBridgeHandler", "_proxy_recv_until_headers", "_proxy_relay"}:
        continue
    if _name != "LocalAuthProxyBridge":
        globals()[_name] = _make_compat_proxy(_browser_runtime, _name, _bind_browser_runtime)
for _name in ['resolve_grok2api_local_token_file', '_normalize_sso_token', 'add_token_to_grok2api_local_pool', 'get_grok2api_remote_api_bases', 'add_token_to_grok2api_remote_pool', 'add_token_to_grok2api_pools']:
    globals()[_name] = _make_compat_proxy(_account_outputs, _name, _bind_account_outputs)
_MAIL_ORIGINALS = dict((name, getattr(_mail_service, name)) for name in ['_pick_list_payload', 'cloudflare_apply_auth_params', 'cloudflare_build_headers', 'cloudflare_create_account', 'cloudflare_create_temp_address', 'cloudflare_get_domains', 'cloudflare_get_message_detail', 'cloudflare_get_messages', 'cloudflare_get_oai_code', 'cloudflare_get_token', 'cloudflare_is_admin_create_path', 'cloudflare_next_default_domain', 'cloudmail_get_email_and_token', 'cloudmail_get_messages', 'cloudmail_get_oai_code', 'cloudmail_next_domain', 'create_account', 'duckmail_get_oai_code', 'extract_verification_code', 'generate_username', 'get_cloudflare_api_base', 'get_cloudflare_api_key', 'get_cloudflare_auth_mode', 'get_cloudflare_path', 'get_cloudmail_api_base', 'get_cloudmail_path', 'get_cloudmail_public_token', 'get_domains', 'get_duckmail_api_key', 'get_email_and_token', 'get_email_provider', 'get_message_detail', 'get_messages', 'get_oai_code', 'get_token', 'get_user_agent', 'get_yyds_api_key', 'get_yyds_jwt', 'pick_domain', 'yyds_create_account', 'yyds_generate_username', 'yyds_get_domains', 'yyds_get_email_and_token', 'yyds_get_message_detail', 'yyds_get_messages', 'yyds_get_oai_code', 'yyds_get_token', 'yyds_pick_domain'])
_MAIL_COMPAT_PROXIES = dict()
for _name in ['_pick_list_payload', 'cloudflare_apply_auth_params', 'cloudflare_build_headers', 'cloudflare_create_account', 'cloudflare_create_temp_address', 'cloudflare_get_domains', 'cloudflare_get_message_detail', 'cloudflare_get_messages', 'cloudflare_get_oai_code', 'cloudflare_get_token', 'cloudflare_is_admin_create_path', 'cloudflare_next_default_domain', 'cloudmail_get_email_and_token', 'cloudmail_get_messages', 'cloudmail_get_oai_code', 'cloudmail_next_domain', 'create_account', 'duckmail_get_oai_code', 'extract_verification_code', 'generate_username', 'get_cloudflare_api_base', 'get_cloudflare_api_key', 'get_cloudflare_auth_mode', 'get_cloudflare_path', 'get_cloudmail_api_base', 'get_cloudmail_path', 'get_cloudmail_public_token', 'get_domains', 'get_duckmail_api_key', 'get_email_and_token', 'get_email_provider', 'get_message_detail', 'get_messages', 'get_oai_code', 'get_token', 'get_user_agent', 'get_yyds_api_key', 'get_yyds_jwt', 'pick_domain', 'yyds_create_account', 'yyds_generate_username', 'yyds_get_domains', 'yyds_get_email_and_token', 'yyds_get_message_detail', 'yyds_get_messages', 'yyds_get_oai_code', 'yyds_get_token', 'yyds_pick_domain']:
    _proxy = _make_compat_proxy(_mail_service, _name, _bind_mail_service)
    _MAIL_COMPAT_PROXIES[_name] = _proxy
    globals()[_name] = _proxy
for _name in ['generate_random_birthdate', 'response_preview', 'is_cloudflare_block_response', 'set_birth_date', 'set_tos_accepted', 'encode_grpc_nsfw_settings', 'update_nsfw_settings', 'enable_nsfw_for_token', 'stop_browser_proxy_bridge', 'start_browser', 'stop_browser', 'restart_browser', 'cleanup_runtime_memory', 'refresh_active_page', 'click_email_signup_button', 'open_signup_page', 'has_profile_form', 'fill_email_and_submit', 'fill_code_and_submit', 'getTurnstileToken', 'build_profile', 'fill_profile_and_submit', 'wait_for_sso_cookie']:
    globals()[_name] = _make_compat_proxy(_registration_browser, _name, _bind_registration_browser)


def __getattr__(name):
    if name == "CONFIG_FILE":
        return _app_config.CONFIG_FILE
    if name == "SIGNUP_URL":
        return _registration_browser.SIGNUP_URL
    if name in {"browser", "page", "browser_proxy_bridge", "browser_started_with_proxy", "cf_clearance"}:
        return getattr(_registration_browser, name)
    if name in {"_cf_domain_index", "_cloudmail_domain_index"}:
        return getattr(_mail_service, name)
    raise AttributeError(name)


class _CompatibilityModule(types.ModuleType):
    def __setattr__(self, name, value):
        if name == "CONFIG_FILE":
            _app_config.CONFIG_FILE = str(value)
            self.__dict__.pop(name, None)
            return
        if name == "SIGNUP_URL":
            _registration_browser.SIGNUP_URL = str(value)
            self.__dict__.pop(name, None)
            return
        if name == "config":
            if value is not _app_config.config:
                if not isinstance(value, dict):
                    raise TypeError("config must be a dict")
                _app_config.config.clear()
                _app_config.config.update(value)
            value = _app_config.config
        elif name in {"_cf_domain_index", "_cloudmail_domain_index"}:
            setattr(_mail_service, name, int(value))
            self.__dict__.pop(name, None)
            return
        elif name in {"browser", "page", "browser_proxy_bridge", "browser_started_with_proxy", "cf_clearance"}:
            setattr(_registration_browser, name, value)
            self.__dict__.pop(name, None)
            return
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _CompatibilityModule


def raise_if_cancelled(cancel_callback=None):
    if cancel_callback and cancel_callback():
        raise RegistrationCancelled("registration cancelled by user")


def sleep_with_cancel(seconds, cancel_callback=None):
    deadline = time.time() + max(seconds, 0)
    while True:
        raise_if_cancelled(cancel_callback)
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))



















































































def setup_light_theme(root):
    try:
        root.option_add("*Background", UI_BG)
        root.option_add("*Foreground", UI_FG)
        root.option_add("*selectBackground", UI_ACTIVE_BG)
        root.option_add("*selectForeground", UI_FG)
        root.option_add("*insertBackground", UI_FG)
        root.option_add("*Entry.Background", UI_ENTRY_BG)
        root.option_add("*Text.Background", UI_ENTRY_BG)
        root.option_add("*Menu.Background", UI_ENTRY_BG)
        root.option_add("*Menu.Foreground", UI_FG)
        style = ttk.Style(root)
        available = set(style.theme_names())
        if "clam" in available:
            style.theme_use("clam")
        elif "default" in available:
            style.theme_use("default")
        root.configure(bg=UI_BG)
        style.configure(".", background=UI_BG, foreground=UI_FG, fieldbackground=UI_ENTRY_BG)
        style.configure("TFrame", background=UI_BG)
        style.configure("TLabelframe", background=UI_BG, foreground=UI_FG)
        style.configure("TLabelframe.Label", background=UI_BG, foreground=UI_FG)
        style.configure("TLabel", background=UI_BG, foreground=UI_FG)
        style.configure("TCheckbutton", background=UI_BG, foreground=UI_FG)
        style.configure("TButton", background=UI_BUTTON_BG, foreground=UI_FG)
        style.configure("TEntry", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
        style.configure("TCombobox", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
        style.configure("TSpinbox", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
    except Exception:
        pass


def tk_label(parent, text="", **kwargs):
    return tk.Label(parent, text=text, bg=kwargs.pop("bg", UI_BG), fg=kwargs.pop("fg", UI_FG), **kwargs)


def tk_entry(parent, textvariable=None, width=30, **kwargs):
    return tk.Entry(
        parent,
        textvariable=textvariable,
        width=width,
        bg=UI_ENTRY_BG,
        fg=UI_FG,
        insertbackground=UI_FG,
        disabledbackground="#2f2f2f",
        disabledforeground=UI_MUTED_FG,
        highlightthickness=1,
        highlightbackground="#555555",
        relief=tk.SOLID,
        **kwargs,
    )


def tk_button(parent, text="", command=None, state="normal", **kwargs):
    return tk.Button(
        parent,
        text=text,
        command=command,
        state=state,
        bg=UI_BUTTON_BG,
        fg=UI_FG,
        activebackground=UI_ACTIVE_BG,
        activeforeground=UI_FG,
        disabledforeground="#777777",
        relief=tk.RAISED,
        padx=10,
        pady=3,
        **kwargs,
    )


def tk_checkbutton(parent, text="", variable=None, **kwargs):
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        bg=UI_BG,
        fg=UI_FG,
        activebackground=UI_BG,
        activeforeground=UI_FG,
        selectcolor="#3d7be0",
        **kwargs,
    )


def tk_option_menu(parent, variable, values, width=12):
    menu = tk.OptionMenu(parent, variable, *values)
    menu.configure(
        width=width,
        bg=UI_ENTRY_BG,
        fg=UI_FG,
        activebackground=UI_ACTIVE_BG,
        activeforeground=UI_FG,
        highlightthickness=1,
        highlightbackground="#555555",
        relief=tk.SOLID,
    )
    menu["menu"].configure(bg=UI_ENTRY_BG, fg=UI_FG, activebackground=UI_ACTIVE_BG, activeforeground=UI_FG)
    return menu































def maybe_export_cpa_xai_after_success(email, password, sso="", log_callback=None, cancel_callback=None):
    if not bool(config.get("cpa_export_enabled", False)):
        return {"ok": False, "skipped": True, "reason": "disabled"}
    logger = log_callback or (lambda message: None)
    try:
        from cpa_export import export_cpa_xai_for_account
    except Exception as exc:
        logger(f"[!] CPA module import failed, skipping OIDC export: {exc}")
        return {"ok": False, "error": str(exc)}
    current_page = None
    try:
        current_page = _registration_browser.page
    except Exception:
        current_page = None
    try:
        result = export_cpa_xai_for_account(
            email=email,
            password=password,
            page=current_page,
            sso=sso,
            config=config,
            log_callback=logger,
            cancel_callback=cancel_callback,
        )
    except Exception as exc:
        logger(f"[!] CPA OIDC export failed, account preserved: {exc}")
        return {"ok": False, "error": str(exc)}
    if result.get("ok"):
        exported_path = result.get("hotload_path") or result.get("path") or ""
        suffix = f": {exported_path}" if exported_path else ""
        if result.get("warning") or result.get("partial") or result.get("cpa_copy_error"):
            detail = result.get("cpa_copy_error") or "post-processing did not complete"
            logger(f"[!] CPA OIDC credentials generated, but post-processing warnings exist{suffix}: {detail}")
        else:
            logger(f"[+] CPA OIDC export succeeded{suffix}")
    elif not result.get("skipped"):
        logger(f"[!] CPA OIDC export failed, account preserved: {result.get('error') or result}")
    return result



def _save_mail_credential(email, credential, log_callback=None):
    from account_outputs import save_mail_credential
    try:
        return save_mail_credential(os.path.dirname(__file__), email, credential)
    except Exception as exc:
        log_exception("failed to save email credential", exc, log_callback)
        return False


def _append_account_line(path, email, password, sso):
    from account_outputs import append_account_line
    return append_account_line(path, email, password, sso)


def _queue_unsaved_account(path, payload, error, log_callback=None):
    from account_outputs import queue_unsaved_account
    try:
        return queue_unsaved_account(path, payload, error)
    except Exception as exc:
        log_exception("failed to write to pending queue", exc, log_callback)
        return False


def retry_pending_file(pending_path, output_path=None, log_callback=None):
    from account_outputs import retry_pending_file as _retry_pending_file
    return _retry_pending_file(pending_path, output_path=output_path, log_callback=log_callback)


def run_registration_common(count, log_callback, cancel_callback, accounts_output_file, observer):
    from registration_flow import RegistrationCallbacks, RegistrationOperations, run_batch
    callbacks = RegistrationCallbacks(log=log_callback, cancelled=cancel_callback)
    operations = RegistrationOperations(
        start_browser=lambda: start_browser(log_callback=log_callback),
        restart_browser=lambda: restart_browser(log_callback=log_callback),
        browser_missing=lambda: _registration_browser.browser is None,
        open_signup_page=lambda: open_signup_page(log_callback=log_callback, cancel_callback=cancel_callback),
        fill_email_and_submit=lambda: fill_email_and_submit(log_callback=log_callback, cancel_callback=cancel_callback),
        save_mail_credential=lambda email, token: _save_mail_credential(email, token, log_callback),
        fill_code_and_submit=lambda email, token: fill_code_and_submit(email, token, log_callback=log_callback, cancel_callback=cancel_callback),
        fill_profile_and_submit=lambda: fill_profile_and_submit(log_callback=log_callback, cancel_callback=cancel_callback),
        wait_for_sso_cookie=lambda: wait_for_sso_cookie(log_callback=log_callback, cancel_callback=cancel_callback),
        enable_nsfw=lambda sso: enable_nsfw_for_token(sso, log_callback=log_callback),
        persist_account_line=lambda email, password, sso: _append_account_line(accounts_output_file, email, password, sso),
        queue_unsaved_result=lambda payload, error: _queue_unsaved_account(accounts_output_file, payload, error, log_callback),
        add_tokens=lambda sso, email: add_token_to_grok2api_pools(sso, email=email, log_callback=log_callback),
        export_cpa=lambda email, password, sso: maybe_export_cpa_xai_after_success(
            email=email, password=password, sso=sso,
            log_callback=log_callback, cancel_callback=cancel_callback,
        ),
        cleanup=lambda reason: cleanup_runtime_memory(log_callback=log_callback, reason=reason),
        sleep=lambda seconds: sleep_with_cancel(seconds, cancel_callback),
        cancelled_exception=RegistrationCancelled,
        retry_exception=AccountRetryNeeded,
    )
    return run_batch(
        count=count,
        callbacks=callbacks,
        observer=observer,
        ops=operations,
        enable_nsfw=bool(config.get("enable_nsfw", True)),
        cleanup_interval=MEMORY_CLEANUP_INTERVAL,
        max_slot_retry=3,
        max_mail_retry=3,
    )


class GrokRegisterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Grok Register")
        self.root.geometry("1120x900")
        self.root.minsize(960, 700)
        self.is_running = False
        self.batch_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.registered_unsaved_count = 0
        self.postprocess_warning_count = 0
        self.results = []
        self.stop_requested = False
        self.ui_queue = queue.Queue()
        self.accounts_output_file = ""
        self.setup_ui()
        self.root.after(50, self.process_ui_queue)

    def setup_ui(self):
        load_config()
        main_frame = tk.Frame(self.root, bg=UI_BG, padx=10, pady=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_rowconfigure(3, weight=1)

        config_frame = tk.LabelFrame(
            main_frame,
            text="Config",
            bg=UI_PANEL_BG,
            fg=UI_FG,
            padx=10,
            pady=10,
            relief=tk.GROOVE,
            borderwidth=1,
        )
        config_frame.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
        config_frame.grid_columnconfigure(1, weight=1, minsize=260)
        config_frame.grid_columnconfigure(3, weight=1, minsize=260)

        def add_label(row, column, text):
            tk_label(config_frame, text=text, bg=UI_PANEL_BG).grid(
                row=row,
                column=column,
                sticky=tk.W,
                padx=(0, 6),
                pady=3,
            )

        def add_field(widget, row, column, columnspan=1, sticky=tk.EW):
            widget.grid(
                row=row,
                column=column,
                columnspan=columnspan,
                sticky=sticky,
                padx=(0, 14),
                pady=3,
            )

        add_label(0, 0, "Email Provider:")
        self.email_provider_var = tk.StringVar(value=config.get("email_provider", "duckmail"))
        self.email_provider_combo = tk_option_menu(config_frame, self.email_provider_var, ["duckmail", "yyds", "cloudflare", "cloudmail"], width=12)
        add_field(self.email_provider_combo, 0, 1, sticky=tk.W)

        add_label(0, 2, "Registration Count:")
        self.count_var = tk.StringVar(value=str(config.get("register_count", 1)))
        self.count_spinbox = tk.Spinbox(
            config_frame,
            from_=1,
            to=2500,
            width=8,
            textvariable=self.count_var,
            bg=UI_ENTRY_BG,
            fg=UI_FG,
            insertbackground=UI_FG,
            buttonbackground=UI_BUTTON_BG,
            disabledbackground="#2f2f2f",
            disabledforeground=UI_MUTED_FG,
            relief=tk.SOLID,
        )
        add_field(self.count_spinbox, 0, 3, sticky=tk.W)

        add_label(1, 0, "Registration Options:")
        self.nsfw_var = tk.BooleanVar(value=config.get("enable_nsfw", True))
        self.nsfw_check = tk_checkbutton(config_frame, text="Enable NSFW after registration", variable=self.nsfw_var)
        add_field(self.nsfw_check, 1, 1, sticky=tk.W)

        add_label(1, 2, "Proxy (optional):")
        self.proxy_var = tk.StringVar(value=config.get("proxy", ""))
        self.proxy_entry = tk_entry(config_frame, textvariable=self.proxy_var, width=34)
        add_field(self.proxy_entry, 1, 3)

        add_label(2, 0, "DuckMail API Key:")
        self.api_key_var = tk.StringVar(value=config.get("duckmail_api_key", ""))
        self.api_key_entry = tk_entry(config_frame, textvariable=self.api_key_var, width=34)
        add_field(self.api_key_entry, 2, 1)

        add_label(2, 2, "Cloudflare Auth Mode:")
        self.cloudflare_auth_mode_var = tk.StringVar(value=config.get("cloudflare_auth_mode", "none"))
        self.cloudflare_auth_mode_combo = tk_option_menu(
            config_frame, self.cloudflare_auth_mode_var, ["query-key", "bearer", "x-api-key", "x-admin-auth", "none"], width=12
        )
        add_field(self.cloudflare_auth_mode_combo, 2, 3, sticky=tk.W)

        add_label(3, 0, "Cloudflare API Base:")
        self.cloudflare_api_base_var = tk.StringVar(value=config.get("cloudflare_api_base", ""))
        self.cloudflare_api_base_entry = tk_entry(config_frame, textvariable=self.cloudflare_api_base_var, width=72)
        add_field(self.cloudflare_api_base_entry, 3, 1, columnspan=3)

        add_label(4, 0, "Cloudflare API Key:")
        self.cloudflare_api_key_var = tk.StringVar(value=config.get("cloudflare_api_key", ""))
        self.cloudflare_api_key_entry = tk_entry(config_frame, textvariable=self.cloudflare_api_key_var, width=34)
        add_field(self.cloudflare_api_key_entry, 4, 1)

        add_label(4, 2, "CF Paths:")
        self.cloudflare_paths_var = tk.StringVar(
            value=",".join(
                [
                    config.get("cloudflare_path_domains", "/api/domains"),
                    config.get("cloudflare_path_accounts", "/api/new_address"),
                    config.get("cloudflare_path_token", "/api/token"),
                    config.get("cloudflare_path_messages", "/api/mails"),
                ]
            )
        )
        self.cloudflare_paths_entry = tk_entry(config_frame, textvariable=self.cloudflare_paths_var, width=34)
        add_field(self.cloudflare_paths_entry, 4, 3)

        add_label(5, 0, "Cloud Mail API Base:")
        self.cloudmail_api_base_var = tk.StringVar(value=config.get("cloudmail_api_base", ""))
        self.cloudmail_api_base_entry = tk_entry(config_frame, textvariable=self.cloudmail_api_base_var, width=34)
        add_field(self.cloudmail_api_base_entry, 5, 1)

        add_label(5, 2, "Cloud Mail Domains:")
        self.cloudmail_domains_var = tk.StringVar(value=config.get("cloudmail_domains", ""))
        self.cloudmail_domains_entry = tk_entry(config_frame, textvariable=self.cloudmail_domains_var, width=34)
        add_field(self.cloudmail_domains_entry, 5, 3)

        add_label(6, 0, "Cloud Mail Public Token:")
        self.cloudmail_public_token_var = tk.StringVar(value=config.get("cloudmail_public_token", ""))
        self.cloudmail_public_token_entry = tk_entry(config_frame, textvariable=self.cloudmail_public_token_var, width=72)
        add_field(self.cloudmail_public_token_entry, 6, 1, columnspan=3)

        add_label(7, 0, "grok2api local pool:")
        self.grok2api_local_auto_var = tk.BooleanVar(value=bool(config.get("grok2api_auto_add_local", True)))
        self.grok2api_local_auto_check = tk_checkbutton(config_frame, variable=self.grok2api_local_auto_var)
        add_field(self.grok2api_local_auto_check, 7, 1, sticky=tk.W)

        add_label(7, 2, "grok2api pool name:")
        self.grok2api_pool_name_var = tk.StringVar(value=str(config.get("grok2api_pool_name", "ssoBasic")))
        self.grok2api_pool_name_combo = tk_option_menu(
            config_frame, self.grok2api_pool_name_var, ["ssoBasic", "ssoSuper"], width=12
        )
        add_field(self.grok2api_pool_name_combo, 7, 3, sticky=tk.W)

        add_label(8, 0, "Local token.json:")
        self.grok2api_local_file_var = tk.StringVar(value=str(config.get("grok2api_local_token_file", "")))
        self.grok2api_local_file_entry = tk_entry(config_frame, textvariable=self.grok2api_local_file_var, width=72)
        add_field(self.grok2api_local_file_entry, 8, 1, columnspan=3)

        add_label(9, 0, "grok2api remote pool:")
        self.grok2api_remote_auto_var = tk.BooleanVar(value=bool(config.get("grok2api_auto_add_remote", False)))
        self.grok2api_remote_auto_check = tk_checkbutton(config_frame, variable=self.grok2api_remote_auto_var)
        add_field(self.grok2api_remote_auto_check, 9, 1, sticky=tk.W)

        add_label(10, 0, "grok2api remote base:")
        self.grok2api_remote_base_var = tk.StringVar(value=str(config.get("grok2api_remote_base", "")))
        self.grok2api_remote_base_entry = tk_entry(config_frame, textvariable=self.grok2api_remote_base_var, width=72)
        add_field(self.grok2api_remote_base_entry, 10, 1, columnspan=3)

        add_label(11, 0, "grok2api remote app_key:")
        self.grok2api_remote_key_var = tk.StringVar(value=str(config.get("grok2api_remote_app_key", "")))
        self.grok2api_remote_key_entry = tk_entry(config_frame, textvariable=self.grok2api_remote_key_var, width=72)
        add_field(self.grok2api_remote_key_entry, 11, 1, columnspan=3)

        add_label(12, 0, "OIDC / CPA:")
        self.cpa_export_var = tk.BooleanVar(value=bool(config.get("cpa_export_enabled", True)))
        self.cpa_export_check = tk_checkbutton(
            config_frame,
            text="Mint OAuth after register (for grok-cli)",
            variable=self.cpa_export_var,
        )
        add_field(self.cpa_export_check, 12, 1, sticky=tk.W)

        self.cpa_push_9router_var = tk.BooleanVar(value=bool(config.get("cpa_push_9router", True)))
        self.cpa_push_9router_check = tk_checkbutton(
            config_frame,
            text="Push to 9Router grok-cli (direct)",
            variable=self.cpa_push_9router_var,
        )
        add_field(self.cpa_push_9router_check, 12, 2, sticky=tk.W)

        self.cpa_save_auth_file_var = tk.BooleanVar(value=bool(config.get("cpa_save_auth_file", False)))
        self.cpa_save_auth_file_check = tk_checkbutton(
            config_frame,
            text="Also save cpa_auths JSON",
            variable=self.cpa_save_auth_file_var,
        )
        add_field(self.cpa_save_auth_file_check, 12, 3, sticky=tk.W)

        add_label(13, 0, "CPA output dir:")
        self.cpa_auth_dir_var = tk.StringVar(value=str(config.get("cpa_auth_dir", "./cpa_auths")))
        self.cpa_auth_dir_entry = tk_entry(config_frame, textvariable=self.cpa_auth_dir_var, width=34)
        add_field(self.cpa_auth_dir_entry, 13, 1, columnspan=2)

        add_label(14, 0, "Browser hide:")
        self.browser_hide_mode_var = tk.StringVar(value=str(config.get("browser_hide_mode", "normal") or "normal"))
        # Prefer Combobox if available; fallback to plain Entry.
        try:
            from tkinter import ttk as _ttk
            self.browser_hide_mode_combo = _ttk.Combobox(
                config_frame,
                textvariable=self.browser_hide_mode_var,
                values=("normal", "minimize", "xvfb"),
                state="readonly",
                width=18,
            )
            add_field(self.browser_hide_mode_combo, 14, 1, sticky=tk.W)
        except Exception:
            self.browser_hide_mode_entry = tk_entry(config_frame, textvariable=self.browser_hide_mode_var, width=18)
            add_field(self.browser_hide_mode_entry, 14, 1, sticky=tk.W)
        tk_label(
            config_frame,
            text="normal=visible · minimize=hidden window · xvfb=virtual display (not headless)",
        ).grid(row=14, column=2, columnspan=2, sticky=tk.W, padx=4)

        btn_frame = tk.Frame(main_frame, bg=UI_BG)
        btn_frame.grid(row=1, column=0, sticky=tk.EW, pady=(0, 6))
        self.start_btn = tk_button(btn_frame, text="Start Registration", command=self.start_registration)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = tk_button(btn_frame, text="Stop", command=self.stop_registration, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        self.clear_btn = tk_button(btn_frame, text="Clear Log", command=self.clear_log)
        self.clear_btn.pack(side=tk.LEFT, padx=5)

        status_frame = tk.Frame(main_frame, bg=UI_BG)
        status_frame.grid(row=2, column=0, sticky=tk.EW, pady=(0, 6))
        self.status_var = tk.StringVar(value="Ready")
        tk_label(status_frame, text="Status: ").pack(side=tk.LEFT)
        self.status_label = tk.Label(status_frame, textvariable=self.status_var, bg=UI_BG, fg="green")
        self.status_label.pack(side=tk.LEFT)
        self.stats_var = tk.StringVar(value="Success: 0 | Failed: 0 | Pending: 0 | PostProc Warnings: 0")
        tk.Label(status_frame, textvariable=self.stats_var, bg=UI_BG, fg=UI_FG).pack(side=tk.RIGHT)
        log_frame = tk.LabelFrame(
            main_frame,
            text="Log",
            bg=UI_PANEL_BG,
            fg=UI_FG,
            padx=5,
            pady=5,
            relief=tk.GROOVE,
            borderwidth=1,
        )
        log_frame.grid(row=3, column=0, sticky=tk.NSEW)
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(0, weight=1)
        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=18,
            width=60,
            bg="#111111",
            fg="#f5f5f5",
            insertbackground="#f5f5f5",
            selectbackground="#345a8a",
            selectforeground="#ffffff",
            relief=tk.SOLID,
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#555555",
        )
        self.log_text.grid(row=0, column=0, sticky=tk.NSEW)
        self.log("[*] GUI ready, config loaded")
        self.log(f"[*] Current email provider: {self.email_provider_var.get()} | Registration count: {self.count_var.get()}")

    def process_ui_queue(self):
        try:
            while True:
                event = self.ui_queue.get_nowait()
                kind = event[0]
                if kind == "log":
                    line = event[1]
                    self.log_text.insert(tk.END, f"{line}\n")
                    self.log_text.see(tk.END)
                elif kind == "clear_log":
                    self.log_text.delete(1.0, tk.END)
                elif kind == "stats":
                    self.stats_var.set(f"Success: {event[1]} | Failed: {event[2]} | Pending: {event[3]} | PostProc Warnings: {event[4]}")
                elif kind == "running":
                    running = bool(event[1])
                    self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
                    self.stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)
                    self.status_var.set("Running..." if running else "Ready")
                    self.status_label.config(foreground="blue" if running else "green")
                elif kind == "error":
                    messagebox.showerror(event[1], event[2])
        except queue.Empty:
            pass
        except Exception as exc:
            print(f"[!] UI queue processing failed: {exc}", file=sys.stderr)
        finally:
            try:
                self.root.after(50, self.process_ui_queue)
            except Exception:
                pass

    def log(self, message):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        self.ui_queue.put(("log", line))

    def clear_log(self):
        self.ui_queue.put(("clear_log",))

    def update_stats(self):
        self.ui_queue.put(("stats", self.success_count, self.fail_count, self.registered_unsaved_count, self.postprocess_warning_count))

    def _set_running_ui(self, running):
        self.is_running = bool(running)
        self.ui_queue.put(("running", self.is_running))


    def should_stop(self):
        return self.stop_requested or not self.is_running

    def _reset_batch_counters(self):
        self.success_count = 0
        self.fail_count = 0
        self.registered_unsaved_count = 0
        self.postprocess_warning_count = 0

    def start_registration(self):
        if self.is_running:
            self.log("[!] A task is already running")
            return

        config["email_provider"] = self.email_provider_var.get().strip() or "duckmail"
        config["enable_nsfw"] = bool(self.nsfw_var.get())
        config["proxy"] = self.proxy_var.get().strip()
        config["duckmail_api_key"] = self.api_key_var.get().strip()
        config["cloudflare_api_base"] = self.cloudflare_api_base_var.get().strip()
        config["cloudflare_api_key"] = self.cloudflare_api_key_var.get().strip()
        config["cloudflare_auth_mode"] = self.cloudflare_auth_mode_var.get().strip() or "none"
        config["cloudmail_api_base"] = self.cloudmail_api_base_var.get().strip()
        config["cloudmail_public_token"] = self.cloudmail_public_token_var.get().strip()
        config["cloudmail_domains"] = self.cloudmail_domains_var.get().strip()
        config["grok2api_auto_add_local"] = bool(self.grok2api_local_auto_var.get())
        config["grok2api_local_token_file"] = self.grok2api_local_file_var.get().strip()
        config["grok2api_pool_name"] = self.grok2api_pool_name_var.get().strip() or "ssoBasic"
        config["grok2api_auto_add_remote"] = bool(self.grok2api_remote_auto_var.get())
        config["grok2api_remote_base"] = self.grok2api_remote_base_var.get().strip()
        config["grok2api_remote_app_key"] = self.grok2api_remote_key_var.get().strip()
        config["cpa_export_enabled"] = bool(self.cpa_export_var.get())
        config["cpa_push_9router"] = bool(self.cpa_push_9router_var.get())
        config["cpa_save_auth_file"] = bool(self.cpa_save_auth_file_var.get())
        config["cpa_auth_dir"] = self.cpa_auth_dir_var.get().strip() or "./cpa_auths"
        config["browser_hide_mode"] = (self.browser_hide_mode_var.get() or "normal").strip().lower() or "normal"
        raw_paths = [x.strip() for x in self.cloudflare_paths_var.get().split(",") if x.strip()]
        if len(raw_paths) >= 4:
            config["cloudflare_path_domains"] = raw_paths[0] if raw_paths[0].startswith("/") else ("/" + raw_paths[0])
            config["cloudflare_path_accounts"] = raw_paths[1] if raw_paths[1].startswith("/") else ("/" + raw_paths[1])
            config["cloudflare_path_token"] = raw_paths[2] if raw_paths[2].startswith("/") else ("/" + raw_paths[2])
            config["cloudflare_path_messages"] = raw_paths[3] if raw_paths[3].startswith("/") else ("/" + raw_paths[3])
        try:
            count = int(self.count_var.get())
            config["register_count"] = count
            validated = validate_run_requirements(config)
            config.clear()
            config.update(validated)
            save_config()
        except (ValueError, ConfigError) as exc:
            self.log(f"[!] Config invalid or save failed: {exc}")
            return
        self.stop_requested = False
        self._reset_batch_counters()
        self.results = []
        now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.accounts_output_file = os.path.join(
            os.path.dirname(__file__), "accounts.txt"
        )
        self.update_stats()
        self._set_running_ui(True)
        self.log(f"[*] Config saved, starting execution. Target count: {count}")
        self.log(f"[*] Successful accounts will be saved to: {self.accounts_output_file}")
        threading.Thread(
            target=self.run_registration,
            args=(count,),
            daemon=True,
        ).start()

    def stop_registration(self):
        self.stop_requested = True
        self.log("[!] Registration cancelled by user")

    def run_registration(self, count):
        def observer(batch, account, output):
            self.success_count = batch.success_count
            self.fail_count = batch.fail_count
            self.registered_unsaved_count = batch.registered_unsaved_count
            self.postprocess_warning_count = batch.postprocess_warning_count
            if account is not None:
                self.results.append({"email": account.email, "sso": account.sso, "profile": account.profile, "output": output})
            self.update_stats()
        try:
            batch = run_registration_common(
                count=count,
                log_callback=self.log,
                cancel_callback=self.should_stop,
                accounts_output_file=self.accounts_output_file,
                observer=observer,
            )
            self.success_count = batch.success_count
            self.fail_count = batch.fail_count
            self.registered_unsaved_count = batch.registered_unsaved_count
            self.postprocess_warning_count = batch.postprocess_warning_count
            self.update_stats()
        except Exception as exc:
            log_exception("task error", exc, self.log)
        finally:
            self._set_running_ui(False)
            self.log("[*] Task finished")




class CliStopController:
    def __init__(self):
        self.stop_requested = False

    def should_stop(self):
        return self.stop_requested

    def stop(self):
        self.stop_requested = True


def cli_log(message):
    # Filter verbose background noise logs to keep console output clean
    msg_str = str(message)
    lower_msg = msg_str.lower()
    
    # Define keywords/patterns to show in CLI mode
    show_keywords = [
        "[+]", 
        "--- starting", 
        "successful accounts", 
        "task finished",
        "email created",
        "nsfw enabled",
        "9router grok-cli",
        "9router grok-web"
    ]
    
    # Check if this message is important
    should_show = any(kw in lower_msg for kw in show_keywords) or lower_msg.startswith("[!]")
    
    if should_show:
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        
        # Color coding for terminal output
        GREEN = "\033[92m"
        RED = "\033[91m"
        CYAN = "\033[96m"
        RESET = "\033[0m"
        
        if "[+]" in msg_str or "succeeded" in lower_msg:
            print(f"{GREEN}[{timestamp}] {msg_str}{RESET}", flush=True)
        elif "[!]" in msg_str or "error" in lower_msg or "failed" in lower_msg:
            print(f"{RED}[{timestamp}] {msg_str}{RESET}", flush=True)
        elif "--- starting" in lower_msg:
            print(f"\n{CYAN}[{timestamp}] {msg_str}{RESET}", flush=True)
        else:
            print(f"[{timestamp}] {msg_str}", flush=True)


def run_registration_cli(count):
    controller = CliStopController()
    accounts_output_file = os.path.join(
        os.path.dirname(__file__),
        "accounts.txt",
    )
    cli_log(f"[*] CLI mode started, target count: {count}")
    cli_log(f"[*] Successful accounts will be saved to: {accounts_output_file}")
    last_stats = {"success": 0, "fail": 0, "pending": 0, "warnings": 0}
    def observer(batch, account, output):
        last_stats["success"] = batch.success_count
        last_stats["fail"] = batch.fail_count
        last_stats["pending"] = batch.registered_unsaved_count
        last_stats["warnings"] = batch.postprocess_warning_count
        cli_log(f"[*] Current stats: Success {batch.success_count} | Failed {batch.fail_count} | Pending {batch.registered_unsaved_count} | PostProc Warnings {batch.postprocess_warning_count}")
    try:
        batch = run_registration_common(
            count=count,
            log_callback=cli_log,
            cancel_callback=controller.should_stop,
            accounts_output_file=accounts_output_file,
            observer=observer,
        )
        last_stats["success"] = batch.success_count
        last_stats["fail"] = batch.fail_count
        last_stats["pending"] = batch.registered_unsaved_count
        last_stats["warnings"] = batch.postprocess_warning_count
    except KeyboardInterrupt:
        controller.stop()
        cli_log("[!] Ctrl+C received, stopping and cleaning up")
    except Exception as exc:
        log_exception("task error", exc, cli_log)
    finally:
        cli_log(f"[*] Task finished. Success {last_stats['success']} | Failed {last_stats['fail']} | Pending {last_stats['pending']} | PostProc Warnings {last_stats['warnings']}")


def main_cli():
    try:
        load_config()
    except ConfigError as exc:
        cli_log(f"[!] {exc}")
        return
    try:
        validated = validate_run_requirements(config)
        config.clear()
        config.update(validated)
    except ConfigError as exc:
        cli_log(f"[!] {exc}")
        return
    
    count = int(config.get("register_count", 1) or 1)
    
    # Check if a custom count was passed via command line argument, e.g. python grok_register_ttk.py cli 5
    if len(sys.argv) > 2:
        try:
            count = int(sys.argv[2])
        except ValueError:
            pass
            
    cli_log("[*] CLI config loaded")
    cli_log(f"[*] Current email provider: {config.get('email_provider', 'cloudflare')} | Registration count: {count}")
    run_registration_cli(count)


def main():
    if len(sys.argv) > 1 and sys.argv[1].strip().lower() == "retry-pending":
        if len(sys.argv) < 3:
            print("Usage: python grok_register_ttk.py retry-pending <pending_file> [output_file]", file=sys.stderr)
            return
        try:
            summary = retry_pending_file(
                sys.argv[2],
                output_path=sys.argv[3] if len(sys.argv) > 3 else None,
                log_callback=cli_log,
            )
            cli_log(
                f"[*] Pending retry complete: restored {summary['restored']} | remaining {summary['remaining']} | output {summary['output_path']}"
            )
        except Exception as exc:
            log_exception("pending retry failed", exc, cli_log)
        return
    if len(sys.argv) > 1 and sys.argv[1].strip().lower() in ("start", "cli", "--cli"):
        main_cli()
        return
    if not TK_AVAILABLE:
        print(f"[!] GUI mode requires Tkinter, but it is not available: {TK_IMPORT_ERROR}", file=sys.stderr)
        print("[*] Use CLI mode instead: python grok_register_ttk.py cli", file=sys.stderr)
        return
    root = tk.Tk()
    setup_light_theme(root)
    try:
        app = GrokRegisterGUI(root)
    except ConfigError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        try:
            messagebox.showerror("Config Error", str(exc))
        except Exception:
            pass
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
