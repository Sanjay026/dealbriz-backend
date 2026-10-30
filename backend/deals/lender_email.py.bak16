"""
The documents email to the lender (NBFC).

When the buyer applies for the loan on a locked deal, the deal's lender gets
ONE email with:
  - the loan application (amount, down payment, tenure, the buyer's
    estimates, name, phone, income)
  - the deal (item, agreed price, reservation) and both parties
  - the buyer's documents (PAN card, Aadhaar card), attached and labelled
    BUYER-...
  - the seller's documents from the listing (RC, insurance, PUC, bank proof,
    loan NOC), attached and labelled SELLER-...
Every attachment was verified by DealBriz or the lender before the deal
locked.

It goes to the lender's documents email (financers.docs_email). It is
queued (a deal_lender_emails row) in the same transaction as the change that
calls for it, and sent after commit on a background thread. The timer job
(deals.jobs, every 5 minutes) sends anything still waiting and retries
failures. A lender with no documents email yet gets status "no_email": the
superadmins and the lender's people are told, and it is sent as soon as an
address is saved.

Triggers
  - the buyer applies for the loan (loans/routes.py)
  - staff change the lender on a deal's loan application (admin EMI review)
  - a lender saves its documents email (rows waiting with "no_email")
  - staff press "Send again" on the deal (POST /api/deals/staff/<id>/lender-email)
"""
import html
import logging
import re
import threading
from datetime import timedelta

from extensions import db
from models import (Deal, DealDocument, DealLenderEmail, EmiApplication, Financer, FinancerMember,
                    ListingDocument, Notification, Product, User)
from models.deal import DOC_APPROVED, LOAN_VEHICLE

log = logging.getLogger("dealbridge.deals.lender_email")

PENDING, SENDING, SENT, FAILED, NO_EMAIL = "pending", "sending", "sent", "failed", "no_email"
MAX_ATTEMPTS = 6
RETRY_MINUTES = 10
STUCK_MINUTES = 15
MAX_ADDRESSES = 3
_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}$")
EXT = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


# --------------------------------------------------------------------------
# Addresses
# --------------------------------------------------------------------------

def parse_emails(raw):
    """'a@x.in, b@x.in' -> (['a@x.in', 'b@x.in'], None) or (None, error)."""
    parts = [p.strip() for p in re.split(r"[,;\s]+", raw or "") if p.strip()]
    if not parts:
        return [], None
    if len(parts) > MAX_ADDRESSES:
        return None, f"Up to {MAX_ADDRESSES} email addresses."
    bad = [p for p in parts if not _EMAIL_RE.match(p) or len(p) > 120]
    if bad:
        return None, f"That doesn't look like an email address: {bad[0]}"
    seen = []
    for p in parts:
        if p.lower() not in [s.lower() for s in seen]:
            seen.append(p)
    return seen, None


def lender_addresses(financer):
    if financer is None:
        return []
    emails, _ = parse_emails(financer.docs_email or "")
    return emails or []


# --------------------------------------------------------------------------
# Queueing (no commit here: callers commit once)
# --------------------------------------------------------------------------

def queue(deal, app=None, actor_id=None, force=False):
    """Queue the email for the deal's current lender. Returns the row, or
    None when the deal has no lender yet (it is queued once one is set).
    Without force, a deal is emailed to a lender once."""
    from .service import linked_application
    if deal is None or not deal.financer_id:
        return None
    app = app or linked_application(deal)
    if app is None:
        return None
    financer = Financer.query.get(deal.financer_id)
    if not force:
        existing = (DealLenderEmail.query
                    .filter(DealLenderEmail.deal_id == deal.id,
                            DealLenderEmail.financer_id == deal.financer_id,
                            DealLenderEmail.status.in_((PENDING, SENDING, SENT, FAILED, NO_EMAIL)))
                    .order_by(DealLenderEmail.created_at.desc()).first())
        if existing is not None:
            if existing.status == NO_EMAIL and lender_addresses(financer):
                existing.to_email = ", ".join(lender_addresses(financer))
                existing.status = PENDING
            return existing
    emails = lender_addresses(financer)
    row = DealLenderEmail(deal_id=deal.id, application_id=app.id, financer_id=deal.financer_id,
                          to_email=", ".join(emails) or None,
                          status=PENDING if emails else NO_EMAIL, requested_by=actor_id)
    db.session.add(row)
    db.session.flush()
    if not emails:
        _tell_no_email(deal, financer)
    return row


def requeue_for_financer(financer):
    """The lender just saved a documents email: everything that waited for
    one goes out now. Returns the row ids to send."""
    emails = lender_addresses(financer)
    if not emails:
        return []
    rows = DealLenderEmail.query.filter_by(financer_id=financer.financer_id, status=NO_EMAIL).all()
    for r in rows:
        r.to_email = ", ".join(emails)
        r.status = PENDING
    return [r.id for r in rows]


def _tell_no_email(deal, financer):
    from utils import is_superadmin
    from .service import staff_user_ids, lender_member_ids
    product = Product.query.get(deal.product_id)
    title = product.title if product else "a listing"
    name = financer.name if financer else "The lender"
    body = (f"{name} has no documents email set, so the loan application and documents for "
            f"{title} couldn't be emailed. Add it in the Lender portal (or Financers). "
            "It is sent as soon as an address is saved.")
    for uid in staff_user_ids("deals.view"):
        u = User.query.get(uid)
        if u is not None and is_superadmin(u):
            Notification.push(uid, "system", f"Lender email needed · {title}"[:200], body=body,
                              link_page="admin", link_ref=deal.product_id)
    if financer is not None:
        for uid in lender_member_ids(financer.financer_id):
            Notification.push(uid, "system", f"Set your documents email · {title}"[:200], body=body,
                              link_page="admin", link_ref=deal.product_id)


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------

def send_soon(row_ids):
    """After commit: send on a background thread so the request returns
    at once. The timer job picks up anything this misses."""
    ids = [i for i in (row_ids or []) if i]
    if not ids:
        return None
    from flask import current_app
    app = current_app._get_current_object()

    def run():
        with app.app_context():
            for rid in ids:
                try:
                    deliver(rid)
                except Exception:
                    db.session.rollback()
                    log.exception("Lender email %s failed", rid)
    t = threading.Thread(target=run, name="lender-email", daemon=True)
    t.start()
    return t


def send_due(now, batch=20):
    """Timer: waiting, failed (after a pause) and stuck emails. Returns how
    many were sent."""
    q = (DealLenderEmail.query
         .filter(db.or_(DealLenderEmail.status == PENDING,
                        db.and_(DealLenderEmail.status == FAILED,
                                DealLenderEmail.attempts < MAX_ATTEMPTS,
                                DealLenderEmail.updated_at <= now - timedelta(minutes=RETRY_MINUTES)),
                        db.and_(DealLenderEmail.status == SENDING,
                                DealLenderEmail.updated_at <= now - timedelta(minutes=STUCK_MINUTES))))
         .order_by(DealLenderEmail.created_at).limit(batch))
    ids = [r.id for r in q.all()]
    sent = 0
    for rid in ids:
        if deliver(rid):
            sent += 1
    return sent


def deliver(row_id):
    """Claims one row, builds and sends the email(s), records the result.
    Commits. Returns True when sent."""
    import mailer
    from .service import add_event, utcnow
    row = (DealLenderEmail.query.filter_by(id=row_id).with_for_update(skip_locked=True).first())
    if row is None or row.status in (SENT, NO_EMAIL):
        db.session.rollback()
        return False
    if row.status == FAILED and row.attempts >= MAX_ATTEMPTS:
        db.session.rollback()
        return False
    if not mailer.is_configured():
        row.last_error = "Email is not set up on the server yet (MAIL_PROVIDER). It will be sent once it is."
        db.session.commit()
        return False
    # Always send to the lender's CURRENT address.
    financer = Financer.query.get(row.financer_id) if row.financer_id else None
    emails = lender_addresses(financer)
    if not emails:
        row.status = NO_EMAIL
        db.session.commit()
        return False
    row.to_email = ", ".join(emails)
    row.status = SENDING
    row.attempts = (row.attempts or 0) + 1
    db.session.commit()

    try:
        messages = compose(row)
        for msg in messages:
            mailer.send(msg)
    except Exception as exc:
        db.session.rollback()
        row = DealLenderEmail.query.get(row_id)
        row.status = FAILED
        row.last_error = str(exc)[:480] or exc.__class__.__name__
        if row.attempts >= MAX_ATTEMPTS:
            _tell_failed(row)
        db.session.commit()
        log.warning("Lender email %s failed (try %s): %s", row_id, row.attempts, exc)
        return False

    row = DealLenderEmail.query.get(row_id)
    row.status = SENT
    row.sent_at = utcnow()
    row.parts = len(messages)
    row.last_error = None
    deal = Deal.query.get(row.deal_id)
    if deal is not None:
        add_event(deal, "lender_emailed", None, to=row.to_email, parts=row.parts,
                  lender=financer.name if financer else None)
    db.session.commit()
    return True


def _tell_failed(row):
    from utils import is_superadmin
    from .service import staff_user_ids
    deal = Deal.query.get(row.deal_id)
    product = Product.query.get(deal.product_id) if deal else None
    title = product.title if product else "a deal"
    for uid in staff_user_ids("deals.view"):
        u = User.query.get(uid)
        if u is not None and is_superadmin(u):
            Notification.push(uid, "system", f"Lender email failed · {title}"[:200],
                              body=f"The documents email to {row.to_email} failed {row.attempts} times: "
                                   f"{(row.last_error or '')[:200]}. Open the deal to send it again.",
                              link_page="admin", link_ref=deal.product_id if deal else None)


# --------------------------------------------------------------------------
# The email itself
# --------------------------------------------------------------------------

def _slug(text):
    # "Buyer's PAN card" -> "PAN-card": the BUYER-/SELLER- prefix says whose.
    text = re.sub(r"^(Buyer|Seller)'s\s+", "", text or "")
    text = text.replace("'", "")
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return s[:60] or "document"


def _when(dt):
    from .service import short_date
    return (short_date(dt) + " IST") if dt else "-"


def _reviewer(user_id, financer_id=None):
    if financer_id:
        f = Financer.query.get(financer_id)
        return f.name if f else "the lender"
    if user_id:
        m = FinancerMember.query.get(user_id)
        if m is not None:
            f = Financer.query.get(m.financer_id)
            return f.name if f else "the lender"
    return "DealBriz"


def _documents(deal, product):
    """[(party, label, filename, bytes|None, content_type, verified_at, verified_by)]
    buyer's first, then the seller's, in the usual order."""
    from . import doc_storage, rules
    out = []
    have = {d.doc_type: d for d in DealDocument.query.filter_by(deal_id=deal.id).all()}
    n = 0
    for t in rules.BUYER_DOC_TYPES:
        d = have.get(t)
        if d is None or d.status != DOC_APPROVED:
            continue
        n += 1
        out.append(_attachment("BUYER", n, t, d, d.reviewed_by, None))
    if product is not None:
        possible = rules.listing_possible_docs(product.category)
        need = set(rules.listing_required_docs(product.category, product.vehicle_had_loan))
        lhave = {d.doc_type: d for d in ListingDocument.query.filter_by(product_id=product.id).all()}
        n = 0
        for t in rules.SELLER_DOC_TYPES:
            d = lhave.get(t)
            if d is None or d.status != DOC_APPROVED or t not in possible or t not in need:
                continue
            n += 1
            out.append(_attachment("SELLER", n, t, d, d.reviewed_by, d.reviewed_financer_id))
    return out


def _attachment(party, n, doc_type, doc, reviewed_by, reviewed_financer_id):
    from . import doc_storage, rules
    label = rules.DOC_TYPES.get(doc_type, {}).get("label", doc_type)
    ctype = doc.content_type or "application/octet-stream"
    ext = EXT.get(ctype) or ((doc.file_name or "").rsplit(".", 1)[-1].lower() if "." in (doc.file_name or "") else "bin")
    name = f"{party}-{n:02d}-{_slug(label)}.{ext}"
    data, error = None, None
    if doc.storage_key and not doc.deleted_at:
        try:
            data = doc_storage.read_bytes(doc.storage_key)
        except Exception as exc:
            # Not sent half-empty: the email fails and is retried.
            log.warning("Could not read %s for the lender email: %s", doc.storage_key, exc)
            error = f"{label}: {exc}"
    return {"party": party, "label": label, "filename": name, "data": data, "ctype": ctype, "error": error,
            "verified_at": doc.reviewed_at, "verified_by": _reviewer(reviewed_by, reviewed_financer_id)}


def compose(row):
    """The email (or emails, when the attachments are too big for one)."""
    import os
    import mailer
    from . import rules
    from .service import display_name, inr
    deal = Deal.query.get(row.deal_id)
    product = Product.query.get(deal.product_id)
    buyer, seller = User.query.get(deal.buyer_id), User.query.get(deal.seller_id)
    app = EmiApplication.query.get(row.application_id) if row.application_id else None
    if app is None:
        from .service import linked_application
        app = linked_application(deal)
    financer = Financer.query.get(row.financer_id) if row.financer_id else None
    lender = financer.name if financer else "Lender"
    site = (os.environ.get("SITE_URL") or "https://dealbriz.com").rstrip("/")
    ref = deal.id[:8].upper()
    title = product.title if product else "Listing"
    vehicle = deal.loan_type == LOAN_VEHICLE
    docs = _documents(deal, product)
    unreadable = [d["error"] for d in docs if d.get("error")]
    if unreadable:
        raise RuntimeError(f"Could not read {len(unreadable)} document file(s) from storage. {unreadable[0]}"[:480])

    # Split the files into emails of at most MAIL_MAX_MB (base64 adds a third).
    limit = mailer.max_attachment_bytes()
    parts, cur, size = [], [], 0
    for d in docs:
        sz = int(len(d["data"]) * 1.37) if d["data"] else 0
        if cur and size + sz > limit:
            parts.append(cur)
            cur, size = [], 0
        cur.append(d)
        size += sz
    parts.append(cur)
    total = len(parts)

    def money(v):
        return inr(v) if v is not None else "-"

    attrs = (product.attributes_json or {}) if product else {}
    item_rows = [("Item", title),
                 ("Category", (product.category or "").title() if product else "-")]
    if product is not None:
        make = " ".join(x for x in (product.brand, product.model) if x)
        if make:
            item_rows.append(("Make and model", make))
        labels = (("year", "Year"), ("km_driven", "Kilometres driven"), ("fuel", "Fuel"),
                  ("transmission", "Transmission"), ("owners", "Number of owners"), ("engine_cc", "Engine (cc)"),
                  ("color", "Colour"))
        for k, lab in labels:
            if attrs.get(k) not in (None, ""):
                item_rows.append((lab, str(attrs[k])))
        item_rows.append(("Location", f"{product.location or '-'} (pincode {product.city or '-'})"))
        item_rows.append(("Listed price", money(product.price)))
    item_rows.append(("Agreed price", money(deal.agreed_price)))

    app_rows = []
    if app is not None:
        app_rows = [
            ("Application reference", app.id[:8].upper()),
            ("Applied on", _when(app.applied_at)),
            ("Applicant", app.applicant_name),
            ("Applicant phone", app.applicant_phone),
            ("Monthly income (stated)", money(app.applicant_income)),
            ("Loan type", "Vehicle loan" if vehicle else "Personal loan"),
            ("Agreed price", money(deal.agreed_price)),
            ("Down payment", money(app.down_payment)),
            ("Loan amount requested", money(app.loan_amount)),
            ("Tenure", f"{app.tenure_months} months"),
            ("Interest rate (buyer's estimate)", f"{float(app.interest_rate):g}% p.a."),
            ("EMI (estimate)", money(app.monthly_emi)),
            ("Total repayable (estimate)", money(app.total_payable)),
        ]
    party_rows = lambda u: [("Name", display_name(u)), ("Phone", (u.phone if u else None) or "-"),
                            ("Email", (u.email if u else None) or "-")]
    deal_rows = [("DealBriz deal reference", ref),
                 ("Deal locked on", _when(deal.locked_at)),
                 ("Listing reserved for the buyer until", _when(deal.reserved_until))]

    messages = []
    for i, files in enumerate(parts, start=1):
        part_note = f" (part {i} of {total})" if total > 1 else ""
        subject = f"DealBriz loan application: {title}, {money(deal.agreed_price)} (Deal {ref}){part_note}"
        text, page = _render(lender, title, ref, site, deal_rows, app_rows, item_rows,
                             party_rows(buyer), party_rows(seller), docs, files, i, total, vehicle)
        attachments = [(d["filename"], d["data"], d["ctype"]) for d in files if d["data"]]
        to = [a.strip() for a in (row.to_email or "").split(",") if a.strip()]
        messages.append(mailer.build(to, subject, text, page, attachments))
    return messages


def _render(lender, title, ref, site, deal_rows, app_rows, item_rows, buyer_rows, seller_rows,
            docs, files, part, total, vehicle):
    """(plain text, html) for one email."""
    e = html.escape
    in_this = {d["filename"] for d in files}

    def doc_lines(party):
        rows = []
        for d in docs:
            if d["party"] != party:
                continue
            if d["data"] is None:
                where = "not attached (file not available, ask DealBriz)"
            elif d["filename"] in in_this:
                where = d["filename"]
            else:
                where = f"{d['filename']} (in another part of this email)"
            rows.append((d["label"], where, f"{_when(d['verified_at'])} by {d['verified_by']}"))
        return rows

    buyer_docs, seller_docs = doc_lines("BUYER"), doc_lines("SELLER")
    seller_head = "Seller's documents (vehicle documents, from the listing)" if vehicle else "Seller's documents"
    portal = f"{site}/admin?section=lender"

    # ---- plain text
    t = [f"Dear {lender} team,", "",
         ("A buyer on DealBriz has applied for a loan on a locked DealLock deal. The buyer and the "
          "seller have agreed the price, both have accepted the deal terms, and every document "
          "attached has been verified by DealBriz or your team."), ""]
    if total > 1:
        t += [f"This is part {part} of {total}: the attachments were too large for one email.", ""]

    def block(head, rows):
        t.append(head.upper())
        for k, v in rows:
            t.append(f"  {k}: {v}")
        t.append("")
    block("Loan application", app_rows)
    block("Deal", deal_rows)
    block("Item", item_rows)
    block("Buyer (loan applicant)", buyer_rows)
    block("Seller", seller_rows)
    for head, rows in (("Buyer's documents", buyer_docs), (seller_head, seller_docs)):
        t.append(head.upper())
        if not rows:
            t.append("  none")
        for label, where, ver in rows:
            t.append(f"  {label}: {where}. Verified {ver}.")
        t.append("")
    t += ["NEXT STEPS",
          "  1. Contact the buyer to continue the application.",
          "  2. Record your decision and the application reference in the DealBriz Lender portal:",
          f"     {portal}",
          "  3. When the loan is sanctioned and the forms are signed, mark the deal Ready to pay there.",
          "",
          ("CONFIDENTIAL: this email contains identity documents shared with your organisation only "
           "to assess this loan application. Store them securely and do not forward them. If you "
           "received it by mistake, please delete it and tell DealBriz by replying to this email."),
          "", "DealBriz"]
    text = "\n".join(t)

    # ---- html (inline styles: email clients ignore stylesheets)
    def table(rows, three=False):
        trs = []
        for r in rows:
            if three:
                trs.append(f"<tr><td style='padding:7px 10px;border-top:1px solid #E5E9EF;font-weight:600'>{e(r[0])}</td>"
                           f"<td style='padding:7px 10px;border-top:1px solid #E5E9EF;font-family:monospace;font-size:12px'>{e(r[1])}</td>"
                           f"<td style='padding:7px 10px;border-top:1px solid #E5E9EF;color:#475569'>{e(r[2])}</td></tr>")
            else:
                trs.append(f"<tr><td style='padding:7px 10px;border-top:1px solid #E5E9EF;color:#475569;width:42%'>{e(r[0])}</td>"
                           f"<td style='padding:7px 10px;border-top:1px solid #E5E9EF;font-weight:600'>{e(str(r[1]))}</td></tr>")
        if not trs:
            trs.append("<tr><td style='padding:7px 10px;color:#475569'>None</td></tr>")
        head = ("<tr style='background:#F8FAFC'><th align='left' style='padding:7px 10px'>Document</th>"
                "<th align='left' style='padding:7px 10px'>Attached file</th>"
                "<th align='left' style='padding:7px 10px'>Verified</th></tr>") if three else ""
        return (f"<table cellpadding='0' cellspacing='0' width='100%' style='border:1px solid #E5E9EF;"
                f"border-radius:8px;border-collapse:separate;font-size:14px;margin:0 0 18px'>{head}{''.join(trs)}</table>")

    def h(text_):
        return f"<h3 style='font-size:15px;margin:22px 0 8px;color:#0F172A'>{e(text_)}</h3>"

    part_html = (f"<p style='background:#FEF3C7;padding:10px 12px;border-radius:8px'>This is <b>part {part} of {total}</b>: "
                 "the attachments were too large for one email.</p>") if total > 1 else ""
    page = f"""<!doctype html><html><body style="margin:0;background:#F4F6F9">
<div style="max-width:680px;margin:0 auto;padding:24px 16px;font-family:Arial,Helvetica,sans-serif;color:#0F172A">
<div style="background:#0F172A;color:#fff;padding:16px 20px;border-radius:12px 12px 0 0">
  <div style="font-size:18px;font-weight:700">DealBriz &middot; Loan application</div>
  <div style="font-size:13px;opacity:.85;margin-top:4px">{e(title)} &middot; Deal {e(ref)}</div>
</div>
<div style="background:#fff;padding:20px;border-radius:0 0 12px 12px">
<p>Dear {e(lender)} team,</p>
<p>A buyer on DealBriz has applied for a loan on a locked DealLock deal. The buyer and the seller have agreed
the price, both have accepted the deal terms, and every document attached has been verified by DealBriz or your team.</p>
{part_html}
{h("Loan application")}{table(app_rows)}
{h("Deal")}{table(deal_rows)}
{h("Item")}{table(item_rows)}
{h("Buyer (loan applicant)")}{table(buyer_rows)}
{h("Seller")}{table(seller_rows)}
{h("Buyer's documents")}{table(buyer_docs, three=True)}
{h(seller_head)}{table(seller_docs, three=True)}
{h("Next steps")}
<ol style="padding-left:20px;line-height:1.6;margin:0 0 18px">
  <li>Contact the buyer to continue the application.</li>
  <li>Record your decision and the application reference in the
      <a href="{e(portal)}">DealBriz Lender portal</a>.</li>
  <li>When the loan is sanctioned and the forms are signed, mark the deal <b>Ready to pay</b> there.</li>
</ol>
<p style="font-size:12px;color:#475569;background:#F8FAFC;padding:10px 12px;border-radius:8px">
<b>Confidential.</b> This email contains identity documents shared with your organisation only to assess this
loan application. Store them securely and do not forward them. If you received it by mistake, please delete it
and tell DealBriz by replying to this email.</p>
<p style="margin:16px 0 0">DealBriz</p>
</div></div></body></html>"""
    return text, page


def summary(deal):
    """For the deal detail (staff and lenders): the latest email's state."""
    row = (DealLenderEmail.query.filter_by(deal_id=deal.id)
           .order_by(DealLenderEmail.created_at.desc()).first())
    return row.to_dict() if row else None
