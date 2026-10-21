from datetime import datetime

from extensions import db
from .base import gen_id, iso_utc


class EmiApplication(db.Model):
    __tablename__ = "emi_applications"
    id = db.Column(db.String(36), primary_key=True, default=gen_id)
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id = db.Column(db.String(36), db.ForeignKey("products.id", ondelete="SET NULL"))
    # Price/title/image are snapshotted at application time on purpose: if the
    # seller later edits or deletes the listing, the loan record still reflects
    # what was actually applied for.
    product_title_snapshot = db.Column(db.String(200), nullable=False)
    product_image_snapshot = db.Column(db.Text)
    product_price_snapshot = db.Column(db.Numeric(12, 2), nullable=False)
    down_payment = db.Column(db.Numeric(12, 2), default=0)
    tenure_months = db.Column(db.Integer, nullable=False)
    interest_rate = db.Column(db.Numeric(5, 2), nullable=False)
    loan_amount = db.Column(db.Numeric(12, 2), nullable=False)
    monthly_emi = db.Column(db.Numeric(12, 2), nullable=False)
    total_payable = db.Column(db.Numeric(12, 2), nullable=False)
    applicant_name = db.Column(db.String(200), nullable=False)
    applicant_phone = db.Column(db.String(20), nullable=False)
    applicant_income = db.Column(db.Numeric(12, 2))
    status = db.Column(db.String(20), default="pending")
    # Archived applications stay in the DB (loan terms are an audit record and
    # a purchase may reference them) but drop out of the default admin view.
    is_archived = db.Column(db.Boolean, default=False, nullable=False)
    applied_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Audit timeline: when each stage happened and who decided it.
    reviewed_at = db.Column(db.DateTime)     # approved / rejected / docs requested
    reviewed_by = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"))
    # Which financer is funding this loan — set by staff during review, not
    # by the applicant. Optional: many applications may go through review
    # without ever being attributed to a specific partner.
    financer_id = db.Column(db.Integer, db.ForeignKey("financers.financer_id", ondelete="SET NULL"))
    disbursed_at = db.Column(db.DateTime)    # funds actually released to the applicant
    closed_at = db.Column(db.DateTime)       # completed or cancelled
    decision_notes = db.Column(db.Text)
    # DealLock: the locked deal this loan pays for (None for a plain EMI
    # enquiry), the lender's own reference number, and its Key Fact
    # Statement link, both entered by staff from what the lender sends.
    deal_id = db.Column(db.String(36), db.ForeignKey("deals.id"))
    nbfc_application_reference = db.Column(db.String(150))
    kfs_url = db.Column(db.String(500))

    def to_dict(self):
        return {
            "id": self.id, "user_id": self.user_id, "product_id": self.product_id,
            "product_title_snapshot": self.product_title_snapshot,
            "product_image_snapshot": self.product_image_snapshot,
            "product_price_snapshot": float(self.product_price_snapshot),
            "down_payment": float(self.down_payment),
            "tenure_months": self.tenure_months,
            "interest_rate": float(self.interest_rate),
            "loan_amount": float(self.loan_amount),
            "monthly_emi": float(self.monthly_emi),
            "total_payable": float(self.total_payable),
            "applicant_name": self.applicant_name, "applicant_phone": self.applicant_phone,
            "applicant_income": float(self.applicant_income) if self.applicant_income else None,
            "status": self.status,
            "is_archived": bool(self.is_archived),
            "applied_at": iso_utc(self.applied_at),
            "reviewed_at": iso_utc(self.reviewed_at),
            "reviewed_by": self.reviewed_by,
            "financer_id": self.financer_id,
            "disbursed_at": iso_utc(self.disbursed_at),
            "closed_at": iso_utc(self.closed_at),
            "decision_notes": self.decision_notes,
            # Convenience flag: did the applicant actually receive the loan?
            "was_disbursed": self.disbursed_at is not None,
            "deal_id": self.deal_id,
            "nbfc_application_reference": self.nbfc_application_reference,
            "kfs_url": self.kfs_url,
        }


class Financer(db.Model):
    __tablename__ = "financers"
    financer_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    contact_email = db.Column(db.String(150))
    contact_phone = db.Column(db.String(20))
    min_interest_rate = db.Column(db.Numeric(5, 2), nullable=False)
    max_interest_rate = db.Column(db.Numeric(5, 2), nullable=False)
    min_loan_amount = db.Column(db.Numeric(12, 2), default=5000)
    max_loan_amount = db.Column(db.Numeric(12, 2), nullable=False)
    status = db.Column(db.String(20), default="active")
    # Office / service location — used to recommend nearby financers to buyers.
    address_line = db.Column(db.String(255))
    city = db.Column(db.String(100))
    state = db.Column(db.String(100))
    pincode = db.Column(db.String(10))
    serves_all_india = db.Column(db.Boolean, default=True, nullable=False)
    # DealLock: which loan types this lender funds, comma separated
    # ("vehicle,personal"). See loan_type_list().
    loan_types = db.Column(db.String(50), nullable=False, default="vehicle,personal")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def loan_type_list(self):
        return [t for t in (x.strip() for x in (self.loan_types or "").split(",")) if t]

    def to_dict(self):
        return {
            "financer_id": self.financer_id, "name": self.name,
            "contact_email": self.contact_email, "contact_phone": self.contact_phone,
            "min_interest_rate": float(self.min_interest_rate), "max_interest_rate": float(self.max_interest_rate),
            "min_loan_amount": float(self.min_loan_amount), "max_loan_amount": float(self.max_loan_amount),
            "status": self.status,
            "address_line": self.address_line, "city": self.city,
            "state": self.state, "pincode": self.pincode,
            "serves_all_india": bool(self.serves_all_india),
            "loan_types": self.loan_type_list(),
            "created_at": iso_utc(self.created_at)
        }


class FinancerMember(db.Model):
    """A user with the lender role works for exactly one lender, and the
    lender portal only ever shows that lender's loans and deals."""
    __tablename__ = "financer_members"
    user_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    financer_id = db.Column(db.Integer, db.ForeignKey("financers.financer_id", ondelete="CASCADE"),
                            nullable=False)
    added_by = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
