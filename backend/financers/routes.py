from flask import Blueprint, request, jsonify

from models import Financer

bp = Blueprint("financers", __name__, url_prefix="/api/financers")


def proximity_rank(financer, pincode):
    """Rank a financer against a buyer's pincode. Lower is nearer.

    Indian PIN codes are hierarchical, so prefix length is a usable
    proximity signal without any geocoding:
        first 3 digits shared -> same sorting district (same city/region)
        first 2 digits shared -> same postal circle (roughly same state)
        first 1 digit  shared -> same zone
    National lenders still appear, just below genuinely local ones.
    """
    fp = (financer.pincode or "").strip()
    if not pincode or not fp:
        return (5, financer.name or "")
    if fp[:3] == pincode[:3]:
        return (0, financer.name or "")
    if fp[:2] == pincode[:2]:
        return (1, financer.name or "")
    if fp[:1] == pincode[:1]:
        return (2, financer.name or "")
    return (4, financer.name or "")


PROXIMITY_LABELS = {
    0: "In your area",
    1: "In your state",
    2: "In your region",
    4: "Nationwide",
    5: "Nationwide",
}


@bp.route("", methods=["GET"])
def list_financers():
    """Public — shown to buyers when choosing an EMI plan.

    Pass ?pincode=160055 to get partners ordered by how close they are to
    the buyer, each tagged with a human-readable proximity label.
    """
    pincode = (request.args.get("pincode") or "").strip()
    financers = Financer.query.filter_by(status="active").all()

    # A purely local lender shouldn't be offered to someone far away, but a
    # national one should appear regardless of where its office sits.
    # Cut-off is the postal circle (rank 1), not the zone (rank 2): a zone
    # spans several states — Delhi and Punjab share zone 1 — so treating a
    # zone match as "local" would show a Delhi-only lender to a Punjab buyer.
    if pincode:
        visible = [f for f in financers
                   if f.serves_all_india or proximity_rank(f, pincode)[0] <= 1]
        visible.sort(key=lambda f: proximity_rank(f, pincode))
        financers = visible

    result = []
    for f in financers:
        d = f.to_dict()
        if pincode:
            rank = proximity_rank(f, pincode)[0]
            d["proximity"] = PROXIMITY_LABELS.get(rank, "Nationwide")
            d["proximity_rank"] = rank
        result.append(d)
    return jsonify(result)
