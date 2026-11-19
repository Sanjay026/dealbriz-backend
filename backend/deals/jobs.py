"""
DealLock timers. Run every 5 minutes from cron on the server:

    docker compose -f docker-compose.prod.yml exec -T backend python -m deals.jobs

What it does each run:
  - open offers past their 48 hours -> expired
  - deals not confirmed in time (the buyer didn't accept the warning within
    24 hours, or the seller didn't confirm within 24 hours after that) ->
    released, not counted against anyone, listing back on sale
  - deals waiting for documents that are all verified -> locked (a safety
    net, e.g. after a rule change made fewer documents necessary)
  - deals still before Ready to pay when the reservation ends (including
    documents not approved in time) -> released, listing back on sale, both
    sides told, loan application withdrawn
  - documents emails to lenders that are waiting, failed (retried every 10
    minutes, up to 6 tries) or stuck -> sent
  - loans with no update for 48 hours -> staff get a bell notification
    (at most once a day per deal)
  - cancel requests nobody answered within 24 hours -> lapse, the one who
    asked is told (they can ask again and force-cancel)
  - document files of deals closed more than 30 days ago -> deleted, and
    listing document files (the seller's) of listings sold or removed, once
    30 days old and no deal on the listing closed in the last 30 days
  - 24 hours after the buyer applied for the loan -> the buyer is asked
    "Did you get your loan?" (email, bell, push), and 4 days after -> both
    sides are asked "Is your deal done?" (deals/done_check.py)

It is safe to run twice at once or to miss a run: rows are claimed with
SELECT ... FOR UPDATE SKIP LOCKED, and the routes already treat a lapsed
offer as expired the moment it lapses, so a late run only delays the
listing going back on sale.

Not run inside Gunicorn on purpose: with several workers, an in-process
scheduler would fire once per worker.
"""
import logging
import sys


def run():
    from app import app
    from extensions import db
    from deals.listing_docs import purge_listing_documents
    from deals.done_check import send_done_checks, send_emails, send_loan_checks
    from deals.service import (Outbox, expire_cancel_requests, expire_offers, flag_stalled_loans,
                               lock_ready_deals, purge_closed_deal_documents,
                               release_expired_reservations, release_unconfirmed,
                               utcnow)

    log = logging.getLogger("dealbridge.deals.jobs")
    with app.app_context():
        now = utcnow()
        expired = released = unconfirmed = stalled = purged = lapsed = locked = mailed = 0
        loan_checks = done_checks = check_mails = 0
        threads = []
        try:
            # Batches keep each transaction short. Loop until a batch comes
            # back smaller than its limit.
            while True:
                n = expire_offers(now)
                db.session.commit()
                expired += n
                if n < 200:
                    break
            while True:
                outbox = Outbox()
                n = release_unconfirmed(now, outbox)
                db.session.commit()
                threads += outbox.send()
                unconfirmed += n
                if n < 100:
                    break
            # Before releasing anything: a deal whose documents are all
            # verified locks rather than running out of time.
            while True:
                outbox = Outbox()
                n = lock_ready_deals(now, outbox)
                db.session.commit()
                threads += outbox.send()
                locked += n
                if n < 100:
                    break
            while True:
                outbox = Outbox()
                n = release_expired_reservations(now, outbox)
                db.session.commit()
                threads += outbox.send()
                released += n
                if n < 100:
                    break
            while True:
                outbox = Outbox()
                n = expire_cancel_requests(now, outbox)
                db.session.commit()
                threads += outbox.send()
                lapsed += n
                if n < 200:
                    break
            # "Did you get your loan?" (24 h) and "Is your deal done?" (4 days).
            # The emails go out after each batch is committed.
            for sender in (send_loan_checks, send_done_checks):
                while True:
                    outbox = Outbox()
                    n, emails = sender(now, outbox)
                    db.session.commit()
                    threads += outbox.send()
                    check_mails += send_emails(emails)
                    if sender is send_loan_checks:
                        loan_checks += n
                    else:
                        done_checks += n
                    if n < 100:
                        break
            # Documents emails to lenders: waiting, failed or stuck ones.
            from deals.lender_email import send_due
            mailed = send_due(now)
            stalled = flag_stalled_loans(now)
            db.session.commit()
            purged = purge_closed_deal_documents(now)
            db.session.commit()
            purged += purge_listing_documents(now)
            db.session.commit()
        except Exception:
            db.session.rollback()
            log.exception("DealLock timer run failed")
            return 1
        # Pushes go out on background threads; give them time to finish
        # before this short-lived process exits.
        for t in threads:
            t.join(timeout=10)
        msg = (f"DealLock timers: {locked} deal(s) locked, {expired} offer(s) expired, {unconfirmed} unconfirmed deal(s) "
               f"released, {released} deal(s) released, {lapsed} cancel request(s) lapsed, "
               f"{stalled} stalled loan(s) flagged, {mailed} lender email(s) sent, "
               f"{purged} document file(s) deleted, {loan_checks} loan check(s) and "
               f"{done_checks} deal-done check(s) sent ({check_mails} email(s))")
        log.info(msg)
        print(msg)
        return 0


if __name__ == "__main__":
    sys.exit(run())
