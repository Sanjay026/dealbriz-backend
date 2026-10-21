from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required

from extensions import db
from models import LegalDocument, User
from utils import current_user, is_superadmin

bp = Blueprint("legal", __name__, url_prefix="/api/legal")

DEFAULT_TITLES = {"terms": "Terms of Service"}
MAX_CONTENT_LENGTH = 100_000  # generous — this is a legal document, not a chat message


@bp.route("/<key>", methods=["GET"])
def get_document(key):
    """Public — the footer's "Terms of Service" link and the sell-page
    disclaimer both read this, so it stays unauthenticated on purpose."""
    doc = LegalDocument.query.get(key)
    if not doc:
        return jsonify({"error": "Not found"}), 404
    d = doc.to_dict()
    if doc.updated_by:
        u = User.query.get(doc.updated_by)
        d["updated_by_name"] = f"{u.first_name} {u.last_name}".strip() if u else None
    else:
        d["updated_by_name"] = None
    return jsonify(d)


@bp.route("/<key>", methods=["PUT"])
@jwt_required()
def update_document(key):
    """Superadmin-only: a legal document is a different category of risk
    from a listing edit or a role change, so it gets the tightest gate
    rather than the general has_staff_access() used elsewhere."""
    user = current_user()
    if not is_superadmin(user):
        return jsonify({"error": "Only superadmins can edit legal documents"}), 403

    data = request.get_json() or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "Content cannot be empty"}), 400
    if len(content) > MAX_CONTENT_LENGTH:
        return jsonify({"error": f"Content is too long (max {MAX_CONTENT_LENGTH:,} characters)"}), 400
    title = (data.get("title") or "").strip()

    doc = LegalDocument.query.get(key)
    if not doc:
        doc = LegalDocument(doc_key=key, title=title or DEFAULT_TITLES.get(key, key.replace("_", " ").title()), content=content)
        db.session.add(doc)
    else:
        doc.content = content
        if title:
            doc.title = title
    doc.updated_by = user.id
    db.session.commit()

    d = doc.to_dict()
    d["updated_by_name"] = f"{user.first_name} {user.last_name}".strip()
    return jsonify(d)
