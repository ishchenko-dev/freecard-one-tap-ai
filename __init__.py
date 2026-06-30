# FreeCard — One-Tap AI — an Anki add-on that builds flashcards from selected text.
# Copyright (C) 2026  FreeCard contributors
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version. This program is distributed WITHOUT ANY WARRANTY. See the GNU
# AGPLv3 (the LICENSE file) for details.
#
# Bump the patch (last number) whenever you change this addon and sync to Anki; restart Anki to load code.
__version__ = "0.1.6"

import sys
import subprocess
import ctypes
import html
import os
import re
from datetime import datetime
def _apply_secondary_font(widget, delta_pt: int = 0, weight_bold: bool = False) -> None:
    """Small High-DPI helper: works with pt, not px. Shifts the widget's font
    size relative to the current one (delta_pt may be < 0)."""
    try:
        f = widget.font()
        ps = f.pointSize()
        if ps <= 0:
            ps = 10
        new_ps = max(7, ps + int(delta_pt))
        f.setPointSize(new_ps)
        if weight_bold:
            try:
                from aqt.qt import QFont as _QF
                f.setWeight(_QF.Weight.DemiBold)
            except Exception:
                f.setBold(True)
        widget.setFont(f)
    except Exception:
        pass


_LOG_MAX_BYTES = 512 * 1024  # rotate ai.log once it passes ~0.5 MB


def _log(message: str) -> None:
    """
    Logging utility for debugging and monitoring.
    Writes to addon/ai.log with timestamps.
    Silent on errors to avoid breaking the addon if logging fails.

    Rotates to ai.log.1 once the file passes _LOG_MAX_BYTES so the log
    can't grow without bound (keeps the previous generation around).
    """
    try:
        base_dir = os.path.dirname(__file__)
        log_path = os.path.join(base_dir, "ai.log")
        try:
            if os.path.exists(log_path) and os.path.getsize(log_path) > _LOG_MAX_BYTES:
                os.replace(log_path, log_path + ".1")
        except Exception:
            pass
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")
    except Exception:
        pass


def _log_exc(where: str) -> None:
    """Log a full traceback under a clear BUG marker, so debug reports capture it."""
    try:
        import traceback
        _log(f"BUG in {where}:\n{traceback.format_exc()}")
    except Exception:
        pass


def _env_info() -> str:
    """One-line environment summary for debug reports."""
    try:
        import platform as _pf
        qt = ""
        try:
            from aqt.qt import QT_VERSION_STR  # type: ignore
            qt = f" qt={QT_VERSION_STR}"
        except Exception:
            pass
        anki_ver = ""
        try:
            from anki.buildinfo import version as _av  # type: ignore
            anki_ver = f" anki={_av}"
        except Exception:
            pass
        return (
            f"FreeCard {__version__} | {sys.platform} {_pf.platform()} | "
            f"py={_pf.python_version()}{qt}{anki_ver}"
        )
    except Exception:
        return f"FreeCard {__version__} | {sys.platform}"


def _build_debug_report(tail_lines: int = 250) -> str:
    """Assemble a copy-pasteable debug report: environment + the tail of ai.log
    (which now includes full tracebacks for crashes)."""
    parts = ["=== FreeCard debug report ===", _env_info(), ""]
    try:
        log_path = os.path.join(os.path.dirname(__file__), "ai.log")
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            parts.append(f"--- ai.log (last {tail_lines} lines) ---")
            parts.append("".join(lines[-tail_lines:]).strip())
        else:
            parts.append("(no ai.log found)")
    except Exception as e:
        parts.append(f"(could not read ai.log: {e!r})")
    report = "\n".join(parts)
    # Safety net: redact anything that looks like an API key before it leaves the
    # machine (the report may be pasted into a public issue).
    try:
        import re as _re
        report = _re.sub(r"AIza[0-9A-Za-z\-_]{10,}", "AIza…REDACTED", report)
        report = _re.sub(r"gsk_[0-9A-Za-z]{10,}", "gsk_…REDACTED", report)
        report = _re.sub(r"(?i)(key=)[0-9A-Za-z\-_]{10,}", r"\1REDACTED", report)
    except Exception:
        pass
    return report


# --- Online bug reporting via Telegram ------------------------------------------
# To enable "Send report": create a bot with @BotFather, put its token below, and
# your numeric chat id (message @userinfobot to get it). Leave empty to disable.
# NOTE: the token ships inside the (public) addon; it can only message THIS chat,
# and is revocable via BotFather if abused.
_BUG_TG_TOKEN = "8805569259:AAGVBpz0_ToI-6ZYdDanFvlHaLWy6fTBQF0"   # bug-report bot
_BUG_TG_CHAT = "485304600"    # developer chat id


def _bug_reporting_enabled() -> bool:
    return bool(_BUG_TG_TOKEN and _BUG_TG_CHAT)


def _send_bug_report_telegram(text: str, contact: str = "") -> "tuple[bool, str]":
    """Send a bug report to the developer's Telegram. Returns (ok, error)."""
    if not _bug_reporting_enabled():
        return False, "reporting not configured"
    try:
        import ssl
        import urllib.parse
        import urllib.request
        head = f"🐞 {_env_info()}\nContact: {contact.strip() or '-'}\n\n"
        # Telegram message limit ~4096 chars. The newest lines (where the error is)
        # are at the END, so keep the header + the most recent TAIL, not the start.
        text = text or ""
        budget = 3900 - len(head)
        if len(text) > budget:
            text = "…(older log truncated)…\n" + text[-(budget - 30):]
        body = head + text
        data = urllib.parse.urlencode({"chat_id": _BUG_TG_CHAT, "text": body}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{_BUG_TG_TOKEN}/sendMessage",
            data=data, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                resp.read()
        except Exception:
            # Some Python builds lack root certificates → retry without verification
            # (same fallback the rest of the addon's network code uses).
            ctx = ssl._create_unverified_context()  # type: ignore[attr-defined]
            with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
                resp.read()
        return True, ""
    except Exception as e:
        _log_exc("telegram send")
        return False, str(e)


def _tg_urlopen(req, timeout: int = 25):
    """urlopen with the same SSL fallback the rest of the addon uses."""
    import ssl
    import urllib.request
    try:
        return urllib.request.urlopen(req, timeout=timeout).read()
    except Exception:
        ctx = ssl._create_unverified_context()  # type: ignore[attr-defined]
        return urllib.request.urlopen(req, timeout=timeout, context=ctx).read()


def _redact_keys(text: str) -> str:
    try:
        import re as _re
        text = _re.sub(r"AIza[0-9A-Za-z\-_]{10,}", "AIza…REDACTED", text)
        text = _re.sub(r"gsk_[0-9A-Za-z]{10,}", "gsk_…REDACTED", text)
        text = _re.sub(r"(?i)(key=)[0-9A-Za-z\-_]{10,}", r"\1REDACTED", text)
    except Exception:
        pass
    return text


def _send_feedback_telegram(text: str, contact: str = "") -> "tuple[bool, str]":
    """Send a free-text suggestion/feedback message to the developer's Telegram."""
    if not _bug_reporting_enabled():
        return False, "reporting not configured"
    try:
        import urllib.parse
        import urllib.request
        msg = f"💬 FreeCard {__version__} feedback\nContact: {contact.strip() or '-'}\n\n{(text or '').strip()}"[:3900]
        data = urllib.parse.urlencode({"chat_id": _BUG_TG_CHAT, "text": msg}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{_BUG_TG_TOKEN}/sendMessage", data=data, method="POST",
        )
        _tg_urlopen(req)
        return True, ""
    except Exception as e:
        _log_exc("telegram feedback")
        return False, str(e)


def _send_full_log_telegram(contact: str = "") -> "tuple[bool, str]":
    """Upload the full ai.log file (up to ~512KB) to the developer's Telegram as a
    document — for serious bugs where the short report isn't enough. Keys redacted."""
    if not _bug_reporting_enabled():
        return False, "reporting not configured"
    try:
        import time
        import urllib.request
        log_path = os.path.join(os.path.dirname(__file__), "ai.log")
        if not os.path.exists(log_path):
            return False, "no log file"
        with open(log_path, "rb") as f:
            raw = f.read()
        raw = _redact_keys(raw.decode("utf-8", "replace")).encode("utf-8")
        boundary = "----FreeCard" + str(int(time.time() * 1000))
        caption = f"🐞 full log — {_env_info()} | contact: {contact.strip() or '-'}"[:1000]

        def _field(name: str, value: str) -> bytes:
            return (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"
            ).encode("utf-8")

        body = b""
        body += _field("chat_id", str(_BUG_TG_CHAT))
        body += _field("caption", caption)
        body += (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; "
            f"filename=\"freecard-ai.log\"\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
        ).encode("utf-8")
        body += raw + b"\r\n"
        body += (f"--{boundary}--\r\n").encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{_BUG_TG_TOKEN}/sendDocument",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        _tg_urlopen(req, timeout=40)
        return True, ""
    except Exception as e:
        _log_exc("telegram full log")
        return False, str(e)


# ---- Anonymous usage counter (opt-out) -------------------------------------
# Reuses the same bot/chat as bug reports. Sends only an anonymous random install
# id (no personal data, no card content) so the developer can gauge how many
# people install/use the addon. Users can turn it off in settings.
def _telemetry_enabled() -> bool:
    try:
        return bool(_get_config().get("telemetry_enabled", True))
    except Exception:
        return True


def _get_install_id() -> str:
    """Stable anonymous id for this install (random hex, created once)."""
    try:
        cfg = _get_config()
        iid = str(cfg.get("install_id", "")).strip()
        if not iid:
            import uuid
            iid = uuid.uuid4().hex[:12]
            cfg["install_id"] = iid
            _set_config(cfg)
        return iid
    except Exception:
        return "unknown"


def _send_stat(event: str, extra: str = "") -> None:
    """Fire-and-forget anonymous stat ping to the developer's Telegram."""
    if not _bug_reporting_enabled() or not _telemetry_enabled():
        return

    def _go() -> None:
        try:
            import urllib.parse
            import urllib.request
            iid = _get_install_id()
            msg = f"📊 {event} | id={iid} | v{__version__} | {sys.platform}"
            if extra:
                msg += f" | {extra}"
            data = urllib.parse.urlencode({"chat_id": _BUG_TG_CHAT, "text": msg[:500]}).encode("utf-8")
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{_BUG_TG_TOKEN}/sendMessage", data=data, method="POST",
            )
            _tg_urlopen(req, timeout=12)
        except Exception:
            pass

    try:
        threading.Thread(target=_go, name="FreeCard-Stat", daemon=True).start()
    except Exception:
        pass


def _stat_on_startup() -> None:
    """On startup: a one-time 'new_install' ping, plus one 'active' heartbeat/day."""
    if not _bug_reporting_enabled() or not _telemetry_enabled():
        return
    try:
        import time as _t
        cfg = _get_config()
        is_new = not str(cfg.get("install_id", "")).strip()
        _get_install_id()  # ensure the id exists (on the main thread)
        if is_new:
            _send_stat("new_install")
        today = _t.strftime("%Y-%m-%d")
        cfg = _get_config()
        if str(cfg.get("stat_last_day", "")) != today:
            cfg["stat_last_day"] = today
            _set_config(cfg)
            total = int(cfg.get("stat_gen_total", 0) or 0)
            _send_stat("active", f"cards={total}")
    except Exception:
        pass


def _stat_on_generation() -> None:
    """Count a successful generation; ping (throttled to 30 min) so live activity
    is visible without spamming the chat."""
    if not _bug_reporting_enabled() or not _telemetry_enabled():
        return
    try:
        import time as _t
        cfg = _get_config()
        total = int(cfg.get("stat_gen_total", 0) or 0) + 1
        last = float(cfg.get("stat_last_ping_ts", 0) or 0)
        now = _t.time()
        cfg["stat_gen_total"] = total
        cfg["stat_last_ping_ts"] = now
        _set_config(cfg)
        if now - last >= 1800:
            _send_stat("request", f"cards={total}")
    except Exception:
        pass


def _classify_error(exc) -> "tuple[str, str]":
    """Map an exception to a user-facing (code, advice) pair so users can self-fix
    common problems."""
    msg = str(exc or "").lower()
    if any(s in msg for s in ("api key not valid", "api_key_invalid", "invalid api key", "api key not specified")):
        return "E001", _t("err_e001")
    if any(s in msg for s in ("quota", "rate limit", "resource_exhausted", "429", "exceeded")):
        return "E002", _t("err_e002")
    if any(s in msg for s in ("timed out", "timeout", "urlopen", "connection", "getaddrinfo", "network", "ssl")):
        return "E003", _t("err_e003")
    if any(s in msg for s in ("not found", "404", "is not found for api version", "no such model")):
        return "E004", _t("err_e004")
    if any(s in msg for s in ("permission", "403", "denied")):
        return "E005", _t("err_e005")
    return "E000", _t("err_e000")


def _show_error(parent, exc) -> None:
    """Friendly error dialog: shows an error code + advice, and an opt-in
    'Send report' button that delivers the log to the developer's Telegram."""
    try:
        code, friendly = _classify_error(exc)
        _log(f"error shown [{code}]: {str(exc)[:200]}")
        dlg = QDialog(parent or mw)
        dlg.setWindowTitle(_t("error_title"))
        try:
            dlg.setMinimumWidth(460)
        except Exception:
            pass
        v = QVBoxLayout(dlg)
        head = QLabel(f"[{code}] {friendly}", dlg)
        head.setWordWrap(True)
        try:
            head.setStyleSheet("font-weight:600; font-size:13px;")
        except Exception:
            pass
        v.addWidget(head)
        det = QLabel(str(exc)[:300], dlg)
        det.setWordWrap(True)
        try:
            det.setStyleSheet("color: gray; font-size: 11px;")
        except Exception:
            pass
        v.addWidget(det)

        contact_edit = None
        if _bug_reporting_enabled():
            v.addSpacing(6)
            v.addWidget(QLabel(_t("error_contact_label"), dlg))
            contact_edit = QLineEdit(dlg)
            contact_edit.setPlaceholderText(_t("error_contact_ph"))
            v.addWidget(contact_edit)

        row = QHBoxLayout()
        close_btn = QPushButton(_t("error_close"), dlg)
        row.addWidget(close_btn)
        row.addStretch(1)
        send_btn = None
        if _bug_reporting_enabled():
            send_btn = QPushButton(_t("error_send"), dlg)
            row.addWidget(send_btn)
        v.addLayout(row)

        def do_send() -> None:
            send_btn.setEnabled(False)
            send_btn.setText(_t("error_sending"))
            contact = contact_edit.text().strip() if contact_edit is not None else ""
            ok, err = _send_bug_report_telegram(_build_debug_report(), contact)
            if ok:
                showInfo(_t("error_sent"))
                dlg.accept()
            else:
                showWarning(_t("error_send_failed", err=err))
                send_btn.setEnabled(True)
                send_btn.setText(_t("error_send"))

        if send_btn is not None:
            qconnect(send_btn.clicked, do_send)
        qconnect(close_btn.clicked, dlg.reject)
        dlg.exec()
    except Exception:
        try:
            showWarning(str(exc))
        except Exception:
            pass



def _start_windows_hotkey_thread() -> None:
    """
    Windows global hotkey implementation using RegisterHotKey WinAPI.
    
    Why RegisterHotKey instead of low-level hooks:
    - Simpler and more reliable for basic hotkey registration
    - No need for complex event filtering
    - Works system-wide even when Anki is minimized
    - Falls back gracefully if registration fails (e.g., hotkey already taken)
    
    Design choice: We read clipboard directly instead of simulating Ctrl+C
    because some applications (especially browsers) block programmatic key simulation
    without elevated permissions. User manually copies, we just read the result.
    """
    try:
        import ctypes.wintypes as wt

        user32 = ctypes.windll.user32
        MOD_ALT = 0x0001
        MOD_CONTROL = 0x0002
        MOD_SHIFT = 0x0004
        MOD_WIN = 0x0008

        # Virtual key code mapping for letters and function keys
        def _vk_for_char(ch: str) -> int:
            ch = ch.upper()
            if len(ch) == 1 and 'A' <= ch <= 'Z':
                return ord(ch)
            # VK_F8 = 0x77
            if ch.lower() == 'f8':
                return 0x77
            return ord('X')

        cfg = _get_config()
        combo = str(cfg.get("hotkey_combo", "ctrl+alt+t")).lower().strip()
        parts = [p.strip() for p in combo.split('+') if p.strip()]
        key = parts[-1] if parts else 't'
        mods = 0
        if any(p in ("ctrl", "control") for p in parts):
            mods |= MOD_CONTROL
        if any(p in ("shift",) for p in parts):
            mods |= MOD_SHIFT
        # Accept macOS naming ("option"/"opt") as Alt on Windows.
        if any(p in ("alt", "option", "opt") for p in parts):
            mods |= MOD_ALT
        # macOS "cmd"/"command" has no Windows twin; map it (and win/meta) to the Win key.
        if any(p in ("win", "meta", "cmd", "command") for p in parts):
            mods |= MOD_WIN
        key_vk = _vk_for_char(key)

        # Never register a bare-key global hotkey: without a modifier we'd hijack a
        # plain letter system-wide. A macOS-only combo like "cmd+option+t" used to
        # collapse to just "T" on Windows and fire on every keypress — guard against it.
        if mods == 0:
            return

        # Register the hotkey (atom_id=1 is arbitrary but unique per process)
        atom_id = 1
        if not user32.RegisterHotKey(None, atom_id, mods, key_vk):
            return

        MSG = wt.MSG
        msg = MSG()
        while True:
            if user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == 0x0312:  # WM_HOTKEY
                    # Read clipboard directly - no simulation, user manually copies
                    txt = _read_clipboard_text()
                    if txt:
                        _enqueue_generation_and_add(txt)
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
    except Exception:
        pass
from aqt import mw
from aqt.qt import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QFont,
    QHBoxLayout,
    QIcon,
    QKeySequence,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPainter,
    QPixmap,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QShortcut,
    QSize,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QThread,
    QVBoxLayout,
    QWidget,
    Qt,
    pyqtSignal,
    qconnect,
)
from aqt.utils import askUser, showInfo, showWarning
from typing import Optional, Tuple
import threading
import json as _json
import time

try:
    # Python 3.7+
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except Exception:  # pragma: no cover - very old Python fallback
    from http.server import BaseHTTPRequestHandler, HTTPServer as ThreadingHTTPServer  # type: ignore

try:
    # Present in many Anki versions
    from anki.notes import Note  # type: ignore
except Exception:  # pragma: no cover - fallback for older versions
    Note = None  # type: ignore


# UI Strings - centralized for potential future localization
# Currently English-only, but structure allows easy addition of other languages
_UI_STRINGS = {
    # Errors
    "error_collection_not_loaded": "Collection not loaded.",
    "error_note_type_not_found": "Failed to get note type.",
    "error_cannot_create_note": "Failed to create note.",
    "error_insufficient_fields": "Selected note type doesn't have enough fields.",
    "error_add_note_method_not_found": "Note addition method not found.",
    "error_cannot_access_note_types": "Failed to access note types list.",
    "error_empty_front": "Fill in the front side.",
    "error_no_words": "Enter word(s) in English for generation.",
    "error_ai": "AI error: {error}",
    "error_api_daily_quota": "Daily quota exceeded. Please try again tomorrow or upgrade your API plan.",
    "error_api_rate_limit": "Rate limit exceeded (too many requests per minute). Please wait a moment and try again.",
    "error_api_quota_billing": "Quota exceeded or billing issue. Check your Google Cloud billing and API quotas.",
    "error_api_forbidden": "Access forbidden: {message}",
    "error_api_invalid_key": "Invalid API key. Please check your Gemini API key in settings.",
    "error_api_invalid_request": "Invalid request: {message}",
    "error_api_service_unavailable": "Service temporarily unavailable (503). The model may be overloaded. Please try again in a few moments.",
    "error_api_generic": "Gemini API error ({code}): {message}",
    
    # Success messages
    "success_card_added": "Card added.",
    "success_card_added_ai": "Card added (AI).",
    "success_card_added_default": "Card added to Default deck.",
    "success_card_auto_added": "Card automatically added to deck.",
    "error_card_not_added": "Error: card not added",
    "error_card_already_exists": "This card already exists in the selected deck.",
    
    # Dialog titles and labels
    "dialog_title": "Add Card",
    "menu_add_card": "Add card",
    # AI review dialog
    "dialog_ai_review_title": "Review card before adding",
    "label_card_final_preview": "Card preview",
    "label_edit_section": "Edit fields",
    "label_card_side_front": "Front",
    "label_card_side_back": "Back",
    "label_preview_front_edit": "Front",
    "label_preview_back_edit": "Back",
    "label_refine_instruction": "Refine with AI",
    "placeholder_refine": "Describe what to change; then click \"Refine with AI\"",
    "button_refine_ai": "Refine with AI",
    "button_confirm_add": "Add",
    "button_cancel_review": "Cancel",
    "button_toggle_edit": "Edit fields",
    "button_toggle_refine": "Refine with AI",
    "dup_warn_title": "Similar card already exists",
    "dup_warn_text": "A similar card already exists in this deck.\n\nExisting Front:\n{existing}\n\nNew Front:\n{new}\n\nAdd anyway?",
    "dup_warn_add_anyway": "Add anyway",
    "len_counter_front": "Front: {n}/{lo}–{hi} chars",
    "len_counter_back": "Back: {n}/{lo}–{hi} chars",
    "len_warn_title": "Card length looks unusual",
    "len_warn_text": "The card fields are outside the typical range for this deck:\n\n{details}\n\nTypical range was learned from your existing cards. Add anyway?",
    "len_warn_add_anyway": "Add anyway",
    "len_warn_front_short": "Front is too short ({n} chars, expected ≥ {lo}).",
    "len_warn_front_long": "Front is too long ({n} chars, expected ≤ {hi}).",
    "len_warn_back_short": "Back is too short ({n} chars, expected ≥ {lo}).",
    "len_warn_back_long": "Back is too long ({n} chars, expected ≤ {hi}).",
    # Reverso Context reference panel
    "reverso_title": "Translation reference",
    "reverso_loading": "Loading translations…",
    "reverso_error": "Lookup failed: {err}",
    "reverso_empty": "No results for “{word}”.",
    "reverso_translations_header": "Popular translations",
    "reverso_examples_header": "Usage examples",
    "reverso_refresh_tooltip": "Refresh translations and examples",
    "checkbox_reverso_panel": "Show translation reference panel in sidebar",
    "label_reverso_src_lang": "Source language",
    "label_reverso_tgt_lang": "Target language",
    "label_editor_block": "Editor",
    "label_prompt_block": "Refine with AI",
    "label_image_block": "Image",
    "button_regenerate_image": "Regenerate",
    "image_placeholder": "No image",
    "images_idle": "No image yet",
    "images_loading": "Searching images…",
    "images_progress": "Downloading {done}/{total}…",
    "images_empty": "No images found",
    "images_broken": "Image could not be decoded",
    "images_failed": "Image search failed: {err}",
    "images_paste": "Paste",
    "images_paste_tooltip": "Copy any image to your clipboard, then click Paste to use it on this card.",
    "images_none_btn": "No image",
    "images_none_tooltip": "Add the card without any picture (use when nothing fits).",

    # Optional AI helpers in the review editor
    "helper_label": "AI helpers:",
    "helper_assoc": "💡 Association",
    "helper_split": "🧩 Split word",
    "helper_assoc_tip": "Generate a memory association (mnemonic) for this word and append it to the Back.",
    "helper_split_tip": "Split a compound word into parts with translations (useful for German) and append it.",
    "helper_undo": "↩ Undo",
    "helper_undo_tip": "Undo the last association / split added to the Back.",

    # Debug / bug reporting
    "debug_copy_btn": "📋 Copy debug report",
    "debug_copy_tip": "Copy environment info + recent log to the clipboard, to send to the developer.",
    "debug_hint": "If something breaks, click this and paste the report to the developer.",
    "debug_copied": "Debug report copied to clipboard. Paste it to the developer.",

    # Error dialog + codes
    "error_title": "Something went wrong",
    "error_close": "Close",
    "error_send": "Send report to developer",
    "error_sending": "Sending…",
    "error_sent": "Thanks! The report was sent.",
    "error_send_failed": "Could not send: {err}",
    "error_contact_label": "Leave your contact and I'll fix your bug fast (optional):",
    "error_contact_ph": "Telegram @handle or email",
    "err_e000": "Unexpected error. You can send the log to the developer.",
    "err_e001": "Your API key looks invalid or missing. Open Settings → AI provider and paste a fresh free key.",
    "err_e002": "Rate/quota limit reached (free model: ~15/min, 500/day). Wait a bit and try again.",
    "err_e003": "Network problem. Check your internet connection and try again.",
    "err_e004": "Model not available. Try again, or pick another model in API settings.",
    "err_e005": "Access denied (403). Your key may not have permission for this model.",

    # Feedback & support section
    "section_feedback": "Feedback & support",
    "tip_feedback": "Send suggestions, or the full log if you hit a serious bug.",
    "feedback_intro": "Found a bug or have an idea? Tell the developer — replies come faster if you leave a contact.",
    "feedback_contact_html": "Bug or question? Open a <a href=\"https://github.com/ishchenko-dev/freecard-one-tap-ai/issues\">GitHub issue</a>, message <a href=\"https://t.me/Lineyka_x\">@Lineyka_x</a> on Telegram, or email <a href=\"mailto:ishchenko.dev@gmail.com\">ishchenko.dev@gmail.com</a>.",
    "telemetry_label": "Send anonymous usage stats",
    "telemetry_hint": "No personal data or card content — just an anonymous count that helps improve FreeCard.",
    "feedback_contact_label": "Your contact (optional):",
    "feedback_contact_ph": "Telegram @handle or email",
    "feedback_label": "Suggestion / feedback",
    "feedback_ph": "What would you improve? Any wishes?",
    "feedback_send": "Send feedback",
    "feedback_empty": "Write something first.",
    "feedback_sent": "Thanks! Your feedback was sent.",
    "feedback_full_log": "📤 Send full log to developer",
    "feedback_full_log_tip": "Upload the complete ai.log (up to ~512 KB) for a serious bug, so you don't have to find it in the filesystem.",
    "feedback_full_log_confirm": "Send the full log (your version, OS and all recent activity; API keys removed) to the developer?",
    "feedback_full_log_sent": "The full log was sent. Thank you!",
    "images_clipboard_empty": "Clipboard has no image",
    "images_refine_placeholder": "Refine search (e.g. \"more abstract\")",
    "images_refine_tooltip": "Describe what you want to see. Replaces auto-hint from the Back field.",
    # Audio / TTS
    "audio_idle": "No audio yet",
    "audio_loading": "Generating audio…",
    "audio_ready": "Audio ready ({lang})",
    "audio_empty": "Audio is empty",
    "audio_failed": "Audio failed: {err}",
    "audio_play_tooltip": "Play pronunciation",
    "audio_regen_tooltip": "Regenerate audio",
    "checkbox_audio_panel": "Auto-generate TTS audio for new cards",
    "error_refine_empty": "Enter the refine prompt text.",
    "tooltip_confirm_shortcut": "Shortcut: Ctrl+Enter or Cmd+Enter",
    "button_confirm_settings": "Confirm Settings",
    "button_exit": "Exit",
    "button_minimize": "Minimize",
    "button_generate_ai": "Generate via AI",
    "button_generate_gpt": "Generate via GPT",
    "button_api_settings": "API Settings",
    "button_prev": "←",
    "button_next": "→",
    
    # Tabs
    "tab_manual": "Manual",
    "tab_ai": "AI",
    "tab_settings": "Settings",
    
    # Form labels
    "label_deck": "Deck",
    "label_note_type": "Note Type",
    "label_front": "Front",
    "label_back": "Back",
    "label_gemini_api_key": "Gemini API Key",
    "label_platform": "Platform",
    "label_api_key": "API Key",
    "label_api_settings": "API Settings",
    "label_custom_prompt": "Custom Prompt",
    "label_prompt_name": "Prompt Name",
    "label_model": "Model",
    "label_deck_ai": "Deck (AI)",
    "label_deck_default": "Deck (Default)",
    "label_words": "Words (EN)",
    "label_global_hotkey": "Global Hotkey",
    "label_hotkey_preset": "Combination (Preset)",
    
    # Placeholders
    "placeholder_api_key": "AIza... (Gemini API Key)",
    "placeholder_custom_prompt": "Custom prompt (optional). If empty, only input words are sent.",
    "placeholder_words": "Words in English (comma-separated)",
    
    # Checkboxes
    "checkbox_auto_add": "Automatically add to deck",
    "checkbox_ai_notify": "Show notification on AI addition",
    "checkbox_manual_notify": "Show notification on manual addition",
    
    # Hotkey modes
    "hotkey_disabled": "Disabled",
    "hotkey_external": "External Listener (HTTP)",
    "hotkey_mac": "Built-in macOS",
    "hotkey_win": "Built-in Windows",
    
    # Hotkey presets
    "hotkey_preset_cmd_opt_t": "Cmd+Option+T (macOS)",
    "hotkey_preset_cmd_shift_y": "Cmd+Shift+Y (macOS)",
    "hotkey_preset_ctrl_shift_x": "Ctrl+Shift+X (Windows)",
    "hotkey_preset_ctrl_alt_x": "Ctrl+Alt+X (Windows)",
    "hotkey_preset_f8": "F8 (all platforms)",
    "hotkey_preset_f7": "F7 (all platforms)",

    # Hotkey UI (redesigned)
    "label_hotkey_section": "Card-creation hotkey",
    "checkbox_double_ctrl_c": "Double-copy creates a card (recommended)",
    "hotkey_double_ctrl_c_hint": "Select text anywhere, press the copy shortcut twice quickly (⌘C on macOS, Ctrl+C on Windows). macOS needs Accessibility permission for Anki.",
    "label_custom_hotkey": "Custom hotkey",
    "hotkey_record_btn": "Click and press keys…",
    "hotkey_press_keys": "Press keys now…",
    "hotkey_not_set": "Not set",
    "hotkey_clear_btn": "Clear",
    "label_hotkey_activation": "Activation",

    # Card-content constructor
    "prompt_advanced": "Advanced: edit raw prompt",
    "prompt_preview_name": "Constructor (current settings, read-only)",
    "card_translations": "Translations",
    "card_examples": "Examples",
    "card_definition": "Include a definition (source language + translation)",
    "card_pos": "Include part of speech",
    "card_extra": "Extra instructions",
    "card_extra_placeholder": "Optional, e.g. \"add synonyms\", \"formal tone\"",
    "card_lang_hint": "Languages are taken from the Settings tab (source / target language).",

    # Redesigned sidebar sections
    "section_create": "Create card",
    "section_ai": "AI provider",
    "section_languages": "Languages",
    "section_content": "Card content",
    "section_hotkey": "Hotkey",
    "section_deck": "Deck & notes",
    "section_advanced": "Advanced",
    "button_add_card": "Add to deck",
    "label_note_type_short": "Note type",
    "label_deck_target": "Deck",
    "new_deck_btn": "+ New deck",
    "new_deck_title": "New deck",
    "new_deck_label": "Deck name:",
    "wizard_deck_title": "Choose a deck",
    "wizard_deck_text": "Pick the deck new cards are added to, or create a new one.",
    "card_lang_hint2": "Pick the language you're learning and the language to translate into.",
    "section_create_hint": "Type a word, generate a card, edit if needed, then add it.",
    "button_open_advanced": "Open advanced settings…",

    # First-run setup wizard
    "button_setup_guide": "🚀  Setup guide (start here)",
    "wizard_title": "FreeCard — Quick Setup",
    "wizard_back": "← Back",
    "wizard_next": "Next →",
    "wizard_finish": "Finish",
    "wizard_step": "Step {n} of {total}",
    "wizard_welcome_title": "👋 Welcome to FreeCard",
    "wizard_welcome_text": "This short guide sets up AI card creation in 3 steps:\n\n  1.  Connect an AI provider (free API key)\n  2.  Choose your languages\n  3.  Done — make your first card!\n\nClick Next to begin.",
    "wizard_ai_title": "Step 1 — Connect an AI provider",
    "wizard_ai_text": "Pick a provider and paste a FREE API key below.",
    "wizard_provider": "Provider",
    "wizard_api_key": "API key",
    "wizard_ai_guide_title": "How to get a free key — step by step:",
    "wizard_ai_step1_html": (
        "<b>1.</b> Log in to Google here: "
        "<a href=\"https://aistudio.google.com/apikey\">aistudio.google.com/apikey</a>"
    ),
    "wizard_ai_step2_html": "<b>2.</b> Google automatically creates an API key for you.",
    "wizard_ai_step3_html": "<b>3.</b> Copy the API key and paste it into the field above.",
    "wizard_rate_note": (
        "Note: the free model allows about 15 requests per minute and 500 per day. "
        "Keep this in mind when generating many cards."
    ),
    "wizard_lang_title": "Step 2 — Choose your languages",
    "wizard_lang_text": "Pick the language you are LEARNING (source) and the language to TRANSLATE INTO (target). You can type to search.",
    "wizard_done_title": "🎉 Congratulations!",
    "wizard_done_text": "All set! Now try your first card:\n\n  1.  Select a word in ANY app (browser, PDF, anywhere).\n  2.  Press the copy shortcut twice quickly (⌘C ⌘C on macOS, Ctrl+C Ctrl+C on Windows).\n  3.  A review window opens with the AI-made card — click Add.\n\nThat's it. Try generating your first card now!",

    # Sidebar tooltips
    "tip_create": "Type a word or phrase, let the AI build a card, review it, and add it to your deck.",
    "tip_ai": "Choose your AI provider (Gemini or Groq), paste your free API key, and pick a model.",
    "tip_languages": "Set the language you're learning and the language to translate into.",
    "tip_content": "Control what goes on the card: translations, definition, examples, part of speech. Advanced users can edit the raw prompt.",
    "tip_hotkey": "Create a card from any app: double-press copy, or set your own global shortcut.",
    "tip_deck": "Pick the target deck and note type, and toggle notifications and extras.",
}


def _t(key: str, **kwargs) -> str:
    """
    Simple translation function.
    Returns English string from _UI_STRINGS dict.
    Format kwargs are supported for error messages.
    
    Future: Can be extended to support multiple languages by checking user config.
    """
    s = _UI_STRINGS.get(key, key)
    if kwargs:
        try:
            return s.format(**kwargs)
        except Exception:
            return s
    return s


def _get_config() -> dict:
    try:
        cfg = mw.addonManager.getConfig(__name__) or {}
        if not isinstance(cfg, dict):
            return {}
        return cfg
    except Exception:
        return {}


def _set_config(cfg: dict) -> None:
    try:
        mw.addonManager.writeConfig(__name__, cfg)
    except Exception:
        pass


# Default prompt was removed per user request.
# Now only custom prompt is used if provided; otherwise only the input words are sent.
# This gives users full control over AI behavior without default instructions interfering.

# Default model: Gemini 2.5 Flash-Lite has higher rate limits (100 requests/day free tier)
# vs gemini-1.5-flash (20 requests/day)
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"
DEFAULT_PLATFORM = "google"

# No embedded API keys in the public build — each user provides their own free key
# (via the setup wizard / API settings). Kept as empty fallbacks.
DEFAULT_GEMINI_API_KEY = ""
DEFAULT_GROQ_API_KEY = ""

PLATFORM_MODELS = {
    # Only Gemini 3.1 Flash Lite is offered in the UI for now (2.5 hidden).
    "google": ["gemini-3.1-flash-lite"],
    "groq": [
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "mixtral-8x7b-32768",
    ],
}

# Recommended field length ranges, derived from the user's "Netherland (theorie)" deck
# (5%/95% winsorized). Used for soft validation and AI prompt hints; NOT enforced.
RECOMMENDED_FRONT_MIN = 5
RECOMMENDED_FRONT_MAX = 20
RECOMMENDED_BACK_MIN = 360
RECOMMENDED_BACK_MAX = 835

# Hard "absurd" thresholds for the soft warning dialog before adding a card.
# Anything inside [min..max] is silently accepted; anything outside triggers a confirm.
SOFT_FRONT_MIN = 2
SOFT_FRONT_MAX = 60
SOFT_BACK_MIN = 120
SOFT_BACK_MAX = 1400

# Neutral English fallback prompt. Used only if the constructor produces nothing
# and no custom prompt is set. The hidden format contract (Front / --- / Back) is
# added separately by the generation functions.
DEFAULT_CUSTOM_PROMPT = """Create a vocabulary flashcard for the given word or phrase.
Front: the original word or phrase, exactly as given.
Back: a few common translations separated by '; '; then a short, simple definition;
then one or two short example sentences. Separate sections with <br/><br/>.
Keep everything short, simple and natural."""


# Hidden output contract the addon ALWAYS prepends, in both constructor and
# advanced (raw) mode. It owns the technical shape of the response so the parser
# (_parse_model_text_output) can reliably split Front from Back. The user never
# has to write this. Front/Back are separated by a line containing only "---".
# Inside Back, visual line breaks must be <br/> (Anki renders fields as HTML, so
# real newlines would NOT show as breaks — only <br/> does).
_FORMAT_CONTRACT = (
    "Output format (STRICT): first the Front content on its own, then a line "
    "containing only three dashes (---), then the Back content. Do NOT add any "
    "'Front:'/'Back:' labels, Markdown, code fences, or JSON. The Back is a single "
    "block: use the HTML tag <br/> for every line break (never real newlines). "
    "Use <br/><br/> to separate logical sections. Do not output any other HTML tags."
)


def _build_content_prompt(cfg: dict) -> str:
    """
    Build the user-facing "content" layer of the prompt.

    - Advanced mode: return the user's raw custom prompt verbatim (placeholders
      like {source_lang}/{target_lang} are substituted by the caller path).
    - Constructor mode: assemble the instruction from simple settings (languages,
      number of translations/examples, definition, part of speech, extra notes).
    The hidden _FORMAT_CONTRACT is added separately by the generation functions.
    """
    mode = str(cfg.get("prompt_mode", "constructor")).strip().lower()
    if mode == "advanced":
        return str(cfg.get("custom_prompt", "") or "").strip()

    src = _lang_display_name(str(cfg.get("reverso_source_lang", REVERSO_DEFAULT_SRC) or REVERSO_DEFAULT_SRC).strip())
    tgt = _lang_display_name(str(cfg.get("reverso_target_lang", REVERSO_DEFAULT_TGT) or REVERSO_DEFAULT_TGT).strip())

    # None-safe reads: a null in config means "use the default", not "off"/"0".
    def _int_or(key, default):
        v = cfg.get(key, default)
        try:
            return int(v)
        except Exception:
            return default

    def _bool_or(key, default):
        v = cfg.get(key, default)
        return default if v is None else bool(v)

    n_tr = max(1, min(6, _int_or("card_translations", 4)))
    n_ex = max(0, min(4, _int_or("card_examples", 2)))
    inc_def = _bool_or("card_definition", True)
    inc_pos = _bool_or("card_pos", False)
    extra = str(cfg.get("card_extra", "") or "").strip()

    lines = []
    lines.append(
        f"You create a {tgt} vocabulary flashcard for a word or phrase written in {src}. "
        f"All translations and explanations on the Back MUST be written in {tgt} "
        f"(the target language), NOT in {src}."
    )
    lines.append("Front: the original word/phrase exactly as given, nothing else.")
    back_bits = [
        f"{n_tr} common translations of the word INTO {tgt} (written in {tgt}, not {src}), "
        "separated by '; ' (no trailing period)"
    ]
    if inc_def:
        back_bits.append(
            f"a short, simple definition written in {src}, followed by its {tgt} "
            "translation in parentheses"
        )
    if n_ex > 0:
        ex_line = (
            f"{n_ex} short natural example sentence(s); format each as: "
            f"<{src} sentence> — <{tgt} translation>"
        )
        if extra:
            ex_line += f". EVERY example sentence MUST fit this context/topic: {extra}"
        back_bits.append(ex_line)
    if inc_pos:
        back_bits.append(f"the part of speech (in {tgt})")
    lines.append(
        "Back must contain, in this order separated by <br/><br/>: " + "; ".join(back_bits) + "."
    )
    lines.append("Keep everything short, simple and natural.")
    if extra:
        # The user's extra instructions are a HARD requirement — give them real
        # weight (a weak model otherwise treats them as a throwaway hint). Tie them
        # to the definition and examples so the WHOLE card follows the context.
        lines.append(
            "IMPORTANT — this is a strict requirement that applies to the WHOLE card "
            "(the definition and EVERY example sentence, not just one of them): "
            f"{extra}. Make the definition and all examples clearly relate to this; "
            "do NOT produce generic or unrelated examples."
        )
    return "\n".join(lines)


def _parse_gemini_error(http_error) -> str:
    """
    Parse Gemini API error response and return user-friendly message.
    
    Gemini API returns errors in format:
    {
        "error": {
            "code": 429,
            "message": "Resource has been exhausted (e.g. check quota).",
            "status": "RESOURCE_EXHAUSTED",
            "details": [...]
        }
    }
    
    Common error codes:
    - 503: Service unavailable (model overloaded or temporarily down)
    - 429: Rate limit exceeded (per minute/hour)
    - 403: Quota exceeded (daily limit)
    - 400: Invalid request
    - 401: Invalid API key
    """
    import json
    try:
        error_body = http_error.read().decode("utf-8")
        error_data = json.loads(error_body)
        error_obj = error_data.get("error", {})
        
        status_code = http_error.code
        error_code = error_obj.get("code", status_code)
        error_status = error_obj.get("status", "")
        error_message = error_obj.get("message", str(http_error))
        
        # Map common error statuses to user-friendly messages
        if status_code == 503 or error_status == "UNAVAILABLE":
            # Service unavailable - model may be overloaded or temporarily down
            return _t("error_api_service_unavailable")
        
        elif status_code == 429 or error_status == "RESOURCE_EXHAUSTED":
            # Check if it's rate limit (per minute) or quota (daily)
            if "quota" in error_message.lower() or "daily" in error_message.lower():
                model_info = ""
                try:
                    details = error_obj.get("details", [])
                    if details and isinstance(details, list) and len(details) > 0:
                        model_info = f" (Model: {details[0].get('@type', 'unknown')})"
                except Exception:
                    pass
                return _t("error_api_daily_quota") + model_info
            else:
                return _t("error_api_rate_limit")
        
        elif status_code == 403:
            if "quota" in error_message.lower() or "billing" in error_message.lower():
                return _t("error_api_quota_billing")
            else:
                return _t("error_api_forbidden", message=error_message)
        
        elif status_code == 401:
            return _t("error_api_invalid_key")
        
        elif status_code == 400:
            return _t("error_api_invalid_request", message=error_message)
        
        else:
            # Return formatted error with status code
            return _t("error_api_generic", code=status_code, message=error_message)
    
    except Exception as parse_error:
        # Fallback if error parsing fails
        try:
            error_body = http_error.read().decode("utf-8")
            _log(f"Error parsing failed, raw error: {error_body[:200]}")
        except Exception:
            pass
        return f"Gemini API error ({http_error.code}): {str(http_error)}"


def _parse_model_text_output(text: str, words: str) -> Tuple[str, str]:
    """
    Normalize model output into (front, back).
    Accepts:
    - two-line plaintext (front on first line, back on second+),
    - JSON with front/back,
    - nested JSON in back field,
    - dictionary-like payload used by custom prompts.
    """
    import json
    import re

    if not text:
        raise RuntimeError("Empty response from model.")

    cleaned = text.strip()
    # Strip a wrapping ```...``` code fence if the model added one.
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    # PRIMARY path: Front / "---" line / Back contract. Split on the first line
    # that is only dashes. Because Back uses <br/> (not real newlines), this
    # delimiter is unambiguous.
    delim_parts = re.split(r"(?m)^[ \t]*-{3,}[ \t]*$", cleaned, maxsplit=1)
    if len(delim_parts) == 2:
        front = delim_parts[0].strip()
        back = delim_parts[1].strip()
        if front and back and not front.startswith("{"):
            return front, back

    lines = cleaned.split("\n")
    if len(lines) >= 2:
        front_candidate = lines[0].strip()
        back_candidate = "\n".join(lines[1:]).strip()
        if front_candidate and back_candidate and not front_candidate.startswith("{"):
            return front_candidate, back_candidate

    try:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object found")
        block = text[start : end + 1]
        try:
            obj = json.loads(block)
        except Exception:
            obj = json.loads(block.replace('\\"', '"'))

        if isinstance(obj, dict) and "front" in obj and "back" in obj:
            front = str(obj.get("front", "")).strip()
            back = str(obj.get("back", "")).strip()
            if front and back:
                return front, back

        if isinstance(obj, dict) and "back" in obj and isinstance(obj["back"], str):
            try:
                obj = json.loads(obj["back"])
            except Exception:
                pass

        if isinstance(obj, dict):
            expected = [
                "Word", "Transcription UK", "Audio UK", "Transcription US", "Audio US",
                "Source", "Part of speech", "Level", "Translation", "Explanation",
            ]
            if any(k in obj for k in expected):
                fixed = {k: (obj.get(k, "") if isinstance(obj.get(k, ""), str) else str(obj.get(k, ""))) for k in expected}
                for k in ("Transcription UK", "Transcription US"):
                    v = fixed.get(k, "").strip()
                    if v and not (v.startswith("/") and v.endswith("/")):
                        fixed[k] = ""
                word = (fixed.get("Word") or words).strip() or words
                uk = fixed.get("Transcription UK", "").strip()
                us = fixed.get("Transcription US", "").strip()
                translation = fixed.get("Translation", "").strip()
                explanation_html = fixed.get("Explanation", "").strip()
                parts = []
                if uk or us:
                    ipa = []
                    if uk:
                        ipa.append(f"UK: {uk}")
                    if us:
                        ipa.append(f"US: {us}")
                    parts.append(f"<div class=\"ipa\">{' | '.join(ipa)}</div>")
                if translation:
                    parts.append(f"<div class=\"tr\">{translation}</div>")
                if explanation_html:
                    parts.append(explanation_html)
                return word, ("\n".join(parts) if parts else translation or explanation_html or "")
    except Exception as parse_exc:
        _log(f"Parse fallback: {parse_exc}")

    return words.strip(), text


def _gemini_generate_card(
    api_key: str,
    words: str,
    custom_prompt: Optional[str],
    model_id: Optional[str] = None,
) -> Tuple[str, str]:
    import json
    import time
    import urllib.parse
    import urllib.request
    import urllib.error

    if not api_key:
        raise RuntimeError("Gemini API Key not specified.")

    # Build prompt: use custom if provided, otherwise send only input words
    # Why minimal prompt when no custom: User requested removal of default instructions
    # to avoid interference with custom prompts. We only enforce JSON format requirement.
    instructions = (custom_prompt or "").strip()
    if instructions:
        prompt_text = (
            f"{_FORMAT_CONTRACT}\n\n"
            f"{instructions}\n\nInput word/phrase: {words}"
        )
    else:
        prompt_text = (
            f"{_FORMAT_CONTRACT}\n\n"
            f"Input word/phrase: {words}"
        )

    body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt_text}],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
        },
    }

    endpoint_model = (model_id or DEFAULT_GEMINI_MODEL)
    base_url = f"https://generativelanguage.googleapis.com/v1beta/models/{endpoint_model}:generateContent"
    url = base_url + "?" + urllib.parse.urlencode({"key": api_key})

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    # Gemini's free tier occasionally returns transient errors (503 overloaded,
    # 429 rate, 5xx). Retry a few times with a short backoff before giving up, so
    # the first request after a cold start doesn't fail outright. This runs in the
    # background generation worker, so the sleep does not block the UI.
    _transient_codes = {429, 500, 502, 503, 504}
    _max_attempts = 3
    data = None
    for _attempt in range(1, _max_attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read().decode("utf-8")
                _log(f"Gemini raw: {raw[:400]}")
                data = json.loads(raw)
            break
        except urllib.error.HTTPError as e:
            # Parse error response from Gemini API
            error_message = _parse_gemini_error(e)
            _log(f"Gemini HTTPError {e.code} (attempt {_attempt}/{_max_attempts}): {error_message}")
            if e.code in _transient_codes and _attempt < _max_attempts:
                time.sleep(min(2 ** _attempt, 6))
                continue
            raise RuntimeError(error_message)
        except Exception as e:
            _log(f"Gemini net error (attempt {_attempt}/{_max_attempts}): {e}")
            if _attempt < _max_attempts:
                time.sleep(2)
                continue
            raise RuntimeError(f"Network error: {e}")

    # Extract text from candidates
    def _extract_text(obj: dict) -> str:
        try:
            candidates = obj.get("candidates", [])
            if not candidates:
                return ""
            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            texts = []
            for p in parts:
                t = p.get("text")
                if isinstance(t, str):
                    texts.append(t)
            return "\n".join(texts).strip()
        except Exception:
            return ""

    text = _extract_text(data)
    _log(f"Extracted text length: {len(text)}, preview: {text[:200]}")
    return _parse_model_text_output(text, words)


def _groq_generate_card(
    api_key: str,
    words: str,
    custom_prompt: Optional[str],
    model_id: Optional[str] = None,
) -> Tuple[str, str]:
    import json
    import urllib.request
    import urllib.error

    if not api_key:
        raise RuntimeError("Groq API Key not specified.")

    instructions = (custom_prompt or "").strip()
    if instructions:
        prompt_text = (
            f"{_FORMAT_CONTRACT}\n\n"
            f"{instructions}\n\nInput word/phrase: {words}"
        )
    else:
        prompt_text = (
            f"{_FORMAT_CONTRACT}\n\nInput word/phrase: {words}"
        )

    model = (model_id or DEFAULT_GROQ_MODEL)
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt_text}],
        "temperature": 0.2,
        "max_tokens": 1200,
    }
    responses_body = {
        "model": model,
        "input": prompt_text,
        "temperature": 0.2,
        "max_output_tokens": 1200,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        # Groq/Cloudflare may block some default urllib clients (403/1010)
        # unless an explicit user-agent is provided.
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    data = {}

    # Try the newer Responses API first, then fallback to chat/completions.
    try:
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/responses",
            data=json.dumps(responses_body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8")
            _log(f"Groq responses raw: {raw[:400]}")
            data = json.loads(raw)
        text = str(data.get("output_text", "")).strip()
        if not text:
            output = data.get("output")
            if isinstance(output, list):
                chunks = []
                for item in output:
                    if not isinstance(item, dict):
                        continue
                    content = item.get("content")
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict):
                                t = c.get("text") or c.get("output_text")
                                if isinstance(t, str):
                                    chunks.append(t)
                text = "\n".join(chunks).strip()
        if text:
            return _parse_model_text_output(text, words)
    except urllib.error.HTTPError as e:
        try:
            err = e.read().decode("utf-8")
        except Exception:
            err = str(e)
        _log(f"Groq responses HTTPError {e.code}: {err[:400]}")
    except Exception as e:
        _log(f"Groq responses network error: {e}")

    try:
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8")
            _log(f"Groq chat raw: {raw[:400]}")
            data = json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            err = e.read().decode("utf-8")
        except Exception:
            err = str(e)
        _log(f"Groq chat HTTPError {e.code}: {err[:400]}")
        raise RuntimeError(f"Groq API error ({e.code}): {err}")
    except Exception as e:
        _log(f"Groq chat network error: {e}")
        raise RuntimeError(f"Network error: {e}")

    try:
        message = data["choices"][0]["message"]
        content = message.get("content", "")
        if isinstance(content, list):
            chunks = []
            for item in content:
                if isinstance(item, dict):
                    txt = item.get("text")
                    if isinstance(txt, str):
                        chunks.append(txt)
            text = "\n".join(chunks).strip()
        else:
            text = str(content).strip()
    except Exception as e:
        _log(f"Groq parse error: {e}; payload={str(data)[:400]}")
        raise RuntimeError("Empty response from model.")
    return _parse_model_text_output(text, words)


def _generate_card_by_platform(
    platform: str,
    api_key: str,
    words: str,
    custom_prompt: Optional[str],
    model_id: Optional[str],
) -> Tuple[str, str]:
    if platform == "groq":
        return _groq_generate_card(api_key, words, custom_prompt, model_id)
    return _gemini_generate_card(api_key, words, custom_prompt, model_id)


def _ai_complete(platform: str, api_key: str, prompt: str, model_id: Optional[str]) -> str:
    """Single-shot raw-text completion (no Front/Back contract). Used by the optional
    Association / Split helpers. Returns the model's plain text."""
    import json
    import urllib.parse
    import urllib.request
    if not api_key:
        raise RuntimeError("API key not specified.")
    platform = (platform or DEFAULT_PLATFORM).strip().lower()
    if platform == "groq":
        model = model_id or DEFAULT_GROQ_MODEL
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.5,
            "max_tokens": 400,
        }
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
    # Gemini
    model = model_id or DEFAULT_GEMINI_MODEL
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?"
        + urllib.parse.urlencode({"key": api_key})
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.5},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    cands = data.get("candidates", [])
    if not cands:
        return ""
    parts = cands[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts if isinstance(p.get("text"), str)).strip()


def _gemini_refine_card(
    api_key: str,
    words: str,
    base_custom: str,
    front: str,
    back: str,
    refine: str,
    model_id: Optional[str] = None,
) -> Tuple[str, str]:
    import json
    import urllib.parse
    import urllib.request
    import urllib.error

    if not api_key:
        raise RuntimeError("Gemini API Key not specified.")
    base_ctx = (base_custom or "").strip() or "(none)"
    prompt_text = (
        "You are a helper that always responds with valid JSON with keys 'front' and 'back'. "
        "Do not use triple/back quotes.\n\n"
        f"Original generation rules (context):\n{base_ctx}\n\n"
        f"Original input words:\n{words}\n\n"
        "Current card draft:\n"
        f"front: {front}\n"
        f"back: {back}\n\n"
        "User instruction to revise the card:\n"
        f"{refine}\n\n"
        "Return JSON with keys 'front' and 'back' with the complete revised card content."
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
        "generationConfig": {"temperature": 0.2},
    }
    endpoint_model = model_id or DEFAULT_GEMINI_MODEL
    base_url = f"https://generativelanguage.googleapis.com/v1beta/models/{endpoint_model}:generateContent"
    url = base_url + "?" + urllib.parse.urlencode({"key": api_key})
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            _log(f"Gemini refine raw: {raw[:400]}")
            data = json.loads(raw)
    except urllib.error.HTTPError as e:
        raise RuntimeError(_parse_gemini_error(e))
    except Exception as e:
        _log(f"Gemini refine net error: {e}")
        raise RuntimeError(f"Network error: {e}")

    def _extract_text(obj: dict) -> str:
        try:
            candidates = obj.get("candidates", [])
            if not candidates:
                return ""
            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            texts = []
            for p in parts:
                t = p.get("text")
                if isinstance(t, str):
                    texts.append(t)
            return "\n".join(texts).strip()
        except Exception:
            return ""

    text = _extract_text(data)
    return _parse_model_text_output(text, words)


def _groq_refine_card(
    api_key: str,
    words: str,
    base_custom: str,
    front: str,
    back: str,
    refine: str,
    model_id: Optional[str] = None,
) -> Tuple[str, str]:
    import json
    import urllib.request
    import urllib.error

    if not api_key:
        raise RuntimeError("Groq API Key not specified.")
    base_ctx = (base_custom or "").strip() or "(none)"
    prompt_text = (
        f"Original generation rules (context):\n{base_ctx}\n\n"
        f"Original input words:\n{words}\n\n"
        "Current card draft:\n"
        f"front: {front}\n"
        f"back: {back}\n\n"
        "User instruction to revise the card:\n"
        f"{refine}\n\n"
        "Return either valid JSON with keys 'front' and 'back' or two plain lines (front then back)."
    )
    model = model_id or DEFAULT_GROQ_MODEL
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt_text}],
        "temperature": 0.2,
        "max_tokens": 1600,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            _log(f"Groq refine raw: {raw[:400]}")
            data = json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            err = e.read().decode("utf-8")
        except Exception:
            err = str(e)
        raise RuntimeError(f"Groq API error ({e.code}): {err}")
    except Exception as e:
        raise RuntimeError(f"Network error: {e}")
    try:
        message = data["choices"][0]["message"]
        content = message.get("content", "")
        if isinstance(content, list):
            chunks = []
            for item in content:
                if isinstance(item, dict):
                    txt = item.get("text")
                    if isinstance(txt, str):
                        chunks.append(txt)
            text = "\n".join(chunks).strip()
        else:
            text = str(content).strip()
    except Exception as e:
        _log(f"Groq refine parse error: {e}")
        raise RuntimeError("Empty response from model.")
    return _parse_model_text_output(text, words)


def _refine_card_by_platform(
    platform: str,
    api_key: str,
    words: str,
    base_custom: str,
    front: str,
    back: str,
    refine: str,
    model_id: Optional[str],
) -> Tuple[str, str]:
    if platform == "groq":
        return _groq_refine_card(api_key, words, base_custom, front, back, refine, model_id)
    return _gemini_refine_card(api_key, words, base_custom, front, back, refine, model_id)


def _try_register_recent_front_for_ai(front: str) -> bool:
    """Anti-burst duplicate by normalized front; returns False if add should be skipped."""
    if not hasattr(_enqueue_generation_and_add, "_lock"):
        _enqueue_generation_and_add._lock = threading.Lock()  # type: ignore[attr-defined]
        _enqueue_generation_and_add._recent_fronts = {}  # type: ignore[attr-defined]
    with _enqueue_generation_and_add._lock:  # type: ignore[attr-defined]
        rf = getattr(_enqueue_generation_and_add, "_recent_fronts")  # type: ignore[attr-defined]
        now_ms = int(time.time() * 1000)
        for k in list(rf.keys()):
            if now_ms - rf[k] > 5000:
                del rf[k]
        fkey = " ".join(str(front).strip().lower().split())
        if fkey and fkey in rf and (now_ms - rf[fkey]) <= 5000:
            return False
        if fkey:
            rf[fkey] = now_ms
    return True


def _assign_deck_to_note(note, deck_id: int) -> None:
    """
    Assign a deck to a note using version-agnostic API.
    
    Why multiple fallbacks: Anki API changed between versions.
    - Newer versions: set_deck_for_new_note() method
    - Older versions: Set did field directly on note type
    - Silent failure: Better to skip than crash if assignment fails
    """
    col = mw.col
    if col is None:
        return
    decks = col.decks
    # Newer Anki versions: specialized method
    set_for_new = getattr(decks, "set_deck_for_new_note", None) or getattr(decks, "setDeckForNewNote", None)
    if callable(set_for_new):
        try:
            set_for_new(note, int(deck_id))
            return
        except Exception:
            pass
    # Fallback: set did field directly on note type
    try:
        note_type = note.note_type() if hasattr(note, "note_type") else note.model()
        note_type["did"] = int(deck_id)
    except Exception:
        # Last resort: do nothing (silent failure to avoid breaking addon)
        return


def _normalize_front_for_duplicate(front: str) -> str:
    import re

    txt = str(front or "").strip().lower()
    # Remove simple HTML markup that may appear in Front.
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = txt.replace("&nbsp;", " ")
    txt = re.sub(r"\s+", " ", txt).strip()
    tokens = txt.split(" ")
    leading_articles = {
        "a", "an", "the",          # English
        "de", "het", "een",        # Dutch
        "der", "die", "das",       # German
        "le", "la", "les", "un", "une",  # French
    }
    if tokens and tokens[0] in leading_articles:
        tokens = tokens[1:]
    return " ".join(tokens).strip()


def _resolve_effective_deck_id(col, deck_id: Optional[int]) -> Optional[int]:
    decks = col.decks
    effective_deck_id: Optional[int] = int(deck_id) if deck_id is not None else None
    if effective_deck_id is None:
        get_current_id = getattr(decks, "get_current_id", None) or getattr(decks, "currentId", None)
        try:
            if callable(get_current_id):
                effective_deck_id = int(get_current_id())
        except Exception:
            effective_deck_id = None
    if effective_deck_id is None:
        get_deck_id = getattr(decks, "id", None) or getattr(decks, "idForName", None)
        try:
            if callable(get_deck_id):
                effective_deck_id = int(get_deck_id("Default"))
        except Exception:
            effective_deck_id = None
    return effective_deck_id


def _card_exists_in_deck(front: str, deck_id: Optional[int], model) -> bool:
    return _find_similar_card_in_deck(front, deck_id, model) is not None


def _find_similar_card_in_deck(front: str, deck_id: Optional[int], model) -> Optional[str]:
    """Return the original first_field of an existing card with the same normalized
    front, or None. Same logic as _card_exists_in_deck — exact match of normalized strings."""
    col = mw.col
    if col is None:
        return None
    effective_deck_id = _resolve_effective_deck_id(col, deck_id)
    if effective_deck_id is None:
        return None
    model_id = int(model.get("id", 0)) if isinstance(model, dict) else 0
    if model_id <= 0:
        return None
    target = _normalize_front_for_duplicate(front)
    if not target:
        return None
    try:
        rows = col.db.all(
            "select distinct n.flds from notes n join cards c on c.nid = n.id where c.did = ? and n.mid = ?",
            int(effective_deck_id),
            int(model_id),
        )
        for row in rows:
            flds = str(row[0]) if row and row[0] is not None else ""
            first_field = flds.split("\x1f", 1)[0] if flds else ""
            if _normalize_front_for_duplicate(first_field) == target:
                return first_field
    except Exception as e:
        _log(f"dup-check error: {e}")
    return None


def _save_image_to_media(data: bytes, hint: str = "") -> "Optional[str]":
    """Save image bytes into Anki media. Returns the FINAL file name (Anki may
    change it for uniqueness) or None.

    The format is detected from magic bytes (PNG/JPEG/GIF/WEBP); anything else is
    written as .png. The name is generated from a "hint" (usually the front word),
    cleaned of unsafe characters."""
    col = mw.col if mw is not None else None
    if col is None or not data:
        return None
    # Detect the extension from the signature. Unknown formats are written as png —
    # in 99% of cases QLabel.loadFromData recognizes that, and Anki shows it as-is.
    head = data[:12]
    ext = "png"
    if head.startswith(b"\xff\xd8\xff"):
        ext = "jpg"
    elif head.startswith(b"GIF8"):
        ext = "gif"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        ext = "webp"
    elif head.startswith(b"\x89PNG"):
        ext = "png"
    # Clean the hint down to a safe file-name slug.
    slug = re.sub(r"[^\w\- ]+", "", (hint or "image")).strip().replace(" ", "_")
    slug = slug[:40] or "image"
    import hashlib
    digest = hashlib.sha1(data).hexdigest()[:8]
    fname = f"ai_{slug}_{digest}.{ext}"
    try:
        write = getattr(col.media, "write_data", None) or getattr(col.media, "writeData", None)
        if callable(write):
            real = write(fname, data)  # Anki returns the final name
            if isinstance(real, str) and real:
                return real
        # Fallback: write directly into collection.media/
        import os
        media_dir = getattr(col.media, "dir", None)
        media_dir = media_dir() if callable(media_dir) else media_dir
        if media_dir:
            path = os.path.join(media_dir, fname)
            with open(path, "wb") as f:
                f.write(data)
            return fname
    except Exception as e:
        _log(f"write_media failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Audio / TTS
# ---------------------------------------------------------------------------
#
# What was chosen and why:
# - We use the public Google Translate TTS endpoint
#   (`translate.google.com/translate_tts`). It returns MP3, requires no
#   API key, and supports 60+ languages (including Dutch). This is the same
#   endpoint that the `gTTS` library uses. We don't need that library —
#   a single urllib request is enough.
# - Alternatives (eSpeak / macOS `say` / Edge-TTS) are kept as a fallback
#   for when the network is unavailable. System TTS quality is noticeably
#   lower, but at least the card isn't left without audio.
# - Google's request limit: ~200 characters per chunk. We split by words.

TTS_TIMEOUT = 8

# Candidate field names for placing audio (case-insensitive).
_AUDIO_FIELD_CANDIDATES: "tuple[str, ...]" = (
    "Audio", "Sound", "Pronunciation", "Произношение", "Аудио", "Звук",
    "TTS", "Speech",
)


def _find_audio_field(field_names: "list[str]") -> "Optional[str]":
    """Find a suitable field for audio in the note model. The logic is the
    same as in `_find_image_field`: we look for an exact (case-insensitive)
    match against the whitelist. If nothing is found — None, and the audio
    simply won't be attached (which is better than junk in Back)."""
    if not field_names:
        return None
    norm = {fn.strip().lower(): fn for fn in field_names if isinstance(fn, str)}
    for cand in _AUDIO_FIELD_CANDIDATES:
        hit = norm.get(cand.lower())
        if hit:
            return hit
    return None


def _gtts_chunks(text: str, max_len: int = 180) -> "list[str]":
    """Break the text into chunks of `max_len` characters on word boundaries.
    Google TTS may silently truncate long requests — better to give it a
    series of short ones. For a single word this is a complete no-op."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    out: list[str] = []
    cur = ""
    for tok in text.split():
        if not cur:
            cur = tok
            continue
        if len(cur) + 1 + len(tok) <= max_len:
            cur += " " + tok
        else:
            out.append(cur)
            cur = tok
    if cur:
        out.append(cur)
    return out


def _gtts_fetch_mp3(text: str, lang: str = "nl", timeout: int = TTS_TIMEOUT) -> bytes:
    """Download MP3 from the public Google Translate TTS endpoint.

    Returns MP3 bytes or raises an exception on a network/HTTP error.
    Uses no third-party libraries — only urllib, like all the rest of the
    addon's networking code."""
    import urllib.parse as _up
    import urllib.request as _ur
    import urllib.error as _ue

    chunks = _gtts_chunks(text)
    if not chunks:
        raise ValueError("empty text for TTS")
    lang_code = (lang or "en").strip().lower() or "en"

    # client=tw-ob — Google Translate Web mode (anonymous, no token).
    # ie=UTF-8 — input encoding. q — text. tl — target language.
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://translate.google.com/",
    }

    parts: list[bytes] = []
    for idx, chunk in enumerate(chunks):
        params = _up.urlencode(
            {
                "ie": "UTF-8",
                "q": chunk,
                "tl": lang_code,
                "client": "tw-ob",
                "ttsspeed": "1",
                "total": str(len(chunks)),
                "idx": str(idx),
                "textlen": str(len(chunk)),
            }
        )
        url = f"https://translate.google.com/translate_tts?{params}"
        req = _ur.Request(url, headers=headers, method="GET")
        # SSL fallback identical to the one already done in _http_get_bytes
        # (some Python builds on macOS lack root certificates).
        try:
            with _ur.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
        except _ue.URLError as e:
            reason = str(getattr(e, "reason", e))
            if "CERTIFICATE_VERIFY_FAILED" in reason or "SSL" in reason.upper():
                import ssl as _ssl
                ctx = _ssl._create_unverified_context()  # type: ignore[attr-defined]
                with _ur.urlopen(req, timeout=timeout, context=ctx) as resp:
                    data = resp.read()
            else:
                raise
        if not data:
            raise RuntimeError("empty TTS response")
        parts.append(data)

    return b"".join(parts)


def _save_audio_to_media(data: bytes, hint: str = "") -> "Optional[str]":
    """Save MP3 bytes to Anki media, return the resulting file name.
    Fully symmetric to `_save_image_to_media`, only the format is hardcoded
    to `mp3` (Google TTS returns only that)."""
    col = mw.col if mw is not None else None
    if col is None or not data:
        return None
    slug = re.sub(r"[^\w\- ]+", "", (hint or "tts")).strip().replace(" ", "_")
    slug = slug[:40] or "tts"
    import hashlib
    digest = hashlib.sha1(data).hexdigest()[:8]
    fname = f"ai_tts_{slug}_{digest}.mp3"
    try:
        write = getattr(col.media, "write_data", None) or getattr(col.media, "writeData", None)
        if callable(write):
            real = write(fname, data)
            if isinstance(real, str) and real:
                return real
        import os
        media_dir = getattr(col.media, "dir", None)
        media_dir = media_dir() if callable(media_dir) else media_dir
        if media_dir:
            path = os.path.join(media_dir, fname)
            with open(path, "wb") as f:
                f.write(data)
            return fname
    except Exception as e:
        _log(f"write_media (audio) failed: {e}")
    return None


# Candidate field names where we can place an image separately from
# Back. The comparison is case-insensitive, by exact match. The list is
# intentionally broad so the user doesn't have to rename an already
# existing field in their model.
_IMAGE_FIELD_CANDIDATES: "tuple[str, ...]" = (
    "Image", "Images", "Picture", "Pictures", "Photo", "Foto",
    "Illustration", "Иллюстрация", "Изображение", "Картинка",
    "Pic", "Img",
)


def _find_image_field(field_names: "list[str]") -> "Optional[str]":
    """Find a suitable field for an image in the note model.

    First we search the whitelist of names (case-insensitive); if nothing
    is found — return None, and then the calling code will attach the image
    to Back, as before. The return value is the real field name
    (with its original case), so that `note[name]` is written correctly.
    """
    if not field_names:
        return None
    norm = {fn.strip().lower(): fn for fn in field_names if isinstance(fn, str)}
    for cand in _IMAGE_FIELD_CANDIDATES:
        hit = norm.get(cand.lower())
        if hit:
            return hit
    return None


def _add_note_to_deck(
    front: str,
    back: str,
    deck_id: Optional[int],
    model_name: str,
    image_html: str = "",
    audio_html: str = "",
) -> bool:
    col = mw.col
    if col is None:
        showWarning(_t("error_collection_not_loaded"))
        _log("add_note: no collection")
        return False

    models = col.models
    by_name = getattr(models, "by_name", None) or getattr(models, "byName", None)
    model = by_name(model_name) if callable(by_name) else None
    if not model:
        showWarning(_t("error_note_type_not_found"))
        _log(f"add_note: model not found '{model_name}'")
        return False

    if hasattr(col, "new_note"):
        note = col.new_note(model)
    elif Note is not None:
        note = Note(col, model)  # type: ignore
    else:
        showWarning(_t("error_cannot_create_note"))
        _log("add_note: cannot create note instance")
        return False

    field_names_method = getattr(models, "field_names", None) or getattr(models, "fieldNames", None)
    field_names = field_names_method(model) if callable(field_names_method) else []

    # If the model has a separate field for the image (Image/Picture/Foto/
    # Изображение/Картинка/...), we put `image_html` there and do NOT also
    # write it into Back. Otherwise — as a fallback we attach the tag to Back,
    # so that old models keep working too.
    image_target_field: "Optional[str]" = None
    if image_html:
        image_target_field = _find_image_field(list(field_names))
        if image_target_field is None:
            sep = "\n\n" if back else ""
            back = f"{back}{sep}{image_html}".strip()

    if "Front" in field_names and "Back" in field_names:
        note["Front"] = front
        note["Back"] = back
    elif len(field_names) >= 2:
        note[field_names[0]] = front
        note[field_names[1]] = back
    else:
        showWarning(_t("error_insufficient_fields"))
        _log("add_note: insufficient fields in model")
        return False

    if image_target_field is not None:
        try:
            note[image_target_field] = image_html
        except Exception as e:
            _log(f"add_note: cannot set image field '{image_target_field}': {e}")
            # Hard fallback: if the field exists but Anki won't let us write
            # (a strange model), we attach it to Back so the card has an image at all.
            try:
                cur_back = str(note["Back"]) if "Back" in field_names else ""
                sep = "\n\n" if cur_back else ""
                note["Back"] = f"{cur_back}{sep}{image_html}".strip()
            except Exception:
                pass

    # Audio (a sound-tag like `[sound:fname.mp3]`). We put it only into the
    # designated field; if the model has no such field — we do NOT push it into
    # Back, otherwise the user would see a raw `[sound:...]` as text. Better to
    # silently skip than to ruin the card.
    if audio_html:
        audio_target_field = _find_audio_field(list(field_names))
        if audio_target_field is not None:
            try:
                note[audio_target_field] = audio_html
            except Exception as e:
                _log(f"add_note: cannot set audio field '{audio_target_field}': {e}")
        else:
            _log("add_note: audio field not found in model — skipping sound tag")

    effective_deck_id = _resolve_effective_deck_id(col, deck_id)

    if effective_deck_id is not None:
        _assign_deck_to_note(note, int(effective_deck_id))

    if _card_exists_in_deck(front, effective_deck_id, model):
        _log(f"skip-existing-card deck_id={effective_deck_id} front='{str(front)[:80]}'")
        return False

    add_note = getattr(col, "add_note", None) or getattr(col, "addNote", None)
    if not callable(add_note):
        showWarning(_t("error_add_note_method_not_found"))
        _log("add_note: add_note method not found")
        return False
    try:
        # In newer Anki versions deck_id is required - always pass if we computed it
        # Why try/except: API signature changed between Anki versions
        if effective_deck_id is not None:
            add_note(note, deck_id=int(effective_deck_id))
        else:
            add_note(note)
    except TypeError:
        # Fallback for very old versions where signature might differ
        add_note(note)

    mw.reset()
    _log(f"add_note: success deck_id={effective_deck_id} model='{model_name}' front='{str(front)[:80]}'")
    return True


def _run_on_main_thread(func) -> None:
    """
    Safely execute a function on the main Qt thread.
    
    Why this is critical: Qt UI operations MUST run on main thread.
    Background threads (API calls, hotkey handlers) cannot directly call showInfo/showWarning.
    This function ensures thread safety by scheduling execution on main thread.
    
    Fallback hierarchy:
    1. taskman.run_on_main() - newer Anki versions
    2. QTimer.singleShot(0, func) - Qt-based scheduling (no delay)
    3. Direct call - last resort (may cause crashes, but better than silent failure)
    """
    # Newer Anki versions have taskman
    taskman = getattr(mw, "taskman", None)
    if taskman and hasattr(taskman, "run_on_main"):
        try:
            taskman.run_on_main(func)
            return
        except Exception:
            pass
    # Fallback - schedule without delay in main event loop
    try:
        from aqt.qt import QTimer

        QTimer.singleShot(0, func)
    except Exception:
        # If nothing works, call directly (undesirable but better than failing silently)
        func()


def _enqueue_generation_and_add(words: str) -> None:
    """
    Queue AI card generation and addition with deduplication.
    
    Why two-level deduplication:
    1. Input-level (3.5s): Prevents duplicate API calls from rapid hotkey presses
       (user might press hotkey multiple times by accident)
    2. Front-level (5s): Prevents duplicate cards even if API returns same front text
       (different inputs might generate same card, or API retries)
    
    Why use function attributes instead of module globals:
    - Thread-safe initialization with Lock
    - Encapsulates state within function scope
    - Easier to test and reason about
    """
    # Deduplication: same normalized input not more than once per 3.5s,
    # plus anti-duplicate by front text for 5s
    if not hasattr(_enqueue_generation_and_add, "_last_norm"):
        _enqueue_generation_and_add._last_norm = ""  # type: ignore[attr-defined]
        _enqueue_generation_and_add._last_ts = 0  # type: ignore[attr-defined]
        _enqueue_generation_and_add._recent_fronts = {}  # type: ignore[attr-defined]
        _enqueue_generation_and_add._lock = threading.Lock()  # type: ignore[attr-defined]

    with _enqueue_generation_and_add._lock:  # type: ignore[attr-defined]
        now = int(time.time() * 1000)
        last_ts = getattr(_enqueue_generation_and_add, "_last_ts")  # type: ignore[attr-defined]
        last_norm = getattr(_enqueue_generation_and_add, "_last_norm")  # type: ignore[attr-defined]
        cur_norm = " ".join((words or "").lower().split())
        if cur_norm and cur_norm == last_norm and (now - int(last_ts)) < 3500:
            _log(f"skip-dup-input cur='{cur_norm[:80]}' dt={now-int(last_ts)}ms")
            return
        _enqueue_generation_and_add._last_norm = cur_norm  # type: ignore[attr-defined]
        _enqueue_generation_and_add._last_ts = now  # type: ignore[attr-defined]

    def worker() -> None:
        try:
            cfg = _get_config()
            platform = str(cfg.get("ai_platform", DEFAULT_PLATFORM)).strip().lower() or DEFAULT_PLATFORM
            if platform == "groq":
                api_key = str(cfg.get("groq_api_key", "")).strip() or DEFAULT_GROQ_API_KEY
                model_id = str(cfg.get("groq_model", DEFAULT_GROQ_MODEL)).strip() or DEFAULT_GROQ_MODEL
            else:
                platform = "google"
                api_key = str(cfg.get("gemini_api_key", "")).strip() or DEFAULT_GEMINI_API_KEY
                # Gemini model is fixed to the single 3.1 option offered in the UI;
                # ignore any stale saved value (older configs may hold gemini-2.5-*).
                model_id = DEFAULT_GEMINI_MODEL
            # Build the content prompt from constructor settings (or raw prompt in
            # advanced mode). Falls back to the legacy default if it comes out empty.
            custom = _build_content_prompt(cfg)
            if not custom:
                custom = DEFAULT_CUSTOM_PROMPT
                _log("DEBUG: Using default custom prompt (builder returned empty)")
            # Deck/note model from config
            ai_deck_id = cfg.get("ai_deck_id")
            model_name = str(cfg.get("note_model_name", "Basic")) or "Basic"
            col = mw.col
            if col is not None:
                by_name = getattr(col.models, "by_name", None) or getattr(col.models, "byName", None)
                model_obj = by_name(model_name) if callable(by_name) else None
                if model_obj and _card_exists_in_deck(words, int(ai_deck_id) if ai_deck_id is not None else None, model_obj):
                    _log(f"skip-existing-before-api words='{(words or '').strip()[:120]}'")
                    def _warn_exists() -> None:
                        try:
                            showWarning(_t("error_card_already_exists"))
                        except Exception:
                            pass
                    _run_on_main_thread(_warn_exists)
                    return

            _log(f"API request start platform='{platform}' model='{model_id}' words='{(words or '').strip()[:120]}'")
            _log(f"DEBUG: API key present: {bool(api_key)}, length: {len(api_key) if api_key else 0}")
            _log(f"DEBUG: Custom prompt length: {len(custom) if custom else 0}")
            t0 = time.time()
            try:
                front, back = _generate_card_by_platform(platform, api_key, words, custom, model_id)
                dt_ms = int((time.time() - t0) * 1000)
                _log(f"API response ok in {dt_ms}ms front='{str(front)[:80]}' back='{str(back)[:80]}'")
                _log(f"DEBUG: Generated front length: {len(front)}, back length: {len(back)}")
                _stat_on_generation()
            except Exception as gen_exc:
                _log_exc("card generation")
                raise

            def open_review_cb() -> None:
                try:
                    _open_ai_review_and_add(
                        words=words,
                        front=front,
                        back=back,
                        model_name=model_name,
                        ai_deck_id=int(ai_deck_id) if ai_deck_id is not None else None,
                        platform=platform,
                        api_key=api_key,
                        model_id=model_id,
                        base_custom=custom,
                    )
                except Exception as dlg_exc:
                    _log(f"AI review dialog: {dlg_exc}")

            _run_on_main_thread(open_review_cb)
        except Exception as e:
            _log_exc("AI generate-and-add")
            # Capture the message NOW — `e` is unbound once this except block exits,
            # but the callback runs later on the main thread.
            err_text = str(e)
            _run_on_main_thread(lambda: _show_error(None, err_text))

    t = threading.Thread(target=worker, name="AI-Generate-And-Add", daemon=True)
    t.start()


def _start_http_listener() -> None:
    """
    Start HTTP server for external hotkey integration.
    
    Why HTTP instead of only built-in hotkeys:
    - Allows external scripts (AutoHotkey, Python listeners) to trigger card generation
    - More flexible: users can customize hotkey behavior outside Anki
    - Works on all platforms without platform-specific code in the addon
    
    Design choice: Silent failure if port is busy (another instance might be running)
    to avoid breaking the addon startup.
    """
    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, code: int, payload: dict) -> None:
            body = _json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass

        def do_POST(self):  # type: ignore
            try:
                if self.path != "/insertFront":
                    self._send_json(404, {"error": "not_found"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    data = _json.loads(raw.decode("utf-8"))
                except Exception:
                    data = {}
                text = str(data.get("text", "")).strip()
                if not text:
                    self._send_json(400, {"error": "empty_text"})
                    return

                _enqueue_generation_and_add(text)
                self._send_json(200, {"status": "queued"})
            except Exception as e:
                self._send_json(500, {"error": str(e)})

        def log_message(self, format: str, *args) -> None:  # quiet
            return

    def run_server():
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", 8766), Handler)
            srv.daemon_threads = True  # type: ignore[attr-defined]
            srv.serve_forever()
        except OSError:
            # Port busy - silently skip (another instance might be running)
            pass
        except Exception:
            pass

    th = threading.Thread(target=run_server, name="AnkiAddon-HTTP", daemon=True)
    th.start()


def _read_clipboard_text() -> str:
    try:
        from aqt.qt import QGuiApplication
        app = QGuiApplication.instance()
        if app is not None:
            cb = app.clipboard()
            txt = cb.text() or ""
            return str(txt).strip()
    except Exception:
        pass
    return ""


def _macos_copy_and_get_text(send_copy: bool = True, max_wait_ms: int = 1500, poll_ms: int = 50) -> str:
    """
    Simulate Cmd+C via System Events and wait for clipboard update via Qt clipboard.sequenceNumber().
    
    Why use sequenceNumber instead of just comparing text:
    - More reliable: detects clipboard changes even if text appears similar
    - Works even if clipboard content is identical (sequence increments on any change)
    - Avoids false positives from text comparison edge cases
    
    Why osascript instead of pynput:
    - More reliable on macOS, especially in browsers
    - Requires Accessibility permissions but works better than programmatic key simulation
    """
    if sys.platform != "darwin":
        return _read_clipboard_text()
    try:
        # Read the clipboard via `pbpaste` (CLI), NOT Qt: this runs on a background
        # thread and Qt clipboard access is only safe on the main thread. pbpaste
        # works from any thread.
        def _pbpaste() -> str:
            try:
                # Capture raw bytes and decode UTF-8 ourselves — `text=True` would
                # use the (often ASCII) locale encoding and crash on any non-ASCII
                # character (curly quotes, accents, etc.), losing the selection.
                out = subprocess.run(["pbpaste"], capture_output=True, timeout=2)
                return (out.stdout or b"").decode("utf-8", "replace").strip()
            except Exception as e:
                _log(f"hotkey: pbpaste error {e!r}")
                return ""

        before_text = _pbpaste()
        if not send_copy:
            # Double-copy gesture: the user already pressed Cmd/Ctrl+C themselves, so
            # the selection is ALREADY on the clipboard. Do NOT synthesize another
            # Cmd+C — firing it while Cmd is still physically held can trigger a
            # browser shortcut (e.g. view-source) instead of a copy. Just read it.
            got = ""
            start = time.time()
            while (time.time() - start) * 1000 < 500:
                got = _pbpaste()
                if got:
                    break
                time.sleep(poll_ms / 1000.0)
            _log(f"hotkey: captured (no synth) len={len(got)} preview={got[:40]!r}")
            return got.strip()
        _log("hotkey: sending synthetic Cmd+C")
        _macos_send_cmd_c()

        start = time.time()
        got_text = ""
        while (time.time() - start) * 1000 < max_wait_ms:
            cur = _pbpaste()
            if cur and cur != before_text:
                got_text = cur
                break
            time.sleep(poll_ms / 1000.0)

        if not got_text:
            # Fall back to whatever is on the clipboard (user may have copied
            # the same text, so it equals before_text).
            got_text = _pbpaste()
        _log(f"hotkey: captured text len={len(got_text)} preview={got_text[:40]!r}")
        return got_text.strip()
    except Exception as e:
        _log(f"hotkey: _macos_copy_and_get_text error {e!r}")
        return _read_clipboard_text()


def _macos_send_cmd_c() -> None:
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to keystroke "c" using {command down}',
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# Live hotkey state shared between the settings dialog and the running event-tap
# thread. The tap thread reads this on every keypress, so changing the hotkey in
# settings takes effect immediately — no Anki restart needed.
_HOTKEY_STATE: dict = {}


def _parse_hotkey_combo(combo: str) -> dict:
    """
    Parse a combo string like "cmd+b", "cmd+option+t", "f8" into the keycode +
    modifier flags the macOS CGEventTap engine matches against. US QWERTY layout.
    """
    combo = (combo or "").lower().strip()
    parts = [p.strip() for p in combo.split('+') if p.strip()]
    key_label = (parts[-1] if parts else "").upper()
    mods_str = '+'.join(parts[:-1])
    want_cmd = ('cmd' in mods_str or 'command' in mods_str)
    want_opt = ('option' in mods_str or 'opt' in mods_str or 'alt' in mods_str)
    want_ctrl = ('ctrl' in mods_str or 'control' in mods_str)
    want_shift = ('shift' in mods_str)
    keycode = -1
    if len(key_label) == 1 and 'A' <= key_label <= 'Z':
        base = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        mapping = [
            0, 11, 8, 2, 14, 3, 5, 4, 34, 38, 40, 37, 46, 45, 31, 35,
            12, 15, 1, 17, 32, 9, 13, 7, 16, 6
        ]
        try:
            keycode = mapping[base.index(key_label)]
        except Exception:
            keycode = -1
    elif key_label in ("F7", "F8"):
        keycode = 100 if key_label == "F8" else 98
    return {
        "enabled": bool(combo and keycode >= 0),
        "keycode": keycode,
        "want_cmd": want_cmd,
        "want_opt": want_opt,
        "want_ctrl": want_ctrl,
        "want_shift": want_shift,
        "combo": combo,
    }


def _refresh_hotkey_state() -> None:
    """Reload hotkey settings from config into the live shared state."""
    try:
        cfg = _get_config()
        combo = str(cfg.get("hotkey_combo", "")).strip()
        _HOTKEY_STATE.clear()
        _HOTKEY_STATE.update(_parse_hotkey_combo(combo))
        _HOTKEY_STATE["double_copy"] = bool(cfg.get("double_ctrl_c_enabled", True))
        _log(f"hotkey: state refreshed double={_HOTKEY_STATE['double_copy']} combo={combo!r} keycode={_HOTKEY_STATE['keycode']}")
    except Exception as e:
        _log(f"hotkey: _refresh_hotkey_state error {e!r}")


def _macos_global_tap_thread() -> None:
    """
    macOS global hotkey implementation using CGEventTap (low-level keyboard hook).
    
    Why CGEventTap instead of RegisterEventHotKey:
    - Works even when Anki is minimized/backgrounded
    - More control over event filtering
    - Can intercept events before they reach applications
    
    Why low-level ctypes instead of higher-level libraries:
    - No external dependencies (pynput requires permissions and may not work in all contexts)
    - Direct control over event handling
    - Works reliably in Anki's environment
    
    Trade-off: Requires Accessibility permissions, but user must grant them anyway.
    """
    if sys.platform != "darwin":
        return
    _log("hotkey: macOS tap thread starting")
    try:
        kCGHIDEventTap = 0
        kCGHeadInsertEventTap = 0
        kCGEventTapOptionDefault = 0
        kCGEventKeyDown = 10
        kCGEventFlagMaskCommand = 1 << 20
        kCGEventFlagMaskAlternate = 1 << 19
        kCGEventFlagMaskControl = 1 << 18
        kCGEventFlagMaskShift = 1 << 17
        kCGKeyboardEventKeycode = 9
        # Load current hotkey settings into the shared live state. The handler
        # reads _HOTKEY_STATE on every event, so settings changes apply instantly.
        _refresh_hotkey_state()

        cg = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        cf = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )

        CGEventMaskBit = lambda event_type: ctypes.c_uint64(1 << event_type)
        event_mask = ctypes.c_uint64(int(CGEventMaskBit(kCGEventKeyDown).value))

        CGEventTapCallBack = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p
        )

        cg.CGEventTapCreate.restype = ctypes.c_void_p
        cg.CGEventTapCreate.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint64, CGEventTapCallBack, ctypes.c_void_p
        ]
        cg.CGEventGetFlags.restype = ctypes.c_uint64
        cg.CGEventGetFlags.argtypes = [ctypes.c_void_p]
        cg.CGEventGetIntegerValueField.restype = ctypes.c_longlong
        cg.CGEventGetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_int64]
        cg.CGEventTapEnable.argtypes = [ctypes.c_void_p, ctypes.c_bool]

        cf.CFMachPortCreateRunLoopSource.restype = ctypes.c_void_p
        cf.CFMachPortCreateRunLoopSource.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
        cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
        cf.CFRunLoopAddSource.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        cf.CFRunLoopRun.argtypes = []
        kCFRunLoopCommonModes = ctypes.c_void_p.in_dll(cf, "kCFRunLoopCommonModes")

        # Event-tap "disabled" notifications macOS sends when the callback is too
        # slow or the system suspends the tap. We must re-enable on these.
        kCGEventTapDisabledByTimeout = 0xFFFFFFFE
        kCGEventTapDisabledByUserInput = 0xFFFFFFFF

        last_ms = 0
        last_copy_ms = 0
        tap = None  # assigned after the tap is created; referenced for re-enable

        def _capture_async(send_copy: bool = True):
            # Heavy work (synthetic Cmd+C + clipboard polling) must NOT run inside
            # the event-tap callback, or macOS disables the tap by timeout. Run it
            # on a short-lived background thread so the callback returns instantly.
            try:
                txt = _macos_copy_and_get_text(send_copy=send_copy)
                if txt:
                    _log("hotkey: enqueueing generation")
                    _enqueue_generation_and_add(txt)
                else:
                    _log("hotkey: no text captured, nothing to do")
            except Exception as e:
                _log(f"hotkey: _capture_async error {e!r}")

        def handler(proxy, etype, event, refcon):
            try:
                nonlocal last_ms, last_copy_ms
                et = int(etype) & 0xFFFFFFFF
                # Re-enable the tap if macOS disabled it.
                if et in (kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput):
                    if tap:
                        cg.CGEventTapEnable(tap, True)
                    return event
                if et != kCGEventKeyDown:
                    return event
                flags = int(cg.CGEventGetFlags(event))
                keycode = int(cg.CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode))
                st = _HOTKEY_STATE  # live state, refreshed on settings save
                # Double-copy gesture (opt-in): press the copy shortcut twice quickly
                # to capture the selection and generate a card. On macOS the natural
                # copy key is Cmd+C, but we accept Ctrl+C too — whichever the user uses.
                # keycode 8 = "C" on the US layout.
                copy_mod = flags & (kCGEventFlagMaskCommand | kCGEventFlagMaskControl)
                if st.get("double_copy", True) and keycode == 8 and copy_mod:
                    now = int(time.time() * 1000)
                    dt = now - last_copy_ms
                    _log(f"hotkey: copy-key seen dt={dt}ms flags={hex(flags)}")
                    if 120 <= dt <= 900:
                        _log("hotkey: double copy TRIGGERED")
                        # No synthetic Cmd+C here: the user's own double Cmd+C already
                        # put the selection on the clipboard.
                        threading.Thread(target=lambda: _capture_async(send_copy=False), daemon=True).start()
                        last_copy_ms = 0  # reset so a third press doesn't re-trigger
                    else:
                        last_copy_ms = now
                    return event
                if st.get("enabled") and keycode == st.get("keycode", -1):
                    cond_cmd = (flags & kCGEventFlagMaskCommand) if st.get("want_cmd") else True
                    cond_opt = (flags & kCGEventFlagMaskAlternate) if st.get("want_opt") else True
                    cond_ctrl = (flags & kCGEventFlagMaskControl) if st.get("want_ctrl") else True
                    cond_shift = (flags & kCGEventFlagMaskShift) if st.get("want_shift") else True
                    if cond_cmd and cond_opt and cond_ctrl and cond_shift:
                        now = int(time.time() * 1000)
                        # Minimal debounce filtering (200ms) — physical key presses
                        # can emit multiple events; 200ms feels instant but de-dupes.
                        if now - last_ms < 200:
                            return event
                        last_ms = now
                        _log(f"hotkey: custom combo TRIGGERED ({st.get('combo')})")
                        threading.Thread(target=_capture_async, daemon=True).start()
            except Exception:
                pass
            return event

        callback = CGEventTapCallBack(handler)
        tap = cg.CGEventTapCreate(
            kCGHIDEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionDefault,
            event_mask,
            callback,
            None,
        )
        if not tap:
            _log("hotkey: CGEventTapCreate returned NULL — Accessibility permission missing or not yet active (restart Anki after granting)")
            return
        _log("hotkey: event tap created OK, entering run loop")
        source = cf.CFMachPortCreateRunLoopSource(None, tap, 0)
        rl = cf.CFRunLoopGetCurrent()
        cf.CFRunLoopAddSource(rl, source, kCFRunLoopCommonModes)
        cg.CGEventTapEnable(tap, True)
        cf.CFRunLoopRun()
    except Exception as e:
        _log(f"hotkey: macOS tap thread crashed {e!r}")
        return


def _start_platform_global_hotkey() -> None:
    """
    Start platform-specific global hotkey listener.
    
    Why separate threads: Hotkey listeners run blocking event loops.
    Must be in separate threads to avoid blocking Anki's main thread.
    
    Why daemon threads: If Anki exits, these threads should terminate automatically.
    No need to clean up explicitly.
    
    Linux: Not implemented - use external HTTP listener instead.
    Reason: X11/Wayland global hotkey APIs are complex and vary by desktop environment.
    External script (pynput-based) is more reliable and maintainable.
    """
    try:
        global _mac_hotkey_started, _win_hotkey_started
        if sys.platform == "darwin" and not _mac_hotkey_started:
            _mac_hotkey_started = True
            t = threading.Thread(target=_macos_global_tap_thread, name="AnkiAddon-macOSHotkey", daemon=True)
            t.start()
        elif sys.platform.startswith("win") and not _win_hotkey_started:
            _win_hotkey_started = True
            t = threading.Thread(target=_start_windows_hotkey_thread, name="AnkiAddon-WinHotkey", daemon=True)
            t.start()
        # Linux: use external listener (HTTP-based)
    except Exception:
        pass


class _HotkeyRecorderButton(QPushButton):
    """
    Button that records a global-hotkey combo by capturing the next keypress.

    Click → it grabs the keyboard and waits for a key combination, then stores
    it as a normalized string like "cmd+option+t", "ctrl+shift+x" or "f8".
    No OS permission is needed just to record the combo.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._combo = ""
        self._recording = False
        self.clicked.connect(self._start_recording)
        self._render()

    def set_combo(self, combo: str) -> None:
        self._combo = (combo or "").strip().lower()
        self._recording = False
        self._render()

    def combo(self) -> str:
        return self._combo

    def _render(self) -> None:
        if self._recording:
            self.setText(_t("hotkey_press_keys"))
        elif self._combo:
            self.setText(self._combo.replace("+", " + ").title())
        else:
            self.setText(_t("hotkey_record_btn"))

    def _start_recording(self) -> None:
        self._recording = True
        self._render()
        try:
            self.grabKeyboard()
        except Exception:
            pass

    def _finish(self) -> None:
        self._recording = False
        try:
            self.releaseKeyboard()
        except Exception:
            pass
        self._render()

    def keyPressEvent(self, e) -> None:  # type: ignore[override]
        if not self._recording:
            return super().keyPressEvent(e)
        try:
            key = e.key()
            # Escape cancels recording without changing the combo.
            if key == Qt.Key.Key_Escape:
                self._finish()
                return
            # Ignore lone modifier presses — wait for a real key.
            mod_keys = {
                Qt.Key.Key_Control, Qt.Key.Key_Shift,
                Qt.Key.Key_Alt, Qt.Key.Key_Meta,
            }
            if key in mod_keys:
                return
            mods = e.modifiers()
            parts: list[str] = []
            # IMPORTANT macOS quirk: by default Qt SWAPS Cmd and Ctrl, reporting the
            # physical Cmd (⌘) key as ControlModifier and physical Control as
            # MetaModifier. Our CGEventTap engine matches on the *physical* key, so we
            # undo the swap here: on macOS ControlModifier means Cmd, Meta means Ctrl.
            ctrl_mod = bool(mods & Qt.KeyboardModifier.ControlModifier)
            meta_mod = bool(mods & Qt.KeyboardModifier.MetaModifier)
            if sys.platform == "darwin":
                if ctrl_mod:
                    parts.append("cmd")
                if meta_mod:
                    parts.append("ctrl")
            else:
                if ctrl_mod:
                    parts.append("ctrl")
                if meta_mod:
                    parts.append("cmd")
            if mods & Qt.KeyboardModifier.AltModifier:
                parts.append("option")
            if mods & Qt.KeyboardModifier.ShiftModifier:
                parts.append("shift")
            key_text = QKeySequence(key).toString().strip().lower()
            if key_text:
                parts.append(key_text)
            if parts and parts[-1] not in ("cmd", "ctrl", "option", "shift"):
                self._combo = "+".join(parts)
            self._finish()
        except Exception:
            self._finish()


class ApiSettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent or mw)
        self.setWindowTitle(_t("label_api_settings"))
        self.platform_combo = QComboBox(self)
        self.platform_combo.addItem("Google", "google")
        # Groq stays supported in code, but is hidden from the UI for now.
        # self.platform_combo.addItem("Groq", "groq")
        self.api_key_edit = QLineEdit(self)
        try:
            self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        except Exception:
            self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.model_combo = QComboBox(self)
        self.model_combo.setEditable(True)
        # Model and provider are fixed (Gemini 3.1 Flash Lite) — only the API key is
        # asked for. The combos stay alive for config logic but are hidden.
        self.platform_combo.hide()
        self.model_combo.hide()

        form = QFormLayout(self)
        form.addRow(_t("label_api_key"), self.api_key_edit)
        _api_note = QLabel(_t("wizard_rate_note"), self)
        try:
            _api_note.setWordWrap(True)
            _api_note.setStyleSheet("color: gray; font-size: 11px;")
        except Exception:
            pass
        form.addRow(_api_note)
        buttons = QDialogButtonBox(self)
        try:
            buttons.setStandardButtons(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
        except Exception:
            buttons.setStandardButtons(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        form.addRow(buttons)
        qconnect(buttons.accepted, self.accept)
        qconnect(buttons.rejected, self.reject)
        qconnect(self.platform_combo.currentIndexChanged, self._refresh_model_and_key)
        self._load_from_config()

    def _refresh_model_and_key(self, idx: int) -> None:
        platform = str(self.platform_combo.itemData(idx) or DEFAULT_PLATFORM)
        cfg = _get_config()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for mid in PLATFORM_MODELS.get(platform, []):
            self.model_combo.addItem(mid)
        saved_model = str(cfg.get("groq_model" if platform == "groq" else "gemini_model", "")).strip()
        if not saved_model:
            saved_model = DEFAULT_GROQ_MODEL if platform == "groq" else DEFAULT_GEMINI_MODEL
        # Google offers a single fixed model (3.1); drop any stale saved value
        # (e.g. an older gemini-2.5-*) instead of re-adding it to the list.
        if platform != "groq" and self.model_combo.findText(saved_model) < 0:
            saved_model = DEFAULT_GEMINI_MODEL
        if self.model_combo.findText(saved_model) < 0:
            self.model_combo.addItem(saved_model)
        self.model_combo.setCurrentText(saved_model)
        self.model_combo.blockSignals(False)

        saved_key = str(cfg.get("groq_api_key" if platform == "groq" else "gemini_api_key", "")).strip()
        if not saved_key:
            saved_key = DEFAULT_GROQ_API_KEY if platform == "groq" else DEFAULT_GEMINI_API_KEY
        self.api_key_edit.setText(saved_key)

    def _load_from_config(self) -> None:
        cfg = _get_config()
        platform = str(cfg.get("ai_platform", DEFAULT_PLATFORM)).strip().lower() or DEFAULT_PLATFORM
        idx = self.platform_combo.findData(platform)
        if idx < 0:
            idx = self.platform_combo.findData(DEFAULT_PLATFORM)
        self.platform_combo.setCurrentIndex(max(0, idx))
        self._refresh_model_and_key(self.platform_combo.currentIndex())

    def save_to_config(self) -> None:
        cfg = _get_config()
        platform = str(self.platform_combo.currentData() or DEFAULT_PLATFORM)
        cfg["ai_platform"] = platform
        if platform == "groq":
            cfg["groq_api_key"] = self.api_key_edit.text().strip() or DEFAULT_GROQ_API_KEY
            cfg["groq_model"] = self.model_combo.currentText().strip() or DEFAULT_GROQ_MODEL
        else:
            cfg["gemini_api_key"] = self.api_key_edit.text().strip() or DEFAULT_GEMINI_API_KEY
            cfg["gemini_model"] = self.model_combo.currentText().strip() or DEFAULT_GEMINI_MODEL
        _set_config(cfg)


def _preview_surface_colors() -> Tuple[str, str]:
    try:
        from aqt.qt import QPalette

        pal = mw.palette()
        br = QPalette.ColorRole.Base if hasattr(QPalette, "ColorRole") else QPalette.Base
        tr = QPalette.ColorRole.Text if hasattr(QPalette, "ColorRole") else QPalette.Text
        return pal.color(br).name(), pal.color(tr).name()
    except Exception:
        return "#fafafa", "#1a1a1a"


def _anki_field_body_html(field_text: str) -> str:
    """Sanitized HTML fragment for one field (Anki-like): plain text escaped, existing HTML kept minus risky bits."""
    s = field_text or ""
    s = re.sub(r"(?is)<script[^>]*>.*?</script>", "", s)
    s = re.sub(r"(?is)<iframe[^>]*>.*?</iframe>", "", s)
    s = re.sub(r"(?is)<\s*object\b[^>]*>.*?</\s*object\s*>", "", s)
    s = re.sub(r"(?i)\son\w+\s*=", " data-stripped=", s)
    if "<" in s and ">" in s:
        return s
    return html.escape(s).replace("\n", "<br/>")


def _anki_card_preview_document(front: str, back: str, font_px: int = 13, lbl_px: int = 9) -> str:
    """Full HTML document: Front block, separator, Back block — as a single card.
    font_px/lbl_px let the dialog shrink the preview only in compact mode."""
    bg, fg = _preview_surface_colors()
    ff = _anki_field_body_html(front)
    bf = _anki_field_body_html(back)
    lf = html.escape(_t("label_card_side_front"))
    lb = html.escape(_t("label_card_side_back"))
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\"/><style>"
        "html, body { margin: 0; padding: 0; }"
        ".card { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; "
        f"font-size: {int(font_px)}px; line-height: 1.3; background: "
        + bg
        + "; color: "
        + fg
        + "; }"
        ".side-pad { padding: 5px 8px; }"
        ".side-pad.front { padding-bottom: 3px; }"
        f".lbl {{ font-size: {int(lbl_px)}px; font-weight: 600; letter-spacing: 0.04em; opacity: 0.55; margin-bottom: 2px; }}"
        ".body { word-wrap: break-word; overflow-wrap: anywhere; }"
        "hr.sep { border: none; border-top: 1px solid rgba(128,128,128,0.35); margin: 0; }"
        "</style></head><body><div class=\"card\">"
        "<div class=\"side-pad front\"><div class=\"lbl\">"
        + lf
        + "</div><div class=\"body\">"
        + ff
        + "</div></div><hr class=\"sep\"/>"
        "<div class=\"side-pad\"><div class=\"lbl\">"
        + lb
        + "</div><div class=\"body\">"
        + bf
        + "</div></div></div></body></html>"
    )


# ---------------------------------------------------------------------------
# Reverso Context integration
# ---------------------------------------------------------------------------
# Lightweight direct client against the public REST endpoint that the
# reverso-api PyPI package wraps internally. We don't ship that package
# (no pip inside Anki addons) — urllib is enough.

REVERSO_TIMEOUT = 10
REVERSO_MAX_TRANSLATIONS = 12
REVERSO_MAX_EXAMPLES = 5
REVERSO_DEFAULT_SRC = "nl"
REVERSO_DEFAULT_TGT = "ru"

# List of supported languages for the UI combobox. The key is ISO-639-1,
# the value is human-readable. Can be extended without limit.
REVERSO_LANG_CHOICES: list[tuple[str, str]] = [
    ("nl", "Nederlands"),
    ("ru", "Русский"),
    ("en", "English"),
    ("de", "Deutsch"),
    ("fr", "Français"),
    ("es", "Español"),
    ("it", "Italiano"),
    ("pt", "Português"),
    ("pl", "Polski"),
    ("uk", "Українська"),
    ("tr", "Türkçe"),
    ("sv", "Svenska"),
    ("ro", "Română"),
    ("he", "עברית"),
    ("ar", "العربية"),
    ("ja", "日本語"),
    ("zh", "中文"),
    ("ko", "한국어"),
]


# Comprehensive world-language list (ISO 639-1 code, English name) for the
# card-constructor language pickers. Translation/TTS backends accept ISO 639-1.
_WORLD_LANGUAGES: list[tuple[str, str]] = [
    ("af", "Afrikaans"), ("sq", "Albanian"), ("am", "Amharic"), ("ar", "Arabic"),
    ("hy", "Armenian"), ("az", "Azerbaijani"), ("eu", "Basque"), ("be", "Belarusian"),
    ("bn", "Bengali"), ("bs", "Bosnian"), ("bg", "Bulgarian"), ("ca", "Catalan"),
    ("ceb", "Cebuano"), ("ny", "Chichewa"), ("zh", "Chinese"), ("co", "Corsican"),
    ("hr", "Croatian"), ("cs", "Czech"), ("da", "Danish"), ("nl", "Dutch"),
    ("en", "English"), ("eo", "Esperanto"), ("et", "Estonian"), ("tl", "Filipino"),
    ("fi", "Finnish"), ("fr", "French"), ("fy", "Frisian"), ("gl", "Galician"),
    ("ka", "Georgian"), ("de", "German"), ("el", "Greek"), ("gu", "Gujarati"),
    ("ht", "Haitian Creole"), ("ha", "Hausa"), ("haw", "Hawaiian"), ("he", "Hebrew"),
    ("hi", "Hindi"), ("hmn", "Hmong"), ("hu", "Hungarian"), ("is", "Icelandic"),
    ("ig", "Igbo"), ("id", "Indonesian"), ("ga", "Irish"), ("it", "Italian"),
    ("ja", "Japanese"), ("jv", "Javanese"), ("kn", "Kannada"), ("kk", "Kazakh"),
    ("km", "Khmer"), ("rw", "Kinyarwanda"), ("ko", "Korean"), ("ku", "Kurdish"),
    ("ky", "Kyrgyz"), ("lo", "Lao"), ("la", "Latin"), ("lv", "Latvian"),
    ("lt", "Lithuanian"), ("lb", "Luxembourgish"), ("mk", "Macedonian"), ("mg", "Malagasy"),
    ("ms", "Malay"), ("ml", "Malayalam"), ("mt", "Maltese"), ("mi", "Maori"),
    ("mr", "Marathi"), ("mn", "Mongolian"), ("my", "Myanmar (Burmese)"), ("ne", "Nepali"),
    ("no", "Norwegian"), ("or", "Odia (Oriya)"), ("ps", "Pashto"), ("fa", "Persian"),
    ("pl", "Polish"), ("pt", "Portuguese"), ("pa", "Punjabi"), ("ro", "Romanian"),
    ("ru", "Russian"), ("sm", "Samoan"), ("gd", "Scots Gaelic"), ("sr", "Serbian"),
    ("st", "Sesotho"), ("sn", "Shona"), ("sd", "Sindhi"), ("si", "Sinhala"),
    ("sk", "Slovak"), ("sl", "Slovenian"), ("so", "Somali"), ("es", "Spanish"),
    ("su", "Sundanese"), ("sw", "Swahili"), ("sv", "Swedish"), ("tg", "Tajik"),
    ("ta", "Tamil"), ("tt", "Tatar"), ("te", "Telugu"), ("th", "Thai"),
    ("tr", "Turkish"), ("tk", "Turkmen"), ("uk", "Ukrainian"), ("ur", "Urdu"),
    ("ug", "Uyghur"), ("uz", "Uzbek"), ("vi", "Vietnamese"), ("cy", "Welsh"),
    ("xh", "Xhosa"), ("yi", "Yiddish"), ("yo", "Yoruba"), ("zu", "Zulu"),
]

_WORLD_LANG_NAME_BY_CODE = {code: name for code, name in _WORLD_LANGUAGES}


def _lang_display_name(code_or_value: str) -> str:
    """Map a stored language value (ISO code, or legacy free text) to a readable
    English name for the AI prompt. Falls back to the raw value."""
    v = (code_or_value or "").strip()
    if not v:
        return v
    return _WORLD_LANG_NAME_BY_CODE.get(v.lower(), v)


def _make_language_combo(parent=None) -> "QComboBox":
    """A searchable language picker: editable combo of all world languages with a
    contains-match completer. Stores the ISO code as item data; displays
    'English name (code)'. Use _language_combo_code()/_set_language_combo() to
    read/write the selected code."""
    combo = QComboBox(parent)
    combo.setEditable(True)
    try:
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    except Exception:
        pass
    for code, name in sorted(_WORLD_LANGUAGES, key=lambda x: x[1].lower()):
        combo.addItem(f"{name} ({code})", code)
    try:
        completer = combo.completer()
        if completer is not None:
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    except Exception:
        pass
    # Keep the combo narrow: don't size to the longest item; let it shrink/grow
    # with the layout (important on small screens / macOS Larger Text).
    try:
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(10)
        combo.setMinimumWidth(120)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    except Exception:
        pass
    return combo


def _language_combo_code(combo: "QComboBox", fallback: str) -> str:
    """Read the selected ISO code from a language combo, tolerating free text the
    user typed (matched by display label, name, or code)."""
    try:
        typed = combo.currentText().strip()
        if not typed:
            return fallback
        # Match the exact display label of an item first (handles dropdown picks
        # without relying on currentData, which can be stale for editable combos).
        for i in range(combo.count()):
            if combo.itemText(i) == typed:
                return str(combo.itemData(i))
        low = typed.lower()
        for code, name in _WORLD_LANGUAGES:
            if low == code or low == name.lower() or low == f"{name.lower()} ({code})":
                return code
        return typed
    except Exception:
        return fallback


def _set_language_combo(combo: "QComboBox", code: str) -> None:
    """Select the entry matching a stored code (or free-text legacy value)."""
    try:
        v = (code or "").strip()
        idx = combo.findData(v.lower())
        if idx < 0 and v:
            idx = combo.findData(v)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setEditText(_lang_display_name(v) or v)
    except Exception:
        pass


def _emoji_icon(emoji: str, px: int = 40) -> "QIcon":
    """Render an emoji glyph into a square QIcon for the sidebar tiles."""
    try:
        pm = QPixmap(px, px)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        f = p.font()
        f.setPointSize(int(px * 0.62))
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, emoji)
        p.end()
        return QIcon(pm)
    except Exception:
        return QIcon()


def _reverso_normalize_lang(code: str, fallback: str) -> str:
    """Returns a 2-letter ISO code. Google Translate and MyMemory both
    accept ISO-639-1, so we just normalize to two lower-case letters."""
    key = (code or "").strip().lower()
    if len(key) >= 2:
        key = key[:2]
    valid = {c for c, _ in REVERSO_LANG_CHOICES} | {c for c, _ in _WORLD_LANGUAGES}
    if key in valid:
        return key
    return (fallback or "en").lower()[:2]


def _reverso_strip_tags(s: str) -> str:
    """Strips HTML tags and normalizes whitespace (used for cleaning
    Google/MyMemory response fragments of markup and junk)."""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
           .replace("&lt;", "<").replace("&gt;", ">")
           .replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r"\s+", " ", s).strip()


def _http_get_bytes(url: str, headers: dict, timeout: int) -> bytes:
    """Download arbitrary bytes (images, files). We apply the same SSL
    safeguard as in `_http_get_json` here too, because some image CDNs
    use certificates that the built-in Anki Python doesn't know."""
    import ssl as _ssl
    import urllib.request as _ur
    import urllib.error as _ue
    req = _ur.Request(url, headers=headers, method="GET")

    def _do_open(ctx):
        with _ur.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read()

    try:
        return _do_open(None)
    except _ue.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}")
    except _ue.URLError as e:
        reason = str(getattr(e, "reason", e))
        if "CERTIFICATE_VERIFY_FAILED" in reason or "SSL" in reason.upper():
            try:
                unverified = _ssl._create_unverified_context()  # type: ignore[attr-defined]
                return _do_open(unverified)
            except Exception as e2:
                raise RuntimeError(f"network: {e2}")
        raise RuntimeError(f"network: {reason}")
    except Exception as e:
        raise RuntimeError(str(e))


def _http_get_text(url: str, headers: dict, timeout: int) -> str:
    """Download HTML/text (needed to extract the DuckDuckGo vqd token)."""
    raw = _http_get_bytes(url, headers, timeout)
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return raw.decode("latin-1", errors="replace")


# ---------------------------------------------------------------------------
# DuckDuckGo Images client (minimal, without the third-party `duckduckgo_search`).
# Protocol: (1) GET the search page HTML → extract the `vqd` token,
# (2) GET i.js JSON → get the list of results with an `image` field (URL).
# This is the same protocol the `duckduckgo_search` library uses.

DDG_HTML_ENDPOINT = "https://duckduckgo.com/"
DDG_JSON_ENDPOINT = "https://duckduckgo.com/i.js"
DDG_TIMEOUT = 10
DDG_IMAGES_COUNT = 5

_DDG_VQD_RE = re.compile(r"vqd=[\"']?([\w\-]+)[\"']?", re.IGNORECASE)


def _ddg_browser_headers(extra: "Optional[dict]" = None) -> dict:
    base = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if extra:
        base.update(extra)
    return base


def _ddg_get_vqd(word: str, timeout: int = DDG_TIMEOUT) -> str:
    """Fetch the one-time `vqd` token from the DDG results page.
    Without it, i.js returns 403. The token lives for a few minutes."""
    import urllib.parse as _up
    url = (
        DDG_HTML_ENDPOINT + "?q=" + _up.quote(word) +
        "&t=h_&iax=images&ia=images"
    )
    raw = _http_get_text(url, _ddg_browser_headers(), timeout)
    m = _DDG_VQD_RE.search(raw)
    if not m:
        raise RuntimeError("ddg: vqd token not found (DDG may have changed)")
    return m.group(1)


def _ddg_build_query(word: str, context: str = "") -> str:
    """Build a search query for DuckDuckGo Images so that the results
    contain PHOTOS rather than logos/icons/vector illustrations.

    Rules:
      - if `context` is passed (e.g. a translation from Back or a topic) —
        `"<word> <context> -logo -icon -vector"`;
      - if context is empty — `"<word> foto -logo -icon -vector"`.

    From `context` we carefully take the first line and limit it to 6
    words, so as not to drag the whole back with examples into the query —
    that breaks DDG relevance.
    """
    w = (word or "").strip()
    c = (context or "").strip()
    if c:
        c = c.split("\n", 1)[0]
        for sep in (";", ":", "(", ")", "—", "–", "-"):
            c = c.replace(sep, " ")
        c = " ".join(c.split()[:6]).strip()
    if c:
        return f"{w} {c} -logo -icon -vector"
    return f"{w} foto -logo -icon -vector"


def _ddg_image_urls(
    word: str,
    count: int = DDG_IMAGES_COUNT,
    timeout: int = DDG_TIMEOUT,
    context: str = "",
) -> list[str]:
    """Return up to `count` image URLs for the `word`+`context` query.

    Under the hood a single query is built (see `_ddg_build_query`), for
    which we first take the vqd token, then the i.js JSON. May raise
    RuntimeError — higher up the stack this is caught and drawn as a load
    error, the carousel stays empty."""
    import urllib.parse as _up
    query = _ddg_build_query(word, context)
    vqd = _ddg_get_vqd(query, timeout=timeout)
    url = (
        DDG_JSON_ENDPOINT +
        "?l=us-en&o=json&q=" + _up.quote(query) +
        "&vqd=" + vqd + "&f=,,,&p=1"
    )
    headers = _ddg_browser_headers({
        "Accept": "application/json, text/plain, */*",
        "Referer": DDG_HTML_ENDPOINT,
        "X-Requested-With": "XMLHttpRequest",
    })
    data = _http_get_json(url, headers, timeout)
    urls: list[str] = []
    try:
        for r in (data.get("results") or []):  # type: ignore[union-attr]
            u = r.get("image") if isinstance(r, dict) else None
            if not u or not isinstance(u, str):
                continue
            urls.append(u)
            if len(urls) >= count:
                break
    except Exception:
        pass
    if not urls:
        raise RuntimeError("ddg: empty results")
    return urls


def _http_get_json(url: str, headers: dict, timeout: int) -> object:
    """Internal GET with JSON decoding. Raises RuntimeError on a
    network/HTTP/parse error — convenient for a worker thread.

    On macOS the Python framework (and sometimes the built-in Anki Python)
    has no root certificates, and any HTTPS request fails with
    CERTIFICATE_VERIFY_FAILED. If that happens — we retry once with
    certificate verification disabled (for an open translator endpoint
    this is an acceptable compromise for the sake of functionality)."""
    import json as _json
    import ssl as _ssl
    import urllib.request as _ur
    import urllib.error as _ue
    req = _ur.Request(url, headers=headers, method="GET")

    def _do_open(ctx):
        with _ur.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read().decode("utf-8", errors="replace")

    try:
        raw = _do_open(None)
    except _ue.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}")
    except _ue.URLError as e:
        reason = str(getattr(e, "reason", e))
        if "CERTIFICATE_VERIFY_FAILED" in reason or "SSL" in reason.upper():
            try:
                unverified = _ssl._create_unverified_context()  # type: ignore[attr-defined]
                raw = _do_open(unverified)
            except Exception as e2:
                raise RuntimeError(f"network: {e2}")
        else:
            raise RuntimeError(f"network: {reason}")
    except Exception as e:
        raise RuntimeError(str(e))

    try:
        return _json.loads(raw)
    except Exception:
        raise RuntimeError("invalid JSON")


def _google_translate_lookup(word: str, src: str, tgt: str, timeout: int) -> dict:
    """We hit the public Google Translate endpoint (used by the `gtx`
    widget and a number of open-source clients, like googletrans). It returns:
    - dt=t  — the main translation
    - dt=bd — dictionary variants with parts of speech

    The endpoint is open, no key, no Cloudflare challenge (unlike
    Reverso), so it works from urllib with a regular User-Agent.

    Returns:
        {
          "main": str,                          # main translation (dt=t)
          "groups": [{"pos": str, "variants": [str, ...]}, ...]
        }
    Where `groups` are dictionary groups by part of speech (if Google returned them).
    """
    import urllib.parse as _up
    q = _up.quote(word, safe="")
    url = (
        "https://translate.googleapis.com/translate_a/single?"
        "client=gtx&sl=" + src + "&tl=" + tgt +
        "&dt=t&dt=bd&q=" + q
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    data = _http_get_json(url, headers, timeout)

    main = ""
    try:
        main = _reverso_strip_tags(str(data[0][0][0] or ""))
    except Exception:
        pass

    groups: list[dict] = []
    try:
        for g in (data[1] or []):
            pos = _reverso_strip_tags(str(g[0] or "")) if len(g) > 0 else ""
            raw_variants = g[1] if len(g) > 1 else []
            variants: list[str] = []
            seen: set[str] = set()
            for v in raw_variants or []:
                vv = _reverso_strip_tags(str(v or ""))
                if vv and vv.lower() not in seen:
                    variants.append(vv)
                    seen.add(vv.lower())
            if variants:
                groups.append({"pos": pos or "", "variants": variants})
    except Exception:
        pass

    return {"main": main, "groups": groups}


def _mymemory_examples(
    word: str,
    src: str,
    tgt: str,
    timeout: int,
    popular: "list[str] | None" = None,
) -> list[dict]:
    """Example segments from the MyMemory translation memory (open endpoint).

    MyMemory is a crowdsourced TM, and "drifted" pairs regularly occur
    there: the source is correct, but the target is already from a neighboring
    segment (like `Zelfs mijn vader.` ↔ `Семья Бергамаски приняла Орфали у себя.`).
    We filter out three layers in a row:

    1. `match` / `quality` — MyMemory's own score (if provided).
       In MyMemory `match` correlates poorly with quality (it's more about
       the "coverage" of the query than the correctness of the pair), so we keep
       the threshold very lax — 0.30; quality is more meaningful, there 60/100.
    2. the length ratio of `source`/`target` — for a normal translation it
       rarely goes beyond 3x, while broken TM pairs almost always differ
       exactly in this (one subtitle is short, the other long).
    3. lexical overlap with the "popular translations" (`popular`,
       the same ones we showed in the POPULAR TRANSLATIONS block). If
       we know that `zelfs → даже, ровно`, then the target sentence
       should contain one of these tokens or its beginning. This is how
       the very case of "nothing in common with the query" is caught.

    We collect two buckets: `prime` (passed all three filters) and `decent`
    (passed 1+2). First we return prime; if there's less than the limit — we top
    up with decent. This is a safeguard: even for exotic words with zero lexical
    match we return something sensible.

    Trivial pairs (target == word) and duplicates are always cut.
    """
    import urllib.parse as _up
    q = _up.quote(word, safe="")
    lp = _up.quote(f"{src}|{tgt}", safe="")
    url = f"https://api.mymemory.translated.net/get?q={q}&langpair={lp}"
    headers = {
        "User-Agent": "Mozilla/5.0 (AnkiAddon)",
        "Accept": "application/json",
    }
    data = _http_get_json(url, headers, timeout)

    # --- Normalize the popular list into a set of "keys": lower-case + the first
    # 5 characters. 5 (not 4) gives fewer false-positive collisions
    # for Russian/Ukrainian/Polish, where unrelated words share many common
    # 3-4-letter beginnings (a classic example — "семья" matched
    # "семь" from a popular variant like "семь раз" and dragged in TMX junk like
    # "Семья Бергамаски...").
    pop_tokens: set[str] = set()
    for p in (popular or []):
        for tok in re.findall(r"[^\W\d_]+", p.lower(), flags=re.UNICODE):
            if len(tok) >= 4:
                pop_tokens.add(tok[:5])

    def _has_lexical_overlap(target_text: str) -> bool:
        if not pop_tokens:
            # No popular ones — the filter is off, everything counts as "passed".
            return True
        for tok in re.findall(r"[^\W\d_]+", target_text.lower(), flags=re.UNICODE):
            if len(tok) >= 4 and tok[:5] in pop_tokens:
                return True
        return False

    # The prefix of the word itself is the most reliable sign that the source
    # segment is really about this word and not something else (MyMemory's TM
    # tables can be dirty: the index matches by roots, but the translation is from
    # a neighboring pair). We check 5 characters (for short words — the whole word).
    word_norm = (word or "").strip().lower()
    word_key = word_norm[:5] if len(word_norm) >= 5 else word_norm

    def _source_mentions_word(s_text: str) -> bool:
        if not word_key:
            return True
        for tok in re.findall(r"[^\W\d_]+", s_text.lower(), flags=re.UNICODE):
            if tok == word_norm:
                return True
            if len(word_key) >= 4 and tok.startswith(word_key):
                return True
        return False

    def _length_ok(s_text: str, t_text: str) -> bool:
        ls, lt = len(s_text), len(t_text)
        if ls == 0 or lt == 0:
            return False
        ratio = ls / lt if ls >= lt else lt / ls
        return ratio <= 3.0

    def _quality_ok(entry: dict) -> bool:
        # match (0..1) — heuristic closeness of the segment to the query;
        # quality (0..100) — trust in the pair itself. If the field exists — we honor it.
        try:
            m = float(entry.get("match") or 0.0)
            if m and m < 0.30:
                return False
        except Exception:
            pass
        try:
            qraw = entry.get("quality")
            qv = float(qraw) if qraw not in (None, "") else 0.0
            if qv and qv < 60.0:
                return False
        except Exception:
            pass
        return True

    prime: list[dict] = []
    decent: list[dict] = []
    seen_src: set[str] = set()
    try:
        matches = data.get("matches") if isinstance(data, dict) else None
        for m in (matches or []):
            s = _reverso_strip_tags(str(m.get("segment") or ""))
            t = _reverso_strip_tags(str(m.get("translation") or ""))
            if not s or not t:
                continue
            if " " not in s or len(s) < 8 or len(t) < 3:
                continue
            if s.strip().lower() == word.lower():
                continue
            key = s.lower()
            if key in seen_src:
                continue
            seen_src.add(key)
            if not _quality_ok(m):
                continue
            if not _length_ok(s, t):
                continue
            # Hard cutoff of TM junk: if the source segment doesn't even
            # contain the root of the studied word — it's definitely an alien pair,
            # whatever its match-score. For short words (<5 characters)
            # a match by word form also counts; for long ones — by the
            # 5-character prefix.
            if not _source_mentions_word(s):
                continue
            item = {"source": s, "target": t}
            if _has_lexical_overlap(t):
                prime.append(item)
            else:
                decent.append(item)
            if len(prime) >= REVERSO_MAX_EXAMPLES:
                break
    except Exception:
        pass

    out: list[dict] = list(prime[:REVERSO_MAX_EXAMPLES])
    if len(out) < REVERSO_MAX_EXAMPLES:
        out.extend(decent[: REVERSO_MAX_EXAMPLES - len(out)])
    return out


def _reverso_query(
    word: str,
    source_lang: str = REVERSO_DEFAULT_SRC,
    target_lang: str = REVERSO_DEFAULT_TGT,
    timeout: int = REVERSO_TIMEOUT,
) -> dict:
    """Look up a word in open sources (Google Translate + MyMemory).

    The name is kept historical (`_reverso_query`) for compatibility with
    the worker and the UI; in reality the public endpoints of Google
    Translate and MyMemory are used, because Reverso Context is behind a
    Cloudflare bot-challenge and returns 403 from a regular Python client.

    Returns:
        {
          "word", "source_lang", "target_lang",
          "translations": [str, ...],   # the first is main, the rest are variants
          "examples":     [{"source": str, "target": str}, ...],
        }

    Raises RuntimeError on a network/parse error.
    """
    word = (word or "").strip()
    if not word:
        raise RuntimeError("empty word")
    src = _reverso_normalize_lang(source_lang, REVERSO_DEFAULT_SRC)
    tgt = _reverso_normalize_lang(target_lang, REVERSO_DEFAULT_TGT)
    if src == tgt:
        raise RuntimeError("source == target")

    groups: list[dict] = []          # [{"pos": str, "variants": [str, ...]}]
    flat: list[str] = []             # flat list (for backward compatibility)
    examples: list[dict] = []
    errors: list[str] = []
    seen_flat: set[str] = set()

    def _push(pos: str, variants: list[str]) -> None:
        """Add a group, deduplicating variants against everything already accumulated."""
        fresh: list[str] = []
        seen_local: set[str] = set()
        for v in variants:
            key = v.strip().lower()
            if not key or key in seen_flat or key in seen_local:
                continue
            fresh.append(v.strip())
            seen_local.add(key)
        if not fresh:
            return
        # If a group with this POS already existed — extend it, otherwise create a new one.
        for g in groups:
            if g["pos"] == pos:
                for v in fresh:
                    g["variants"].append(v)
                    flat.append(v)
                    seen_flat.add(v.lower())
                return
        groups.append({"pos": pos, "variants": list(fresh)})
        for v in fresh:
            flat.append(v)
            seen_flat.add(v.lower())

    # 1) Direct query src → tgt.
    direct_main = ""
    try:
        res = _google_translate_lookup(word, src, tgt, timeout)
        direct_main = res.get("main") or ""
        if direct_main:
            _push("", [direct_main])
        for g in res.get("groups") or []:
            _push(g.get("pos") or "", g.get("variants") or [])
    except Exception as e:
        errors.append(f"google: {e}")

    # 2) If there are <2 dictionary groups (typical for rare pairs like nl→ru,
    #    where Google returns only one sense), we reinforce via an English
    #    bridge — taking ONE OR TWO variants from each English group
    #    (POS), translating them back to the target language and taking the main
    #    meaning. This is the key to homonyms: for "weer" it gives both "снова" (adverb)
    #    and "погода/защита" (noun).
    if src != "en" and tgt != "en" and len([g for g in groups if g["pos"]]) < 2:
        try:
            bridge = _google_translate_lookup(word, src, "en", timeout)
            bridge_groups = bridge.get("groups") or []
            # If bd to en is empty — at least take the main translation as the bridge word.
            if not bridge_groups and bridge.get("main"):
                bridge_groups = [{"pos": "", "variants": [bridge["main"]]}]
            for bg in bridge_groups:
                pos = bg.get("pos") or ""
                # We take the first two bridge variants from EACH group, so as
                # not to skew the result toward one part of speech.
                for b in (bg.get("variants") or [])[:2]:
                    try:
                        back = _google_translate_lookup(b, "en", tgt, timeout)
                    except Exception:
                        continue
                    back_main = back.get("main") or ""
                    if back_main:
                        _push(pos, [back_main])
                    # We take variants only from the FIRST back subgroup
                    # (the main meaning of the bridge word) — otherwise one bridge
                    # word grabs the limit through its POS branches,
                    # and we never reach the adverb/adjective of other meanings.
                    back_groups = back.get("groups") or []
                    if back_groups:
                        _push(pos, (back_groups[0].get("variants") or [])[:2])
                if len(flat) >= REVERSO_MAX_TRANSLATIONS:
                    break
        except Exception as e:
            errors.append(f"bridge: {e}")

    # 3) MyMemory — usage examples. `flat` (popular translations)
    #    goes there as a lexical filter — it cuts off TM failures.
    try:
        mm = _mymemory_examples(word, src, tgt, timeout, popular=flat)
        for ex in mm:
            examples.append(ex)
            if len(examples) >= REVERSO_MAX_EXAMPLES:
                break
    except Exception as e:
        errors.append(f"mymemory: {e}")

    if not flat and not examples:
        raise RuntimeError("; ".join(errors) if errors else "no data")

    # Bring groups to the limit: no more than REVERSO_MAX_TRANSLATIONS values
    # in total, but preserving the distribution across POS (3 from each group).
    trimmed: list[dict] = []
    total = 0
    for g in groups:
        vs = g["variants"][:3]
        if not vs:
            continue
        remain = REVERSO_MAX_TRANSLATIONS - total
        if remain <= 0:
            break
        vs = vs[:remain]
        trimmed.append({"pos": g["pos"], "variants": vs})
        total += len(vs)

    return {
        "word": word,
        "source_lang": src,
        "target_lang": tgt,
        "translations": flat[:REVERSO_MAX_TRANSLATIONS],
        "groups": trimmed,
        "examples": examples[:REVERSO_MAX_EXAMPLES],
    }


def _safe_release_worker(thread, *, wait_ms: int = 3000, terminate_wait_ms: int = 500) -> None:
    """Safely stop a QThread without `qFatal: Destroyed while running`.

    The crash scenario we're fixing: a worker is sitting on a blocking network
    read (SSL read), the user closes the dialog, Qt destroys the
    panel → its child-QThread → `~QThread()` sees `isRunning()` and
    abort()s the whole process. Therefore:

    1. we toggle the cooperative stop flag, if any (`request_stop`);
    2. we call `quit()` to interrupt the thread's event loop (just in
       case, our run() is custom, without exec());
    3. we wait for completion up to `wait_ms`. A Python SSL socket doesn't
       react to an interrupt immediately — we give it up to 3 seconds to finish
       the current operation;
    4. if the thread is still alive — we call `terminate()` (crude, but
       safe: the OS removes the thread, and we then wait for it via
       `wait`). This is the last resort and a hard kill, but it's better
       than a SIGABRT of the whole application.
    """
    if thread is None:
        return
    try:
        if hasattr(thread, "request_stop"):
            try:
                thread.request_stop()
            except Exception:
                pass
        try:
            thread.quit()
        except Exception:
            pass
        try:
            if thread.isRunning():
                thread.wait(int(wait_ms))
        except Exception:
            pass
        try:
            if thread.isRunning():
                thread.terminate()
                thread.wait(int(terminate_wait_ms))
        except Exception:
            pass
    except Exception:
        pass


class _ReversoWorker(QThread):
    """Background QThread running `_reverso_query` off the UI thread, so
    Anki doesn't freeze while waiting for the remote server."""

    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, word: str, source_lang: str, target_lang: str, parent=None) -> None:
        super().__init__(parent)
        self._word = word
        self._src = source_lang
        self._tgt = target_lang

    def run(self) -> None:  # type: ignore[override]
        try:
            res = _reverso_query(self._word, self._src, self._tgt)
            self.finished_ok.emit(res)
        except Exception as e:
            try:
                self.failed.emit(str(e) or "unknown error")
            except Exception:
                pass


class ReversoPanel(QWidget):
    """Embedded reference panel — lives inside the review dialog (left half
    of the top row) next to the AI card preview (right half). Shows
    objective translations + example sentences so the user has a non-LLM
    reference right next to the AI-generated card."""

    def __init__(
        self,
        parent: QWidget,
        word: str,
        source_lang: str = REVERSO_DEFAULT_SRC,
        target_lang: str = REVERSO_DEFAULT_TGT,
    ) -> None:
        super().__init__(parent)
        self._word = (word or "").strip()
        self._src = source_lang
        self._tgt = target_lang
        self._worker: "Optional[_ReversoWorker]" = None

        try:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        except Exception:
            pass

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        header = QLabel(
            f"<b>{_t('reverso_title')}</b> &nbsp;·&nbsp; <code>{html.escape(self._word)}</code>",
            self,
        )
        try:
            header.setTextFormat(Qt.TextFormat.RichText)
        except Exception:
            pass
        v.addWidget(header)

        # --- Quick language selection right in the panel --------------------
        # So that the addon can be used not only for nl→ru, but also
        # for any pairs without going into settings. The choice is saved in config.
        lang_row = QHBoxLayout()
        lang_row.setContentsMargins(0, 0, 0, 0)
        lang_row.setSpacing(4)
        self.src_combo = QComboBox(self)
        self.tgt_combo = QComboBox(self)
        for combo in (self.src_combo, self.tgt_combo):
            for code, label in REVERSO_LANG_CHOICES:
                combo.addItem(f"{label} ({code})", code)
        self._select_combo(self.src_combo, self._src)
        self._select_combo(self.tgt_combo, self._tgt)
        _apply_secondary_font(self.src_combo, delta_pt=-1)
        _apply_secondary_font(self.tgt_combo, delta_pt=-1)
        arrow = QLabel("→", self)
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow.setMinimumWidth(14)
        lang_row.addWidget(self.src_combo, 1)
        lang_row.addWidget(arrow, 0)
        lang_row.addWidget(self.tgt_combo, 1)
        # "Refresh" button — MyMemory/Google may return noise from
        # translation memory; a quick repeat query often pulls out the
        # correct set of examples (their cache refreshes, and our filters
        # are now stochastic due to the order of matches).
        self.refresh_btn = QPushButton("↻", self)
        self.refresh_btn.setToolTip(_t("reverso_refresh_tooltip"))
        self.refresh_btn.setFixedWidth(28)
        _apply_secondary_font(self.refresh_btn, delta_pt=-1)
        # autoDefault=False — otherwise Enter in any QLineEdit/QPlainTextEdit
        # inside AiCardReviewDialog would automatically click this button
        # as the "default", or (worse) trigger the dialog's save button.
        try:
            self.refresh_btn.setAutoDefault(False)
            self.refresh_btn.setDefault(False)
        except Exception:
            pass
        qconnect(self.refresh_btn.clicked, self._on_refresh_clicked)
        lang_row.addWidget(self.refresh_btn, 0)
        v.addLayout(lang_row)
        qconnect(self.src_combo.currentIndexChanged, self._on_lang_changed)
        qconnect(self.tgt_combo.currentIndexChanged, self._on_lang_changed)

        self.status_lbl = QLabel(_t("reverso_loading"), self)
        self.status_lbl.setStyleSheet("color: #7a7a7a;")
        v.addWidget(self.status_lbl)

        self.body = QTextBrowser(self)
        self.body.setOpenExternalLinks(True)
        self.body.setVisible(False)
        v.addWidget(self.body, 1)

        self.retry_btn = QPushButton("Retry", self)
        self.retry_btn.setVisible(False)
        try:
            self.retry_btn.setAutoDefault(False)
            self.retry_btn.setDefault(False)
        except Exception:
            pass
        qconnect(self.retry_btn.clicked, self._start)
        v.addWidget(self.retry_btn)

        self._start()

    # --- helpers -------------------------------------------------------
    @staticmethod
    def _select_combo(combo: "QComboBox", code: str) -> None:
        idx = 0
        for i in range(combo.count()):
            if combo.itemData(i) == code:
                idx = i
                break
        try:
            combo.setCurrentIndex(idx)
        except Exception:
            pass

    def _on_refresh_clicked(self) -> None:
        """Manual restart of the query. Useful when MyMemory returned bad
        matches (TM noise); a repeated call usually gives a different order
        of matches and our filters successfully pull out normal examples."""
        self._start()

    def _on_lang_changed(self, *_args) -> None:
        try:
            new_src = str(self.src_combo.currentData() or self._src)
            new_tgt = str(self.tgt_combo.currentData() or self._tgt)
        except Exception:
            return
        if new_src == self._src and new_tgt == self._tgt:
            return
        if new_src == new_tgt:
            self.status_lbl.setVisible(True)
            self.status_lbl.setText(_t("reverso_error", err="source == target"))
            return
        self._src = new_src
        self._tgt = new_tgt
        # Save the choice in config so that next time it opens the same way.
        try:
            from aqt import mw as _mw
            cfg = _mw.addonManager.getConfig(__name__) or {}
            cfg["reverso_source_lang"] = self._src
            cfg["reverso_target_lang"] = self._tgt
            _mw.addonManager.writeConfig(__name__, cfg)
        except Exception:
            pass
        self._start()

    # --- lifecycle -----------------------------------------------------
    def _start(self) -> None:
        self.body.setVisible(False)
        self.retry_btn.setVisible(False)
        self.status_lbl.setVisible(True)
        self.status_lbl.setText(_t("reverso_loading"))
        try:
            self.refresh_btn.setEnabled(False)
        except Exception:
            pass
        if self._worker is not None:
            _safe_release_worker(self._worker)
        self._worker = _ReversoWorker(self._word, self._src, self._tgt, None)
        try:
            qconnect(self._worker.finished, self._worker.deleteLater)
        except Exception:
            pass
        qconnect(self._worker.finished_ok, self._on_success)
        qconnect(self._worker.failed, self._on_failed)
        self._worker.start()

    def stop_worker(self) -> None:
        _safe_release_worker(self._worker)

    def closeEvent(self, e) -> None:  # type: ignore[override]
        self.stop_worker()
        super().closeEvent(e)

    # --- slots ---------------------------------------------------------
    def _on_success(self, data: dict) -> None:
        word = str(data.get("word") or self._word)
        translations = list(data.get("translations") or [])
        groups = list(data.get("groups") or [])
        examples = list(data.get("examples") or [])
        try:
            self.refresh_btn.setEnabled(True)
        except Exception:
            pass
        if not translations and not groups and not examples:
            self.status_lbl.setText(_t("reverso_empty", word=word))
            return
        self.status_lbl.setVisible(False)
        self.body.setVisible(True)
        self.body.setHtml(self._render_html(word, translations, groups, examples))

    def _on_failed(self, err: str) -> None:
        self.status_lbl.setText(_t("reverso_error", err=err))
        self.retry_btn.setVisible(True)
        try:
            self.refresh_btn.setEnabled(True)
        except Exception:
            pass

    # --- render --------------------------------------------------------
    def _render_html(
        self,
        word: str,
        translations: list[str],
        groups: list[dict],
        examples: list[dict],
    ) -> str:
        """HTML for QTextBrowser.

        Important: QTextBrowser uses a very stripped-down Qt HTML/CSS engine.
        NOT working: display: inline-block, border-radius, flex/grid, padding
        on inline elements. Working: color, font-weight, links, <ul>/<li>,
        <table>, <p>, <br>, block indents via margin. Therefore the chips
        are re-laid-out as plain text "variant, variant, variant" with a
        part-of-speech heading and bold highlighting of the words themselves.
        """
        esc = html.escape
        parts: list[str] = [
            "<html><head><style>"
            "body { font-family: -apple-system, 'Segoe UI', sans-serif; font-size: 12px; color: #ddd; }"
            "h3 { margin: 10px 0 4px; font-size: 11px; color: #8ca3c0; text-transform: uppercase; letter-spacing: .06em; }"
            "p { margin: 0 0 6px; line-height: 1.4; }"
            ".pos { color: #7d8ea2; font-style: italic; margin-right: 4px; }"
            ".var { color: #c7d7ff; font-weight: 600; }"
            ".sep { color: #5a6778; }"
            ".ex { margin: 0 0 8px; }"
            ".ex .src { color: #e8e8e8; }"
            ".ex .tgt { color: #9aa6b4; }"
            "</style></head><body>"
        ]

        if groups:
            parts.append(f"<h3>{esc(_t('reverso_translations_header'))}</h3>")
            for g in groups:
                pos = g.get("pos") or ""
                variants = g.get("variants") or []
                if not variants:
                    continue
                chunk = "<span class='sep'>, </span>".join(
                    f"<span class='var'>{esc(v)}</span>" for v in variants
                )
                if pos:
                    parts.append(f"<p><span class='pos'>{esc(pos)}:</span> {chunk}</p>")
                else:
                    parts.append(f"<p>{chunk}</p>")
        elif translations:
            parts.append(f"<h3>{esc(_t('reverso_translations_header'))}</h3>")
            chunk = "<span class='sep'>, </span>".join(
                f"<span class='var'>{esc(t)}</span>" for t in translations
            )
            parts.append(f"<p>{chunk}</p>")

        if examples:
            parts.append(f"<h3>{esc(_t('reverso_examples_header'))}</h3>")
            for ex in examples:
                src_s = esc(ex.get('source', ''))
                tgt_s = esc(ex.get('target', ''))
                parts.append(
                    "<div class='ex'>"
                    f"<div class='src'>{src_s}</div>"
                    f"<div class='tgt'>{tgt_s}</div>"
                    "</div>"
                )

        parts.append("</body></html>")
        return "".join(parts)


class _CollapsibleSection(QWidget):
    """Collapsible block: a heading button + content. The content has a
    deterministic size, so when toggled the layout doesn't drift —
    Qt simply shows/hides a block of known height."""

    def __init__(self, title: str, parent: "QWidget | None" = None, expanded: bool = False) -> None:
        super().__init__(parent)
        try:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        except Exception:
            pass
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self._toggle = QPushButton(self)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setFlat(True)
        try:
            self._toggle.setStyleSheet(
                "QPushButton { text-align: left; padding: 3px 6px; font-weight: 600; }"
            )
        except Exception:
            pass
        self._title = title
        self._content = QWidget(self)
        try:
            self._content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        except Exception:
            pass
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(8, 4, 0, 4)
        self._content_layout.setSpacing(4)
        self._content.setVisible(expanded)
        v.addWidget(self._toggle)
        v.addWidget(self._content)
        self._update_label()
        qconnect(self._toggle.toggled, self._on_toggled)

    def _update_label(self) -> None:
        arrow = "▼" if self._toggle.isChecked() else "▶"
        self._toggle.setText(f"{arrow}  {self._title}")

    def _on_toggled(self, checked: bool) -> None:
        self._content.setVisible(checked)
        self._update_label()
        # The owner window has a fixed size, while card_preview is Expanding
        # vertically: it will take/give back the freed height itself. There's
        # nothing to recalculate.

    def add_widget(self, w: "QWidget") -> None:
        self._content_layout.addWidget(w)

    def add_layout(self, lay) -> None:
        self._content_layout.addLayout(lay)

    def set_expanded(self, expanded: bool) -> None:
        self._toggle.setChecked(expanded)

    def is_expanded(self) -> bool:
        try:
            return bool(self._toggle.isChecked())
        except Exception:
            return False

    @property
    def toggled(self):  # convenience — lets you subscribe to "section.toggled"
        return self._toggle.toggled


class _FixedLinesEdit(QPlainTextEdit):
    """QPlainTextEdit with a deterministic height = N lines by fontMetrics.

    Why: the previous auto-height (by the document's contentsHeight) was
    recalculated on every resize/show, and when expanding collapsible sections
    the viewport width wasn't final yet — hence the "floating" layout. Here the
    height is known once at creation time and depends neither on the text nor on
    the width. If the text doesn't fit — an internal scroll (the full text is
    visible in the card preview above anyway).
    """

    def __init__(self, parent: "QWidget | None" = None, lines: int = 3) -> None:
        super().__init__(parent)
        self._lines = max(1, int(lines))
        try:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        except Exception:
            pass
        try:
            self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        except Exception:
            try:
                self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
            except Exception:
                pass
        self._apply_fixed_height()

    def set_visible_lines(self, lines: int) -> None:
        self._lines = max(1, int(lines))
        self._apply_fixed_height()

    def _apply_fixed_height(self) -> None:
        try:
            fm = self.fontMetrics()
            line_h = fm.lineSpacing()
            margins = self.contentsMargins()
            frame = 2 * self.frameWidth()
            doc_margin = int(self.document().documentMargin()) * 2
            h = line_h * self._lines + margins.top() + margins.bottom() + frame + doc_margin + 4
            self.setFixedHeight(int(h))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Canvas / sidebar widgets used by AiCardReviewDialog (new layout).
# ---------------------------------------------------------------------------


class _CanvasPreview(QScrollArea):
    """Central card preview (the "Canvas" pattern).

    Behavior:
    - The content is a QLabel with wordWrap=True and HTML rendering.
    - The QLabel always occupies the FULL width of the visible area (symmetric
      left/right padding) — this provides a layout without alignment flags,
      vertical centering is implemented via stretch spacers.
    - If the text fits entirely — it's centered vertically (top+bottom
      stretch), inside the label alignment is centered.
    - If it doesn't fit — the top stretch is removed, the label is pinned
      to the top, a vertical scroll appears. The outer size doesn't jump.
    """

    _PAD_H = 28  # horizontal padding (logical px)
    _PAD_V = 20  # vertical padding

    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._host = QWidget(self)
        self._host_layout = QVBoxLayout(self._host)
        self._host_layout.setContentsMargins(self._PAD_H, self._PAD_V, self._PAD_H, self._PAD_V)
        self._host_layout.setSpacing(0)

        # Label — without alignment flags in the layout, so it takes all the
        # available width (otherwise wordWrap fires by sizeHint and the right
        # part stays empty — exactly the bug that existed before).
        self._label = QLabel(self._host)
        self._label.setWordWrap(True)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._label.setMinimumWidth(0)

        # Two stretch spacers: by changing their factor we get centering or top.
        self._host_layout.addStretch(1)   # index 0
        self._host_layout.addWidget(self._label)  # index 1 — always without alignment
        self._host_layout.addStretch(1)   # index 2

        self.setWidget(self._host)
        self._centered = False
        self._top_only = False  # compact mode pins the card to the top (no centering)

    def set_top_only(self, value: bool) -> None:
        self._top_only = bool(value)
        self._centered = False
        self._refresh_alignment()

    def set_html(self, html_text: str) -> None:
        self._label.setText(html_text or "")
        try:
            from aqt.qt import QTimer
            QTimer.singleShot(0, self._refresh_alignment)
        except Exception:
            self._refresh_alignment()

    def resizeEvent(self, e) -> None:  # type: ignore[override]
        super().resizeEvent(e)
        self._refresh_alignment()

    def _refresh_alignment(self) -> None:
        try:
            if self._top_only:
                # Compact: always pin to the top — no centering, no big empty gap.
                self._host_layout.setStretch(0, 0)
                self._host_layout.setStretch(2, 1)
                self._label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                return
            vh = self.viewport().height()
            # The label's sizeHint depends on its width (wordWrap): we set the
            # width explicitly, so that heightForWidth gives a correct value.
            inner_w = max(0, self.viewport().width() - 2 * self._PAD_H)
            needed_h = self._label.heightForWidth(inner_w) if inner_w > 0 else self._label.sizeHint().height()
            content_h = int(needed_h) + 2 * self._PAD_V
            fits = content_h <= vh
            if fits and not self._centered:
                self._centered = True
                self._host_layout.setStretch(0, 1)
                self._host_layout.setStretch(2, 1)
                self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            elif not fits and self._centered:
                self._centered = False
                self._host_layout.setStretch(0, 0)
                self._host_layout.setStretch(2, 1)
                self._label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            elif not fits:
                # Right after the first render the state is already "top" — let's make sure.
                self._host_layout.setStretch(0, 0)
                self._host_layout.setStretch(2, 1)
                self._label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            else:
                self._host_layout.setStretch(0, 1)
                self._host_layout.setStretch(2, 1)
                self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        except Exception:
            pass


class _ImagesWorker(QThread):
    """Background thread: searches for N links via DDG and downloads all images
    (bytes). Emits:
      - `progress(done, total)` — after each downloaded file
      - `finished_ok(list[bytes])` — a non-empty list of successfully downloaded ones
      - `failed(str)` — if the search stage failed or no image was
        downloaded."""

    progress = pyqtSignal(int, int)
    finished_ok = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(
        self,
        word: str,
        count: int = DDG_IMAGES_COUNT,
        parent=None,
        context: str = "",
    ) -> None:
        super().__init__(parent)
        self._word = word
        self._context = context
        self._count = count
        self._stop_flag = False

    def request_stop(self) -> None:
        self._stop_flag = True

    def run(self) -> None:  # type: ignore[override]
        try:
            urls = _ddg_image_urls(self._word, self._count, DDG_TIMEOUT, context=self._context)
        except Exception as e:
            try:
                self.failed.emit(f"search: {e}")
            except Exception:
                pass
            return
        results: list[bytes] = []
        total = len(urls)
        img_headers = _ddg_browser_headers({"Accept": "image/*,*/*;q=0.8"})
        for i, u in enumerate(urls):
            if self._stop_flag:
                break
            try:
                data = _http_get_bytes(u, img_headers, timeout=DDG_TIMEOUT)
                if data and len(data) > 256:  # minimal sanity check
                    results.append(data)
            except Exception:
                # Silently skip a broken/unavailable image, don't fail the whole batch.
                pass
            try:
                self.progress.emit(i + 1, total)
            except Exception:
                pass
        if not results:
            try:
                self.failed.emit("no images downloaded")
            except Exception:
                pass
            return
        try:
            self.finished_ok.emit(results)
        except Exception:
            pass


class ImagesPanel(QFrame):
    """Image carousel for the dialog's right panel.

    Features:
    - Asynchronous search of 5 relevant images on DuckDuckGo and their download
      in the background (`_ImagesWorker`), the UI doesn't freeze.
    - Scrolling "◀ / ▶" through the five downloaded ones.
    - Ctrl+V (or the "Paste" button) — paste an image from the system
      clipboard; the pasted image is added at the current position
      and immediately becomes active. It can be attached to the card.
    - "Refresh" button — restart the search (useful when replacing the word).
    - `get_current_image()` — the bytes of the image currently shown.
    """

    def __init__(self, parent: "QWidget | None" = None, word: str = "", context: str = "") -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # Minimum — a 220×220 square plus room for the controls
        # and the refine field (≈ +80 pt). We render the image 1:1 inside
        # _img_label, the label height is recalculated in resizeEvent.
        self.setMinimumSize(220, 300)

        # Data.
        self._items: list[bytes] = []  # source bytes, one per image
        self._idx: int = 0
        self._word: str = (word or "").strip()
        # The context is needed so the DDG query is built more precisely —
        # "word + topic" gives dramatically better images than just the
        # word by itself (fewer logos/icons/vectors).
        self._context: str = (context or "").strip()
        # Manual refinement from the user (by analogy with the refine prompt
        # for the card). If set — it completely replaces the auto-context
        # from back when building the DDG query.
        self._refine: str = ""
        self._worker: "Optional[_ImagesWorker]" = None

        # --- UI ---------------------------------------------------------
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        self._img_label = QLabel(self)
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setStyleSheet("color: #9a9a9a;")
        # Height = width is set in resizeEvent (a square).
        self._img_label.setMinimumHeight(200)
        self._img_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._img_label.setText(_t("images_idle"))
        root.addWidget(self._img_label, 0)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        self.prev_btn = QPushButton("◀", self)
        self.next_btn = QPushButton("▶", self)
        self.counter_lbl = QLabel("— / —", self)
        self.counter_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.counter_lbl.setMinimumWidth(40)
        self.paste_btn = QPushButton(_t("images_paste"), self)
        self.paste_btn.setToolTip(_t("images_paste_tooltip"))
        # "No image" — for when nothing fits; the card is added without a picture.
        self.none_btn = QPushButton(_t("images_none_btn"), self)
        self.none_btn.setCheckable(True)
        self.none_btn.setToolTip(_t("images_none_tooltip"))
        qconnect(self.none_btn.toggled, self._on_none_toggled)
        self._no_image = False
        for b in (self.prev_btn, self.next_btn, self.paste_btn, self.none_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            _apply_secondary_font(b, delta_pt=-1)
            # CRITICAL: inside a QDialog any QPushButton has autoDefault=True
            # by default, and Enter in any QLineEdit/QPlainTextEdit
            # starts clicking the "default" button — the dialog ends up going to
            # accept() and closing. These buttons must never
            # react to Enter/Return; let the dialog itself decide what
            # to do with Enter.
            try:
                b.setAutoDefault(False)
                b.setDefault(False)
            except Exception:
                pass
        _apply_secondary_font(self.counter_lbl, delta_pt=-1)
        controls.addWidget(self.prev_btn)
        controls.addWidget(self.counter_lbl, 1)
        controls.addWidget(self.next_btn)
        controls.addSpacing(6)
        controls.addWidget(self.paste_btn)
        controls.addWidget(self.none_btn)
        root.addLayout(controls)

        # --- Query refinement field ------------------------------------
        # By analogy with the card's refine prompt: if the auto-hint
        # (word + the first line of back) gives irrelevant junk
        # (especially for abstract words like "weer/погода"), you can
        # type any refinement, for example "снова абстрактно", and DDG
        # rebuilds the query: "{word} {refine} -logo -icon -vector".
        refine_row = QHBoxLayout()
        refine_row.setContentsMargins(0, 0, 0, 0)
        refine_row.setSpacing(4)
        self.refine_edit = QLineEdit(self)
        self.refine_edit.setPlaceholderText(_t("images_refine_placeholder"))
        self.refine_edit.setClearButtonEnabled(True)
        self.refine_edit.setToolTip(_t("images_refine_tooltip"))
        _apply_secondary_font(self.refine_edit, delta_pt=-1)
        self.refine_go_btn = QPushButton("🔍", self)
        self.refine_go_btn.setFixedWidth(32)
        self.refine_go_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _apply_secondary_font(self.refine_go_btn, delta_pt=-1)
        # See the comment above: without this, Enter in refine_edit activates
        # the dialog's default button (Save) and AiCardReviewDialog closes
        # exactly at the moment the user just wanted to run the search.
        try:
            self.refine_go_btn.setAutoDefault(False)
            self.refine_go_btn.setDefault(False)
        except Exception:
            pass
        refine_row.addWidget(self.refine_edit, 1)
        refine_row.addWidget(self.refine_go_btn, 0)
        root.addLayout(refine_row)

        qconnect(self.prev_btn.clicked, self._show_prev)
        qconnect(self.next_btn.clicked, self._show_next)
        qconnect(self.paste_btn.clicked, self._paste_from_clipboard)
        qconnect(self.refine_edit.returnPressed, self._apply_refine)
        qconnect(self.refine_go_btn.clicked, self._apply_refine)

        # Ctrl+V/Cmd+V — we intercept at the widget level (WidgetShortcut),
        # so as not to conflict with keys in the editor fields.
        for seq in ("Ctrl+V", "Meta+V"):
            try:
                sc = QShortcut(QKeySequence(seq), self)
                sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                qconnect(sc.activated, self._paste_from_clipboard)
            except Exception:
                pass

        self._update_controls_enabled()

        if self._word:
            self._restart_search()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_word(self, word: str, context: str = "") -> None:
        """Change the query and restart the search. If the word matches the
        already loaded one and there's something — we don't reload.

        `context` is an optional hint (translation/topic/back field), it
        goes into the DDG query as "word context -logo -icon -vector" and
        sharply increases the relevance of the results."""
        w = (word or "").strip()
        c = (context or "").strip()
        if not w:
            return
        if w == self._word and c == self._context and self._items:
            return
        self._word = w
        self._context = c
        self._restart_search()

    def set_context(self, context: str) -> None:
        """Update the context without changing the word itself. If the context
        actually changed — restart the search to pick up the new topic."""
        c = (context or "").strip()
        if c == self._context:
            return
        self._context = c
        if self._word:
            self._restart_search()

    def _on_none_toggled(self, checked: bool) -> None:
        """When 'No image' is on, the card is added without a picture; the carousel
        is visually dimmed to make the choice obvious."""
        self._no_image = bool(checked)
        try:
            self._img_label.setEnabled(not checked)
            self.prev_btn.setEnabled(not checked)
            self.next_btn.setEnabled(not checked)
            self.paste_btn.setEnabled(not checked)
        except Exception:
            pass

    def get_current_image(self) -> "Optional[bytes]":
        """The bytes of the image currently displayed. None — if nothing
        is loaded/pasted, or the user chose 'No image'."""
        if getattr(self, "_no_image", False):
            return None
        if not self._items:
            return None
        if not (0 <= self._idx < len(self._items)):
            return None
        return self._items[self._idx]

    def stop_worker(self) -> None:
        """Stop the background thread (the dialog calls this on close).

        IMPORTANT about the crash: previously `wait(200)` was not enough — a Python
        SSL-read blocks waiting for the DDG/CDN image response and doesn't react to
        `quit()` instantly. If the dialog was closing at that moment, Qt
        tried to delete ImagesPanel → its child-QThread → `~QThread` saw
        `isRunning()` and called `qFatal`/abort of the whole Anki.

        The new implementation delegates stopping to `_safe_release_worker`,
        which waits up to 3 seconds and, if necessary, does a
        `terminate()`. Also, workers are now created WITHOUT a parent,
        so that Qt doesn't try to delete them cascadingly — we manage
        their lifecycle ourselves + `finished → deleteLater`.
        """
        _safe_release_worker(self._worker)

    def closeEvent(self, e) -> None:  # type: ignore[override]
        # A safeguard: even if the dialog for some reason doesn't call
        # stop_worker itself, we won't let Qt destroy a running QThread.
        self.stop_worker()
        super().closeEvent(e)

    # ------------------------------------------------------------------
    # Search lifecycle
    # ------------------------------------------------------------------
    def _apply_refine(self) -> None:
        """Take the text from the refine field and restart the search.
        An empty string = reset to the auto-context from back."""
        try:
            text = self.refine_edit.text().strip()
        except Exception:
            text = ""
        # If refine matches what's already applied — we do nothing;
        # but if both are empty (both _refine and text), and there are already
        # images, we also don't touch it, so as not to hit DDG again needlessly.
        if text == self._refine:
            if not text and self._items:
                return
        self._refine = text
        self._restart_search()

    def _restart_search(self) -> None:
        self.stop_worker()
        self._items = []
        self._idx = 0
        self._render_current()
        self._img_label.setPixmap(QPixmap())  # type: ignore[arg-type]
        self._img_label.setText(_t("images_loading"))
        self._update_controls_enabled()
        if not self._word:
            self._img_label.setText(_t("images_idle"))
            return
        try:
            # Refine takes priority: the user explicitly said they want
            # to search not by the auto-hint from back, but by their refinement.
            effective_ctx = self._refine or self._context
            # parent=None is intentional: the worker must NOT be a Qt child of the
            # panel, otherwise when the panel is destroyed Qt will cascadingly delete
            # the running thread and crash with qFatal. We manage the lifecycle
            # ourselves: finished → deleteLater (Qt destroys the object safely in
            # the event loop), and if the worker hasn't finished yet —
            # _safe_release_worker stops it correctly.
            self._worker = _ImagesWorker(
                self._word, DDG_IMAGES_COUNT, None, context=effective_ctx
            )
            try:
                qconnect(self._worker.finished, self._worker.deleteLater)
            except Exception:
                pass
            qconnect(self._worker.progress, self._on_progress)
            qconnect(self._worker.finished_ok, self._on_ready)
            qconnect(self._worker.failed, self._on_failed)
            self._worker.start()
        except Exception as e:
            self._img_label.setText(_t("images_failed", err=str(e)))

    def _on_progress(self, done: int, total: int) -> None:
        # While downloading — we show progress as text, so it's
        # visible that the search is alive (without a spinner for simplicity).
        try:
            self._img_label.setText(_t("images_progress", done=int(done), total=int(total)))
        except Exception:
            pass

    def _on_ready(self, items: list) -> None:
        try:
            self._items = [bytes(x) for x in items if x]
        except Exception:
            self._items = []
        self._idx = 0
        if not self._items:
            self._img_label.setText(_t("images_empty"))
        self._render_current()
        self._update_controls_enabled()

    def _on_failed(self, err: str) -> None:
        try:
            self._img_label.setText(_t("images_failed", err=err))
        except Exception:
            self._img_label.setText("failed: " + err)
        self._update_controls_enabled()

    # ------------------------------------------------------------------
    # Navigation + rendering
    # ------------------------------------------------------------------
    def _show_prev(self) -> None:
        if not self._items:
            return
        self._idx = (self._idx - 1) % len(self._items)
        self._render_current()

    def _show_next(self) -> None:
        if not self._items:
            return
        self._idx = (self._idx + 1) % len(self._items)
        self._render_current()

    def _render_current(self) -> None:
        try:
            if not self._items:
                self.counter_lbl.setText("— / —")
                return
            data = self._items[self._idx]
            pm = QPixmap()
            if not pm.loadFromData(data):
                self._img_label.setText(_t("images_broken"))
                return
            lw = max(16, self._img_label.width())
            lh = max(16, self._img_label.height())
            self._img_label.setPixmap(pm.scaled(
                lw, lh,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
            self._img_label.setText("")
            self.counter_lbl.setText(f"{self._idx + 1} / {len(self._items)}")
        except Exception:
            pass

    def resizeEvent(self, e) -> None:  # type: ignore[override]
        super().resizeEvent(e)
        # A square slot for the image: the label height = its current
        # width. Inside a QVBoxLayout a label with Fixed-height doesn't stretch
        # vertically, so the buttons and the refine field are always visible.
        try:
            w = self._img_label.width()
            if w > 48:
                self._img_label.setFixedHeight(w)
        except Exception:
            pass
        if self._items:
            self._render_current()

    def _update_controls_enabled(self) -> None:
        has = bool(self._items)
        self.prev_btn.setEnabled(has)
        self.next_btn.setEnabled(has)

    # ------------------------------------------------------------------
    # Clipboard paste
    # ------------------------------------------------------------------
    def _paste_from_clipboard(self) -> None:
        """Take an image from the clipboard (or an image URL as text) and
        put it into the carousel at the current position. Made active."""
        try:
            from aqt.qt import QApplication, QByteArray, QBuffer, QIODevice
        except Exception:
            return
        clipboard = QApplication.clipboard() if QApplication is not None else None
        if clipboard is None:
            return

        data: "Optional[bytes]" = None
        # 1) A direct image from the clipboard (a screenshot, copy from a browser, etc.).
        try:
            img = clipboard.image()
            if img is not None and not img.isNull():
                ba = QByteArray()
                buf = QBuffer(ba)
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                img.save(buf, "PNG")
                buf.close()
                data = bytes(ba.data())
        except Exception:
            pass

        # 2) If the clipboard contains an image URL — we can download it.
        #    We do this only if shift is not pressed (so as not to hit
        #    random http links).
        if data is None:
            try:
                text = clipboard.text() or ""
                text = text.strip()
                if text.startswith("http://") or text.startswith("https://"):
                    try:
                        data = _http_get_bytes(text, _ddg_browser_headers({"Accept": "image/*,*/*;q=0.8"}), timeout=10)
                    except Exception:
                        data = None
            except Exception:
                pass

        if data is None or len(data) < 128:
            try:
                self._img_label.setText(_t("images_clipboard_empty"))
            except Exception:
                pass
            return

        # Insert at the current position, make it active.
        if not self._items:
            self._items = [data]
            self._idx = 0
        else:
            insert_at = self._idx + 1 if self._idx < len(self._items) else len(self._items)
            self._items.insert(insert_at, data)
            self._idx = insert_at
        self._render_current()
        self._update_controls_enabled()


class _TTSWorker(QThread):
    """Background thread for TTS: pulls MP3 from Google Translate. The network call
    is blocking (urllib), so we do it strictly off the UI thread."""

    finished_ok = pyqtSignal(bytes)
    failed = pyqtSignal(str)

    def __init__(self, text: str, lang: str, parent=None) -> None:
        super().__init__(parent)
        self._text = text
        self._lang = lang
        self._stop_flag = False

    def request_stop(self) -> None:
        self._stop_flag = True

    def run(self) -> None:  # type: ignore[override]
        try:
            data = _gtts_fetch_mp3(self._text, self._lang, TTS_TIMEOUT)
            if self._stop_flag:
                return
            if not data:
                self.failed.emit("empty audio")
                return
            self.finished_ok.emit(data)
        except Exception as e:
            try:
                self.failed.emit(str(e) or "tts error")
            except Exception:
                pass


class AudioPanel(QFrame):
    """A small panel for the word's audio pronunciation.

    Behavior:
    - On a `set_word(word, lang)` call, a background TTS request to
      Google Translate is started (if it differs from the already loaded one).
    - When the mp3 is ready — the ▶ button becomes active. Clicking it
      plays the audio via `mw.av_player` (or afplay/start as a
      fallback).
    - When saving the card, `get_audio_bytes()` returns the MP3 bytes
      (or None, if nothing was loaded/it failed).
    - `request_regenerate()` — force a re-download (in case the
      network blinked).
    """

    def __init__(self, parent: "QWidget | None" = None, word: str = "", lang: str = REVERSO_DEFAULT_SRC) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._word: str = (word or "").strip()
        self._lang: str = (lang or REVERSO_DEFAULT_SRC).strip().lower() or "en"
        self._audio_bytes: "Optional[bytes]" = None
        self._worker: "Optional[_TTSWorker]" = None
        self._tmp_path: "Optional[str]" = None  # for caching playback

        root = QHBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(6)

        self.play_btn = QPushButton("▶", self)
        self.play_btn.setFixedWidth(36)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setToolTip(_t("audio_play_tooltip"))
        try:
            self.play_btn.setAutoDefault(False)
            self.play_btn.setDefault(False)
        except Exception:
            pass
        _apply_secondary_font(self.play_btn, delta_pt=-1)

        self.regen_btn = QPushButton("↻", self)
        self.regen_btn.setFixedWidth(28)
        self.regen_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.regen_btn.setToolTip(_t("audio_regen_tooltip"))
        try:
            self.regen_btn.setAutoDefault(False)
            self.regen_btn.setDefault(False)
        except Exception:
            pass
        _apply_secondary_font(self.regen_btn, delta_pt=-1)

        self.status_lbl = QLabel(_t("audio_idle"), self)
        self.status_lbl.setStyleSheet("color: #9a9a9a;")
        _apply_secondary_font(self.status_lbl, delta_pt=-1)

        root.addWidget(self.play_btn, 0)
        root.addWidget(self.regen_btn, 0)
        root.addWidget(self.status_lbl, 1)

        qconnect(self.play_btn.clicked, self._on_play_clicked)
        qconnect(self.regen_btn.clicked, self._on_regen_clicked)

        self._update_controls_enabled()
        if self._word:
            self._restart_fetch()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_word(self, word: str, lang: "Optional[str]" = None) -> None:
        w = (word or "").strip()
        if not w:
            return
        new_lang = (lang or self._lang or REVERSO_DEFAULT_SRC).strip().lower()
        if w == self._word and new_lang == self._lang and self._audio_bytes:
            return
        self._word = w
        self._lang = new_lang or self._lang
        self._audio_bytes = None
        self._tmp_path = None
        self._restart_fetch()

    def set_lang(self, lang: str) -> None:
        new_lang = (lang or "").strip().lower()
        if not new_lang or new_lang == self._lang:
            return
        self._lang = new_lang
        if self._word:
            self._audio_bytes = None
            self._tmp_path = None
            self._restart_fetch()

    def get_audio_bytes(self) -> "Optional[bytes]":
        return self._audio_bytes

    def get_lang(self) -> str:
        return self._lang

    def stop_worker(self) -> None:
        _safe_release_worker(self._worker)

    def closeEvent(self, e) -> None:  # type: ignore[override]
        self.stop_worker()
        super().closeEvent(e)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _restart_fetch(self) -> None:
        self.stop_worker()
        self._update_controls_enabled(loading=True)
        self.status_lbl.setText(_t("audio_loading"))
        try:
            # parent=None — the same rule as for the Images/Reverso workers:
            # we don't let Qt cascadingly kill a running thread when the panel
            # is destroyed. The lifecycle is via _safe_release_worker and
            # finished → deleteLater.
            self._worker = _TTSWorker(self._word, self._lang, None)
            try:
                qconnect(self._worker.finished, self._worker.deleteLater)
            except Exception:
                pass
            qconnect(self._worker.finished_ok, self._on_ready)
            qconnect(self._worker.failed, self._on_failed)
            self._worker.start()
        except Exception as e:
            self.status_lbl.setText(_t("audio_failed", err=str(e)))
            self._update_controls_enabled()

    def _on_ready(self, data: "bytes | bytearray") -> None:
        try:
            self._audio_bytes = bytes(data)
        except Exception:
            self._audio_bytes = None
        if self._audio_bytes:
            self.status_lbl.setText(_t("audio_ready", lang=self._lang))
        else:
            self.status_lbl.setText(_t("audio_empty"))
        self._update_controls_enabled()

    def _on_failed(self, err: str) -> None:
        self._audio_bytes = None
        self.status_lbl.setText(_t("audio_failed", err=err))
        self._update_controls_enabled()

    def _on_regen_clicked(self) -> None:
        if not self._word:
            return
        self._audio_bytes = None
        self._tmp_path = None
        self._restart_fetch()

    def _on_play_clicked(self) -> None:
        if not self._audio_bytes:
            return
        path = self._ensure_tmp_path()
        if not path:
            return
        # 1) First we try Anki's built-in player — it works
        #    correctly with the aqt flow and knows about Anki's sound.
        try:
            from aqt.sound import av_player  # type: ignore
            av_player.play_file(path)
            return
        except Exception as e:
            _log(f"av_player.play_file failed: {e}")
        # 2) Fallback: the system player. On macOS — `afplay`, on
        #    Windows — `start`, on Linux — we'll try `mpg123`/`ffplay`.
        try:
            import sys, subprocess
            if sys.platform == "darwin":
                subprocess.Popen(["afplay", path])
            elif sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                # 'mpg123 -q' is quiet, 'ffplay -nodisp -autoexit' if installed
                for cmd in (["mpg123", "-q", path], ["ffplay", "-nodisp", "-autoexit", path]):
                    try:
                        subprocess.Popen(cmd)
                        return
                    except FileNotFoundError:
                        continue
        except Exception as e:
            _log(f"audio system playback failed: {e}")

    def _ensure_tmp_path(self) -> "Optional[str]":
        if self._tmp_path and os.path.exists(self._tmp_path):
            return self._tmp_path
        if not self._audio_bytes:
            return None
        try:
            import tempfile
            fd, path = tempfile.mkstemp(prefix="ai_tts_", suffix=".mp3")
            with os.fdopen(fd, "wb") as f:
                f.write(self._audio_bytes)
            self._tmp_path = path
            return path
        except Exception as e:
            _log(f"tts tmp file write failed: {e}")
            return None

    def _update_controls_enabled(self, loading: bool = False) -> None:
        try:
            self.play_btn.setEnabled(bool(self._audio_bytes) and not loading)
            self.regen_btn.setEnabled(bool(self._word) and not loading)
        except Exception:
            pass


class _RightSidebar(QWidget):
    """Right column: an image placeholder at the top + Reverso (in its own scroll area)
    for all the remaining space. Hidden entirely via setVisible(False)."""

    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        self.images_panel = ImagesPanel(self)
        v.addWidget(self.images_panel, 0)

        # Audio: a compact panel (▶ ↻ + status). The size is fixed in
        # height, so as not to eat into the space of the image and Reverso.
        self.audio_panel = AudioPanel(self)
        v.addWidget(self.audio_panel, 0)

        # An internal QScrollArea for Reverso — it gets its own independent scroll.
        self.reverso_scroll = QScrollArea(self)
        self.reverso_scroll.setWidgetResizable(True)
        self.reverso_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.reverso_host = QWidget(self.reverso_scroll)
        self._reverso_host_layout = QVBoxLayout(self.reverso_host)
        self._reverso_host_layout.setContentsMargins(0, 0, 0, 0)
        self._reverso_host_layout.setSpacing(0)
        placeholder = QLabel(_t("reverso_title"), self.reverso_host)
        placeholder.setStyleSheet("color: #9a9a9a; padding: 6px;")
        self._reverso_host_layout.addWidget(placeholder, 0, Qt.AlignmentFlag.AlignTop)
        self._reverso_placeholder = placeholder
        self.reverso_scroll.setWidget(self.reverso_host)
        v.addWidget(self.reverso_scroll, 1)

    def attach_reverso(self, panel: "ReversoPanel") -> None:
        # Remove the placeholder and put in the live panel.
        try:
            self._reverso_placeholder.setVisible(False)
            self._reverso_host_layout.addWidget(panel, 1)
        except Exception:
            pass


class AiCardReviewDialog(QDialog):
    """Main window for previewing/editing the AI-generated card.

    Architecture (description per the user's requirements):
    - The window opens `showMaximized()` (full screen, but not fullscreen:
      the OS taskbar/dock is visible).
    - The central widget is a `QSplitter(Horizontal)`: the work area on the left
      (~75–80 %), the sidebar on the right (~20–25 %). Stretch factors 4/1.
    - All fonts are in pt. The sizes of the main panels are via `setStretchFactor`,
      without `setFixedSize` on the main areas.
    - The sidebar is hidden entirely via `setVisible(False)` — the left one smoothly
      takes up 100 %.
    """

    def __init__(
        self,
        parent: QWidget,
        words: str,
        front: str,
        back: str,
        model_name: str,
        ai_deck_id: Optional[int],
        platform: str,
        api_key: str,
        model_id: str,
        base_custom: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_t("dialog_ai_review_title"))
        self._words = words
        self._model_name = model_name
        self._ai_deck_id = ai_deck_id
        self._platform = platform
        self._api_key = api_key
        self._model_id = model_id
        self._base_custom = base_custom

        # --- Root layout ----------------------------------------------------
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)
        root.addWidget(self.splitter)

        # --- LEFT PANE ------------------------------------------------------
        left = QWidget(self.splitter)
        self._left_pane = left
        left_v = QVBoxLayout(left)
        left_v.setContentsMargins(8, 4, 8, 4)
        left_v.setSpacing(12)

        # 1) Canvas preview (stretch 5)
        preview_wrap = QFrame(left)
        preview_wrap.setObjectName("card")
        preview_v = QVBoxLayout(preview_wrap)
        preview_v.setContentsMargins(14, 12, 14, 12)
        preview_v.setSpacing(5)
        lbl_card = QLabel(_t("label_card_final_preview"), preview_wrap)
        lbl_card.setObjectName("sectionTitle")
        _apply_secondary_font(lbl_card, delta_pt=-1, weight_bold=True)
        preview_v.addWidget(lbl_card, 0)
        self.card_preview = _CanvasPreview(preview_wrap)
        self.card_preview.setMinimumHeight(180)
        preview_v.addWidget(self.card_preview, 1)
        left_v.addWidget(preview_wrap, 0)

        # 2) Editor block (stretch 3)
        editor_wrap = QFrame(left)
        editor_wrap.setObjectName("card")
        editor_v = QVBoxLayout(editor_wrap)
        editor_v.setContentsMargins(14, 12, 14, 12)
        editor_v.setSpacing(5)

        lbl_editor = QLabel(_t("label_editor_block"), editor_wrap)
        lbl_editor.setObjectName("sectionTitle")
        _apply_secondary_font(lbl_editor, delta_pt=-1, weight_bold=True)
        editor_v.addWidget(lbl_editor)

        # Optional AI helpers: append a mnemonic association or a compound-word split.
        helper_row = QHBoxLayout()
        helper_row.setSpacing(6)
        helper_row.addWidget(QLabel(_t("helper_label"), editor_wrap))
        self.assoc_btn = QPushButton(_t("helper_assoc"), editor_wrap)
        self.assoc_btn.setToolTip(_t("helper_assoc_tip"))
        self.split_btn = QPushButton(_t("helper_split"), editor_wrap)
        self.split_btn.setToolTip(_t("helper_split_tip"))
        self.helper_undo_btn = QPushButton(_t("helper_undo"), editor_wrap)
        self.helper_undo_btn.setToolTip(_t("helper_undo_tip"))
        self.helper_undo_btn.setEnabled(False)
        self._back_undo_stack: list = []
        for b in (self.assoc_btn, self.split_btn, self.helper_undo_btn):
            try:
                b.setAutoDefault(False)
                b.setDefault(False)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
            except Exception:
                pass
        qconnect(self.assoc_btn.clicked, self._on_helper_association)
        qconnect(self.split_btn.clicked, self._on_helper_split)
        qconnect(self.helper_undo_btn.clicked, self._on_helper_undo)
        helper_row.addWidget(self.assoc_btn)
        helper_row.addWidget(self.split_btn)
        helper_row.addWidget(self.helper_undo_btn)
        helper_row.addStretch(1)
        editor_v.addLayout(helper_row)

        editor_v.addWidget(QLabel(_t("label_preview_front_edit"), editor_wrap))
        self.front_edit = _FixedLinesEdit(editor_wrap, lines=2)
        self.front_edit.setPlainText(front)
        editor_v.addWidget(self.front_edit)

        editor_v.addWidget(QLabel(_t("label_preview_back_edit"), editor_wrap))
        self.back_edit = _FixedLinesEdit(editor_wrap, lines=10)
        self.back_edit.setPlainText(back)
        editor_v.addWidget(self.back_edit)

        left_v.addWidget(editor_wrap, 0)

        # 3) Bottom action bar: Cancel (left) + Add (right, accent).
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.cancel_btn = QPushButton(_t("button_cancel_review"), left)
        self.confirm_btn = QPushButton(_t("button_confirm_add"), left)
        self.confirm_btn.setObjectName("addBtn")
        self.confirm_btn.setDefault(True)
        self.confirm_btn.setAutoDefault(True)
        self.confirm_btn.setToolTip(_t("tooltip_confirm_shortcut"))
        _apply_secondary_font(self.confirm_btn, delta_pt=2, weight_bold=True)
        self.confirm_btn.setMinimumHeight(self.confirm_btn.sizeHint().height() + 6)

        btn_row.addWidget(self.cancel_btn, 0)
        btn_row.addStretch(1)
        btn_row.addWidget(self.confirm_btn, 2)
        left_v.addLayout(btn_row)

        # --- RIGHT SIDEBAR --------------------------------------------------
        self.sidebar = _RightSidebar(self.splitter)

        # --- Splitter assembly & stretch factors ---------------------------
        self.splitter.addWidget(left)
        self.splitter.addWidget(self.sidebar)
        # A ~70/30 ratio: the left area is the leading one, the sidebar is noticeably
        # wider than before (it was 80/20). The user can move the splitter handle.
        self.splitter.setStretchFactor(0, 7)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setCollapsible(1, True)
        # We explicitly set the initial widths, otherwise QSplitter on the first
        # show may give the left area the whole slot (stretch is applied
        # only on stretching, not on the initial layout).
        try:
            from aqt.qt import QTimer
            def _apply_initial_sizes():
                try:
                    total = self.splitter.width() or 1200
                    self.splitter.setSizes([int(total * 0.7), int(total * 0.3)])
                except Exception:
                    pass
            QTimer.singleShot(0, _apply_initial_sizes)
        except Exception:
            pass

        # --- Signals --------------------------------------------------------
        qconnect(self.front_edit.textChanged, self._sync_card_preview)
        qconnect(self.back_edit.textChanged, self._sync_card_preview)
        qconnect(self.confirm_btn.clicked, self._on_confirm_add)
        qconnect(self.cancel_btn.clicked, self.reject)

        for seq in ("Ctrl+Return", "Meta+Return"):
            try:
                scut = QShortcut(QKeySequence(seq), self)
                qconnect(scut.activated, self._on_confirm_add)
            except Exception:
                pass

        # --- Initial paint --------------------------------------------------
        self._sync_card_preview()

        # Reverso — we place it in the sidebar if the feature is enabled.
        self.reverso_panel: "Optional[ReversoPanel]" = None
        try:
            self._maybe_attach_reverso(words or front or "")
        except Exception as e:
            _log(f"reverso panel init error: {e}")

        # Images — we start an image search on DDG by the first word of front.
        # The translation/topic from back goes into the query as context — "word
        # context -logo -icon -vector" (cuts off logos and vector
        # placeholders, greatly increases the relevance of the results).
        try:
            lookup_word = self._extract_lookup_word(words or front or "")
            if lookup_word:
                self.sidebar.images_panel.set_word(
                    lookup_word, context=self._ddg_context_from_back(back)
                )
        except Exception as e:
            _log(f"images panel init error: {e}")

        # Audio TTS — we take not a single lookup word, but a short phrase from Front
        # (usually 1-3 words). Otherwise the "second word" would never be voiced.
        # We take the language from the reverso_source_lang setting (this is the "language
        # of the studied word"), the fallback is REVERSO_DEFAULT_SRC.
        try:
            cfg_audio = _get_config()
            tts_lang = str(cfg_audio.get("reverso_source_lang") or REVERSO_DEFAULT_SRC)
            tts_text = self._extract_tts_text(words or front or "")
            if tts_text and bool(cfg_audio.get("audio_panel_enabled", True)):
                self.sidebar.audio_panel.set_word(tts_text, lang=tts_lang)
        except Exception as e:
            _log(f"audio panel init error: {e}")

        # Apply the "sidebar visibility" from settings.
        cfg = _get_config()
        self.set_sidebar_visible(bool(cfg.get("sidebar_visible", True)))

        self._compact_mode = None
        try:
            self.resize(1080, 720)
        except Exception:
            pass
        self._apply_responsive()

    def _apply_responsive(self) -> None:
        """One responsive breakpoint (like a CSS media query): on small screens or
        macOS Large Text, switch to a compact look (smaller fonts/paddings/radii and
        shorter preview/editor) so nothing gets cut off."""
        try:
            scr = self.screen() if self.screen() is not None else QApplication.primaryScreen()
            g = scr.availableGeometry()
            compact = (g.height() < 900) or (g.width() < 1200) or (self.height() < 700)
            if self._compact_mode == compact:
                return
            self._compact_mode = compact
            self._apply_review_styles(compact)
            try:
                # Always pin the card to the top (no vertical centering) so there is
                # no empty gap above/below the card in either mode.
                self.card_preview.set_top_only(True)
                if compact:
                    self.card_preview.setMaximumHeight(260)
                    self.back_edit.set_visible_lines(5)
                else:
                    self.card_preview.setMaximumHeight(320)
                    self.back_edit.set_visible_lines(10)
            except Exception:
                pass
            # Tighten the card paddings in compact mode.
            try:
                mh, mv = (8, 8) if compact else (14, 12)
                for fr in self.findChildren(QFrame):
                    if fr.objectName() == "card" and fr.layout() is not None:
                        fr.layout().setContentsMargins(mh, mv, mh, mv)
            except Exception:
                pass
            # Re-render the preview so its font matches the new mode (smaller only
            # in compact, full size on normal screens).
            try:
                self._sync_card_preview()
            except Exception:
                pass
        except Exception:
            pass

    def resizeEvent(self, e):  # type: ignore[override]
        try:
            super().resizeEvent(e)
            self._apply_responsive()
        except Exception:
            pass

    def _apply_review_styles(self, compact: bool = False) -> None:
        """Modern, theme-aware QSS. In compact mode everything is smaller (fonts,
        paddings, radii) to fit small screens / Large Text."""
        try:
            try:
                from aqt import theme as _theme_mod
                night = bool(_theme_mod.theme_manager.night_mode)
            except Exception:
                night = True
            card_bg = "rgba(255,255,255,0.05)" if night else "rgba(0,0,0,0.035)"
            card_border = "rgba(255,255,255,0.10)" if night else "rgba(0,0,0,0.10)"
            input_bg = "rgba(0,0,0,0.20)" if night else "rgba(255,255,255,0.65)"
            if compact:
                base_font = "QWidget { font-size: 11px; }"
                radius, in_radius, in_pad = 7, 5, "2px 4px"
                add_radius, add_pad = 6, "4px 10px"
            else:
                base_font = ""
                radius, in_radius, in_pad = 12, 8, "6px 8px"
                add_radius, add_pad = 9, "9px 18px"
            self.setStyleSheet(f"""
                {base_font}
                QFrame#card {{
                    background-color: {card_bg};
                    border: 1px solid {card_border};
                    border-radius: {radius}px;
                }}
                QLabel#sectionTitle {{ font-weight: 600; }}
                QFrame#card QPlainTextEdit, QFrame#card QLineEdit {{
                    border: 1px solid {card_border};
                    border-radius: {in_radius}px;
                    padding: {in_pad};
                    background-color: {input_bg};
                }}
                QPushButton#addBtn {{
                    background-color: #4A9EFF;
                    color: white;
                    border: none;
                    border-radius: {add_radius}px;
                    padding: {add_pad};
                    font-weight: 700;
                }}
                QPushButton#addBtn:hover {{ background-color: #5fb0ff; }}
                QPushButton#addBtn:pressed {{ background-color: #3f8be0; }}
            """)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_sidebar_visible(self, visible: bool) -> None:
        """Show/hide the right sidebar entirely. When hidden — the left
        area smoothly takes up 100 % of the width via QSplitter."""
        self.sidebar.setVisible(visible)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        # Size the window to the LEFT pane's content height (clamped, centered) so
        # there is no leftover empty space below the editor. The right sidebar
        # scrolls its own content (Reverso) to fit.
        try:
            scr = self.screen() if self.screen() is not None else QApplication.primaryScreen()
            g = scr.availableGeometry()
            left_h = self._left_pane.sizeHint().height() if getattr(self, "_left_pane", None) else 640
            w = min(max(self.sizeHint().width(), 1040), int(g.width() * 0.94))
            h = min(max(left_h + 40, 560), int(g.height() * 0.92))
            self.resize(w, h)
            fr = self.frameGeometry()
            fr.moveCenter(g.center())
            self.move(fr.topLeft())
        except Exception:
            pass
        # Focus on opening — in the editor's Front field (the most common place to edit).
        try:
            self.front_edit.setFocus()
        except Exception:
            pass
        # We re-apply the splitter ratio AFTER showMaximized(),
        # when the window size has become final. Without this, on the first show
        # QSplitter could give the left area almost all the width.
        try:
            from aqt.qt import QTimer
            def _apply_split():
                try:
                    total = self.splitter.width()
                    if total <= 0:
                        total = self.width() or 1400
                    self.splitter.setSizes([int(total * 0.7), int(total * 0.3)])
                except Exception:
                    pass
            QTimer.singleShot(0, _apply_split)
            QTimer.singleShot(80, _apply_split)
        except Exception:
            pass

    def done(self, result) -> None:  # type: ignore[override]
        try:
            if self.reverso_panel is not None:
                self.reverso_panel.stop_worker()
        except Exception:
            pass
        try:
            if getattr(self, "sidebar", None) is not None:
                self.sidebar.images_panel.stop_worker()
        except Exception:
            pass
        try:
            if getattr(self, "sidebar", None) is not None:
                self.sidebar.audio_panel.stop_worker()
        except Exception:
            pass
        super().done(result)

    # ------------------------------------------------------------------
    # Canvas + counters
    # ------------------------------------------------------------------
    def _sync_card_preview(self) -> None:
        try:
            fpx = 11 if getattr(self, "_compact_mode", False) else 13
            lpx = 8 if getattr(self, "_compact_mode", False) else 9
            html_doc = _anki_card_preview_document(
                self.front_edit.toPlainText(), self.back_edit.toPlainText(), fpx, lpx
            )
            self.card_preview.set_html(html_doc)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Sidebar modules
    # ------------------------------------------------------------------
    # Articles/function words that must NEVER take on
    # the role of the "studied word" in the lookup modules (Reverso/Images).
    # A real case: Front="de geschiedenis" → previously the word "de" was used,
    # the translation module showed article variants, the images also went by
    # "de". The list covers definite/indefinite articles and the basic function
    # words of the main languages the addon is used with.
    _LOOKUP_STOP_WORDS: "set[str]" = {
        # Dutch
        "de", "het", "een", "'t", "'n",
        # German
        "der", "die", "das", "dem", "den", "des",
        "ein", "eine", "einer", "einen", "einem", "eines",
        # English
        "the", "a", "an", "to",
        # French
        "le", "la", "les", "un", "une", "des", "du", "au", "aux",
        # Spanish
        "el", "los", "las", "unos", "unas",
        # Italian
        "il", "lo", "i", "gli", "uno", "una",
        # Portuguese
        "o", "os", "as", "um", "uma",
    }

    def _extract_lookup_word(self, text: str) -> str:
        """Extract the "studied word" from Front for the sidebar modules
        (Reverso and Images).

        Rules:
        1. We take the first line, cut by `,;:`.
        2. From what remains — the first non-stop word. An article/function word
           like "de", "het", "der", "the", "une", … is skipped, and we
           move to the next token. So "de geschiedenis" gives
           "geschiedenis", and "het verleden" → "verleden".
        3. If for some reason all tokens turned out to be stop words
           (for example, front = "de") — we return the first one we found,
           so the behavior doesn't break.
        """
        source = (text or "").strip()
        if not source:
            source = self.front_edit.toPlainText().strip()
        if not source:
            return ""
        source = source.split("\n", 1)[0]
        for sep in (";", ",", ":"):
            source = source.split(sep, 1)[0]
        parts = source.strip().split()
        if not parts:
            return ""
        for token in parts:
            cleaned = token.strip().strip(".,:;!?\"'()[]{}")
            if not cleaned:
                continue
            if cleaned.lower() in self._LOOKUP_STOP_WORDS:
                continue
            return cleaned
        # All tokens are stop words. Better to return something than nothing.
        return parts[0].strip(".,:;!?\"'()[]{}")

    def _extract_tts_text(self, text: str) -> str:
        """Prepare the text for TTS from Front.

        Unlike `_extract_lookup_word`, which is needed for search and
        translation (1 word), here we deliberately take a phrase so that
        2+ words are voiced (for example, `de grammatica`).
        """
        source = (text or "").strip()
        if not source:
            source = self.front_edit.toPlainText().strip()
        if not source:
            return ""
        # We take only the first line of front and clean up extra separators.
        source = source.split("\n", 1)[0].strip()
        for sep in (";", ",", ":"):
            source = source.split(sep, 1)[0]
        source = re.sub(r"\s+", " ", source).strip()
        # We limit the length so that TTS is fast and without long chunks.
        # For phrases/terms 80 characters is more than enough.
        return source[:80].strip()

    def _ddg_context_from_back(self, back: str) -> str:
        """Pull a short topic hint from the Back field for the image query.

        Back is usually multi-line: the first line is the translation, the rest
        are examples. The first line + a few words is enough to disambiguate the
        image search (e.g. "bank" + "river" vs "money"). This is language-agnostic
        — it uses whatever the user's TARGET language is, NOT a hardcoded one.
        `_ddg_build_query` trims it further."""
        s = (back or "").strip()
        if not s:
            return ""
        return s.split("\n", 1)[0].strip()

    def _maybe_attach_reverso(self, lookup_word: str) -> None:
        cfg = _get_config()
        if not bool(cfg.get("reverso_panel_enabled", True)):
            return
        word = self._extract_lookup_word(lookup_word)
        if not word:
            return
        src = str(cfg.get("reverso_source_lang") or REVERSO_DEFAULT_SRC)
        tgt = str(cfg.get("reverso_target_lang") or REVERSO_DEFAULT_TGT)
        self.reverso_panel = ReversoPanel(self.sidebar.reverso_host, word, src, tgt)
        self.sidebar.attach_reverso(self.reverso_panel)

    def _set_busy(self, busy: bool) -> None:
        self.confirm_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(not busy)
        self.front_edit.setReadOnly(busy)
        self.back_edit.setReadOnly(busy)
        self.card_preview.setEnabled(not busy)
        try:
            self.assoc_btn.setEnabled(not busy)
            self.split_btn.setEnabled(not busy)
            self.helper_undo_btn.setEnabled((not busy) and bool(self._back_undo_stack))
        except Exception:
            pass

    # --- Optional AI helpers (append to Back) ---------------------------------
    def _helper_langs(self) -> tuple:
        cfg = _get_config()
        src = _lang_display_name(str(cfg.get("reverso_source_lang", REVERSO_DEFAULT_SRC) or REVERSO_DEFAULT_SRC))
        tgt = _lang_display_name(str(cfg.get("reverso_target_lang", REVERSO_DEFAULT_TGT) or REVERSO_DEFAULT_TGT))
        return src, tgt

    def _on_helper_association(self) -> None:
        word = self.front_edit.toPlainText().strip()
        if not word:
            return
        src, tgt = self._helper_langs()
        meaning = self.back_edit.toPlainText().split("<br/>")[0].strip()[:120]
        prompt = (
            f"You help a {tgt}-speaking learner memorize the {src} word \"{word}\" "
            f"(meaning: {meaning}). Give ONE short, vivid memory association / mnemonic "
            f"written in {tgt} (1-2 sentences) that links the sound or form of the word to "
            f"its meaning. Output ONLY the association text, no preamble, no quotes."
        )
        self._run_helper(prompt, "💡 ")

    def _on_helper_split(self) -> None:
        word = self.front_edit.toPlainText().strip()
        if not word:
            return
        src, tgt = self._helper_langs()
        prompt = (
            f"Break down the {src} input \"{word}\" for a learner and translate each part into {tgt}.\n"
            f"STRICT rules:\n"
            f"- If it is SEVERAL words, translate EACH WORD separately (in the original order).\n"
            f"- If it is ONE compound word built from real standalone words (common in German/Dutch), "
            f"split it into those real component words only.\n"
            f"- NEVER invent prefixes, suffixes or morphemes that are not real standalone words; "
            f"use only parts that actually appear in the input.\n"
            f"- If it is a simple single word (not compound), output just: {word} (its {tgt} translation).\n"
            f"Output exactly ONE line: part1 ({tgt} translation) + part2 ({tgt} translation) + ... "
            f"No preamble, no notes, no extra lines."
        )
        self._run_helper(prompt, "🧩 ")

    def _run_helper(self, prompt: str, prefix: str) -> None:
        self._set_busy(True)
        platform, api_key, model_id = self._platform, self._api_key, self._model_id

        def worker() -> None:
            try:
                txt = _ai_complete(platform, api_key, prompt, model_id)
            except Exception as e:
                _log_exc("AI helper")
                err_text = str(e)
                def err() -> None:
                    self._set_busy(False)
                    _show_error(self, err_text)
                _run_on_main_thread(err)
                return

            def ok() -> None:
                self._set_busy(False)
                t = " ".join((txt or "").split()).strip()
                if t:
                    prev = self.back_edit.toPlainText()
                    self._back_undo_stack.append(prev)
                    cur = prev.rstrip()
                    sep = "<br/><br/>" if cur else ""
                    self.back_edit.setPlainText(cur + sep + prefix + t)
                    self._sync_card_preview()
                    try:
                        self.helper_undo_btn.setEnabled(True)
                    except Exception:
                        pass
            _run_on_main_thread(ok)

        threading.Thread(target=worker, name="AI-Helper", daemon=True).start()

    def _on_helper_undo(self) -> None:
        """Restore the Back text to before the last association/split was appended."""
        if not self._back_undo_stack:
            return
        prev = self._back_undo_stack.pop()
        self.back_edit.setPlainText(prev)
        self._sync_card_preview()
        try:
            self.helper_undo_btn.setEnabled(bool(self._back_undo_stack))
        except Exception:
            pass

    def _on_confirm_add(self) -> None:
        front = self.front_edit.toPlainText().strip()
        back = self.back_edit.toPlainText().strip()
        if not front:
            showWarning(_t("error_empty_front"))
            return
        if not _try_register_recent_front_for_ai(front):
            _log("review-dialog: skip duplicate front burst")
            showWarning(_t("error_card_already_exists"))
            return

        # If there's a selected image in the right panel — we save it
        # to Anki media and build a ready <img ...> tag. Where exactly
        # to put this tag (into a separate "Image" field or attach it to
        # Back) is decided by `_add_note_to_deck` — it looks at the fields
        # of the current note model. We do this BEFORE creating the note: Anki
        # will link the file to the new card itself.
        image_html = ""
        try:
            image_bytes = self.sidebar.images_panel.get_current_image()
        except Exception:
            image_bytes = None
        if image_bytes:
            try:
                fname = _save_image_to_media(image_bytes, hint=front)
                if fname:
                    image_html = f'<img src="{html.escape(fname, quote=True)}">'
            except Exception as e:
                _log(f"image attach failed: {e}")

        # Audio (TTS): fully analogous to the image. If the model has
        # an Audio/Sound/Аудио/... field — we put [sound:fname.mp3] there.
        # Otherwise the audio simply isn't attached (better not to litter
        # Back with junk — a user who has no field usually doesn't want
        # audio at all).
        audio_sound_tag = ""
        try:
            audio_bytes = self.sidebar.audio_panel.get_audio_bytes()
        except Exception:
            audio_bytes = None
        if audio_bytes:
            try:
                fname_audio = _save_audio_to_media(audio_bytes, hint=front)
                if fname_audio:
                    audio_sound_tag = f"[sound:{fname_audio}]"
            except Exception as e:
                _log(f"audio attach failed: {e}")

        ok = _add_note_to_deck(
            front,
            back,
            self._ai_deck_id,
            self._model_name,
            image_html=image_html,
            audio_html=audio_sound_tag,
        )
        if not ok:
            try:
                col_local = mw.col
                by_name_local = None
                if col_local is not None:
                    by_name_local = getattr(col_local.models, "by_name", None) or getattr(col_local.models, "byName", None)
                model_local = by_name_local(self._model_name) if callable(by_name_local) else None
                if model_local and _card_exists_in_deck(front, self._ai_deck_id, model_local):
                    showWarning(_t("error_card_already_exists"))
                else:
                    showWarning(_t("error_card_not_added"))
            except Exception:
                pass
        if ok:
            self.accept()
        else:
            # allow user to edit again if duplicate / failed
            pass


def _all_decks() -> "list[tuple[str, int]]":
    """List (name, id) of all decks. Module-level so both the settings window and
    the setup wizard can use it."""
    col = mw.col
    if col is None:
        return []
    decks = col.decks
    items: "list[tuple[str, int]]" = []
    fn = getattr(decks, "all_names_and_ids", None)
    if callable(fn):
        for entry in fn():
            name = getattr(entry, "name", None)
            did = getattr(entry, "id", None)
            if name is None or did is None:
                try:
                    name, did = entry  # type: ignore[misc]
                except Exception:
                    continue
            items.append((str(name), int(did)))
    else:
        all_names = getattr(decks, "allNames", None)
        get_id = getattr(decks, "id", None) or getattr(decks, "idForName", None)
        if callable(all_names) and callable(get_id):
            for name in all_names():
                items.append((str(name), int(get_id(name))))
    return items


def _create_deck_prompt(parent) -> Optional[int]:
    """Ask for a deck name and create it. Returns the new deck id (or None)."""
    col = mw.col
    if col is None:
        return None
    try:
        from aqt.qt import QInputDialog
        name, ok = QInputDialog.getText(parent, _t("new_deck_title"), _t("new_deck_label"))
    except Exception:
        return None
    name = (name or "").strip()
    if not ok or not name:
        return None
    try:
        did = col.decks.id(name, create=True)
    except TypeError:
        did = col.decks.id(name)
    try:
        mw.reset()
    except Exception:
        pass
    return int(did) if did else None


def _add_card_note(front: str, back: str, model_name: str, deck_id: Optional[int]) -> None:
    """Add a Front/Back note directly to a deck (used for the auto-add path)."""
    col = mw.col
    if col is None:
        showWarning(_t("error_collection_not_loaded"))
        return
    front = (front or "").strip()
    back = (back or "").strip()
    if not front:
        showWarning(_t("error_empty_front"))
        return
    models = col.models
    by_name = getattr(models, "by_name", None) or getattr(models, "byName", None)
    model = by_name(model_name) if callable(by_name) else None
    if not model:
        showWarning(_t("error_note_type_not_found"))
        return
    if hasattr(col, "new_note"):
        note = col.new_note(model)
    elif Note is not None:
        note = Note(col, model)  # type: ignore
    else:
        showWarning(_t("error_cannot_create_note"))
        return
    field_names_method = getattr(models, "field_names", None) or getattr(models, "fieldNames", None)
    field_names = field_names_method(model) if callable(field_names_method) else []
    if "Front" in field_names and "Back" in field_names:
        note["Front"] = front
        note["Back"] = back
    elif len(field_names) >= 2:
        note[field_names[0]] = front
        note[field_names[1]] = back
    else:
        showWarning(_t("error_insufficient_fields"))
        return
    if deck_id:
        _assign_deck_to_note(note, int(deck_id))
    add_note = getattr(col, "add_note", None) or getattr(col, "addNote", None)
    if not callable(add_note):
        showWarning(_t("error_add_note_method_not_found"))
        return
    try:
        if deck_id is not None:
            add_note(note, deck_id=int(deck_id))
        else:
            add_note(note)
    except TypeError:
        add_note(note)
    mw.reset()
    showInfo(_t("success_card_added"))


def _open_ai_review_and_add(
    words: str,
    front: str,
    back: str,
    model_name: str,
    ai_deck_id: Optional[int],
    platform: str,
    api_key: str,
    model_id: str,
    base_custom: str,
) -> None:
    similar_existing: Optional[str] = None
    try:
        col = mw.col
        if col is not None:
            by_name = getattr(col.models, "by_name", None) or getattr(col.models, "byName", None)
            model_obj = by_name(model_name) if callable(by_name) else None
            if model_obj is not None:
                similar_existing = _find_similar_card_in_deck(front, ai_deck_id, model_obj)
    except Exception as e:
        _log(f"pre-review dup-check error: {e}")

    if similar_existing:
        from aqt.qt import QMessageBox
        box = QMessageBox(mw)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(_t("dup_warn_title"))
        preview_existing = (similar_existing or "").strip()
        if len(preview_existing) > 240:
            preview_existing = preview_existing[:240] + "…"
        preview_new = (front or "").strip()
        if len(preview_new) > 240:
            preview_new = preview_new[:240] + "…"
        box.setText(_t("dup_warn_text").format(existing=preview_existing, new=preview_new))
        add_btn = box.addButton(_t("dup_warn_add_anyway"), QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = box.addButton(_t("button_cancel_review"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        if box.clickedButton() is not add_btn:
            _log(f"dup-warn: user cancelled. existing='{preview_existing[:80]}' new='{preview_new[:80]}'")
            return

    dlg = AiCardReviewDialog(
        mw,
        words,
        front,
        back,
        model_name,
        ai_deck_id,
        platform,
        api_key,
        model_id,
        base_custom,
    )
    dlg.exec()


class SetupWizard(QDialog):
    """First-run, step-by-step setup: provider+key → languages → done. Primitive,
    explicit instructions. Saves to config on Finish."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent or mw)
        self.setWindowTitle(_t("wizard_title"))
        try:
            self.setMinimumWidth(520)
        except Exception:
            pass
        cfg = _get_config()
        # Keeps QMovie / QMediaPlayer objects alive for the dialog's lifetime.
        self._keepalive_media: list = []

        self.stack = QStackedWidget(self)

        # --- Page 0: welcome ---
        self.stack.addWidget(self._text_page(_t("wizard_welcome_title"), _t("wizard_welcome_text")))

        # --- Page 1: AI provider + key (with step-by-step guide + screenshots) ---
        p_ai_inner = QWidget(self)
        v = QVBoxLayout(p_ai_inner)
        v.addWidget(self._title_label(_t("wizard_ai_title")))
        v.addWidget(self._body_label(_t("wizard_ai_text")))
        form = QFormLayout()
        self.provider_combo = QComboBox(self)
        self.provider_combo.addItem("Google Gemini", "google")
        # Groq hidden from UI for now (still supported in code).
        idx = self.provider_combo.findData(str(cfg.get("ai_platform", DEFAULT_PLATFORM)))
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        self.key_edit = QLineEdit(self)
        self.key_edit.setPlaceholderText("AIza…")
        form.addRow(_t("wizard_provider"), self.provider_combo)
        form.addRow(_t("wizard_api_key"), self.key_edit)
        v.addLayout(form)
        # Step-by-step guide with clickable link + screenshots under steps 2 and 3.
        # Drop key2.png under step 2 and key3.png under step 3 into the images/ folder.
        v.addSpacing(10)
        v.addWidget(self._title_label(_t("wizard_ai_guide_title")))
        v.addWidget(self._html_label(_t("wizard_ai_step1_html")))
        v.addWidget(self._html_label(_t("wizard_ai_step2_html")))
        v.addWidget(self._html_label(_t("wizard_ai_step3_html")))
        # Screenshots side by side (key2 = step 2, key3 = step 3) — a row keeps the
        # page short so it doesn't overflow the screen height.
        shots = QWidget(self)
        shots_h = QHBoxLayout(shots)
        shots_h.setContentsMargins(0, 0, 0, 0)
        shots_h.setSpacing(10)
        shots_h.addWidget(self._guide_images_widget("key2", max_width=330), 1)
        shots_h.addWidget(self._guide_images_widget("key3", max_width=330), 1)
        v.addSpacing(6)
        v.addWidget(shots)
        note = self._html_label(_t("wizard_rate_note"))
        try:
            note.setStyleSheet("color: gray;")
        except Exception:
            pass
        v.addSpacing(6)
        v.addWidget(note)
        v.addStretch(1)
        # Wrap in a scroll area so the guide + screenshots never overflow the dialog.
        p_ai = QScrollArea(self)
        p_ai.setWidgetResizable(True)
        p_ai.setWidget(p_ai_inner)
        try:
            p_ai.setFrameShape(QFrame.Shape.NoFrame)
        except Exception:
            pass
        self.stack.addWidget(p_ai)

        def _prefill_key():
            prov = self.provider_combo.currentData()
            existing = str(cfg.get("groq_api_key" if prov == "groq" else "gemini_api_key", "")).strip()
            self.key_edit.setText(existing)
        _prefill_key()
        qconnect(self.provider_combo.currentIndexChanged, lambda _i: _prefill_key())

        # --- Page 2: languages ---
        p_lang = QWidget(self)
        v = QVBoxLayout(p_lang)
        v.addWidget(self._title_label(_t("wizard_lang_title")))
        v.addWidget(self._body_label(_t("wizard_lang_text")))
        form = QFormLayout()
        self.src_combo = _make_language_combo(self)
        self.tgt_combo = _make_language_combo(self)
        _set_language_combo(self.src_combo, str(cfg.get("reverso_source_lang", REVERSO_DEFAULT_SRC)))
        _set_language_combo(self.tgt_combo, str(cfg.get("reverso_target_lang", REVERSO_DEFAULT_TGT)))
        form.addRow(_t("label_reverso_src_lang"), self.src_combo)
        form.addRow(_t("label_reverso_tgt_lang"), self.tgt_combo)
        v.addLayout(form)
        v.addStretch(1)
        self.stack.addWidget(p_lang)

        # --- Page 3: deck (pick or create the target deck) ---
        p_deck = QWidget(self)
        v = QVBoxLayout(p_deck)
        v.addWidget(self._title_label(_t("wizard_deck_title")))
        v.addWidget(self._body_label(_t("wizard_deck_text")))
        form = QFormLayout()
        self.deck_combo = QComboBox(self)
        for _name, _did in _all_decks():
            self.deck_combo.addItem(_name, _did)
        saved_deck = cfg.get("ai_deck_id")
        if saved_deck is not None:
            di = self.deck_combo.findData(int(saved_deck))
            if di >= 0:
                self.deck_combo.setCurrentIndex(di)
        self.deck_new_btn = QPushButton(_t("new_deck_btn"), self)
        qconnect(self.deck_new_btn.clicked, self._wizard_new_deck)
        deck_row = QWidget(self)
        drh = QHBoxLayout(deck_row)
        drh.setContentsMargins(0, 0, 0, 0)
        drh.addWidget(self.deck_combo, 1)
        drh.addWidget(self.deck_new_btn)
        form.addRow(_t("label_deck_target"), deck_row)
        v.addLayout(form)
        v.addStretch(1)
        self.stack.addWidget(p_deck)

        # --- Page 4: done (with optional animation: images/video.* or video.gif) ---
        p_done_inner = QWidget(self)
        dv = QVBoxLayout(p_done_inner)
        dv.addWidget(self._title_label(_t("wizard_done_title")))
        dv.addSpacing(6)
        dv.addWidget(self._body_label(_t("wizard_done_text")))
        dv.addSpacing(8)
        dv.addWidget(self._guide_images_widget("video"))
        dv.addStretch(1)
        p_done = QScrollArea(self)
        p_done.setWidgetResizable(True)
        p_done.setWidget(p_done_inner)
        try:
            p_done.setFrameShape(QFrame.Shape.NoFrame)
        except Exception:
            pass
        self.stack.addWidget(p_done)

        # Nav bar
        self.step_label = QLabel(self)
        try:
            self.step_label.setStyleSheet("color: gray;")
        except Exception:
            pass
        self.back_btn = QPushButton(_t("wizard_back"), self)
        self.next_btn = QPushButton(_t("wizard_next"), self)
        qconnect(self.back_btn.clicked, lambda: self._go(-1))
        qconnect(self.next_btn.clicked, lambda: self._go(1))
        nav = QHBoxLayout()
        nav.addWidget(self.step_label)
        nav.addStretch(1)
        nav.addWidget(self.back_btn)
        nav.addWidget(self.next_btn)

        root = QVBoxLayout(self)
        root.addWidget(self.stack, 1)
        root.addLayout(nav)
        self._update_nav()
        # Re-fit once the event loop has laid widgets out (sizeHints accurate).
        try:
            from aqt.qt import QTimer
            QTimer.singleShot(0, self._resize_to_current)
        except Exception:
            pass

    def _title_label(self, text: str) -> "QLabel":
        lbl = QLabel(text, self)
        try:
            lbl.setStyleSheet("font-size:18px; font-weight:600;")
            lbl.setWordWrap(True)
        except Exception:
            pass
        return lbl

    def _body_label(self, text: str) -> "QLabel":
        lbl = QLabel(text, self)
        try:
            lbl.setWordWrap(True)
        except Exception:
            pass
        return lbl

    def _html_label(self, html: str) -> "QLabel":
        """Rich-text label with clickable external links."""
        lbl = QLabel(self)
        try:
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setText(html)
            lbl.setWordWrap(True)
            lbl.setOpenExternalLinks(True)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        except Exception:
            lbl.setText(html)
        return lbl

    def _guide_images_widget(self, prefix: str, max_width: int = 460) -> "QWidget":
        """Show media the user drops into the addon's images/ folder. Files whose
        names start with `prefix` are shown in sorted order. Supports static images
        (png/jpg/jpeg), animated GIFs, and video (mp4/mov/webm). Empty if none."""
        w = QWidget(self)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        try:
            images_dir = os.path.join(os.path.dirname(__file__), "images")
            files = sorted(
                f for f in os.listdir(images_dir)
                if f.lower().startswith(prefix.lower())
                and f.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".mp4", ".mov", ".webm"))
            )
            for fn in files:
                path = os.path.join(images_dir, fn)
                low = fn.lower()
                if low.endswith((".mp4", ".mov", ".webm")):
                    vid = self._video_widget(path)
                    if vid is not None:
                        v.addWidget(vid)
                elif low.endswith(".gif"):
                    lbl = QLabel(self)
                    try:
                        from aqt.qt import QMovie
                        mov = QMovie(path)
                        lbl.setMovie(mov)
                        mov.start()
                        self._keepalive_media.append(mov)
                    except Exception:
                        pm = QPixmap(path)
                        if not pm.isNull():
                            lbl.setPixmap(pm if pm.width() <= max_width else pm.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation))
                    v.addWidget(lbl)
                else:
                    pm = QPixmap(path)
                    if pm.isNull():
                        continue
                    if pm.width() > max_width:
                        pm = pm.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation)
                    lbl = QLabel(self)
                    lbl.setPixmap(pm)
                    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    v.addWidget(lbl)
        except Exception:
            pass
        return w

    def _video_widget(self, path: str) -> "Optional[QWidget]":
        """Best-effort looping, muted video player via QtMultimedia. Returns None
        if QtMultimedia is unavailable (then GIF/screenshots are the fallback)."""
        try:
            from aqt.qt import QUrl
            from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PyQt6.QtMultimediaWidgets import QVideoWidget
            vw = QVideoWidget(self)
            vw.setMinimumSize(420, 260)
            vw.setMaximumWidth(560)
            try:
                vw.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
            except Exception:
                pass
            player = QMediaPlayer(self)
            audio = QAudioOutput(self)
            audio.setMuted(True)
            player.setAudioOutput(audio)
            player.setVideoOutput(vw)
            player.setSource(QUrl.fromLocalFile(path))
            try:
                player.setLoops(QMediaPlayer.Loops.Infinite)
            except Exception:
                pass
            player.play()
            self._keepalive_media.append(player)
            self._keepalive_media.append(audio)
            return vw
        except Exception as e:
            _log(f"wizard video unavailable: {e!r}")
            return None

    def _text_page(self, title: str, body: str) -> "QWidget":
        w = QWidget(self)
        v = QVBoxLayout(w)
        v.addWidget(self._title_label(title))
        v.addSpacing(6)
        v.addWidget(self._body_label(body))
        v.addStretch(1)
        return w

    def _wizard_new_deck(self) -> None:
        did = _create_deck_prompt(self)
        if did is None:
            return
        self.deck_combo.clear()
        for name, d in _all_decks():
            self.deck_combo.addItem(name, d)
        idx = self.deck_combo.findData(did)
        if idx >= 0:
            self.deck_combo.setCurrentIndex(idx)

    def _update_nav(self) -> None:
        i = self.stack.currentIndex()
        last = self.stack.count() - 1
        self.back_btn.setEnabled(i > 0)
        self.next_btn.setText(_t("wizard_finish") if i == last else _t("wizard_next"))
        self.step_label.setText(_t("wizard_step").format(n=i + 1, total=self.stack.count()))
        self._resize_to_current()

    def _resize_to_current(self) -> None:
        """Size the dialog to fit the current step's content (so nothing is cut off
        and short pages aren't padded by the tallest page). Clamped to the screen."""
        try:
            idx = self.stack.currentIndex()
            # Make the QStackedWidget follow only the current page's size hint.
            for i in range(self.stack.count()):
                pol = QSizePolicy.Policy.Preferred if i == idx else QSizePolicy.Policy.Ignored
                self.stack.widget(i).setSizePolicy(pol, pol)
            cur = self.stack.currentWidget()
            inner = cur.widget() if isinstance(cur, QScrollArea) else cur
            inner.adjustSize()
            hint = inner.sizeHint()
            screen = QApplication.primaryScreen().availableGeometry()
            target_w = min(max(620, hint.width() + 80), int(screen.width() * 0.92))
            target_h = min(hint.height() + 120, int(screen.height() * 0.92))
            self.resize(target_w, target_h)
            # Re-center on screen so a tall step isn't anchored at the top (cut off).
            fg = self.frameGeometry()
            fg.moveCenter(screen.center())
            self.move(fg.topLeft())
        except Exception:
            pass

    def _go(self, delta: int) -> None:
        i = self.stack.currentIndex()
        last = self.stack.count() - 1
        if delta > 0 and i == last:
            self._finish()
            return
        self.stack.setCurrentIndex(max(0, min(last, i + delta)))
        self._update_nav()

    def _finish(self) -> None:
        try:
            cfg = _get_config()
            prov = self.provider_combo.currentData() or DEFAULT_PLATFORM
            cfg["ai_platform"] = prov
            key = self.key_edit.text().strip()
            if key:
                cfg["groq_api_key" if prov == "groq" else "gemini_api_key"] = key
            cfg["reverso_source_lang"] = _language_combo_code(self.src_combo, REVERSO_DEFAULT_SRC)
            cfg["reverso_target_lang"] = _language_combo_code(self.tgt_combo, REVERSO_DEFAULT_TGT)
            deck_data = self.deck_combo.currentData()
            if deck_data is not None:
                cfg["ai_deck_id"] = int(deck_data)
            cfg["setup_done"] = True
            _set_config(cfg)
            _refresh_hotkey_state()
        except Exception as e:
            _log(f"setup wizard finish error {e!r}")
        self.accept()


class AddCardDialog(QDialog):
    def __init__(self) -> None:
        super().__init__(mw)
        self.setWindowTitle(_t("dialog_title"))

        # Version label (bottom right)
        from aqt.qt import QLabel, Qt
        self.version_label = QLabel(f"v{__version__}", self)
        font = self.version_label.font()
        font.setPointSize(9)
        self.version_label.setFont(font)
        self.version_label.setStyleSheet("color: #888; padding-right:8px;")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom if hasattr(Qt, 'AlignmentFlag') else Qt.AlignRight | Qt.AlignBottom)


        self.deck_combo = QComboBox(self)
        self.model_combo = QComboBox(self)

        # AI tab settings are now handled by separate API Settings dialog.
        from aqt.qt import QLabel
        self.platform_value_label = QLabel(self)
        self.model_value_label = QLabel(self)
        self.api_settings_btn = QPushButton(_t("button_api_settings"), self)
        self.prompt_name_edit = QLineEdit(self)
        self.prompt_prev_btn = QPushButton(_t("button_prev"), self)
        self.prompt_next_btn = QPushButton(_t("button_next"), self)
        self.custom_prompt_edit = QPlainTextEdit(self)
        self.custom_prompt_edit.setPlaceholderText(_t("placeholder_custom_prompt"))
        # --- Card-content constructor (beginner-friendly; builds the prompt) ---
        self.card_translations_spin = QSpinBox(self)
        self.card_translations_spin.setRange(1, 6)
        self.card_examples_spin = QSpinBox(self)
        self.card_examples_spin.setRange(0, 4)
        self.card_definition_checkbox = QCheckBox(_t("card_definition"), self)
        self.card_pos_checkbox = QCheckBox(_t("card_pos"), self)
        self.card_extra_edit = QPlainTextEdit(self)
        self.card_extra_edit.setPlaceholderText(_t("card_extra_placeholder"))
        try:
            self.card_extra_edit.setFixedHeight(70)
            self.card_extra_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        except Exception:
            pass
        # Advanced toggle: hides the constructor and reveals the raw prompt editor.
        self.prompt_advanced_checkbox = QCheckBox(_t("prompt_advanced"), self)
        # Duplicate deck selection for AI (separate from manual tab)
        # Why separate: User may want different decks for manual vs AI-generated cards
        self.ai_deck_combo = QComboBox(self)
        # ===== Widgets that live in the settings sections =====
        self.settings_deck_combo = QComboBox(self)
        # Keep wide controls from forcing the window wide on small/Large-Text screens.
        for _cb in (self.model_combo, self.ai_deck_combo):
            try:
                _cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                _cb.setMinimumContentsLength(10)
                _cb.setMinimumWidth(120)
                _cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            except Exception:
                pass
        self.double_ctrl_c_checkbox = QCheckBox(_t("checkbox_double_ctrl_c"), self)
        self.double_ctrl_c_hint = self._hint_label(_t("hotkey_double_ctrl_c_hint"))
        self.hotkey_recorder = _HotkeyRecorderButton(self)
        try:
            self.hotkey_recorder.setMinimumWidth(120)
            self.hotkey_recorder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        except Exception:
            pass
        self.hotkey_clear_btn = QPushButton(_t("hotkey_clear_btn"), self)
        self.hotkey_clear_btn.clicked.connect(lambda: self.hotkey_recorder.set_combo(""))
        hotkey_row = QWidget(self)
        hotkey_row_layout = QHBoxLayout(hotkey_row)
        hotkey_row_layout.setContentsMargins(0, 0, 0, 0)
        hotkey_row_layout.addWidget(self.hotkey_recorder, 1)
        hotkey_row_layout.addWidget(self.hotkey_clear_btn)
        self.reverso_panel_checkbox = QCheckBox(_t("checkbox_reverso_panel"), self)
        self.sidebar_visible_checkbox = QCheckBox("Show review-dialog sidebar (Reverso + Image)", self)
        self.audio_panel_checkbox = QCheckBox(_t("checkbox_audio_panel"), self)
        self.reverso_src_edit = _make_language_combo(self)
        self.reverso_tgt_edit = _make_language_combo(self)

        # Manual deck combo is superseded by the AI deck; keep it hidden but synced.
        self.deck_combo.hide()
        self.settings_deck_combo.hide()

        # Constructor box (beginner): builds the prompt from a few toggles.
        self.constructor_box = QWidget(self)
        constructor_form = QFormLayout(self.constructor_box)
        constructor_form.setContentsMargins(0, 0, 0, 0)
        constructor_form.addRow(_t("card_translations"), self.card_translations_spin)
        constructor_form.addRow(_t("card_examples"), self.card_examples_spin)
        constructor_form.addRow(self.card_definition_checkbox)
        constructor_form.addRow(self.card_pos_checkbox)
        constructor_form.addRow(_t("card_extra"), self.card_extra_edit)

        # Advanced box (raw prompt + presets), revealed by the advanced checkbox.
        self.advanced_box = QWidget(self)
        advanced_form = QFormLayout(self.advanced_box)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        prompt_name_row = QWidget(self)
        prompt_name_layout = QHBoxLayout(prompt_name_row)
        prompt_name_layout.setContentsMargins(0, 0, 0, 0)
        prompt_name_layout.addWidget(self.prompt_prev_btn)
        prompt_name_layout.addWidget(self.prompt_name_edit)
        prompt_name_layout.addWidget(self.prompt_next_btn)
        advanced_form.addRow(_t("label_prompt_name"), prompt_name_row)
        advanced_form.addRow(_t("label_custom_prompt"), self.custom_prompt_edit)
        qconnect(self.prompt_advanced_checkbox.toggled, self._on_prompt_mode_toggled)

        # ===== Build the scrollable, single-page content with sidebar nav =====
        self._sections: list[tuple] = []  # (emoji, label, tip, section widget)
        content = QWidget(self)
        content_v = QVBoxLayout(content)
        content_v.setContentsMargins(18, 18, 18, 18)
        content_v.setSpacing(20)

        # Setup-guide launcher at the very top.
        self.setup_btn = QPushButton(_t("button_setup_guide"), self)
        try:
            self.setup_btn.setStyleSheet(
                "QPushButton { padding:10px; border-radius:10px; font-size:14px; font-weight:600;"
                " background-color: rgba(74,158,255,0.18); }"
                "QPushButton:hover { background-color: rgba(74,158,255,0.30); }"
            )
        except Exception:
            pass
        qconnect(self.setup_btn.clicked, self._open_setup_wizard)
        content_v.addWidget(self.setup_btn)

        # 1) AI provider
        sec, form = self._begin_section("🔑", _t("section_ai"), tip=_t("tip_ai"))
        form.addRow(self.api_settings_btn)
        form.addRow(self._hint_label(_t("wizard_rate_note")))
        # Provider/model are fixed now; keep the labels alive (used by the summary
        # refresh) but hidden.
        self.platform_value_label.hide()
        self.model_value_label.hide()
        content_v.addWidget(sec)

        # 3) Languages
        sec, form = self._begin_section("🌐", _t("section_languages"), _t("card_lang_hint2"), _t("tip_languages"))
        form.addRow(_t("label_reverso_src_lang"), self.reverso_src_edit)
        form.addRow(_t("label_reverso_tgt_lang"), self.reverso_tgt_edit)
        content_v.addWidget(sec)

        # 4) Card content (constructor + advanced toggle)
        sec, form = self._begin_section("🗂", _t("section_content"), tip=_t("tip_content"))
        form.addRow(self.prompt_advanced_checkbox)
        form.addRow(self.constructor_box)
        form.addRow(self.advanced_box)
        content_v.addWidget(sec)

        # 5) Hotkey
        sec, form = self._begin_section("⌨️", _t("section_hotkey"), tip=_t("tip_hotkey"))
        form.addRow(self.double_ctrl_c_checkbox)
        form.addRow(self.double_ctrl_c_hint)
        form.addRow(_t("label_custom_hotkey"), hotkey_row)
        content_v.addWidget(sec)

        # 6) Deck & notes
        sec, form = self._begin_section("📚", _t("section_deck"), tip=_t("tip_deck"))
        form.addRow(_t("label_note_type_short"), self.model_combo)
        self.new_deck_btn = QPushButton(_t("new_deck_btn"), self)
        qconnect(self.new_deck_btn.clicked, self._on_new_deck)
        deck_row = QWidget(self)
        deck_row_h = QHBoxLayout(deck_row)
        deck_row_h.setContentsMargins(0, 0, 0, 0)
        deck_row_h.addWidget(self.ai_deck_combo, 1)
        deck_row_h.addWidget(self.new_deck_btn)
        form.addRow(_t("label_deck_target"), deck_row)
        form.addRow(self.reverso_panel_checkbox)
        form.addRow(self.sidebar_visible_checkbox)
        form.addRow(self.audio_panel_checkbox)
        self.debug_report_btn = QPushButton(_t("debug_copy_btn"), self)
        self.debug_report_btn.setToolTip(_t("debug_copy_tip"))
        qconnect(self.debug_report_btn.clicked, self._on_copy_debug_report)
        form.addRow(self.debug_report_btn)
        form.addRow(self._hint_label(_t("debug_hint")))
        content_v.addWidget(sec)

        # 7) Feedback & support (last)
        sec, form = self._begin_section("💬", _t("section_feedback"), _t("feedback_intro"), _t("tip_feedback"))
        contact_lbl = QLabel(_t("feedback_contact_html"), self)
        try:
            contact_lbl.setTextFormat(Qt.TextFormat.RichText)
            contact_lbl.setOpenExternalLinks(True)
            contact_lbl.setWordWrap(True)
        except Exception:
            pass
        form.addRow(contact_lbl)
        self.feedback_contact_edit = QLineEdit(self)
        self.feedback_contact_edit.setPlaceholderText(_t("feedback_contact_ph"))
        form.addRow(_t("feedback_contact_label"), self.feedback_contact_edit)
        self.feedback_edit = QPlainTextEdit(self)
        self.feedback_edit.setPlaceholderText(_t("feedback_ph"))
        try:
            self.feedback_edit.setFixedHeight(64)
        except Exception:
            pass
        form.addRow(_t("feedback_label"), self.feedback_edit)
        self.feedback_send_btn = QPushButton(_t("feedback_send"), self)
        qconnect(self.feedback_send_btn.clicked, self._on_send_feedback)
        form.addRow(self.feedback_send_btn)
        self.full_log_btn = QPushButton(_t("feedback_full_log"), self)
        self.full_log_btn.setToolTip(_t("feedback_full_log_tip"))
        qconnect(self.full_log_btn.clicked, self._on_send_full_log)
        form.addRow(self.full_log_btn)
        self.telemetry_checkbox = QCheckBox(_t("telemetry_label"), self)
        form.addRow(self.telemetry_checkbox)
        form.addRow(self._hint_label(_t("telemetry_hint")))
        if not _bug_reporting_enabled():
            self.feedback_send_btn.setEnabled(False)
            self.full_log_btn.setEnabled(False)
        content_v.addWidget(sec)
        content_v.addStretch(1)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(content)
        try:
            self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
            # No horizontal scroll — content is constrained to the viewport width
            # (long labels wrap, fields shrink) instead of overflowing to the right.
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        except Exception:
            pass

        # Sidebar navigation as large icon tiles (icon on top, caption below) with
        # tooltips. Click jumps to a section; scrolling highlights the one in view.
        self.nav_list = QListWidget(self)
        self.nav_list.setFixedWidth(132)
        try:
            self.nav_list.setViewMode(QListView.ViewMode.IconMode)
            self.nav_list.setMovement(QListView.Movement.Static)
            self.nav_list.setFlow(QListView.Flow.TopToBottom)
            self.nav_list.setWrapping(False)
            self.nav_list.setResizeMode(QListView.ResizeMode.Adjust)
            self.nav_list.setIconSize(QSize(40, 40))
            self.nav_list.setSpacing(6)
            self.nav_list.setWordWrap(True)
            self.nav_list.setUniformItemSizes(True)
            nav_font = self.nav_list.font()
            nav_font.setPointSize(max(12, nav_font.pointSize() + 2))
            self.nav_list.setFont(nav_font)
        except Exception:
            pass
        for emoji, label, tip, _box in self._sections:
            item = QListWidgetItem(_emoji_icon(emoji), label)
            try:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setSizeHint(QSize(120, 78))
                if tip:
                    item.setToolTip(tip)
            except Exception:
                pass
            self.nav_list.addItem(item)
        self._nav_syncing = False
        qconnect(self.nav_list.currentRowChanged, self._on_nav_row_changed)
        try:
            self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll_spy)
        except Exception:
            pass

        body_row = QWidget(self)
        body_h = QHBoxLayout(body_row)
        body_h.setContentsMargins(0, 0, 0, 0)
        body_h.setSpacing(12)
        body_h.addWidget(self.nav_list)
        body_h.addWidget(self.scroll_area, 1)

        buttons = QDialogButtonBox(self)
        try:
            # PyQt6 API
            buttons.setStandardButtons(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
        except Exception:
            # PyQt5 / older API fallback
            buttons.setStandardButtons(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)

        footer = QWidget(self)
        footer_h = QHBoxLayout(footer)
        footer_h.setContentsMargins(8, 0, 8, 0)
        footer_h.addWidget(self.version_label)
        footer_h.addStretch(1)
        footer_h.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(body_row, 1)
        layout.addWidget(footer)
        try:
            # Fit the screen: never wider/taller than the available area (matters on
            # small screens and macOS "Larger Text" mode). Overflow goes to scroll.
            screen = QApplication.primaryScreen().availableGeometry()
            w = min(660, int(screen.width() * 0.95))
            h = min(700, int(screen.height() * 0.92))
            self.setMaximumSize(int(screen.width() * 0.98), int(screen.height() * 0.96))
            self.resize(w, h)
        except Exception:
            pass

        # Rename buttons and reassign actions
        # Why custom behavior: User requested "Confirm settings" (saves but doesn't close) and "Exit" (closes)
        # Instead of standard Ok/Cancel behavior
        try:
            ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
            cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        except Exception:
            ok_btn = buttons.button(QDialogButtonBox.Ok)
            cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        if ok_btn is not None:
            try:
                ok_btn.setText(_t("button_confirm_settings"))
            except Exception:
                pass
            qconnect(ok_btn.clicked, self._on_confirm_settings)
        if cancel_btn is not None:
            try:
                cancel_btn.setText(_t("button_exit"))
            except Exception:
                pass
            qconnect(cancel_btn.clicked, self.reject)

        # Additional button: minimize window
        # Why minimize: User requested ability to minimize dialog without closing
        minimize_btn = QPushButton(_t("button_minimize"), self)
        try:
            action_role = QDialogButtonBox.ButtonRole.ActionRole  # PyQt6
        except Exception:
            action_role = getattr(QDialogButtonBox, "ActionRole", None)  # PyQt5
            if action_role is None:
                # Last resort fallback to any safe role
                action_role = getattr(QDialogButtonBox, "ResetRole", 0)
        try:
            buttons.addButton(minimize_btn, action_role)
        except Exception:
            # If API doesn't support addButton with role
            try:
                buttons.addButton(minimize_btn, 0)  # type: ignore[arg-type]
            except Exception:
                pass
        qconnect(minimize_btn.clicked, self._on_minimize)

        qconnect(self.api_settings_btn.clicked, self._open_api_settings)
        qconnect(self.prompt_prev_btn.clicked, self._go_prev_prompt)
        qconnect(self.prompt_next_btn.clicked, self._go_next_prompt)

        self._populate_decks()
        self._populate_ai_decks()
        self._populate_settings_decks()
        self._sync_deck_combos_initial()
        self._populate_models()
        self._prefill_defaults()
        self._load_config_into_ui()
        self._connect_deck_sync_signals()
        self._refresh_api_settings_summary()
        self._apply_styles()
        try:
            self.nav_list.setCurrentRow(0)
        except Exception:
            pass

    def _on_new_deck(self) -> None:
        did = _create_deck_prompt(self)
        if did is None:
            return
        # Refresh all deck combos and select the newly created deck for AI cards.
        try:
            self._populate_decks()
            self._populate_ai_decks()
            self._populate_settings_decks()
            idx = self.ai_deck_combo.findData(did)
            if idx >= 0:
                self.ai_deck_combo.setCurrentIndex(idx)
        except Exception:
            pass

    def _on_copy_debug_report(self) -> None:
        """Copy an environment + log report to the clipboard so the user can paste it
        to the developer for bug diagnosis."""
        try:
            report = _build_debug_report()
            from aqt.qt import QGuiApplication
            app = QGuiApplication.instance()
            if app is not None:
                app.clipboard().setText(report)
            showInfo(_t("debug_copied"))
        except Exception as e:
            _log_exc("copy debug report")
            showWarning(str(e))

    def _on_send_feedback(self) -> None:
        text = self.feedback_edit.toPlainText().strip()
        if not text:
            showInfo(_t("feedback_empty"))
            return
        contact = self.feedback_contact_edit.text().strip()
        ok, err = _send_feedback_telegram(text, contact)
        if ok:
            self.feedback_edit.setPlainText("")
            showInfo(_t("feedback_sent"))
        else:
            showWarning(_t("error_send_failed", err=err))

    def _on_send_full_log(self) -> None:
        if not askUser(_t("feedback_full_log_confirm")):
            return
        contact = self.feedback_contact_edit.text().strip()
        ok, err = _send_full_log_telegram(contact)
        if ok:
            showInfo(_t("feedback_full_log_sent"))
        else:
            showWarning(_t("error_send_failed", err=err))

    def _open_setup_wizard(self) -> None:
        try:
            dlg = SetupWizard(self)
            dlg.exec()
            # Repopulate deck combos first: the wizard may have created a NEW deck
            # that isn't in the combos yet — without this the saved ai_deck_id won't
            # match any item and the new deck won't show up in the list.
            self._populate_decks()
            self._populate_ai_decks()
            self._populate_settings_decks()
            # Reload settings into the UI so wizard changes show immediately.
            self._load_config_into_ui()
            self._refresh_api_settings_summary()
        except Exception as e:
            _log(f"open setup wizard error {e!r}")

    def _apply_styles(self) -> None:
        """Modern, theme-aware QSS: section cards, sidebar tiles, accent button."""
        try:
            try:
                from aqt import theme as _theme_mod
                night = bool(_theme_mod.theme_manager.night_mode)
            except Exception:
                night = True
            card_bg = "rgba(255,255,255,0.05)" if night else "rgba(0,0,0,0.035)"
            card_border = "rgba(255,255,255,0.10)" if night else "rgba(0,0,0,0.10)"
            sel_bg = "rgba(74,158,255,0.22)"
            hover_bg = "rgba(127,127,127,0.12)"
            self.setStyleSheet(f"""
                QFrame#card {{
                    background-color: {card_bg};
                    border: 1px solid {card_border};
                    border-radius: 12px;
                }}
                QLabel#cardTitle {{
                    font-size: 16px;
                    font-weight: 600;
                }}
                QListWidget {{
                    border: none;
                    background: transparent;
                    outline: 0;
                }}
                QListWidget::item {{
                    border-radius: 10px;
                    margin: 3px 4px;
                    padding: 6px 2px;
                }}
                QListWidget::item:hover {{ background-color: {hover_bg}; }}
                QListWidget::item:selected {{ background-color: {sel_bg}; color: palette(text); }}
            """)
        except Exception:
            pass

    # ---- Sidebar / scrollable-section helpers ----
    def _hint_label(self, text: str) -> "QLabel":
        lbl = QLabel(text, self)
        try:
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: gray; font-size: 11px;")
        except Exception:
            pass
        return lbl

    def _begin_section(self, emoji: str, label: str, hint: str = "", tip: str = "") -> tuple:
        """Create a titled section card; returns (container_widget, QFormLayout).
        Registers (emoji, label, tip, box) for the sidebar tiles + scroll-spy."""
        box = QFrame(self)
        box.setObjectName("card")
        v = QVBoxLayout(box)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(8)
        title = QLabel(f"{emoji}  {label}", box)
        title.setObjectName("cardTitle")
        v.addWidget(title)
        if hint:
            v.addWidget(self._hint_label(hint))
        form = QFormLayout()
        form.setContentsMargins(0, 8, 0, 0)
        form.setVerticalSpacing(11)
        form.setHorizontalSpacing(14)
        try:
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        except Exception:
            pass
        v.addLayout(form)
        self._sections.append((emoji, label, tip, box))
        return box, form

    def _on_nav_row_changed(self, row: int) -> None:
        if getattr(self, "_nav_syncing", False):
            return
        if row < 0 or row >= len(self._sections):
            return
        box = self._sections[row][3]
        self._nav_syncing = True
        try:
            self.scroll_area.verticalScrollBar().setValue(max(0, box.y() - 8))
        except Exception:
            try:
                self.scroll_area.ensureWidgetVisible(box)
            except Exception:
                pass
        finally:
            self._nav_syncing = False

    def _on_scroll_spy(self, value: int) -> None:
        if getattr(self, "_nav_syncing", False):
            return
        current = 0
        try:
            for i, sect in enumerate(self._sections):
                box = sect[3]
                if box.y() <= value + 24:
                    current = i
                else:
                    break
            if self.nav_list.currentRow() != current:
                self._nav_syncing = True
                self.nav_list.setCurrentRow(current)
                self._nav_syncing = False
        except Exception:
            self._nav_syncing = False

    def _on_confirm_settings(self) -> None:
        # Save current settings, window remains open
        # Why not close: User requested "Confirm settings" to save without closing dialog
        # Allows testing settings changes without reopening dialog
        self._save_config_from_ui()

    def _on_minimize(self) -> None:
        try:
            self.showMinimized()
        except Exception:
            try:
                self.hide()
            except Exception:
                pass

    def _get_all_decks(self) -> list[tuple[str, int]]:
        col = mw.col
        if col is None:
            return []
        decks = col.decks
        items: list[tuple[str, int]] = []

        all_names_and_ids = getattr(decks, "all_names_and_ids", None)
        if callable(all_names_and_ids):
            for entry in all_names_and_ids():
                name = getattr(entry, "name", None)
                deck_id = getattr(entry, "id", None)
                if name is None or deck_id is None:
                    try:
                        name, deck_id = entry  # type: ignore[misc]
                    except Exception:
                        continue
                items.append((str(name), int(deck_id)))
        else:
            all_names = getattr(decks, "allNames", None)
            get_id = getattr(decks, "id", None) or getattr(decks, "idForName", None)
            if callable(all_names) and callable(get_id):
                for name in all_names():
                    deck_id = get_id(name)
                    items.append((name, deck_id))
        return items

    def _populate_decks(self) -> None:
        # Block signals while rebuilding: clear()/addItem() emit currentIndexChanged,
        # and the deck-sync handlers would fire mid-repopulation, cascading across all
        # three combos. On Windows/Qt that re-entrant storm can freeze the UI.
        items = self._get_all_decks()
        self.deck_combo.blockSignals(True)
        self.deck_combo.clear()
        for name, deck_id in items:
            self.deck_combo.addItem(name, deck_id)
        self.deck_combo.blockSignals(False)

    def _populate_ai_decks(self) -> None:
        items = self._get_all_decks()
        self.ai_deck_combo.blockSignals(True)
        self.ai_deck_combo.clear()
        for name, deck_id in items:
            self.ai_deck_combo.addItem(name, deck_id)
        self.ai_deck_combo.blockSignals(False)

    def _populate_settings_decks(self) -> None:
        items = self._get_all_decks()
        self.settings_deck_combo.blockSignals(True)
        self.settings_deck_combo.clear()
        for name, deck_id in items:
            self.settings_deck_combo.addItem(name, deck_id)
        self.settings_deck_combo.blockSignals(False)

    def _populate_models(self) -> None:
        col = mw.col
        if col is None:
            return
        models = col.models
        all_names = getattr(models, "all_names", None)
        if callable(all_names):
            names = list(all_names())
        else:
            # older API
            all_ = getattr(models, "all", None)
            names = [m["name"] for m in all_()] if callable(all_) else []

        names.sort(key=lambda x: x.lower())
        for name in names:
            self.model_combo.addItem(name)

    def _prefill_defaults(self) -> None:
        # Select Basic if available
        idx = self.model_combo.findText("Basic")
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        # Select Default deck if available
        idx = self.deck_combo.findText("Default")
        if idx >= 0:
            self.deck_combo.setCurrentIndex(idx)
        # Sync AI deck with main one on initial setup
        idx_ai = self.ai_deck_combo.findText("Default")
        if idx_ai >= 0:
            self.ai_deck_combo.setCurrentIndex(idx_ai)
        # And for settings
        idx_settings = self.settings_deck_combo.findText("Default")
        if idx_settings >= 0:
            self.settings_deck_combo.setCurrentIndex(idx_settings)

    def _sync_deck_combos_initial(self) -> None:
        # Set same index if names match
        # Why sync: User expects deck selection to be consistent across tabs
        # Initial sync ensures all combos start with same selection
        name = self.deck_combo.currentText()
        if name:
            idx_ai = self.ai_deck_combo.findText(name)
            if idx_ai >= 0:
                self.ai_deck_combo.setCurrentIndex(idx_ai)
            idx_settings = self.settings_deck_combo.findText(name)
            if idx_settings >= 0:
                self.settings_deck_combo.setCurrentIndex(idx_settings)

    def _connect_deck_sync_signals(self) -> None:
        def sync_from_manual(idx: int) -> None:
            name = self.deck_combo.itemText(idx)
            target_idx = self.ai_deck_combo.findText(name)
            if target_idx >= 0 and target_idx != self.ai_deck_combo.currentIndex():
                self.ai_deck_combo.blockSignals(True)
                self.ai_deck_combo.setCurrentIndex(target_idx)
                self.ai_deck_combo.blockSignals(False)
            target_idx2 = self.settings_deck_combo.findText(name)
            if target_idx2 >= 0 and target_idx2 != self.settings_deck_combo.currentIndex():
                self.settings_deck_combo.blockSignals(True)
                self.settings_deck_combo.setCurrentIndex(target_idx2)
                self.settings_deck_combo.blockSignals(False)

        def sync_from_ai(idx: int) -> None:
            name = self.ai_deck_combo.itemText(idx)
            target_idx = self.deck_combo.findText(name)
            if target_idx >= 0 and target_idx != self.deck_combo.currentIndex():
                self.deck_combo.blockSignals(True)
                self.deck_combo.setCurrentIndex(target_idx)
                self.deck_combo.blockSignals(False)
            target_idx2 = self.settings_deck_combo.findText(name)
            if target_idx2 >= 0 and target_idx2 != self.settings_deck_combo.currentIndex():
                self.settings_deck_combo.blockSignals(True)
                self.settings_deck_combo.setCurrentIndex(target_idx2)
                self.settings_deck_combo.blockSignals(False)

        def sync_from_settings(idx: int) -> None:
            name = self.settings_deck_combo.itemText(idx)
            target_idx = self.deck_combo.findText(name)
            if target_idx >= 0 and target_idx != self.deck_combo.currentIndex():
                self.deck_combo.blockSignals(True)
                self.deck_combo.setCurrentIndex(target_idx)
                self.deck_combo.blockSignals(False)
            target_idx2 = self.ai_deck_combo.findText(name)
            if target_idx2 >= 0 and target_idx2 != self.ai_deck_combo.currentIndex():
                self.ai_deck_combo.blockSignals(True)
                self.ai_deck_combo.setCurrentIndex(target_idx2)
                self.ai_deck_combo.blockSignals(False)

        try:
            qconnect(self.deck_combo.currentIndexChanged, sync_from_manual)
        except Exception:
            pass
        try:
            qconnect(self.ai_deck_combo.currentIndexChanged, sync_from_ai)
        except Exception:
            pass
        try:
            qconnect(self.settings_deck_combo.currentIndexChanged, sync_from_settings)
        except Exception:
            pass

    def _load_config_into_ui(self) -> None:
        cfg = _get_config()
        fallback_prompt = str(cfg.get("custom_prompt", "") or "").strip()
        self._prompt_presets = self._normalize_prompt_presets(cfg.get("prompt_presets"), fallback_prompt)
        saved_idx = cfg.get("prompt_preset_index", 0)
        try:
            self._prompt_preset_index = int(saved_idx)
        except Exception:
            self._prompt_preset_index = 0
        if self._prompt_preset_index < 0 or self._prompt_preset_index > len(self._prompt_presets):
            self._prompt_preset_index = 0
        # The actual preset display happens later (after the constructor widgets and
        # language combos are loaded, so the preview page reflects real settings).
        self.reverso_panel_checkbox.setChecked(bool(cfg.get("reverso_panel_enabled", True)))
        self.sidebar_visible_checkbox.setChecked(bool(cfg.get("sidebar_visible", True)))
        self.audio_panel_checkbox.setChecked(bool(cfg.get("audio_panel_enabled", True)))
        self.telemetry_checkbox.setChecked(bool(cfg.get("telemetry_enabled", True)))
        _set_language_combo(self.reverso_src_edit, str(cfg.get("reverso_source_lang", REVERSO_DEFAULT_SRC)))
        _set_language_combo(self.reverso_tgt_edit, str(cfg.get("reverso_target_lang", REVERSO_DEFAULT_TGT)))
        # Double Ctrl+C gesture (default on)
        self.double_ctrl_c_checkbox.setChecked(bool(cfg.get("double_ctrl_c_enabled", True)))
        # Custom hotkey combo (recorder)
        self.hotkey_recorder.set_combo(str(cfg.get("hotkey_combo", "")).strip())
        # Card-content constructor
        try:
            self.card_translations_spin.setValue(int(cfg.get("card_translations", 4)))
        except Exception:
            self.card_translations_spin.setValue(4)
        try:
            self.card_examples_spin.setValue(int(cfg.get("card_examples", 2)))
        except Exception:
            self.card_examples_spin.setValue(2)
        self.card_definition_checkbox.setChecked(bool(cfg.get("card_definition", True)))
        self.card_pos_checkbox.setChecked(bool(cfg.get("card_pos", False)))
        self.card_extra_edit.setPlainText(str(cfg.get("card_extra", "") or ""))
        advanced = str(cfg.get("prompt_mode", "constructor")).strip().lower() == "advanced"
        self.prompt_advanced_checkbox.setChecked(advanced)
        self._on_prompt_mode_toggled(advanced)
        # Now that constructor widgets + languages are loaded, show the preset page
        # (page 1 preview reflects the real settings).
        self._show_prompt_preset(int(getattr(self, "_prompt_preset_index", 0)))
        # Restore the AI deck, if saved
        ai_deck_id = cfg.get("ai_deck_id")
        if ai_deck_id is not None:
            # Find the index by userdata
            for i in range(self.ai_deck_combo.count()):
                if int(self.ai_deck_combo.itemData(i)) == int(ai_deck_id):
                    self.ai_deck_combo.setCurrentIndex(i)
                    break
        # Restore the Settings deck if saved
        settings_deck_id = cfg.get("settings_deck_id")
        if settings_deck_id is not None:
            for i in range(self.settings_deck_combo.count()):
                if int(self.settings_deck_combo.itemData(i)) == int(settings_deck_id):
                    self.settings_deck_combo.setCurrentIndex(i)
                    break
        # Restore the note type, if saved
        note_model_name = str(cfg.get("note_model_name", "")).strip()
        if note_model_name:
            idx = self.model_combo.findText(note_model_name)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
        self._refresh_api_settings_summary()

    def _save_config_from_ui(self) -> None:
        cfg = _get_config()
        self._save_current_prompt_preset()
        cfg["prompt_presets"] = self._prompt_presets
        view = int(getattr(self, "_prompt_preset_index", 0))
        cfg["prompt_preset_index"] = view
        # Active advanced prompt: on the preview page (view 0) generation behaves
        # like the constructor (content-only, contract added later); on a preset
        # page it's that preset's text.
        if view >= 1 and (view - 1) < len(self._prompt_presets):
            cfg["custom_prompt"] = str(self._prompt_presets[view - 1].get("prompt", "")).strip()
        else:
            cfg["custom_prompt"] = self._constructor_content_from_ui()
        cfg["settings_deck_id"] = int(self.settings_deck_combo.currentData()) if self.settings_deck_combo.currentData() is not None else None
        # Activation engine is always the platform built-in now (no UI toggle).
        cfg["hotkey_mode"] = "mac" if sys.platform == "darwin" else ("win" if sys.platform.startswith("win") else "external")
        cfg["double_ctrl_c_enabled"] = bool(self.double_ctrl_c_checkbox.isChecked())
        # Custom hotkey combo recorded by the user (e.g. "cmd+option+t", "f8").
        cfg["hotkey_combo"] = self.hotkey_recorder.combo()
        # Save selected AI deck and note type
        cfg["ai_deck_id"] = int(self.ai_deck_combo.currentData()) if self.ai_deck_combo.currentData() is not None else None
        cfg["note_model_name"] = self.model_combo.currentText().strip() or "Basic"
        cfg["reverso_panel_enabled"] = bool(self.reverso_panel_checkbox.isChecked())
        cfg["sidebar_visible"] = bool(self.sidebar_visible_checkbox.isChecked())
        cfg["audio_panel_enabled"] = bool(self.audio_panel_checkbox.isChecked())
        cfg["telemetry_enabled"] = bool(self.telemetry_checkbox.isChecked())
        cfg["reverso_source_lang"] = _language_combo_code(self.reverso_src_edit, REVERSO_DEFAULT_SRC)
        cfg["reverso_target_lang"] = _language_combo_code(self.reverso_tgt_edit, REVERSO_DEFAULT_TGT)
        # Card-content constructor
        cfg["prompt_mode"] = "advanced" if self.prompt_advanced_checkbox.isChecked() else "constructor"
        cfg["card_translations"] = int(self.card_translations_spin.value())
        cfg["card_examples"] = int(self.card_examples_spin.value())
        cfg["card_definition"] = bool(self.card_definition_checkbox.isChecked())
        cfg["card_pos"] = bool(self.card_pos_checkbox.isChecked())
        cfg["card_extra"] = self.card_extra_edit.toPlainText().strip()
        _set_config(cfg)
        # Apply hotkey changes immediately to the running listener (no restart).
        _refresh_hotkey_state()

    def _normalize_prompt_presets(self, raw, fallback_prompt: str = "") -> list[dict]:
        presets = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                prompt = str(item.get("prompt", ""))
                if not name:
                    name = "Default"
                presets.append({"name": name, "prompt": prompt})
        if not presets:
            presets = [{"name": "Default", "prompt": fallback_prompt or DEFAULT_CUSTOM_PROMPT}]
        return presets[:100]

    # Advanced-mode preset navigation. View index 0 is a READ-ONLY preview of the
    # prompt the constructor currently produces ("page 1"); views 1..N map to the
    # stored editable custom presets ("page 2"+). The user's saved prompt is
    # presets[0], so it naturally lands on page 2.
    def _constructor_content_from_ui(self) -> str:
        """Build the constructor CONTENT (no format contract) from the current UI
        widget values, regardless of saved config."""
        cfg = dict(_get_config())
        cfg["prompt_mode"] = "constructor"
        cfg["reverso_source_lang"] = _language_combo_code(self.reverso_src_edit, REVERSO_DEFAULT_SRC)
        cfg["reverso_target_lang"] = _language_combo_code(self.reverso_tgt_edit, REVERSO_DEFAULT_TGT)
        cfg["card_translations"] = int(self.card_translations_spin.value())
        cfg["card_examples"] = int(self.card_examples_spin.value())
        cfg["card_definition"] = bool(self.card_definition_checkbox.isChecked())
        cfg["card_pos"] = bool(self.card_pos_checkbox.isChecked())
        cfg["card_extra"] = self.card_extra_edit.toPlainText().strip()
        return _build_content_prompt(cfg)

    def _constructor_preview_text(self) -> str:
        """The full prompt (format contract + constructor content) for the current
        constructor settings — what 'page 1' shows."""
        try:
            return _FORMAT_CONTRACT + "\n\n" + self._constructor_content_from_ui()
        except Exception:
            return _FORMAT_CONTRACT

    def _set_prompt_readonly(self, ro: bool) -> None:
        try:
            self.custom_prompt_edit.setReadOnly(ro)
            self.prompt_name_edit.setReadOnly(ro)
        except Exception:
            pass

    def _prompt_view_count(self) -> int:
        return 1 + len(getattr(self, "_prompt_presets", []) or [])

    def _show_prompt_preset(self, index: int) -> None:
        if not getattr(self, "_prompt_presets", None):
            self._prompt_presets = [{"name": "Default", "prompt": DEFAULT_CUSTOM_PROMPT}]
        total = self._prompt_view_count()
        if index < 0:
            index = total - 1
        if index >= total:
            index = 0
        self._prompt_preset_index = index
        if index == 0:
            # Page 1: read-only live preview of the constructor prompt.
            self.prompt_name_edit.setText(_t("prompt_preview_name"))
            self.custom_prompt_edit.setPlainText(self._constructor_preview_text())
            self._set_prompt_readonly(True)
        else:
            current = self._prompt_presets[index - 1]
            self.prompt_name_edit.setText(str(current.get("name", "")).strip() or "Unnamed")
            self.custom_prompt_edit.setPlainText(str(current.get("prompt", "")))
            self._set_prompt_readonly(False)

    def _save_current_prompt_preset(self) -> None:
        # Never persist the read-only preview page (view 0).
        if not getattr(self, "_prompt_presets", None):
            self._prompt_presets = [{"name": "Default", "prompt": DEFAULT_CUSTOM_PROMPT}]
        view = int(getattr(self, "_prompt_preset_index", 0))
        if view <= 0:
            return
        idx = view - 1
        if idx < 0 or idx >= len(self._prompt_presets):
            return
        name = self.prompt_name_edit.text().strip() or "Unnamed"
        prompt = self.custom_prompt_edit.toPlainText()
        self._prompt_presets[idx] = {"name": name, "prompt": prompt}

    def _go_prev_prompt(self) -> None:
        self._save_current_prompt_preset()
        self._show_prompt_preset(int(getattr(self, "_prompt_preset_index", 0)) - 1)

    def _go_next_prompt(self) -> None:
        self._save_current_prompt_preset()
        if not self._prompt_presets:
            self._prompt_presets = [{"name": "Default", "prompt": DEFAULT_CUSTOM_PROMPT}]
        view = int(getattr(self, "_prompt_preset_index", 0))
        # On the last preset page, auto-grow a new empty preset (cap at 100).
        if view == self._prompt_view_count() - 1 and len(self._prompt_presets) < 100:
            self._prompt_presets.append({"name": "New Preset", "prompt": ""})
        self._show_prompt_preset(view + 1)

    def _refresh_api_settings_summary(self) -> None:
        cfg = _get_config()
        platform = str(cfg.get("ai_platform", DEFAULT_PLATFORM)).strip().lower() or DEFAULT_PLATFORM
        if platform == "groq":
            model = str(cfg.get("groq_model", DEFAULT_GROQ_MODEL)).strip() or DEFAULT_GROQ_MODEL
            platform_title = "Groq"
        else:
            platform = "google"
            model = DEFAULT_GEMINI_MODEL  # fixed: only the 3.1 model is supported
            platform_title = "Google"
        self.platform_value_label.setText(platform_title)
        self.model_value_label.setText(model)

    def _open_api_settings(self) -> None:
        dlg = ApiSettingsDialog(self)
        result = dlg.exec()
        try:
            accepted_code = QDialog.DialogCode.Accepted
        except Exception:
            accepted_code = getattr(QDialog, "Accepted", 1)
        if int(result) == int(accepted_code):
            dlg.save_to_config()
            self._refresh_api_settings_summary()

    def _on_prompt_mode_toggled(self, advanced: bool) -> None:
        """Show the raw-prompt editor in advanced mode, the constructor otherwise."""
        try:
            self.constructor_box.setVisible(not advanced)
            self.advanced_box.setVisible(advanced)
        except Exception:
            pass


def add_card_with_dialog() -> None:
    """Open the FreeCard window (Create + settings). Card adding happens inside it
    via Generate → review/auto-add; there is no separate manual-entry flow."""
    col = mw.col
    if col is None:
        showWarning(_t("error_collection_not_loaded"))
        return
    dlg = AddCardDialog()
    dlg.exec()


ui_action = QAction(_t("menu_add_card"), mw)
qconnect(ui_action.triggered, add_card_with_dialog)
mw.form.menuTools.addAction(ui_action)

# Initialize global hotkey state flags
_mac_hotkey_started = False
_win_hotkey_started = False

# Activate the global hotkey. The built-in platform listener is the default;
# the external HTTP listener is only started if the user explicitly opts in.
try:
    cfg_boot = _get_config()
    default_mode = "mac" if sys.platform == "darwin" else ("win" if sys.platform.startswith("win") else "external")
    mode = str(cfg_boot.get("hotkey_mode", default_mode))
    _log(f"startup: hotkey activation mode={mode}")
    if mode == "external":
        _start_http_listener()
    elif mode in ("mac", "win"):
        _start_platform_global_hotkey()
except Exception as e:
    _log(f"startup: hotkey activation error {e!r}")

# The addon's load path — check ai.log if Anki picks up the "wrong" copy.
try:
    _log(f"addon_loaded path={os.path.abspath(__file__)} __version__={__version__}")
except Exception:
    pass


# First-run setup wizard + anonymous startup stat. Runs once the profile (and
# collection) is fully loaded; the wizard only appears until setup is completed.
def _on_profile_open_freecard() -> None:
    try:
        _stat_on_startup()
    except Exception as e:
        _log(f"startup stat error {e!r}")
    try:
        if _get_config().get("setup_done"):
            return

        def _show_wizard() -> None:
            try:
                if _get_config().get("setup_done"):
                    return
                SetupWizard(mw).exec()
            except Exception as e:
                _log(f"first-run wizard error {e!r}")

        # Defer briefly so the main window is fully painted before the modal opens.
        QTimer.singleShot(400, _show_wizard)
    except Exception as e:
        _log(f"first-run wizard check error {e!r}")


try:
    from aqt import gui_hooks as _gui_hooks
    _gui_hooks.profile_did_open.append(_on_profile_open_freecard)
    _log("startup: profile_did_open hook registered (first-run wizard + stats)")
except Exception as e:
    _log(f"startup: profile hook registration error {e!r}")
