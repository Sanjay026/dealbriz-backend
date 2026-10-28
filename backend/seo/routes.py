"""Shareable listing pages: /item/<slug>-<id> and the listings sitemap.

The site is a single-page app, so every URL is served the same index.html
and the listing only appears once app.js runs. WhatsApp, Facebook and most
link-preview bots never run JavaScript: they read the <title> and og: tags
in the raw HTML. Without this route every shared listing previewed as the
generic home page.

nginx sends /item/ here. This route takes the same index.html the frontend
container serves, swaps in the listing's title, description, photo and
canonical URL, and returns it. The SPA then boots exactly as before and
opens the listing. If anything goes wrong (listing gone, frontend
unreachable), the plain index.html is returned so the page still works.
"""
import html
import json
import os
import re
import threading
import time
import urllib.request
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape

from flask import Blueprint, Response, current_app, redirect, request

from models import Product

bp = Blueprint("seo", __name__)

_UUID_TAIL = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", re.I)
PUBLIC_STATUSES = ("active", "reserved")

_index_lock = threading.Lock()
_index_cache = {"html": None, "at": 0.0}
INDEX_TTL = 60  # seconds; index.html changes only on a frontend deploy


def site_url():
    primary = os.environ.get("PRIMARY_DOMAIN", "").strip().lower()
    return f"https://{primary or 'dealbriz.com'}"


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:70].strip("-")


def place_name(p):
    """First part of "Mandi, Himachal Pradesh"; empty if only a pincode."""
    loc = (p.location or "").split(",")[0].strip()
    return "" if loc.isdigit() else loc


def item_path(p):
    slug = slugify(f"{p.title} {place_name(p)}")
    return f"/item/{slug}-{p.id}" if slug else f"/item/{p.id}"


def id_from_rest(rest):
    rest = (rest or "").strip("/")
    m = _UUID_TAIL.search(rest)
    if m:
        return m.group(1).lower()
    # Older links: /item/<slug>_<id> and /item/<id>
    return rest.rsplit("_", 1)[-1] if rest else ""


def inr(amount):
    """220000 -> 2,20,000 (Indian grouping)."""
    try:
        n = int(round(float(amount)))
    except (TypeError, ValueError):
        return ""
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
        s = f"{head},{tail}"
    return ("-" if n < 0 else "") + s


def absolute(url):
    if not url:
        return f"{site_url()}/og-image.png"
    if url.startswith(("http://", "https://")):
        return url
    return site_url() + ("" if url.startswith("/") else "/") + url


def load_index():
    now = time.time()
    with _index_lock:
        if _index_cache["html"] and now - _index_cache["at"] < INDEX_TTL:
            return _index_cache["html"]
    src = os.environ.get("SPA_INDEX_URL", "http://frontend/index.html")
    with urllib.request.urlopen(src, timeout=4) as r:
        page = r.read().decode("utf-8")
    with _index_lock:
        _index_cache.update(html=page, at=now)
    return page


def _sub_once(pattern, repl, text):
    return re.sub(pattern, lambda _m: repl, text, count=1, flags=re.I | re.S)


def listing_meta(p):
    url = site_url() + item_path(p)
    place = place_name(p)
    price = inr(p.price)
    title = p.title.strip()
    page_title = f"{title} – ₹{price}" + (f" in {place}" if place else "") + " | DealBriz"
    desc = re.sub(r"\s+", " ", (p.description or "")).strip()
    if not desc:
        desc = f"{title} for ₹{price}" + (f" in {place}" if place else "") + ". Buy it safely on DealBriz."
    if len(desc) > 160:
        desc = desc[:157].rsplit(" ", 1)[0] + "…"
    image = absolute(p.image_url)
    in_stock = p.status in PUBLIC_STATUSES
    ld = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": title,
        "image": [image],
        "description": desc,
        "url": url,
        "category": p.category,
        "offers": {
            "@type": "Offer",
            "price": f"{float(p.price):.2f}",
            "priceCurrency": "INR",
            "itemCondition": "https://schema.org/UsedCondition",
            "availability": "https://schema.org/InStock" if in_stock else "https://schema.org/SoldOut",
            "url": url,
        },
    }
    if p.brand:
        ld["brand"] = {"@type": "Brand", "name": p.brand}
    return dict(url=url, title=page_title, desc=desc, image=image,
                index=in_stock, ld=ld)


def inject(page, m):
    e = lambda s: html.escape(s, quote=True)  # noqa: E731
    page = _sub_once(r"<title>.*?</title>", f"<title>{e(m['title'])}</title>", page)
    page = _sub_once(r'<meta\s+name="description"[^>]*>',
                     f'<meta name="description" content="{e(m["desc"])}">', page)
    page = _sub_once(r'<link\s+rel="canonical"[^>]*>',
                     f'<link rel="canonical" href="{e(m["url"])}">', page)
    robots = "index, follow, max-image-preview:large" if m["index"] else "noindex, follow"
    page = _sub_once(r'<meta\s+name="robots"[^>]*>',
                     f'<meta name="robots" content="{robots}">', page)
    ld = json.dumps(m["ld"], ensure_ascii=False).replace("</", "<\\/")
    tags = "\n".join([
        '<meta property="og:type" content="product">',
        '<meta property="og:site_name" content="DealBriz">',
        f'<meta property="og:title" content="{e(m["title"])}">',
        f'<meta property="og:description" content="{e(m["desc"])}">',
        f'<meta property="og:url" content="{e(m["url"])}">',
        f'<meta property="og:image" content="{e(m["image"])}">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{e(m["title"])}">',
        f'<meta name="twitter:description" content="{e(m["desc"])}">',
        f'<meta name="twitter:image" content="{e(m["image"])}">',
        f'<script type="application/ld+json" id="dealbriz-ld-item">{ld}</script>',
    ])
    return page.replace("</head>", tags + "\n</head>", 1)


def html_response(body, status=200):
    resp = Response(body, status=status, mimetype="text/html")
    # Same rule as index.html in nginx: never cache the page shell, or a
    # deploy leaves people on asset URLs that no longer exist.
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


@bp.route("/item/<path:rest>", methods=["GET", "HEAD"])
def item_page(rest):
    try:
        page = load_index()
    except Exception as exc:
        current_app.logger.error("seo: cannot load index.html: %s", exc)
        # 502 makes nginx fall back to serving index.html itself.
        return Response("index unavailable", status=502)
    try:
        p = Product.query.get(id_from_rest(rest))
    except Exception as exc:
        current_app.logger.error("seo: listing lookup failed: %s", exc)
        return html_response(page)
    if not p or p.status in ("removed", "deleted", "rejected"):
        # The SPA still loads and shows its own "not found"; the 404 tells
        # search engines to drop the URL.
        return html_response(page, 404)
    want = item_path(p)
    if request.path != want:
        qs = request.query_string.decode("utf-8", "ignore")
        return redirect(want + (f"?{qs}" if qs else ""), code=301)
    try:
        return html_response(inject(page, listing_meta(p)))
    except Exception as exc:
        current_app.logger.error("seo: meta inject failed for %s: %s", p.id, exc)
        return html_response(page)


@bp.route("/sitemap-items.xml", methods=["GET"])
def sitemap_items():
    rows = (Product.query
            .filter(Product.status.in_(PUBLIC_STATUSES))
            .order_by(Product.created_at.desc())
            .limit(45000).all())
    base = site_url()
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for p in rows:
        mod = (p.created_at or datetime.utcnow()).strftime("%Y-%m-%d")
        out.append(f"<url><loc>{xml_escape(base + item_path(p))}</loc>"
                   f"<lastmod>{mod}</lastmod></url>")
    out.append("</urlset>")
    resp = Response("\n".join(out), mimetype="application/xml")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp
