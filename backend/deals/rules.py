"""
DealLock rules in one place.

The open questions in the design (reservation length, counter rounds, which
categories) are answered here, not in the database, so changing one is an
.env edit and a restart, not a migration. Every value has a default that
matches the design doc.

    DEALLOCK_CATEGORIES          categories DealLock is open to.   all
                                 "all", or comma-separated ids
    DEALLOCK_VEHICLE_CATEGORIES  these get a vehicle loan, and the  cars,bikes
                                 seller uploads the vehicle
                                 documents ON THE LISTING. Every
                                 other category gets a personal loan.
    DEALLOCK_PERSONAL_DOCS       documents a seller of any other    (none)
                                 category uploads on the listing,
                                 from the seller types in DOC_TYPES
    DEALLOCK_OFFER_HOURS         how long an offer stays open       48
    DEALLOCK_MAX_COUNTERS        counter rounds per offer chain     3
    DEALLOCK_BUYER_DOCS          documents every buyer uploads      buyer_pan,
                                 (all deals)                        buyer_aadhaar
    DEALLOCK_CONFIRM_HOURS       time for the buyer to accept the   24
                                 warning, then (again) for the
                                 seller to confirm
    DEALLOCK_DOCS_DAYS           time for both sides' documents to  7
                                 be uploaded and verified after
                                 the seller confirms
    DEALLOCK_RESERVATION_DAYS    reservation once the deal LOCKS    7
                                 (documents verified)
    DEALLOCK_CANCEL_REQUEST_HOURS  how long the other side has to   24
                                 agree to a cancel request
    DEALLOCK_CANCEL_LIMIT        force cancels in a row before the  3
                                 ban (agreed cancels don't count)
    DEALLOCK_BAN_DAYS            ban length (no new listings or     7
                                 deals)
    DEALLOCK_DOC_RETENTION_DAYS  document files are deleted this    30
                                 long after the deal closes
"""
import os
from decimal import Decimal, InvalidOperation


def _int_env(name, default, low, high):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return min(max(value, low), high)


def _list_env(name, default):
    return tuple(c.strip().lower() for c in os.environ.get(name, default).split(",") if c.strip())


# "all" (the default) opens DealLock on every category.
CATEGORIES = _list_env("DEALLOCK_CATEGORIES", "all")
VEHICLE_CATEGORIES = _list_env("DEALLOCK_VEHICLE_CATEGORIES", "cars,bikes")
OFFER_HOURS = _int_env("DEALLOCK_OFFER_HOURS", 48, 1, 24 * 14)
MAX_COUNTERS = _int_env("DEALLOCK_MAX_COUNTERS", 3, 0, 20)
CONFIRM_HOURS = _int_env("DEALLOCK_CONFIRM_HOURS", 24, 1, 24 * 7)
DOCS_DAYS = _int_env("DEALLOCK_DOCS_DAYS", 7, 1, 60)
RESERVATION_DAYS = _int_env("DEALLOCK_RESERVATION_DAYS", 7, 1, 60)
CANCEL_REQUEST_HOURS = _int_env("DEALLOCK_CANCEL_REQUEST_HOURS", 24, 1, 24 * 7)
CANCEL_LIMIT = _int_env("DEALLOCK_CANCEL_LIMIT", 3, 1, 20)
BAN_DAYS = _int_env("DEALLOCK_BAN_DAYS", 7, 1, 365)
DOC_RETENTION_DAYS = _int_env("DEALLOCK_DOC_RETENTION_DAYS", 30, 1, 3650)

LOAN_VEHICLE = "vehicle"
LOAN_PERSONAL = "personal"

# The DealLock loan form: the rate is not chosen by the buyer. It is shown
# as a range and the stored estimate (EMI, total) is worked out at the top
# of it, LOAN_RATE_MAX. Tenure is one of LOAN_TENURES (12 months minimum).
LOAN_RATE_MIN = 10
LOAN_RATE_MAX = 16
LOAN_TENURES = (12, 18, 24, 36, 48, 60)


def emi_estimate(loan_amount, tenure_months, rate=LOAN_RATE_MAX):
    """(monthly EMI, total payable), whole rupees."""
    loan = float(loan_amount)
    r = float(rate) / 12 / 100
    n = int(tenure_months)
    if loan <= 0:
        return 0, 0
    emi = loan * r * (1 + r) ** n / ((1 + r) ** n - 1) if r > 0 else loan / n
    return round(emi), round(emi) * n

# Every document a buyer or seller can be asked for. Order is the order shown.
# Aadhaar and PAN are accepted as they are (a full Aadhaar card is fine,
# 22 Oct 2026). The files are private: only superadmins and the lender open
# them, and they are deleted 30 days after the deal closes.
DOC_TYPES = {
    "rc":             {"label": "Registration Certificate (RC)",
                       "hint": "Both sides of the RC card, or the RC page from mParivahan/DigiLocker."},
    "insurance":      {"label": "Insurance policy",
                       "hint": "The current, valid policy for this vehicle."},
    "puc":            {"label": "PUC certificate",
                       "hint": "Valid Pollution Under Control certificate."},
    "seller_pan":     {"label": "Seller's PAN card",
                       "hint": "The lender needs the seller's PAN to pay you."},
    "seller_aadhaar": {"label": "Seller's Aadhaar card",
                       "hint": "Both sides of the Aadhaar card as a clear photo, or the e-Aadhaar PDF from myAadhaar or DigiLocker."},
    "bank_proof":     {"label": "Bank proof",
                       "hint": "A cancelled cheque or the first page of your passbook, for the account the lender pays into."},
    "loan_noc":       {"label": "Loan NOC and Form 35",
                       "hint": "From the bank that financed this vehicle, showing the loan is closed."},
    "buyer_pan":      {"label": "Buyer's PAN card",
                       "hint": "Your own PAN card. The lender needs it for the loan."},
    "buyer_aadhaar":  {"label": "Buyer's Aadhaar card",
                       "hint": "Both sides of your Aadhaar card as a clear photo, or the e-Aadhaar PDF from myAadhaar or DigiLocker."},
}
# Who uploads each document. Phase 5: the seller's documents belong to the
# LISTING (listing_documents), uploaded when posting or from Edit listing.
# In a deal only the buyer uploads (deal_documents).
BUYER_DOC_TYPES = ("buyer_pan", "buyer_aadhaar")
DOC_PARTY = {t: ("buyer" if t in BUYER_DOC_TYPES else "seller") for t in DOC_TYPES}
# The seller's PAN and Aadhaar are NOT asked for (21 Oct 2026): the vehicle
# papers and the bank account the lender pays into are enough. The types
# stay defined so older uploads still have a label.
VEHICLE_DOCS = ("rc", "insurance")
# Added for vehicles the seller says had a loan (hypothecation on the RC).
VEHICLE_LOAN_DOCS = ("loan_noc",)
PERSONAL_DOCS = tuple(d for d in _list_env("DEALLOCK_PERSONAL_DOCS", "")
                      if d in DOC_TYPES and DOC_PARTY[d] == "seller")
BUYER_DOCS = tuple(d for d in _list_env("DEALLOCK_BUYER_DOCS", "buyer_pan,buyer_aadhaar")
                   if d in DOC_TYPES and DOC_PARTY[d] == "buyer")
DOC_MAX_BYTES = 8 * 1024 * 1024
DOC_EXTENSIONS = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                  "webp": "image/webp", "pdf": "application/pdf"}

MAX_NOTE_CHARS = 500
MAX_CANCEL_NOTE_CHARS = 255
# DECIMAL(12,2) holds up to 9,999,999,999.99. Offers are whole rupees.
MAX_PRICE = Decimal("9999999999")


def public_rules():
    """What the clients need to word the UI ("valid for 48 hours" etc.)."""
    return {
        "categories": list(CATEGORIES),
        "vehicle_categories": _vehicle_categories(),
        "offer_valid_hours": OFFER_HOURS,
        "max_counter_rounds": MAX_COUNTERS,
        "confirm_hours": CONFIRM_HOURS,
        "docs_days": DOCS_DAYS,
        "reservation_days": RESERVATION_DAYS,
        "cancel_request_hours": CANCEL_REQUEST_HOURS,
        "cancel_limit": CANCEL_LIMIT,
        "ban_days": BAN_DAYS,
        "doc_types": [{"type": k, "label": v["label"], "hint": v["hint"], "party": DOC_PARTY[k]}
                      for k, v in DOC_TYPES.items()],
        # Seller documents on the listing, by kind of listing.
        # The loan question and its NOC are gone (22 Oct 2026): kept as an
        # empty list so older clients reading it don't break.
        "listing_docs": {"vehicle": list(VEHICLE_DOCS), "vehicle_loan_extra": [],
                         "other": list(PERSONAL_DOCS)},
        "buyer_docs": list(BUYER_DOCS),
        "loan_rate_min": LOAN_RATE_MIN,
        "loan_rate_max": LOAN_RATE_MAX,
        "loan_tenures": list(LOAN_TENURES),
        "doc_max_mb": DOC_MAX_BYTES // (1024 * 1024),
        "doc_extensions": sorted(DOC_EXTENSIONS),
    }


def parse_price(raw):
    """Whole rupees, 1 to MAX_PRICE. Accepts 340000, "340000", "3,40,000".
    Returns (Decimal, None) or (None, error message)."""
    if raw is None or isinstance(raw, bool):
        return None, "Please enter a price."
    text = str(raw).replace(",", "").replace("₹", "").strip()
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return None, "Please enter the price as a number."
    if not value.is_finite() or value != value.to_integral_value():
        return None, "Please enter the price in whole rupees."
    if value < 1:
        return None, "The price must be more than zero."
    if value > MAX_PRICE:
        return None, "That price is too large."
    return value, None


def category_allowed(category):
    return "all" in CATEGORIES or (category or "").lower() in CATEGORIES


def loan_type_for_category(category):
    return LOAN_VEHICLE if (category or "").lower() in VEHICLE_CATEGORIES else LOAN_PERSONAL


def required_buyer_docs():
    """The documents the buyer must upload before the deal locks (every deal)."""
    return list(BUYER_DOCS)


def required_all_docs(loan_type, vehicle_had_loan=None):
    """Everything both sides must upload before the deal locks."""
    return required_docs(loan_type, vehicle_had_loan) + required_buyer_docs()


SELLER_DOC_TYPES = tuple(t for t in DOC_TYPES if DOC_PARTY[t] == "seller")


def is_vehicle_category(category):
    """Vehicle = RC + insurance on the listing and vehicle loans. Set per
    category in Admin -> Categories (categories table). Before that table
    exists, DEALLOCK_VEHICLE_CATEGORIES (default cars,bikes) decides."""
    from categories import util
    cat = util.get(category)
    if cat is not None:
        return bool(cat["is_vehicle"])
    return (category or "").lower() in VEHICLE_CATEGORIES


def _vehicle_categories():
    from categories import util
    return util.vehicle_slugs()


def listing_possible_docs(category):
    """Every seller document a listing of this category can ever need (the
    loan NOC included). Older uploads of other types are not shown."""
    if is_vehicle_category(category):
        return list(VEHICLE_DOCS)
    return list(PERSONAL_DOCS)


def listing_required_docs(category, vehicle_had_loan=None):
    """The documents a listing of this category needs (seller's, on the
    listing). For a vehicle whose loan question isn't answered yet, the
    list without the NOC."""
    if is_vehicle_category(category):
        return required_docs(LOAN_VEHICLE, vehicle_had_loan)
    return list(PERSONAL_DOCS)


def required_docs(loan_type, vehicle_had_loan=None):
    """The documents the SELLER must have verified (on the listing)."""
    if loan_type == LOAN_VEHICLE:
        # RC and insurance only. vehicle_had_loan no longer changes the list.
        return list(VEHICLE_DOCS)
    return list(PERSONAL_DOCS)
