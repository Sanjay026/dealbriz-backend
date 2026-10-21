"""Location search + EMI coverage lookup."""
import uuid

from flask import Blueprint, jsonify, request
from sqlalchemy import func, or_

from extensions import db
from models import EmiServiceArea, Pincode
from permissions import require_capability
from settings_util import emi_enabled
from utils import current_user

bp = Blueprint("locations", __name__, url_prefix="/api/locations")

SCOPE_TYPES = ("pincode", "district", "state")


# ===================== PUBLIC SEARCH =====================

@bp.route("/search", methods=["GET"])
def search_locations():
    """Real-time location search by pincode, city/office or state.

    With no query, returns the major cities so the picker always opens with
    something useful rather than an empty box.
    """
    q = (request.args.get("q") or "").strip()
    limit = min(int(request.args.get("limit", 25) or 25), 100)

    if not q:
        rows = (Pincode.query.filter(Pincode.is_major.is_(True))
                .order_by(Pincode.office_name.asc()).limit(limit).all())
        return jsonify({"query": "", "major": True, "results": [r.to_dict() for r in rows]})

    digits = "".join(ch for ch in q if ch.isdigit())
    query = Pincode.query

    if digits:
        # Numeric input can only be a pincode. Prefix match so results
        # narrow as the user types rather than only matching all 6 digits.
        query = query.filter(Pincode.pincode.like(f"{digits}%"))
        order = [Pincode.pincode.asc()]
    else:
        like = f"%{q}%"
        starts = f"{q}%"
        query = query.filter(or_(
            Pincode.office_name.like(like),
            Pincode.district.like(like),
            Pincode.state.like(like),
        ))
        # Names starting with the query rank above ones merely containing
        # it, and major cities above minor post offices — otherwise
        # searching "Mandi" buries Mandi city under its sub-offices.
        order = [
            func.if_(Pincode.office_name.like(starts), 0, 1),
            Pincode.is_major.desc(),
            Pincode.office_name.asc(),
        ]

    rows = query.order_by(*order).limit(limit).all()
    return jsonify({"query": q, "major": False, "results": [r.to_dict() for r in rows]})


@bp.route("/states", methods=["GET"])
def list_states():
    rows = db.session.query(Pincode.state).distinct().order_by(Pincode.state.asc()).all()
    return jsonify([r[0] for r in rows])


@bp.route("/districts", methods=["GET"])
def list_districts():
    q = db.session.query(Pincode.district, Pincode.state).distinct()
    state = request.args.get("state")
    if state:
        q = q.filter(Pincode.state == state)
    rows = q.order_by(Pincode.district.asc()).all()
    return jsonify([{"district": r[0], "state": r[1]} for r in rows])


# ===================== EMI COVERAGE =====================

def resolve_emi_availability(pincode=None, district=None, state=None):
    """Is EMI available at this location?

    COVERAGE decides availability, not the global switch. That allows a
    pilot: run EMI in Mandi while the rest of the country sees nothing.

    The global switch (Admin -> Site Settings) controls VISIBILITY instead:
      - ON  : EMI is advertised site-wide (badges, homepage section, nav)
      - OFF : EMI branding is hidden from everyone EXCEPT users whose
              location is explicitly covered

    Rules are checked most-specific-first — pincode, then district, then
    state — so a whole state can be enabled and individual areas excluded
    from it without deleting the broad rule.

    Default is UNAVAILABLE. With no coverage rows at all, EMI is offered
    nowhere regardless of the switch — which is the state to leave things
    in until a financing partner is signed.
    """
    if pincode and not (district and state):
        row = Pincode.query.filter_by(pincode=pincode).first()
        if row:
            district = district or row.district
            state = state or row.state

    checks = []
    if pincode:
        checks.append(("pincode", pincode))
    if district:
        checks.append(("district", district))
    if state:
        checks.append(("state", state))

    for scope_type, scope_value in checks:
        area = EmiServiceArea.query.filter_by(
            scope_type=scope_type, scope_value=scope_value
        ).first()
        if area:
            if area.is_enabled:
                return {"available": True, "matched": f"{scope_type}:{scope_value}",
                        "financer_id": area.financer_id,
                        "globally_visible": emi_enabled()}
            # An explicit exclusion wins immediately — that's the point of
            # being able to carve an area out of an enabled state.
            return {"available": False, "reason": "excluded",
                    "matched": f"{scope_type}:{scope_value}",
                    "message": "EMI is not available in your area yet."}

    return {"available": False, "reason": "not_covered",
            "message": "EMI is not available in your area yet."}


@bp.route("/emi-availability", methods=["GET"])
def emi_availability():
    """Called by the frontend before showing EMI options for a location."""
    return jsonify(resolve_emi_availability(
        pincode=(request.args.get("pincode") or "").strip() or None,
        district=(request.args.get("district") or "").strip() or None,
        state=(request.args.get("state") or "").strip() or None,
    ))


# ===================== ADMIN: COVERAGE MANAGEMENT =====================

@bp.route("/emi-areas", methods=["GET"])
@require_capability("settings.manage")
def list_emi_areas():
    rows = EmiServiceArea.query.order_by(
        EmiServiceArea.scope_type.asc(), EmiServiceArea.scope_value.asc()
    ).all()
    return jsonify([r.to_dict() for r in rows])


@bp.route("/emi-areas", methods=["POST"])
@require_capability("settings.manage")
def create_emi_area():
    data = request.get_json() or {}
    scope_type = (data.get("scope_type") or "").strip()
    scope_value = (data.get("scope_value") or "").strip()

    if scope_type not in SCOPE_TYPES:
        return jsonify({"error": "scope_type must be pincode, district or state"}), 400
    if not scope_value:
        return jsonify({"error": "A location is required"}), 400
    if scope_type == "pincode" and (not scope_value.isdigit() or len(scope_value) != 6):
        return jsonify({"error": "A pincode must be exactly 6 digits"}), 400

    existing = EmiServiceArea.query.filter_by(
        scope_type=scope_type, scope_value=scope_value).first()
    if existing:
        # Updating beats erroring: re-adding an area the admin previously
        # excluded is the natural way to turn it back on.
        existing.is_enabled = bool(data.get("is_enabled", True))
        existing.financer_id = data.get("financer_id") or None
        existing.note = (data.get("note") or "").strip()[:255] or None
        db.session.commit()
        return jsonify(existing.to_dict())

    area = EmiServiceArea(
        area_id=str(uuid.uuid4()),
        scope_type=scope_type,
        scope_value=scope_value,
        is_enabled=bool(data.get("is_enabled", True)),
        financer_id=data.get("financer_id") or None,
        note=(data.get("note") or "").strip()[:255] or None,
        created_by=current_user().id,
    )
    db.session.add(area)
    db.session.commit()
    return jsonify(area.to_dict()), 201


@bp.route("/emi-areas/<area_id>", methods=["PUT"])
@require_capability("settings.manage")
def update_emi_area(area_id):
    area = EmiServiceArea.query.get(area_id)
    if not area:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json() or {}
    if "is_enabled" in data:
        area.is_enabled = bool(data["is_enabled"])
    if "financer_id" in data:
        area.financer_id = data["financer_id"] or None
    if "note" in data:
        area.note = (data["note"] or "").strip()[:255] or None
    db.session.commit()
    return jsonify(area.to_dict())


@bp.route("/emi-areas/<area_id>", methods=["DELETE"])
@require_capability("settings.manage")
def delete_emi_area(area_id):
    area = EmiServiceArea.query.get(area_id)
    if not area:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(area)
    db.session.commit()
    return jsonify({"ok": True})
