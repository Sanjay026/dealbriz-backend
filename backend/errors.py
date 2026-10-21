"""Global error handling + the endpoint that receives frontend errors.

The rule applied throughout: log the detail, return the summary. A client
gets a plain message and an error id; the traceback stays server-side.
"""
import logging

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import HTTPException

from logging_config import new_error_id

log = logging.getLogger("dealbridge.errors")
client_log = logging.getLogger("dealbridge.client")

bp = Blueprint("logs", __name__, url_prefix="/api/logs")

# A browser can produce errors in a loop (a broken render, a retrying
# request). Without a cap, one bad tab could write gigabytes into the log.
MAX_MESSAGE = 500
MAX_STACK = 4000
MAX_URL = 300


def register_error_handlers(app):
    @app.errorhandler(HTTPException)
    def handle_http_exception(e):
        """404s, 405s, 413 payload-too-large and friends. These are expected
        and don't need a stack trace — just a consistent JSON shape so the
        frontend never has to parse an HTML error page."""
        if e.code and e.code >= 500:
            error_id = new_error_id()
            log.error("[%s] %s %s -> %s", error_id, request.method, request.path, e, exc_info=True)
            return jsonify({"error": e.description, "error_id": error_id}), e.code
        log.info("%s %s -> %s %s", request.method, request.path, e.code, e.name)
        return jsonify({"error": e.description}), e.code

    @app.errorhandler(Exception)
    def handle_unexpected(e):
        """Anything not already handled. The traceback goes to the log with an
        id; the client is told only that something failed, plus that id."""
        error_id = new_error_id()
        log.error(
            "[%s] Unhandled error on %s %s", error_id, request.method, request.path,
            exc_info=True,
        )
        return jsonify({
            "error": "Something went wrong on our end. Please try again.",
            "error_id": error_id,
        }), 500

    @app.errorhandler(413)
    def handle_too_large(e):
        # Worth its own message: the generic 413 text doesn't tell a user
        # what to actually do about it.
        return jsonify({"error": "That file is too large. Maximum size is 10 MB."}), 413


@bp.route("/client", methods=["POST"])
def log_client_error():
    """Receives JavaScript errors from browsers.

    Deliberately unauthenticated — the errors most worth seeing are the ones
    that break the page before a user can log in. That makes it writable by
    anyone, so everything is length-capped and it's logged to its own file,
    never mixed into the server's own logs.
    """
    data = request.get_json(silent=True) or {}

    message = str(data.get("message") or "")[:MAX_MESSAGE]
    if not message:
        return jsonify({"ok": False}), 400

    entry = {
        "message": message,
        "source": str(data.get("source") or "")[:MAX_URL],
        "line": data.get("line"),
        "column": data.get("column"),
        "stack": str(data.get("stack") or "")[:MAX_STACK],
        "page": str(data.get("page") or "")[:MAX_URL],
        "user_agent": str(request.headers.get("User-Agent", ""))[:MAX_URL],
        "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
    }

    client_log.error(
        "JS error: %s | page=%s | source=%s:%s:%s | ua=%s | ip=%s\n%s",
        entry["message"], entry["page"], entry["source"], entry["line"],
        entry["column"], entry["user_agent"], entry["ip"], entry["stack"],
    )
    # Always 204: a logging endpoint that returns errors would make the
    # frontend's error handler itself a source of errors.
    return "", 204


# ===================== LOG VIEWER (staff) =====================
# Reading logs through the admin panel rather than `docker compose exec`
# means an on-call developer doesn't need shell access to the production
# host just to see why something failed.

import os
import re
from datetime import datetime

from permissions import require_capability

# Only these three. A whitelist rather than a path parameter, because
# accepting a filename would let someone read /etc/passwd via ../ traversal.
LOG_FILES = {
    "app": ("app.log", "Everything at the configured level and above"),
    "error": ("error.log", "Errors only \u2014 what you want at 2am"),
    "client": ("client.log", "JavaScript errors reported by browsers"),
}

MAX_LINES = 2000

# "2026-08-21 14:03:11 ERROR    [dealbriz.errors] message"
LOG_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(?P<level>[A-Z]+)\s+"
    r"\[(?P<logger>[^\]]+)\]\s+"
    r"(?P<message>.*)$"
)


@bp.route("/files", methods=["GET"])
@require_capability("logs.view")
def list_log_files():
    from logging_config import LOG_DIR
    out = []
    for key, (name, desc) in LOG_FILES.items():
        path = os.path.join(LOG_DIR, name)
        exists = os.path.exists(path)
        out.append({
            "key": key, "name": name, "description": desc,
            "exists": exists,
            "size_bytes": os.path.getsize(path) if exists else 0,
            "modified": (datetime.utcfromtimestamp(os.path.getmtime(path)).isoformat()
                         if exists else None),
        })
    return jsonify({"files": out, "log_dir": LOG_DIR})


@bp.route("/view/<key>", methods=["GET"])
@require_capability("logs.view")
def view_log(key):
    """Returns the tail of a log file, newest first, parsed into fields so
    the UI can colour by level and filter without re-parsing text."""
    from logging_config import LOG_DIR

    if key not in LOG_FILES:
        return jsonify({"error": "Unknown log file"}), 404

    try:
        limit = min(int(request.args.get("limit", 300)), MAX_LINES)
    except (TypeError, ValueError):
        limit = 300
    level_filter = (request.args.get("level") or "").upper()
    search = (request.args.get("q") or "").strip().lower()

    path = os.path.join(LOG_DIR, LOG_FILES[key][0])
    if not os.path.exists(path):
        return jsonify({"entries": [], "total": 0, "note": "This log file doesn't exist yet."})

    try:
        # Read the tail only. Logs rotate at 5 MB, so loading the whole file
        # to show the last 300 lines would be wasteful on every refresh.
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            window = min(size, 1_500_000)
            fh.seek(size - window)
            raw = fh.read().decode("utf-8", errors="replace")
    except OSError as exc:
        log.error("Could not read log %s: %s", key, exc)
        return jsonify({"error": "Could not read that log file."}), 500

    lines = raw.splitlines()
    if window < size and lines:
        lines = lines[1:]          # drop a partial first line

    entries, current = [], None
    for line in lines:
        m = LOG_LINE.match(line)
        if m:
            current = {
                "timestamp": m.group("ts"),
                "level": m.group("level"),
                "logger": m.group("logger"),
                "message": m.group("message"),
                "detail": [],
            }
            entries.append(current)
        elif current is not None:
            # Traceback lines belong to the entry above them, not on their own.
            current["detail"].append(line)
        else:
            entries.append({"timestamp": None, "level": "INFO", "logger": "",
                            "message": line, "detail": []})

    if level_filter and level_filter != "ALL":
        entries = [e for e in entries if e["level"] == level_filter]
    if search:
        entries = [e for e in entries
                   if search in e["message"].lower()
                   or search in e["logger"].lower()
                   or any(search in d.lower() for d in e["detail"])]

    total = len(entries)
    entries = entries[-limit:][::-1]      # newest first
    for e in entries:
        e["detail"] = "\n".join(e["detail"])[:4000]

    counts = {}
    for e in entries:
        counts[e["level"]] = counts.get(e["level"], 0) + 1

    return jsonify({
        "entries": entries, "total": total, "showing": len(entries),
        "counts": counts, "file": LOG_FILES[key][0],
    })
