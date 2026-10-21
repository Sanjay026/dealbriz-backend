"""Application logging.

Two deliberate choices worth knowing:

1. Logs go to BOTH stdout and rotating files. stdout is what `docker logs`
   and most orchestrators read; the files survive a container restart when
   the log directory is mounted as a volume.

2. Nothing here ever puts a stack trace in an HTTP response. Tracebacks name
   file paths, library versions and sometimes query fragments — useful to us,
   useful to an attacker. The client gets an error id; the trace goes to the
   log, and the two are matched up by that id.
"""
import logging
import os
import sys
import uuid
from logging.handlers import RotatingFileHandler

LOG_DIR = os.environ.get("LOG_DIR", "logs")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# 5 MB per file, 5 files kept — bounded so logs can't quietly fill the disk
# and take the app down with them.
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def new_error_id():
    """Short id shown to the user and written to the log, so a report of
    'I saw error 7f3a91c2' can be found instantly."""
    return uuid.uuid4().hex[:8]


def _file_handler(filename, level):
    os.makedirs(LOG_DIR, exist_ok=True)
    h = RotatingFileHandler(
        os.path.join(LOG_DIR, filename),
        maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8",
    )
    h.setLevel(level)
    h.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    return h


def configure_logging(app):
    """Wire up handlers once, on the root logger, so libraries that log
    (SQLAlchemy, Werkzeug) land in the same place as our own messages."""
    root = logging.getLogger()
    root.setLevel(LOG_LEVEL)

    # Re-running this (e.g. gunicorn importing the module twice) would
    # otherwise duplicate every line.
    if any(getattr(h, "_dealbridge", False) for h in root.handlers):
        return

    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(LOG_LEVEL)
    stream.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))

    handlers = [stream]
    try:
        handlers.append(_file_handler("app.log", LOG_LEVEL))
        # Errors also get their own file — when something breaks at 2am you
        # want the failures without scrolling past every request log.
        handlers.append(_file_handler("error.log", logging.ERROR))
        # Frontend reports are separate: they're lower-trust (anyone can POST
        # to that endpoint) and shouldn't be mixed into server logs.
        client_logger = logging.getLogger("dealbridge.client")
        client_logger.propagate = False
        ch = _file_handler("client.log", logging.INFO)
        ch._dealbridge = True
        client_logger.addHandler(ch)
        client_logger.addHandler(stream)
    except OSError as exc:
        # A read-only or missing log volume must not stop the app booting —
        # stdout logging still works, so we degrade instead of crashing.
        stream.handle(logging.LogRecord(
            "dealbridge", logging.WARNING, __file__, 0,
            "File logging disabled (%s). Logging to stdout only.", (exc,), None,
        ))

    for h in handlers:
        h._dealbridge = True
        root.addHandler(h)

    app.logger.setLevel(LOG_LEVEL)
    logging.getLogger("werkzeug").setLevel(
        logging.WARNING if LOG_LEVEL != "DEBUG" else logging.INFO
    )
    app.logger.info("Logging configured (level=%s, dir=%s)", LOG_LEVEL, LOG_DIR)
