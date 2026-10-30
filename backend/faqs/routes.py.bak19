from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required

from extensions import db
from models import Faq
from utils import current_user, has_staff_access

bp = Blueprint("faqs", __name__, url_prefix="/api/faqs")

FAQ_CATEGORIES = {
    "general": "General",
    "buying": "Buying",
    "selling": "Selling",
    # DealLock: offers in chat, the seller's confirmation, documents,
    # the loan inside a deal and the handover meeting.
    "deals": "Offers & Deals",
    "emi": "EMI & Financing",
    "account": "Account",
    # Split out from "Account & Safety": staying safe when meeting a
    # stranger is the most-read topic on a marketplace and shouldn't be
    # buried under account settings.
    "safety": "Safety & Trust",
}


@bp.route("", methods=["GET"])
def list_faqs():
    """Public — the Help Centre reads this. Unpublished entries are hidden
    unless a staff member asks for them (?all=true), so staff can draft an
    answer before it goes live."""
    show_all = request.args.get("all") == "true"
    # The public Help Centre passes help_only=true so bulk-imported answers
    # teach the chatbot without flooding that page. The chatbot itself omits
    # the flag and searches everything published.
    help_only = request.args.get("help_only") == "true"

    q = Faq.query
    if help_only:
        q = q.filter(Faq.show_in_help.is_(True))
    if show_all:
        user = None
        try:
            from flask_jwt_extended import verify_jwt_in_request
            verify_jwt_in_request(optional=True)
            user = current_user()
        except Exception:
            user = None
        if not (user and has_staff_access(user)):
            q = q.filter(Faq.is_published.is_(True))
    else:
        q = q.filter(Faq.is_published.is_(True))

    rows = q.order_by(Faq.category.asc(), Faq.sort_order.asc(), Faq.created_at.asc()).all()
    return jsonify({
        "categories": [{"code": k, "label": v} for k, v in FAQ_CATEGORIES.items()],
        "items": [f.to_dict() for f in rows],
    })


@bp.route("", methods=["POST"])
@jwt_required()
def create_faq():
    user = current_user()
    if not has_staff_access(user):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    question = (data.get("question") or "").strip()
    answer = (data.get("answer") or "").strip()
    if not question or not answer:
        return jsonify({"error": "Question and answer are both required"}), 400

    category = data.get("category") or "general"
    if category not in FAQ_CATEGORIES:
        return jsonify({"error": "Unknown category"}), 400

    faq = Faq(
        question=question[:300], answer=answer, category=category,
        keywords=(data.get("keywords") or "").strip()[:400] or None,
        sort_order=int(data.get("sort_order") or 0),
        is_published=bool(data.get("is_published", True)),
        show_in_help=bool(data.get("show_in_help", True)),
        source=(data.get("source") or "manual")[:20],
    )
    db.session.add(faq)
    db.session.commit()
    return jsonify(faq.to_dict()), 201


@bp.route("/<fid>", methods=["PUT"])
@jwt_required()
def update_faq(fid):
    user = current_user()
    if not has_staff_access(user):
        return jsonify({"error": "Forbidden"}), 403

    faq = Faq.query.get(fid)
    if not faq:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json() or {}
    if "question" in data:
        q = (data["question"] or "").strip()
        if not q:
            return jsonify({"error": "Question cannot be empty"}), 400
        faq.question = q[:300]
    if "answer" in data:
        a = (data["answer"] or "").strip()
        if not a:
            return jsonify({"error": "Answer cannot be empty"}), 400
        faq.answer = a
    if "category" in data:
        if data["category"] not in FAQ_CATEGORIES:
            return jsonify({"error": "Unknown category"}), 400
        faq.category = data["category"]
    if "keywords" in data:
        faq.keywords = (data["keywords"] or "").strip()[:400] or None
    if "sort_order" in data:
        faq.sort_order = int(data["sort_order"] or 0)
    if "is_published" in data:
        faq.is_published = bool(data["is_published"])
    if "show_in_help" in data:
        faq.show_in_help = bool(data["show_in_help"])

    db.session.commit()
    return jsonify(faq.to_dict())


@bp.route("/<fid>", methods=["DELETE"])
@jwt_required()
def delete_faq(fid):
    user = current_user()
    if not has_staff_access(user):
        return jsonify({"error": "Forbidden"}), 403
    faq = Faq.query.get(fid)
    if not faq:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(faq)
    db.session.commit()
    return jsonify({"ok": True})
