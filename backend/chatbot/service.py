"""Chatbot answer resolution.

Every user-data handler filters on the authenticated user's id, which comes
from the JWT — never from the request body. A user id supplied by the
frontend is untrusted input; honouring it would let anyone read anyone
else's listings by editing one field.
"""
from models import EmiApplication, Faq, Message, Product, SavedProduct

# Never returned by the chatbot, whatever is asked. Listed explicitly so the
# rule is visible rather than implied by which fields happen to be used.
NEVER_EXPOSE = {"password_hash", "jwt_token_hash", "avatar_url"}

MAX_LISTED = 8  # keep replies short; link out for the rest


def _money(v):
    try:
        return f"\u20b9{int(float(v)):,}".replace(",", ",")
    except (TypeError, ValueError):
        return "\u2014"


def _listing_line(p):
    return f"\u2022 {p.title} \u2014 {_money(p.price)} ({p.status})"


# ---------------------------------------------------------------------
# User-data handlers. Each returns (text, action) where action optionally
# tells the UI to offer a button.
# ---------------------------------------------------------------------

def _my_listings(user, _message):
    rows = Product.query.filter_by(posted_by=user.id).order_by(Product.created_at.desc()).all()
    if not rows:
        return ("You haven't posted any listings yet. Tap **Sell** to create your first one.",
                {"label": "Post a listing", "page": "sell"})
    active = [p for p in rows if p.status == "active"]
    lines = [_listing_line(p) for p in rows[:MAX_LISTED]]
    more = f"\n\n...and {len(rows) - MAX_LISTED} more." if len(rows) > MAX_LISTED else ""
    return (f"You have **{len(rows)} listing{'s' if len(rows) != 1 else ''}** "
            f"({len(active)} active):\n\n" + "\n".join(lines) + more,
            {"label": "View my listings", "page": "mylistings"})


def _my_active_listings(user, _message):
    rows = Product.query.filter_by(posted_by=user.id, status="active") \
                        .order_by(Product.created_at.desc()).all()
    if not rows:
        return ("You have no active listings right now.",
                {"label": "My listings", "page": "mylistings"})
    lines = [_listing_line(p) for p in rows[:MAX_LISTED]]
    return (f"You have **{len(rows)} active listing{'s' if len(rows) != 1 else ''}**:\n\n"
            + "\n".join(lines), {"label": "My listings", "page": "mylistings"})


def _my_sold_listings(user, _message):
    rows = Product.query.filter_by(posted_by=user.id, status="sold").all()
    if not rows:
        return ("You haven't marked any listings as sold yet.", None)
    return (f"You've sold **{len(rows)} item{'s' if len(rows) != 1 else ''}**:\n\n"
            + "\n".join(_listing_line(p) for p in rows[:MAX_LISTED]), None)


def _latest_listing_status(user, _message):
    p = Product.query.filter_by(posted_by=user.id).order_by(Product.created_at.desc()).first()
    if not p:
        return ("You haven't posted any listings yet.", {"label": "Post a listing", "page": "sell"})
    return (f"Your most recent listing is **{p.title}**, priced at {_money(p.price)}.\n\n"
            f"Status: **{p.status}** \u00b7 {p.views or 0} view{'s' if (p.views or 0) != 1 else ''}",
            {"label": "My listings", "page": "mylistings"})


def _listing_price(user, message):
    """Matches the listing by words from the question, e.g. "price of my
    laptop listing" against a title containing "laptop"."""
    from .intents import tokens
    rows = Product.query.filter_by(posted_by=user.id).all()
    if not rows:
        return ("You haven't posted any listings yet.", None)
    q = tokens(message) - {"price", "cost", "listing", "product", "ad"}
    best, best_hits = None, 0
    for p in rows:
        hits = len(q & tokens(p.title))
        if hits > best_hits:
            best, best_hits = p, hits
    if not best:
        # Don't guess which listing was meant — show them instead.
        return ("I'm not sure which listing you mean. Here are yours:\n\n"
                + "\n".join(_listing_line(p) for p in rows[:MAX_LISTED]),
                {"label": "My listings", "page": "mylistings"})
    return (f"**{best.title}** is listed at {_money(best.price)}"
            + (f" (was {_money(best.original_price)})" if best.original_price else "") + ".", None)


def _my_email(user, _message):
    return (f"Your registered email is **{user.email}**.",
            {"label": "Edit profile", "page": "profile"})


def _my_account(user, _message):
    parts = [f"\u2022 Name: {user.first_name} {user.last_name}".strip(),
             f"\u2022 Email: {user.email}"]
    if user.phone:
        parts.append(f"\u2022 Phone: {user.phone}")
    if user.city:
        parts.append(f"\u2022 Pincode: {user.city}")
    return ("Here's what's on your account:\n\n" + "\n".join(parts),
            {"label": "Edit profile", "page": "profile"})


def _my_inquiries(user, _message):
    count = Message.query.filter_by(receiver_id=user.id).count()
    unread = Message.query.filter_by(receiver_id=user.id, is_read=False).count()
    if not count:
        return ("You haven't received any messages yet.", None)
    return (f"You have **{count} message{'s' if count != 1 else ''}**"
            + (f", **{unread} unread**." if unread else "."),
            {"label": "Open my chats", "page": "mychats"})


def _my_emi_applications(user, _message):
    rows = EmiApplication.query.filter_by(user_id=user.id) \
                               .order_by(EmiApplication.applied_at.desc()).all()
    if not rows:
        return ("You don't have any EMI applications.", None)
    lines = [f"\u2022 {a.product_title_snapshot} \u2014 {_money(a.loan_amount)} "
             f"({a.status})" for a in rows[:MAX_LISTED]]
    return (f"You have **{len(rows)} EMI application{'s' if len(rows) != 1 else ''}**:\n\n"
            + "\n".join(lines), {"label": "EMI status", "page": "emi-status"})


def _my_saved(user, _message):
    count = SavedProduct.query.filter_by(user_id=user.id).count()
    if not count:
        return ("You haven't saved any listings yet. Tap the heart on a listing to save it.", None)
    return (f"You have **{count} saved listing{'s' if count != 1 else ''}**.",
            {"label": "View saved", "page": "myads"})


USER_HANDLERS = {
    "my_listings": _my_listings,
    "my_active_listings": _my_active_listings,
    "my_sold_listings": _my_sold_listings,
    "latest_listing_status": _latest_listing_status,
    "listing_price": _listing_price,
    "my_email": _my_email,
    "my_account": _my_account,
    "my_inquiries": _my_inquiries,
    "my_emi_applications": _my_emi_applications,
    "my_saved": _my_saved,
}


def best_faq_match(message):
    """Highest-scoring published FAQ, or None if nothing clears the
    confidence threshold."""
    from .intents import FAQ_CONFIDENCE_THRESHOLD, score_faq
    faqs = Faq.query.filter(Faq.is_published.is_(True)).all()
    if not faqs:
        return None, 0.0
    scored = sorted(((score_faq(message, f), f) for f in faqs),
                    key=lambda x: x[0], reverse=True)
    top_score, top_faq = scored[0]
    return (top_faq, top_score) if top_score >= FAQ_CONFIDENCE_THRESHOLD else (None, top_score)


def suggest_related(message, exclude_id=None, limit=3):
    """Near-misses offered as 'did you mean' rather than answered outright."""
    from .intents import score_faq
    faqs = [f for f in Faq.query.filter(Faq.is_published.is_(True)).all()
            if f.faq_id != exclude_id]
    scored = sorted(((score_faq(message, f), f) for f in faqs),
                    key=lambda x: x[0], reverse=True)
    return [f.question for score, f in scored[:limit] if score > 0.3]
