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

EMAIL_RE = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+$")

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


def _answer(body, question, faq_id=None):
    """The JSON for one answer. Adds feedback_token (thumbs up / down, and
    the options after a thumbs down; chatbot/feedback.py) when the answer
    can be rated, and can_retry: whether "Ask again" can give a different
    answer (only the AI can)."""
    from . import ai
    from .feedback import make_token
    try:
        token = make_token(question, body.get("reply") or "", body.get("source"), faq_id)
    except Exception:
        log.warning("could not sign chatbot feedback token", exc_info=True)
        token = None
    if token:
        body["feedback_token"] = token
    body["can_retry"] = bool(token) and ai.enabled()
    return jsonify(body)


def _lessons(message, just_disliked=None, retry=False):
    """Instructions for the AI built from earlier thumbs down / up."""
    from .feedback import lessons_for, lessons_prompt
    try:
        return lessons_prompt(lessons_for(message, just_disliked), retry=retry)
    except Exception:
        log.warning("could not build chatbot lessons", exc_info=True)
        return ""


RETRY_UNAVAILABLE = ("I can't write a different answer to that right now. You can look at our FAQ "
                     "answers, or our support team can help \u2014 they usually reply within two working days.")


@bp.route("", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()[:MAX_MESSAGE]

    # The area the user has selected in the app, so "near me" means
    # something. Only ever used to FILTER public listings — never to look
    # up anything private — so accepting it from the client is safe.
    viewer_pincode = str(data.get("pincode") or "").strip()
    viewer_pincode = viewer_pincode if viewer_pincode.isdigit() and len(viewer_pincode) == 6 else None

    user = _resolve_user()

    # ---- 0. "Ask again" after a thumbs down -------------------------
    # The question and the disliked answer come from the signed token of
    # that answer, not from the request, so the AI is told about an answer
    # the server really gave. Only the AI can write a different answer.
    if data.get("regenerate"):
        from . import ai
        from .feedback import read_token
        payload = read_token(data.get("feedback_token"))
        if not payload:
            return jsonify({"error": "That answer is too old to ask again. Please type your question."}), 400
        question = payload["q"]
        result = None
        if ai.enabled():
            result = ai.answer(question, user=user, pincode=viewer_pincode, history=data.get("history"),
                               lessons=_lessons(question, just_disliked=payload["a"], retry=True),
                               retry=True)
        if result and result["reply"].strip() != payload["a"].strip():
            return _answer({"reply": result["reply"], "source": "ai", "action": result["action"]}, question)
        return jsonify({
            "reply": RETRY_UNAVAILABLE, "source": "retry_unavailable", "can_retry": False,
            "related": suggest_related(question),
            "action": {"type": "page", "page": "contact", "label": "Contact Us"},
        })

    if not message:
        return jsonify({"error": "Please type a question."}), 400

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
            return _answer({
                "reply": text, "source": "user_data", "intent": intent,
                "action": ({"type": "page", **action} if action else None),
            }, message)

    # ---- 2. The AI assistant (chatbot/ai.py), when it's switched on -----
    # Read-only tools only: public listings, help articles, and the
    # signed-in user's OWN deals, offers and EMI applications (user from
    # the session cookie, never from the request). Off, failing or slow:
    # None, and the rule-based answers below take over. It is told which
    # earlier answers to this question users disliked (or liked).
    from . import ai
    if ai.enabled():
        result = ai.answer(message, user=user, pincode=viewer_pincode, history=data.get("history"),
                           lessons=_lessons(message))
        if result:
            return _answer({"reply": result["reply"], "source": "ai", "action": result["action"]}, message)

    # ---- 3. Live listings, when that's clearly what's being asked ----
    if looks_like_inventory(message):
        handler_name, result = try_marketplace(message, viewer_pincode)
        if result:
            text, action = result
            return _answer({
                "reply": text, "source": "marketplace", "handler": handler_name,
                "action": ({"type": "page", **action} if action else None),
            }, message)

    # ---- 4. FAQ knowledge base -------------------------------------
    # Checked before live data: "how does EMI work" is a procedure question
    # and deserves the written explanation, not a count of listings.
    # An FAQ answer that most people disliked for this question isn't given
    # as THE answer any more (chatbot/feedback.py faq_answer_disliked): the
    # question is logged as unanswered for staff, and the FAQ is still
    # offered below as a related question to tap.
    from .feedback import faq_answer_disliked
    faq, score = best_faq_match(message)
    if faq and faq_answer_disliked(message, faq):
        log.info("chatbot: FAQ %s skipped, disliked for %r", faq.faq_id, message[:80])
        faq = None
    if faq:
        return _answer({
            "reply": faq.answer, "source": "faq",
            "faq_question": faq.question, "confidence": round(score, 2),
            "related": suggest_related(message, exclude_id=faq.faq_id),
        }, message, faq_id=faq.faq_id)

    # ---- 5. Live marketplace data ----------------------------------
    # Real queries against current listings, partners and coverage, so the
    # answer changes as the database does.
    handler_name, result = try_marketplace(message, viewer_pincode)
    if result:
        text, action = result
        return _answer({
            "reply": text, "source": "marketplace", "handler": handler_name,
            "action": ({"type": "page", **action} if action else None),
        }, message)

    # ---- 6. Nothing matched: hand over, don't guess -----------------
    record_unanswered(message, score)
    return _answer({
        "reply": FALLBACK, "source": "fallback",
        # Near-misses are offered as questions to tap, never presented as
        # the answer — that distinction is the whole point.
        "related": suggest_related(message),
        "action": {"type": "page", "page": "contact", "label": "Contact Us"},
    }, message)


# ===================== FEEDBACK (thumbs up / down) =====================

def _voter(data, user):
    from .feedback import voter_key
    return voter_key(user, data.get("guest_key"))


@bp.route("/feedback", methods=["POST"])
def rate_answer():
    """{feedback_token, vote: "like" | "dislike", reason?, guest_key?}.
    Saves this person's latest review of that answer; rating the same
    question again replaces the earlier review."""
    from .feedback import read_token, record_vote, REASONS
    data = request.get_json(silent=True) or {}
    vote = data.get("vote")
    if vote not in ("like", "dislike"):
        return jsonify({"error": "Choose thumbs up or thumbs down."}), 400
    payload = read_token(data.get("feedback_token"))
    if not payload:
        return jsonify({"error": "This answer is too old to rate. Ask again to rate the new answer."}), 400
    reason = data.get("reason")
    if reason is not None and reason not in REASONS:
        return jsonify({"error": "Unknown reason."}), 400
    user = _resolve_user()
    voter = _voter(data, user)
    if not voter:
        return jsonify({"error": "Could not save your feedback. Please reload the page."}), 400
    try:
        question, row = record_vote(payload, vote, voter, reason)
    except Exception:
        from extensions import db
        db.session.rollback()
        log.error("could not save chatbot feedback", exc_info=True)
        return jsonify({"error": "Could not save your feedback right now."}), 503
    return jsonify({"ok": True, "question_id": question.id, "vote": vote, "reason": row.reason})


@bp.route("/faq-matches", methods=["POST"])
def faq_answers():
    """{feedback_token}: the published FAQ answers closest to that answer's
    question, for "See FAQ answers" after a thumbs down. Each comes with its
    own feedback token, so it can be rated too."""
    from .feedback import faq_matches, make_token, read_token
    data = request.get_json(silent=True) or {}
    payload = read_token(data.get("feedback_token"))
    if not payload:
        return jsonify({"error": "That answer is too old. Please type your question again."}), 400
    exclude = payload.get("f") if payload.get("s") == "faq" else None
    items = []
    for f in faq_matches(payload["q"], exclude_id=exclude):
        items.append({"faq_id": f.faq_id, "question": f.question, "answer": f.answer,
                      "feedback_token": make_token(payload["q"], f.answer, "faq", f.faq_id)})
    return jsonify({"items": items})


@bp.route("/support", methods=["POST"])
def contact_support():
    """{feedback_token, note?, email (guests), history?, guest_key?}: sends the
    question, the bot's answer and the person's note to the support team.
    Saved as a problem report (Admin -> Reports), and emailed to
    support@dealbriz.com (or SUPPORT_EMAIL) when email is set up. Counts as a thumbs down if they
    hadn't rated the answer."""
    from extensions import db
    from models import Report
    from .feedback import (SUPPORT_PER_DAY, email_support, read_token, record_vote,
                           support_details, support_recent_count)
    from datetime import datetime as _dt
    data = request.get_json(silent=True) or {}
    payload = read_token(data.get("feedback_token"))
    if not payload:
        return jsonify({"error": "That answer is too old. Please use Contact Us instead."}), 400
    user = _resolve_user()
    voter = _voter(data, user)
    if not voter:
        return jsonify({"error": "Could not send right now. Please reload the page."}), 400

    note = " ".join(str(data.get("note") or "").split())[:1000]
    email = str(data.get("email") or "").strip()[:255]
    if user and not email:
        email = user.email or ""
    if not email:
        return jsonify({"error": "Please enter your email so our team can reply."}), 400
    if not EMAIL_RE.match(email):
        return jsonify({"error": "Please enter a valid email address."}), 400
    if support_recent_count(voter[0], email) >= SUPPORT_PER_DAY:
        return jsonify({"error": "You've sent several requests today. Our team will reply to those; "
                                 "for anything else please use Contact Us."}), 429

    who = (f"{user.first_name or ''} {user.last_name or ''}".strip() + f" <{email}> (signed in, user {user.id})"
           if user else f"{email} (not signed in)")
    try:
        # The thumbs down stays (with the reason they picked), or is added
        # if they went straight to support.
        question, row = record_vote(payload, "dislike", voter, keep_reason=True)
        details = support_details(payload, note, data.get("history"), who)
        report = Report(report_type="problem", reported_user_id=None,
                        reporter_id=user.id if user else None, product_id=None, reason="other",
                        subject=("Chatbot: " + " ".join(payload["q"].split()))[:200],
                        contact_email=email, details=details, status="open")
        db.session.add(report)
        db.session.flush()
        row.support_report_id = report.report_id
        row.support_requested_at = _dt.utcnow()
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.error("could not save chatbot support request", exc_info=True)
        return jsonify({"error": "Could not send right now. Please try Contact Us."}), 503

    emailed = email_support(f"[DealBriz chatbot] {' '.join(payload['q'].split())[:80]}", details, email)
    return jsonify({"ok": True, "report_id": report.report_id, "emailed": emailed, "email": email}), 201


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


# ===================== ADMIN: WHAT PEOPLE THOUGHT OF THE ANSWERS =====================

@bp.route("/feedback", methods=["GET"])
@require_capability("faqs.manage")
def list_feedback():
    """Questions whose answers were rated, with the totals.
    ?view=disliked (default): at least one thumbs down, most disliked first.
    ?view=support: someone asked for support from the answer.
    ?view=all: every rated question, latest first.
    Each comes with the latest disliked answer and the reasons given."""
    from sqlalchemy import case, func
    from extensions import db
    from models import ChatbotFeedback, ChatbotQuestion
    from .feedback import REASONS
    view = request.args.get("view", "disliked")
    likes = func.sum(ChatbotFeedback.likes).label("likes")
    dislikes = func.sum(ChatbotFeedback.dislikes).label("dislikes")
    support = func.sum(case((ChatbotFeedback.support_report_id.isnot(None), 1), else_=0)).label("support")
    last = func.max(ChatbotFeedback.updated_at).label("last")
    q = (db.session.query(ChatbotQuestion.id, ChatbotQuestion.text, likes, dislikes, support, last)
         .join(ChatbotFeedback, ChatbotFeedback.question_id == ChatbotQuestion.id)
         .group_by(ChatbotQuestion.id, ChatbotQuestion.text))
    if view == "support":
        q = q.having(support > 0).order_by(last.desc())
    elif view == "all":
        q = q.order_by(last.desc())
    else:
        q = q.having(dislikes > 0).order_by(dislikes.desc(), last.desc())
    rows = q.limit(100).all()

    ids = [r.id for r in rows]
    latest_bad, reasons = {}, {}
    if ids:
        for f in (ChatbotFeedback.query
                  .filter(ChatbotFeedback.question_id.in_(ids), ChatbotFeedback.dislikes > 0)
                  .order_by(ChatbotFeedback.updated_at.desc()).all()):
            latest_bad.setdefault(f.question_id, f)
            if f.reason:
                counts = reasons.setdefault(f.question_id, {})
                counts[f.reason] = counts.get(f.reason, 0) + 1
    from models.base import iso_utc
    out = []
    for r in rows:
        bad = latest_bad.get(r.id)
        out.append({
            "question_id": r.id, "question": r.text,
            "likes": int(r.likes or 0), "dislikes": int(r.dislikes or 0),
            "support_requests": int(r.support or 0), "last_rated": iso_utc(r.last),
            "disliked_answer": bad.answer_text if bad else None,
            "disliked_source": bad.answer_source if bad else None,
            "reasons": [{"label": REASONS[k], "count": n}
                        for k, n in sorted(reasons.get(r.id, {}).items(), key=lambda x: -x[1]) if k in REASONS],
        })
    return jsonify({"items": out})


# ===================== ADMIN: THE FEEDBACK TABLES, ROW BY ROW =====================
# Admin -> Chatbot -> "Feedback data". Paged on the server: these tables
# grow with every rating.

def _page_args():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = min(500, max(10, int(request.args.get("per_page", 25))))
    except (TypeError, ValueError):
        per_page = 25
    return page, per_page


def _paged(query, page, per_page):
    total = query.order_by(None).count()
    pages = max(1, -(-total // per_page))
    page = min(page, pages)
    return query.limit(per_page).offset((page - 1) * per_page).all(), total, page, pages


def _search(column, text):
    """Case-insensitive "contains", with % and _ taken literally."""
    from sqlalchemy import func
    return func.lower(column).contains(text.lower(), autoescape=True)


@bp.route("/feedback/records", methods=["GET"])
@require_capability("faqs.manage")
def feedback_records():
    """Table 2 (chatbot_feedback), one row per question per person, newest
    first, with the question's text, who rated (name and email, or "guest"),
    the answer they rated and their support request's status.
    ?vote=like|dislike  ?support=1  ?question_id=<id>  ?q=<text in the
    question, the person's name or email>  ?page  ?per_page

    users and reports are NOT joined in SQL: their id columns use the
    database's default collation (utf8mb4_0900_ai_ci) and the feedback
    tables use utf8mb4_unicode_ci, and MySQL refuses "=" between the two
    (error 1267). They're looked up by id lists instead (WHERE id IN (...)
    compares with plain values, which is fine)."""
    from sqlalchemy import or_
    from extensions import db
    from models import ChatbotFeedback, ChatbotQuestion, Report, User
    from models.base import iso_utc
    from .feedback import REASONS
    page, per_page = _page_args()
    q = (db.session.query(ChatbotFeedback, ChatbotQuestion.text)
         .join(ChatbotQuestion, ChatbotQuestion.id == ChatbotFeedback.question_id))
    vote = request.args.get("vote")
    if vote == "like":
        q = q.filter(ChatbotFeedback.likes > 0)
    elif vote == "dislike":
        q = q.filter(ChatbotFeedback.dislikes > 0)
    if request.args.get("support") == "1":
        q = q.filter(ChatbotFeedback.support_report_id.isnot(None))
    qid = request.args.get("question_id", type=int)
    if qid:
        q = q.filter(ChatbotFeedback.question_id == qid)
    text = (request.args.get("q") or "").strip()[:100]
    if text:
        # People whose name or email matches, found in the users table on its own.
        user_ids = [uid for (uid,) in (db.session.query(User.id)
                                       .filter(or_(_search(User.email, text), _search(User.first_name, text),
                                                   _search(User.last_name, text)))
                                       .limit(1000).all())]
        conds = [_search(ChatbotQuestion.text, text)]
        if user_ids:
            conds.append(ChatbotFeedback.user_id.in_(user_ids))
        q = q.filter(or_(*conds))
    q = q.order_by(ChatbotFeedback.updated_at.desc(), ChatbotFeedback.id.desc())
    rows, total, page, pages = _paged(q, page, per_page)

    uids = {f.user_id for f, _ in rows if f.user_id}
    rids = {f.support_report_id for f, _ in rows if f.support_report_id}
    people = {u.id: u for u in User.query.filter(User.id.in_(uids)).all()} if uids else {}
    report_status = ({r.report_id: r.status for r in Report.query.filter(Report.report_id.in_(rids)).all()}
                     if rids else {})
    items = []
    for f, qtext in rows:
        u = people.get(f.user_id) if f.user_id else None
        items.append({
            "id": f.id, "question_id": f.question_id, "question": qtext,
            "user_id": f.user_id,
            "user_name": (f"{u.first_name or ''} {u.last_name or ''}".strip() or None) if u else None,
            "user_email": u.email if u else None,
            "guest": not f.user_id,
            "guest_key": (f.guest_key or "")[:8] if not f.user_id else None,
            "likes": int(f.likes or 0), "dislikes": int(f.dislikes or 0),
            "vote": "like" if f.likes else ("dislike" if f.dislikes else None),
            "reason": REASONS.get(f.reason) if f.reason else None,
            "answer": f.answer_text, "answer_source": f.answer_source,
            "support_report_id": f.support_report_id,
            "support_status": report_status.get(f.support_report_id) if f.support_report_id else None,
            "support_requested_at": iso_utc(f.support_requested_at),
            "created_at": iso_utc(f.created_at), "updated_at": iso_utc(f.updated_at),
        })
    return jsonify({"items": items, "total": total, "page": page, "pages": pages, "per_page": per_page})


@bp.route("/feedback/questions", methods=["GET"])
@require_capability("faqs.manage")
def feedback_questions():
    """Table 1 (chatbot_questions), with each question's totals from table
    2: thumbs up, thumbs down, people who rated, support requests.
    ?q=<text>  ?sort=recent|disliked|liked|id  ?page  ?per_page"""
    from sqlalchemy import case, func
    from extensions import db
    from models import ChatbotFeedback, ChatbotQuestion
    from models.base import iso_utc
    page, per_page = _page_args()
    likes = func.coalesce(func.sum(ChatbotFeedback.likes), 0).label("likes")
    dislikes = func.coalesce(func.sum(ChatbotFeedback.dislikes), 0).label("dislikes")
    people = func.count(ChatbotFeedback.id).label("people")
    support = func.coalesce(func.sum(case((ChatbotFeedback.support_report_id.isnot(None), 1), else_=0)), 0).label("support")
    last = func.max(ChatbotFeedback.updated_at).label("last")
    q = (db.session.query(ChatbotQuestion, likes, dislikes, people, support, last)
         .outerjoin(ChatbotFeedback, ChatbotFeedback.question_id == ChatbotQuestion.id)
         .group_by(ChatbotQuestion.id))
    text = (request.args.get("q") or "").strip()[:100]
    if text:
        q = q.filter(_search(ChatbotQuestion.text, text))
    sort = request.args.get("sort", "recent")
    if sort == "disliked":
        q = q.order_by(dislikes.desc(), last.desc(), ChatbotQuestion.id.desc())
    elif sort == "liked":
        q = q.order_by(likes.desc(), last.desc(), ChatbotQuestion.id.desc())
    elif sort == "id":
        q = q.order_by(ChatbotQuestion.id.asc())
    else:
        q = q.order_by(last.desc(), ChatbotQuestion.id.desc())
    count_q = db.session.query(func.count(ChatbotQuestion.id))
    if text:
        count_q = count_q.filter(_search(ChatbotQuestion.text, text))
    total = count_q.scalar() or 0
    pages = max(1, -(-total // per_page))
    page = min(page, pages)
    rows = q.limit(per_page).offset((page - 1) * per_page).all()
    items = [{
        "id": qq.id, "question": qq.text, "created_at": iso_utc(qq.created_at),
        "likes": int(lk or 0), "dislikes": int(dk or 0), "people": int(pp or 0),
        "support_requests": int(sp or 0), "last_rated": iso_utc(ls),
    } for qq, lk, dk, pp, sp, ls in rows]
    return jsonify({"items": items, "total": total, "page": page, "pages": pages, "per_page": per_page})

