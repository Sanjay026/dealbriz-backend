"""
EMI application statuses, and the one place a status change is applied.

Shared by the admin panel (admin/routes.py) and the lender portal
(lender/routes.py), so both stamp the same audit fields and send the
applicant the same notification.
"""
from datetime import datetime

from models import Notification

# What the applicant sees on the bell for each EMI status. Phrased from the
# buyer's point of view, not the reviewer's - "under review", not "moved to
# review". A status with no entry here falls back to a generic line rather
# than leaking the raw status string into the UI.
EMI_NOTIFICATION_TITLES = {
    "pending": "Your EMI application was reopened",
    # NOTE: EMI_STATUSES comments "review" as "documents needed from
    # applicant", which does not match the name. Wording kept neutral until
    # that is settled - change this one string once it is.
    "review": "Your EMI application is being reviewed",
    "approved": "Your EMI application was approved",
    "rejected": "Your EMI application was not approved",
    "disbursed": "Your EMI loan has been disbursed",
    "completed": "Your EMI loan is now closed",
    "cancelled": "Your EMI application was cancelled",
}

# Full application lifecycle. The first four are the review stage; the last
# three describe what happened after approval, so an application no longer
# sits at "approved" forever with no way to say it's finished.
EMI_STATUSES = {
    "pending",    # awaiting review
    "review",     # documents needed from applicant
    "approved",   # approved, not yet paid out
    "rejected",   # declined
    "disbursed",  # loan paid out, repayment in progress
    "completed",  # fully repaid / settled
    "cancelled",  # applicant withdrew or deal fell through
}

# Statuses that represent live financial exposure — used for the dashboard's
# "Total Loan Value" so settled and dead applications stop inflating it.
EMI_ACTIVE_STATUSES = {"approved", "disbursed"}

# Reaching one of these means the application is finished and can be archived.
EMI_TERMINAL_STATUSES = {"completed", "cancelled", "rejected"}

# Reaching one of these counts as a reviewer decision, so it stamps
# reviewed_at / reviewed_by.
EMI_DECISION_STATUSES = {"approved", "rejected", "review"}


def set_emi_status(emi, new_status, actor):
    """Applies new_status (already validated against EMI_STATUSES) and
    returns the old status. Stamps the audit timeline and tells the
    applicant, but only on a real change. The caller commits."""
    old_status = emi.status
    if new_status == old_status:
        return old_status

    # Stamp the audit timeline as the application moves through stages, so
    # the detail view can show when each decision happened and who made it.
    now = datetime.utcnow()
    actor_id = actor.id if actor else None
    if new_status in EMI_DECISION_STATUSES:
        emi.reviewed_at = now
        emi.reviewed_by = actor_id
    if new_status == "disbursed" and not emi.disbursed_at:
        emi.disbursed_at = now
        # A loan can't be disbursed without having been approved first;
        # backfill the decision stamp if staff skipped straight to it.
        if not emi.reviewed_at:
            emi.reviewed_at = now
            emi.reviewed_by = actor_id
    if new_status in ("completed", "cancelled"):
        emi.closed_at = now
    # Moving back into an open state clears the closure stamp.
    if new_status in ("pending", "review"):
        emi.closed_at = None

    # Tell the applicant. Writing the same status twice (staff reopening the
    # row, or a double-click on Save) returned above, so the bell never
    # stacks duplicates.
    Notification.push(
        user_id=emi.user_id,
        kind="emi",
        title=EMI_NOTIFICATION_TITLES.get(new_status, "Your EMI application was updated"),
        body=emi.product_title_snapshot,
        link_page="emi-status",
        link_ref=emi.id,
    )
    emi.status = new_status
    return old_status
