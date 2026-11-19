"""
DealLock: "Did you get the loan?" and "Is your deal done?", and the
document-upload consent record. 16 Nov 2026.

The flow (on top of the existing deal states, nothing removed):
  - 24 hours after the buyer applies for the loan (Deal.loan_applied_at),
    the buyer gets an email, a bell notification and a push: "Did you get
    your loan?" with Yes / No. From then on both sides also see a "Confirm
    the deal" card in the deal (to_dict: can_confirm_done).
  - 4 days after the loan was applied for, whoever hasn't said yes yet is
    asked "Is your deal done?" the same way (email, bell, push).
  - The buyer's yes AND the seller's yes complete the deal: the listing is
    sold and the purchase recorded (service.complete_deal), without the
    handover code. The loan application is marked disbursed.
  - A "no" from either side goes to staff (bell) and the other side; the
    deal stays as it is.
  - While one side has said yes, the reservation timer doesn't release the
    deal (service.release_expired_reservations).
  The handover steps (code, money/vehicle received) still work as before.

Routes (blueprint registered in app.py):
    POST /api/deals/<id>/done              {answer: "yes"|"no", note?}  signed in
    GET  /api/deals/email-answer?t=&a=     page from the email's link
    POST /api/deals/email-answer           the page's buttons (t, a)
    POST /api/deals/docs-consent           {context}: "I agree" ticked before
                                           uploading documents

The email links carry a signed token (deal id + the person), valid 14 days,
so answering from the email needs no sign-in. The link opens a page with
the buttons rather than answering on open: email scanners open links.

Timers: send_loan_checks() and send_done_checks(), called by deals/jobs.py.
Settings (.env, optional): DEALLOCK_LOAN_CHECK_HOURS (24), DEALLOCK_DONE_CHECK_DAYS (4),
SITE_URL (https://dealbriz.com) for the links.
"""
import html as _html
import logging
import os
from datetime import timedelta

from flask import Blueprint, current_app, jsonify, make_response, request
from flask_jwt_extended import jwt_required
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Deal, DealEvent, Product, User
from models.deal import DEAL_COMPLETED, DONE_CHECK_STATES
from utils import current_user

from .service import (Outbox, add_event, add_loan_event, complete_deal, display_name,
                      linked_application, notify, notify_staff, post_thread_message, utcnow)

log = logging.getLogger("dealbridge.deals.done_check")

bp = Blueprint("deal_done", __name__)


def _int_env(name, default, lo, hi):
    try:
        v = int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


LOAN_CHECK_HOURS = _int_env("DEALLOCK_LOAN_CHECK_HOURS", 24, 1, 24 * 14)
DONE_CHECK_DAYS = _int_env("DEALLOCK_DONE_CHECK_DAYS", 4, 1, 60)

TOKEN_SALT = "deal-done-v1"
TOKEN_MAX_AGE = 14 * 24 * 3600
MAX_NOTE = 500
# A second "no" from the same person this soon doesn't notify again.
NO_REPEAT_HOURS = 12
# Which policy the consent tick refers to (stored with each consent).
CONSENT_POLICY = "privacy-2026-11-16"


def _site():
    return (os.environ.get("SITE_URL") or "https://dealbriz.com").rstrip("/")


def _esc(text):
    return _html.escape(str(text or ""))


# ------------------------------------------------------------------ tokens

def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=TOKEN_SALT)


def make_token(deal_id, user_id):
    return _serializer().dumps({"d": str(deal_id), "u": str(user_id)})


def read_token(token):
    """{"d": deal id, "u": user id} or None (missing, altered, too old)."""
    if not isinstance(token, str) or not token or len(token) > 1000:
        return None
    try:
        data = _serializer().loads(token, max_age=TOKEN_MAX_AGE)
    except BadSignature:   # includes SignatureExpired
        return None
    if not isinstance(data, dict) or not data.get("d") or not data.get("u"):
        return None
    return data


def answer_link(deal_id, user_id, answer=None):
    from urllib.parse import urlencode
    q = {"t": make_token(deal_id, user_id)}
    if answer in ("yes", "no"):
        q["a"] = answer
    return f"{_site()}/api/deals/email-answer?{urlencode(q)}"


# ------------------------------------------------------------------ helpers

def _title(deal):
    product = Product.query.get(deal.product_id)
    return product.title if product else "your deal"


def _role(deal, user_id):
    if user_id == deal.buyer_id:
        return "buyer"
    if user_id == deal.seller_id:
        return "seller"
    return None


def _question(role):
    if role == "buyer":
        return "Did you get your loan, and is the deal done?"
    return "Is your deal done (have you been paid and handed over)?"


def _yes_label(role):
    return "Yes, I got the loan" if role == "buyer" else "Yes, the deal is done"


def _no_label(role):
    return "No, not yet" if role == "buyer" else "No, not done yet"


# ------------------------------------------------------------------ answering

class AnswerError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.message = message
        self.status = status


def record_answer(deal, user, answer, via, outbox, note=None):
    """One side's answer. deal must be locked (with_for_update). Nothing is
    committed here. Returns "completed", "confirmed", "already",
    "already_completed" or "reported". Raises AnswerError."""
    role = _role(deal, user.id)
    if role is None:
        raise AnswerError("Deal not found.", 404)
    if answer not in ("yes", "no"):
        raise AnswerError("Answer yes or no.", 400)
    if deal.status == DEAL_COMPLETED:
        return "already_completed"
    if deal.status not in DONE_CHECK_STATES:
        raise AnswerError(f"This deal can't be confirmed now (it is {deal.status.replace('_', ' ')}).", 409)
    if deal.loan_check_sent_at is None:
        raise AnswerError(f"The deal can be confirmed {LOAN_CHECK_HOURS} hours after the loan was applied for.", 409)

    now = utcnow()
    title = _title(deal)
    other_id = deal.seller_id if role == "buyer" else deal.buyer_id
    mine = deal.buyer_done_at if role == "buyer" else deal.seller_done_at
    note = " ".join(str(note or "").split())[:MAX_NOTE] or None

    if answer == "yes":
        if mine is not None:
            return "already"
        if role == "buyer":
            deal.buyer_done_at = now
        else:
            deal.seller_done_at = now
        add_event(deal, f"{role}_confirmed_done", user.id, via=via)

        if deal.buyer_done_at and deal.seller_done_at:
            app = linked_application(deal)
            if app is not None and app.status not in ("disbursed", "completed"):
                app.status = "disbursed"
                app.disbursed_at = app.disbursed_at or now
                add_loan_event(app, "disbursed", "confirmation", user.id,
                               "Buyer and seller confirmed the deal is done")
            deal.money_received_at = deal.money_received_at or now
            deal.vehicle_received_at = deal.vehicle_received_at or now
            complete_deal(deal, user.id, outbox)
            return "completed"

        if role == "buyer":
            text = ("The buyer confirmed they got the loan and the deal is done. Seller: please "
                    "confirm the deal is done too, so it can be completed.")
            heading = f"Buyer confirmed the deal · {title}"
        else:
            text = ("The seller confirmed the deal is done. Buyer: please confirm you got the "
                    "loan, so the deal can be completed.")
            heading = f"Seller confirmed the deal · {title}"
        post_thread_message(deal.product_id, user.id, other_id, text, "system")
        notify(other_id, heading[:200], text, deal.product_id)
        outbox.push(other_id, heading, text, deal.product_id, user.id)
        return "confirmed"

    # answer == "no"
    if mine is not None:
        raise AnswerError("You've already confirmed this deal is done.", 409)
    since = now - timedelta(hours=NO_REPEAT_HOURS)
    recent = (DealEvent.query.filter(DealEvent.deal_id == deal.id,
                                     DealEvent.event_type == "not_done_reported",
                                     DealEvent.actor_id == user.id,
                                     DealEvent.created_at > since).first())
    add_event(deal, "not_done_reported", user.id, role=role, via=via, note=note)
    if recent is not None:
        return "reported"
    if role == "buyer":
        text = "The buyer says the loan hasn't come through yet, so the deal isn't done. DealBriz support has been told."
    else:
        text = "The seller says the deal isn't done yet. DealBriz support has been told."
    post_thread_message(deal.product_id, user.id, other_id, text, "system")
    notify(other_id, f"Deal not done yet · {title}"[:200], text, deal.product_id)
    outbox.push(other_id, f"Deal not done yet · {title}", text, deal.product_id, user.id)
    notify_staff(f"Deal not done · {title}",
                 f"The {role} ({display_name(user)}) says the deal isn't done"
                 + (f": {note}" if note else ".") + f" Deal status: {deal.status}.",
                 deal.product_id)
    return "reported"


def _commit():
    try:
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        return False


@bp.route("/api/deals/<deal_id>/done", methods=["POST"])
@jwt_required()
def answer_in_app(deal_id):
    user = current_user()
    if not user or (user.account_status or "active") != "active":
        return jsonify({"error": "Please sign in again."}), 401
    deal = Deal.query.filter_by(id=deal_id).with_for_update().first()
    if not deal or user.id not in (deal.buyer_id, deal.seller_id):
        return jsonify({"error": "Deal not found."}), 404
    data = request.get_json(silent=True) or {}
    outbox = Outbox()
    try:
        result = record_answer(deal, user, str(data.get("answer") or "").strip().lower(), "app",
                               outbox, data.get("note"))
    except AnswerError as exc:
        db.session.rollback()
        return jsonify({"error": exc.message}), exc.status
    if not _commit():
        return jsonify({"error": "Something changed while you were doing that. Please refresh and try again."}), 409
    outbox.send()
    from .routes import deal_detail
    out = deal_detail(deal, user)
    out["done_result"] = result
    return jsonify(out)


# ------------------------------------------------------------------ email page

_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title} · DealBriz</title>
<style>
body{{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#F5F7FA;color:#0F172A}}
.card{{max-width:440px;margin:40px auto;background:#fff;border:1px solid #E2E8F0;border-radius:20px;padding:28px 24px;box-shadow:0 8px 30px rgba(15,23,42,.06)}}
h1{{font-size:21px;margin:0 0 10px}} p{{font-size:15px;line-height:1.55;color:#334155;margin:0 0 14px}}
.item{{font-weight:700;color:#0F172A}} .btns{{display:flex;flex-direction:column;gap:10px;margin-top:18px}}
button,.link{{display:block;width:100%;box-sizing:border-box;padding:14px;border-radius:14px;font-size:16px;font-weight:700;border:0;cursor:pointer;text-align:center;text-decoration:none}}
.yes{{background:#1A56DB;color:#fff}} .no{{background:#F1F5F9;color:#334155}} .link{{background:#1A56DB;color:#fff}}
textarea{{width:100%;box-sizing:border-box;border:1px solid #CBD5E1;border-radius:12px;padding:10px;font:inherit;font-size:14px;margin-top:6px}}
.ok{{color:#047857}} .err{{color:#B91C1C}} small{{color:#64748B}}
</style></head><body><div class="card">
<p style="font-weight:800;color:#1A56DB;margin-bottom:18px">dealbriz</p>
{body}
</div></body></html>"""


def _page(title, body, status=200):
    resp = make_response(_PAGE.format(title=_esc(title), body=body), status)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def _bad_link():
    return _page("Link expired",
                 "<h1>This link has expired</h1><p>Open the deal in the DealBriz app or on "
                 f"<a href=\"{_esc(_site())}\">dealbriz.com</a> to confirm it.</p>", 400)


@bp.route("/api/deals/email-answer", methods=["GET"])
def email_answer_page():
    payload = read_token(request.args.get("t"))
    if not payload:
        return _bad_link()
    deal = Deal.query.get(payload["d"])
    user = User.query.get(payload["u"])
    if not deal or not user:
        return _bad_link()
    role = _role(deal, user.id)
    if role is None:
        return _bad_link()
    title = _title(deal)
    if deal.status == DEAL_COMPLETED:
        return _page("Deal completed", f"<h1 class=\"ok\">This deal is complete</h1><p class=\"item\">{_esc(title)}</p>"
                     "<p>Nothing more to do. Thank you for using DealBriz.</p>")
    mine = deal.buyer_done_at if role == "buyer" else deal.seller_done_at
    if mine is not None:
        return _page("Already confirmed", f"<h1 class=\"ok\">You've already confirmed</h1><p class=\"item\">{_esc(title)}</p>"
                     "<p>We're waiting for the other side to confirm. The deal is completed as soon as they do.</p>")
    if deal.status not in DONE_CHECK_STATES:
        return _page("Deal closed", f"<h1>This deal can't be confirmed</h1><p class=\"item\">{_esc(title)}</p>"
                     "<p>It isn't waiting for a confirmation any more. Open it in the DealBriz app for details.</p>")
    token = _esc(request.args.get("t"))
    pre = request.args.get("a")
    focus_no = pre == "no"
    body = (f"<h1>{_esc(_question(role))}</h1><p class=\"item\">{_esc(title)}</p>"
            "<p>When both the buyer and the seller say yes, DealBriz marks the deal as completed and the "
            "listing as sold.</p>"
            "<form method=\"post\" action=\"/api/deals/email-answer\" class=\"btns\">"
            f"<input type=\"hidden\" name=\"t\" value=\"{token}\">"
            f"<button class=\"yes\" type=\"submit\" name=\"a\" value=\"yes\">{_esc(_yes_label(role))}</button>"
            + ("<label><small>What's wrong? (optional)</small><textarea name=\"note\" rows=\"3\" maxlength=\"500\"></textarea></label>"
               if focus_no else "")
            + f"<button class=\"no\" type=\"submit\" name=\"a\" value=\"no\">{_esc(_no_label(role))}</button>"
            "</form>")
    return _page("Confirm your deal", body)


@bp.route("/api/deals/email-answer", methods=["POST"])
def email_answer_submit():
    payload = read_token(request.form.get("t"))
    if not payload:
        return _bad_link()
    answer = (request.form.get("a") or "").strip().lower()
    deal = Deal.query.filter_by(id=payload["d"]).with_for_update().first()
    user = User.query.get(payload["u"])
    if not deal or not user or (user.account_status or "active") != "active":
        db.session.rollback()
        return _bad_link()
    outbox = Outbox()
    title = _title(deal)
    try:
        result = record_answer(deal, user, answer, "email", outbox, request.form.get("note"))
    except AnswerError as exc:
        db.session.rollback()
        return _page("Couldn't confirm", f"<h1 class=\"err\">Couldn't save that</h1><p class=\"item\">{_esc(title)}</p>"
                     f"<p>{_esc(exc.message)}</p>", exc.status)
    if not _commit():
        return _page("Try again", "<h1 class=\"err\">Something changed</h1><p>Please open the link again.</p>", 409)
    outbox.send()
    msgs = {
        "completed": ("Deal completed", "<h1 class=\"ok\">Deal completed</h1><p>Both of you confirmed. The listing is now "
                                        "marked as sold. Thank you for using DealBriz.</p>"),
        "already_completed": ("Deal completed", "<h1 class=\"ok\">This deal is complete</h1><p>Nothing more to do.</p>"),
        "confirmed": ("Thank you", "<h1 class=\"ok\">Thank you, confirmed</h1><p>We've asked the other side to "
                                   "confirm too. The deal is completed as soon as they do.</p>"),
        "already": ("Already confirmed", "<h1 class=\"ok\">You've already confirmed</h1><p>We're waiting for the "
                                         "other side.</p>"),
        "reported": ("Thanks for telling us", "<h1>Thanks for telling us</h1><p>DealBriz support and the other side "
                                              "have been told. The deal stays open; you can confirm it later from the "
                                              "deal in the app.</p>"),
    }
    t, body = msgs.get(result, msgs["confirmed"])
    return _page(t, f"{body}<p class=\"item\">{_esc(title)}</p>")


# ------------------------------------------------------------------ emails

def _send_email(user, subject, lead, deal_id):
    """The question email with Yes / No links. Returns True when handed to
    the mail provider. Never raises: the bell and push are already queued."""
    import mailer
    if not user or not user.email or not mailer.is_configured():
        return False
    role = None
    deal = Deal.query.get(deal_id)
    if deal is not None:
        role = _role(deal, user.id)
    yes = answer_link(deal_id, user.id, "yes")
    no = answer_link(deal_id, user.id, "no")
    name = (user.first_name or "").strip() or "there"
    q = _question(role)
    text = (f"Hi {name},\n\n{lead}\n\n{q}\n\n{_yes_label(role)}: {yes}\n\n{_no_label(role)}: {no}\n\n"
            "When both the buyer and the seller say yes, the deal is completed and the listing marked as sold. "
            "You can also answer from the deal in the DealBriz app.\n\nDealBriz")
    html = (f"<p>Hi {_esc(name)},</p><p>{_esc(lead)}</p><p style=\"font-size:17px;font-weight:700\">{_esc(q)}</p>"
            f"<p><a href=\"{_esc(yes)}\" style=\"display:inline-block;background:#1A56DB;color:#fff;padding:12px 20px;"
            f"border-radius:10px;text-decoration:none;font-weight:700\">{_esc(_yes_label(role))}</a>&nbsp;&nbsp;"
            f"<a href=\"{_esc(no)}\" style=\"display:inline-block;background:#F1F5F9;color:#334155;padding:12px 20px;"
            f"border-radius:10px;text-decoration:none;font-weight:700\">{_esc(_no_label(role))}</a></p>"
            "<p style=\"color:#666\">When both the buyer and the seller say yes, the deal is completed and the "
            "listing marked as sold. You can also answer from the deal in the DealBriz app.</p><p>DealBriz</p>")
    try:
        mailer.send(mailer.build([user.email], subject[:150], text, html))
        return True
    except Exception as exc:
        log.error("deal confirmation email to user %s failed: %s", user.id, exc)
        return False


def send_loan_checks(now, outbox, batch=100):
    """Deals whose loan was applied for LOAN_CHECK_HOURS ago: ask the buyer
    "Did you get your loan?" (bell + push now, email after commit).
    Returns (count, emails) - emails are (user_id, subject, lead, deal_id),
    sent by the caller after the commit."""
    cutoff = now - timedelta(hours=LOAN_CHECK_HOURS)
    deals = (Deal.query
             .filter(Deal.status.in_(DONE_CHECK_STATES), Deal.loan_applied_at.isnot(None),
                     Deal.loan_applied_at <= cutoff, Deal.loan_check_sent_at.is_(None))
             .order_by(Deal.loan_applied_at)
             .limit(batch)
             .with_for_update(skip_locked=True)
             .all())
    emails = []
    for deal in deals:
        deal.loan_check_sent_at = now
        add_event(deal, "loan_check_sent", None)
        title = _title(deal)
        if deal.buyer_done_at is None:
            text = ("Did you get your loan? If yes, confirm the deal in DealBriz. When the seller "
                    "confirms too, the deal is completed.")
            notify(deal.buyer_id, f"Did you get your loan? · {title}"[:200], text, deal.product_id)
            outbox.push(deal.buyer_id, f"Did you get your loan? · {title}", text, deal.product_id, deal.seller_id)
            emails.append((deal.buyer_id, f"Did you get your loan? · {title}",
                           f"You applied for a loan for “{title}” on DealBriz.", deal.id))
    return len(deals), emails


def send_done_checks(now, outbox, batch=100):
    """Deals whose loan was applied for DONE_CHECK_DAYS ago and still not
    completed: ask whoever hasn't said yes "Is your deal done?"."""
    cutoff = now - timedelta(days=DONE_CHECK_DAYS)
    deals = (Deal.query
             .filter(Deal.status.in_(DONE_CHECK_STATES), Deal.loan_applied_at.isnot(None),
                     Deal.loan_applied_at <= cutoff, Deal.done_check_sent_at.is_(None))
             .order_by(Deal.loan_applied_at)
             .limit(batch)
             .with_for_update(skip_locked=True)
             .all())
    emails = []
    for deal in deals:
        deal.done_check_sent_at = now
        # The confirm card needs the first check; set it if that run was missed.
        if deal.loan_check_sent_at is None:
            deal.loan_check_sent_at = now
        add_event(deal, "done_check_sent", None)
        title = _title(deal)
        for uid, other, done in ((deal.buyer_id, deal.seller_id, deal.buyer_done_at),
                                 (deal.seller_id, deal.buyer_id, deal.seller_done_at)):
            if done is not None:
                continue
            text = (f"It's been {DONE_CHECK_DAYS} days since the loan was applied for. Is your deal done? "
                    "Confirm it in DealBriz, or tell us if it isn't.")
            notify(uid, f"Is your deal done? · {title}"[:200], text, deal.product_id)
            outbox.push(uid, f"Is your deal done? · {title}", text, deal.product_id, other)
            emails.append((uid, f"Is your deal done? · {title}",
                           f"It's been {DONE_CHECK_DAYS} days since the loan for “{title}” was applied for.",
                           deal.id))
    return len(deals), emails


def send_emails(emails):
    """After the commit: the question emails. Returns how many went out."""
    sent = 0
    for uid, subject, lead, deal_id in emails:
        if _send_email(User.query.get(uid), subject, lead, deal_id):
            sent += 1
    return sent


# ------------------------------------------------------------------ consent

@bp.route("/api/deals/docs-consent", methods=["POST"])
@jwt_required()
def docs_consent():
    """{context}: the user ticked "I agree to DealBriz storing my documents and
    sharing them with the lender, as in the Privacy Policy" before uploading.
    One row per tick (a repeat within 10 minutes for the same place is not
    stored again)."""
    from models.consent import DocumentConsent
    user = current_user()
    if not user:
        return jsonify({"error": "Please sign in again."}), 401
    data = request.get_json(silent=True) or {}
    context = " ".join(str(data.get("context") or "").split())[:80] or None
    now = utcnow()
    try:
        recent = (DocumentConsent.query
                  .filter(DocumentConsent.user_id == user.id, DocumentConsent.context == context,
                          DocumentConsent.consented_at > now - timedelta(minutes=10))
                  .first())
        if recent is None:
            ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()[:45]
            recent = DocumentConsent(user_id=user.id, context=context, policy=CONSENT_POLICY,
                                     consented_at=now, ip=ip or None)
            db.session.add(recent)
            db.session.commit()
    except Exception:
        db.session.rollback()
        log.error("could not record document consent", exc_info=True)
        return jsonify({"error": "Could not save your consent. Please try again."}), 503
    return jsonify({"ok": True, **recent.to_dict()}), 201
