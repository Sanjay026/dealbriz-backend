"""Support chatbot endpoint.

The user is identified from the JWT only. A user id in the request body is
ignored entirely — trusting it would turn this endpoint into a way to read
any account's listings by editing one field.
"""
import logging
import re

from flask import Blueprint, jsonify, request
from flask_jwt_extended import verify_jwt_in_request

from permissions import require_capability
from utils import current_user

from .intents import HOWTO_PREFIX, match_user_intent, normalise
from .marketplace import detect_category, try_marketplace
from .service import USER_HANDLERS, best_faq_match, suggest_related

log = logging.getLogger("dealbridge.chatbot")

bp = Blueprint("chatbot", __name__, url_prefix="/api/chatbot")

MAX_MESSAGE = 500

FALLBACK = ("I couldn't find the information needed to answer that. "
            "Our support team can help \u2014 they usually reply within two working days.")

LOGIN_REQUIRED = ("I can look that up once you're signed in \u2014 your listings and "
                  "account details are only visible to you.")

GREETING = ("Hi! I can help with how DealBriz works, or look up your own "
            "listings and account. What do you need?")

# "How many cars near me", "any phones available", "bikes under 50000",
# "phones in Mandi": an item type plus a stock cue is a question about live
# listings. The FAQ search used to answer these first, and wrongly ("How
# many photos can I add?" for "how many cars near me"). Words about a
# procedure (selling, EMI, documents...) keep a question on the FAQ path.
INVENTORY_CUES = re.compile(
    r"\b(how many|any|available|do you have|have you got|got any|show me|"
    r"under|below|less than|upto|up to|within|near me|nearby|near by|"
    r"in my (area|city)|around me|for sale|cheap|cheapest)\b|\bin [a-z]+$"
)
NOT_INVENTORY = {
    "sell", "selling", "post", "posting", "list", "emi", "loan", "finance",
    "document", "documents", "docs", "deal", "offer", "report", "safe",
    "deliver", "delivery", "return", "number", "insurance", "photo", "photos",
}


def looks_like_inventory(message):
    text = normalise(message)
    if HOWTO_PREFIX.search(text) or not detect_category(message):
        return False
    if set(text.split()) & NOT_INVENTORY:
        return False
    return bool(INVENTORY_CUES.search(text))


# One suggestion that isn't an FAQ question, but is always answerable
# because it's handled by the user-data path.
USER_DATA_SUGGESTION = "Show me my listings"


def build_quick_replies(limit=4):
    """Suggestions are taken from ACTUAL published FAQ questions.

    They used to be a hardcoded list, which meant a suggested question could
    have no matching FAQ — and one did: the bot offered "How do I post a
    listing?" and then couldn't answer it. Sourcing them from the FAQ table
    makes a suggestion answerable by construction.
    """
    from models import Faq
    rows = (Faq.query.filter(Faq.is_published.is_(True))
            .order_by(Faq.sort_order.asc(), Faq.created_at.asc())
            .limit(limit - 1).all())
    return [f.question for f in rows] + [USER_DATA_SUGGESTION]


def _resolve_user():
    """Authenticated user, or None. Optional so FAQ answers still work for
    someone who can't sign in — often exactly why they need support."""
    try:
        verify_jwt_in_request(optional=True)
        return current_user()
    except Exception:
        return None


@bp.route("/greeting", methods=["GET"])
def greeting():
    return jsonify({"reply": GREETING, "quick_replies": build_quick_replies()})


@bp.route("", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()[:MAX_MESSAGE]
    if not message:
        return jsonify({"error": "Please type a question."}), 400

    # The area the user has selected in the app, so "near me" means
    # something. Only ever used to FILTER public listings — never to look
    # up anything private — so accepting it from the client is safe.
    viewer_pincode = str(data.get("pincode") or "").strip()
    viewer_pincode = viewer_pincode if viewer_pincode.isdigit() and len(viewer_pincode) == 6 else None

    user = _resolve_user()

    # ---- 1. The user's own data ------------------------------------
    intent = match_user_intent(message)
    if intent:
        if not user:
            return jsonify({
                "reply": LOGIN_REQUIRED, "source": "auth_required",
                "action": {"type": "login", "label": "Sign in"},
            })
        handler = USER_HANDLERS.get(intent)
        if handler:
            try:
                text, action = handler(user, message)
            except Exception as exc:
                # A broken lookup must not answer with something wrong —
                # fall through to support instead.
                log.error("chatbot intent %s failed: %s", intent, exc, exc_info=True)
                return jsonify({"reply": FALLBACK, "source": "error",
                                "action": {"type": "page", "page": "contact", "label": "Contact Us"}})
            return jsonify({
                "reply": text, "source": "user_data", "intent": intent,
                "action": ({"type": "page", **action} if action else None),
            })

    # ---- 2. Live listings, when that's clearly what's being asked ----
    if looks_like_inventory(message):
        handler_name, result = try_marketplace(message, viewer_pincode)
        if result:
            text, action = result
            return jsonify({
                "reply": text, "source": "marketplace", "handler": handler_name,
                "action": ({"type": "page", **action} if action else None),
            })

    # ---- 3. FAQ knowledge base -------------------------------------
    # Checked before live data: "how does EMI work" is a procedure question
    # and deserves the written explanation, not a count of listings.
    faq, score = best_faq_match(message)
    if faq:
        return jsonify({
            "reply": faq.answer, "source": "faq",
            "faq_question": faq.question, "confidence": round(score, 2),
            "related": suggest_related(message, exclude_id=faq.faq_id),
        })

    # ---- 4. Live marketplace data ----------------------------------
    # Real queries against current listings, partners and coverage, so the
    # answer changes as the database does.
    handler_name, result = try_marketplace(message, viewer_pincode)
    if result:
        text, action = result
        return jsonify({
            "reply": text, "source": "marketplace", "handler": handler_name,
            "action": ({"type": "page", **action} if action else None),
        })

    # ---- 5. Nothing matched: hand over, don't guess -----------------
    record_unanswered(message, score)
    return jsonify({
        "reply": FALLBACK, "source": "fallback",
        # Near-misses are offered as questions to tap, never presented as
        # the answer — that distinction is the whole point.
        "related": suggest_related(message),
        "action": {"type": "page", "page": "contact", "label": "Contact Us"},
    })


def record_unanswered(message, best_score):
    """Logs a miss so staff can answer it later.

    Failures here are swallowed: a bot that errors because it couldn't
    write an analytics row would be worse than one that quietly forgets.
    """
    import uuid
    from extensions import db
    from models import ChatbotUnanswered

    try:
        key = normalise(message)[:500]
        if not key:
            return
        row = ChatbotUnanswered.query.filter_by(normalised=key).first()
        if row:
            row.ask_count += 1
            row.is_resolved = False   # asked again, so it's still a gap
        else:
            db.session.add(ChatbotUnanswered(
                id=str(uuid.uuid4()), question=message[:500], normalised=key,
                best_score=round(best_score, 2),
            ))
        db.session.commit()
    except Exception as exc:
        log.warning("could not record unanswered question: %s", exc)
        try:
            db.session.rollback()
        except Exception:
            pass


# ===================== ADMIN: TEACH THE BOT =====================

@bp.route("/unanswered", methods=["GET"])
@require_capability("faqs.manage")
def list_unanswered():
    """Questions users asked that the bot couldn't answer, most-asked first
    — so the highest-value answer to write is at the top."""
    from models import ChatbotUnanswered
    show_resolved = request.args.get("resolved") == "true"
    q = ChatbotUnanswered.query
    if not show_resolved:
        q = q.filter(ChatbotUnanswered.is_resolved.is_(False))
    rows = q.order_by(ChatbotUnanswered.ask_count.desc(),
                      ChatbotUnanswered.last_asked.desc()).limit(100).all()
    return jsonify([r.to_dict() for r in rows])


@bp.route("/unanswered/<uid>/resolve", methods=["POST"])
@require_capability("faqs.manage")
def resolve_unanswered(uid):
    """Marks a gap as handled. Used both when an answer has been written and
    when the question simply isn't something we should answer."""
    from extensions import db
    from models import ChatbotUnanswered
    row = ChatbotUnanswered.query.get(uid)
    if not row:
        return jsonify({"error": "Not found"}), 404
    row.is_resolved = True
    db.session.commit()
    return jsonify(row.to_dict())


@bp.route("/unanswered/<uid>", methods=["DELETE"])
@require_capability("faqs.manage")
def delete_unanswered(uid):
    from extensions import db
    from models import ChatbotUnanswered
    row = ChatbotUnanswered.query.get(uid)
    if not row:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})
