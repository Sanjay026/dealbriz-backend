"""Product analytics: what visitors look at, for Admin -> Analytics.

  POST /api/track                         the website (and later the app) sends events in batches
  GET  /api/admin/analytics/summary       totals, CTR, dwell, devices, categories explored, per day
  GET  /api/admin/analytics/products      per listing: impressions, clicks, CTR, visitors, dwell

What is stored (models/analytics.py ProductEvent): event type, listing,
category, dwell time, where on the site (source), a random per-tab session
id, the user id when signed in, device type (from the User-Agent), and a
keyed hash of the IP. No raw IP. Not recorded at all: bots, staff accounts,
and a seller's views of their own listings.

Setting (optional, ~/deal_briz/.env): ANALYTICS_IP_KEY, the key for the IP
hash. Without it a key derived from SECRET_KEY is used.
"""
import hashlib
import hmac
import os
import re
from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import case, distinct, func

from extensions import db
from permissions import require_capability

bp = Blueprint("analytics", __name__)

TYPES = ("impression", "click", "dwell", "category")
MAX_EVENTS = 50
MAX_DWELL_MS = 30 * 60 * 1000
MIN_DWELL_MS = 500
SESSION_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
BOT_RE = re.compile(r"bot|crawl|spider|slurp|facebookexternalhit|whatsapp|preview|headless|curl|wget|python-requests",
                    re.I)


def device_type(ua):
    ua = ua or ""
    if not ua or BOT_RE.search(ua):
        return "bot"
    if re.search(r"iPad|Tablet|PlayBook|Silk|Android(?!.*Mobile)", ua, re.I):
        return "tablet"
    if re.search(r"Mobi|iPhone|iPod|Android.*Mobile|Windows Phone|Opera Mini", ua, re.I):
        return "mobile"
    return "desktop"


def ip_hash(ip):
    if not ip:
        return None
    key = os.environ.get("ANALYTICS_IP_KEY", "").encode()
    if not key:
        key = hmac.new(str(current_app.config.get("SECRET_KEY", "")).encode(),
                       b"dealbriz-analytics-ip", hashlib.sha256).digest()
    return hmac.new(key, ip.encode(), hashlib.sha256).hexdigest()


def _viewer():
    """Who is looking, for counting only (never for permission).

    Access tokens last 30 minutes. Once one expires, verify_jwt_in_request
    raises, and treating that as "not signed in" made a seller count as an
    anonymous visitor on their own listing, and counted a buyer a second
    time under an anonymous key. So if the normal check fails, read the
    access cookie again with expiry allowed. The signature is still checked,
    so the identity can't be forged. It only decides who is excluded or
    which key a view is counted under. It grants nothing."""
    from flask_jwt_extended import decode_token, verify_jwt_in_request
    from models import User
    from utils import current_user
    try:
        verify_jwt_in_request(optional=True)
        return current_user()
    except Exception:
        pass
    token = request.cookies.get(current_app.config.get("JWT_ACCESS_COOKIE_NAME") or "access_token_cookie")
    if not token:
        return None
    try:
        claims = decode_token(token, allow_expired=True)
    except Exception:
        return None
    uid = claims.get(current_app.config.get("JWT_IDENTITY_CLAIM") or "sub")
    if claims.get("type") != "access" or not uid:
        return None
    return db.session.get(User, str(uid))


@bp.route("/api/track", methods=["POST"])
def track():
    """{session, events: [{type, product_id?, category?, dwell_ms?, source?}]}.
    Always answers 204 for a well-formed batch (dropped events included), so
    the page never retries or shows anything."""
    from categories.util import all_categories
    from models import Product, ProductEvent
    from utils import has_staff_access
    data = request.get_json(silent=True) or {}
    events = data.get("events")
    if not isinstance(events, list):
        return jsonify({"error": "events must be a list"}), 400
    events = [e for e in events[:MAX_EVENTS] if isinstance(e, dict)]
    device = device_type(request.headers.get("User-Agent", ""))
    if device == "bot" or not events:
        return "", 204
    user = _viewer()
    if user is not None and has_staff_access(user):
        return "", 204
    session = str(data.get("session") or "")
    session = session if SESSION_RE.match(session) else None

    pids = {str(e.get("product_id"))[:36] for e in events if e.get("product_id")}
    products = ({p.id: p for p in Product.query.filter(Product.id.in_(pids)).all()} if pids else {})
    known_cats = {c["slug"] for c in all_categories()}
    hashed = ip_hash(request.remote_addr)
    now = datetime.utcnow()

    rows = []
    for e in events:
        etype = e.get("type")
        if etype not in TYPES:
            continue
        pid = str(e.get("product_id") or "")[:36] or None
        product = products.get(pid) if pid else None
        if etype in ("impression", "click", "dwell"):
            if product is None:
                continue
            if user is not None and product.posted_by == user.id:
                continue   # a seller looking at their own listing
            category = product.category
        else:
            category = str(e.get("category") or "").strip().lower()[:30]
            if category not in known_cats:
                continue
            pid = None
        dwell = None
        if etype == "dwell":
            try:
                dwell = int(e.get("dwell_ms"))
            except (TypeError, ValueError):
                continue
            if dwell < MIN_DWELL_MS:
                continue
            dwell = min(dwell, MAX_DWELL_MS)
        source = re.sub(r"[^a-z_-]", "", str(e.get("source") or "").lower())[:20] or None
        rows.append(ProductEvent(event_type=etype, product_id=pid, category=category, dwell_ms=dwell,
                                 source=source, session_id=session, user_id=user.id if user else None,
                                 device_type=device, ip_hash=hashed, created_at=now))
    if rows:
        db.session.add_all(rows)
        db.session.commit()
    return "", 204


# ------------------------------------------------------------------ admin

def _since():
    """?days=N (default 30); days=0 means all time. Returns a datetime or None."""
    try:
        days = int(request.args.get("days", 30))
    except (TypeError, ValueError):
        days = 30
    days = max(0, min(days, 3650))
    return (datetime.utcnow() - timedelta(days=days)) if days else None


def _base(since):
    from models import ProductEvent
    q = db.session.query(ProductEvent)
    if since is not None:
        q = q.filter(ProductEvent.created_at >= since)
    return q


def _ctr(clicks, impressions):
    return round(100.0 * clicks / impressions, 2) if impressions else None


@bp.route("/api/admin/analytics/summary", methods=["GET"])
@require_capability("dashboard.view")
def summary():
    from categories.util import label_for
    from models import ProductEvent as E
    since = _since()
    imp = func.sum(case((E.event_type == "impression", 1), else_=0))
    clk = func.sum(case((E.event_type == "click", 1), else_=0))
    exp = func.sum(case((E.event_type == "category", 1), else_=0))
    dwell_avg = func.avg(case((E.event_type == "dwell", E.dwell_ms), else_=None))

    t = _base(since).with_entities(imp, clk, func.count(distinct(E.ip_hash)), func.count(distinct(E.session_id)),
                                   dwell_avg).one()
    impressions, clicks = int(t[0] or 0), int(t[1] or 0)
    totals = {"impressions": impressions, "clicks": clicks, "ctr_pct": _ctr(clicks, impressions),
              "unique_visitors": int(t[2] or 0), "sessions": int(t[3] or 0),
              "avg_dwell_seconds": round(float(t[4]) / 1000, 1) if t[4] is not None else None}

    devices = [{"device": d or "unknown", "visitors": int(v or 0), "events": int(n or 0)}
               for d, v, n in _base(since).with_entities(E.device_type, func.count(distinct(E.ip_hash)), func.count(E.id))
               .group_by(E.device_type).order_by(func.count(distinct(E.ip_hash)).desc()).all()]

    cats = []
    for c, ex, vis, i, k, dw in (_base(since).with_entities(
            E.category, exp, func.count(distinct(case((E.event_type == "category", E.ip_hash), else_=None))),
            imp, clk, dwell_avg)
            .filter(E.category.isnot(None)).group_by(E.category).all()):
        i, k = int(i or 0), int(k or 0)
        cats.append({"category": c, "label": label_for(c), "explored": int(ex or 0), "explorers": int(vis or 0),
                     "impressions": i, "clicks": k, "ctr_pct": _ctr(k, i),
                     "avg_dwell_seconds": round(float(dw) / 1000, 1) if dw is not None else None})
    cats.sort(key=lambda x: (x["explored"] + x["clicks"]), reverse=True)

    day = func.date(E.created_at)
    daily = [{"day": str(d), "impressions": int(i or 0), "clicks": int(k or 0), "visitors": int(v or 0)}
             for d, i, k, v in _base(since).with_entities(day, imp, clk, func.count(distinct(E.ip_hash)))
             .group_by(day).order_by(day).all()]

    sources = [{"source": s or "direct", "clicks": int(n or 0)}
               for s, n in _base(since).filter(E.event_type == "click")
               .with_entities(E.source, func.count(E.id)).group_by(E.source)
               .order_by(func.count(E.id).desc()).all()]

    return jsonify({"since": since.isoformat() + "Z" if since else None, "totals": totals, "devices": devices,
                    "categories": cats, "daily": daily, "click_sources": sources})


@bp.route("/api/admin/analytics/products", methods=["GET"])
@require_capability("dashboard.view")
def products():
    """Per listing, most clicked first (?sort=clicks|impressions|ctr|dwell,
    ?category=slug, ?limit=1..500)."""
    from models import Product
    from models import ProductEvent as E
    since = _since()
    imp = func.sum(case((E.event_type == "impression", 1), else_=0)).label("imp")
    clk = func.sum(case((E.event_type == "click", 1), else_=0)).label("clk")
    vis = func.count(distinct(case((E.event_type == "click", E.ip_hash), else_=None))).label("vis")
    dwell_avg = func.avg(case((E.event_type == "dwell", E.dwell_ms), else_=None)).label("dwavg")
    dwell_sum = func.sum(case((E.event_type == "dwell", E.dwell_ms), else_=0)).label("dwsum")
    q = (_base(since).filter(E.product_id.isnot(None))
         .with_entities(E.product_id, imp, clk, vis, dwell_avg, dwell_sum).group_by(E.product_id))
    cat = (request.args.get("category") or "").strip().lower()
    if cat:
        q = q.filter(E.category == cat)
    sort = request.args.get("sort", "clicks")
    order = {"impressions": imp.desc(), "dwell": dwell_sum.desc(), "clicks": clk.desc()}.get(sort, clk.desc())
    try:
        limit = max(1, min(int(request.args.get("limit", 100)), 500))
    except (TypeError, ValueError):
        limit = 100
    rows = q.order_by(order).limit(limit if sort != "ctr" else 2000).all()
    info = {p.id: p for p in Product.query.filter(Product.id.in_([r[0] for r in rows])).all()} if rows else {}
    out = []
    for pid, i, k, v, da, ds in rows:
        p = info.get(pid)
        i, k = int(i or 0), int(k or 0)
        out.append({"product_id": pid, "title": p.title if p else "(deleted listing)",
                    "category": p.category if p else None, "status": p.status if p else None,
                    "price": float(p.price) if p else None,
                    "impressions": i, "clicks": k, "ctr_pct": _ctr(k, i), "unique_visitors": int(v or 0),
                    "avg_dwell_seconds": round(float(da) / 1000, 1) if da is not None else None,
                    "total_dwell_seconds": round(int(ds or 0) / 1000)})
    if sort == "ctr":
        out.sort(key=lambda r: (r["ctr_pct"] or -1, r["impressions"]), reverse=True)
        out = out[:limit]
    return jsonify({"since": since.isoformat() + "Z" if since else None, "products": out})
