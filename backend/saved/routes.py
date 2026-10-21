from flask import Blueprint, jsonify
from flask_jwt_extended import jwt_required

from extensions import db
from models import Product, SavedProduct
from utils import current_user

bp = Blueprint("saved", __name__, url_prefix="/api/saved")


@bp.route("", methods=["GET"])
@jwt_required()
def get_saved():
    user = current_user()
    saved = SavedProduct.query.filter_by(user_id=user.id).all()
    products = [Product.query.get(s.product_id).to_dict() for s in saved if Product.query.get(s.product_id)]
    return jsonify(products)


@bp.route("/<pid>", methods=["POST"])
@jwt_required()
def save_product(pid):
    user = current_user()
    if not SavedProduct.query.filter_by(user_id=user.id, product_id=pid).first():
        db.session.add(SavedProduct(user_id=user.id, product_id=pid))
        db.session.commit()
    return jsonify({"ok": True})


@bp.route("/<pid>", methods=["DELETE"])
@jwt_required()
def unsave_product(pid):
    user = current_user()
    SavedProduct.query.filter_by(user_id=user.id, product_id=pid).delete()
    db.session.commit()
    return jsonify({"ok": True})
