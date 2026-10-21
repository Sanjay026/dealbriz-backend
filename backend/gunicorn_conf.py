"""Gunicorn configuration.

Values are environment-overridable so the same image runs on a small VPS
and a larger box without a rebuild.
"""
import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:5000")

# (2 x cores) + 1 is the usual starting point for sync workers. Capped at 8:
# each worker opens its own database connections, and it's easy to exhaust
# MySQL's connection limit on a small server long before CPU is the problem.
_default_workers = min((multiprocessing.cpu_count() * 2) + 1, 8)
workers = int(os.environ.get("GUNICORN_WORKERS", _default_workers))
threads = int(os.environ.get("GUNICORN_THREADS", 2))

worker_class = "gthread"

# Longer than the default 30s: image uploads on a slow mobile connection
# were being cut off mid-request.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 120))
graceful_timeout = 30
keepalive = 5

# Recycle workers periodically. Guards against a slow leak in any dependency
# turning into an out-of-memory kill days later; jitter stops every worker
# restarting at the same moment.
max_requests = 1000
max_requests_jitter = 100

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()
# Includes response time (%(L)s) — the first thing you want when someone
# says "the site feels slow".
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(L)ss "%(a)s"'

proc_name = "dealbridge"


def on_starting(server):
    server.log.info("DealBriz starting: %s workers, %s threads each", workers, threads)
