import re
from datetime import datetime

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, verify_jwt_in_request

from extensions import db
from models import BlogPost, User
from utils import current_user, has_staff_access

bp = Blueprint("blog", __name__, url_prefix="/api/blog")


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:160] or "post"


def unique_slug(base, exclude_id=None):
    """Slugs are the post's public identifier, so they must not collide.
    Appends -2, -3 ... until free rather than rejecting the save, so an
    author doesn't have to invent a different title just to publish."""
    slug = base
    n = 1
    while True:
        q = BlogPost.query.filter_by(slug=slug)
        if exclude_id:
            q = q.filter(BlogPost.post_id != exclude_id)
        if not q.first():
            return slug
        n += 1
        slug = f"{base[:150]}-{n}"


def _is_staff():
    try:
        verify_jwt_in_request(optional=True)
        u = current_user()
        return bool(u and has_staff_access(u))
    except Exception:
        return False


@bp.route("", methods=["GET"])
def list_posts():
    """Public list. Drafts are only returned to staff, so an unfinished post
    never shows up on the public Blog page."""
    q = BlogPost.query
    if not (request.args.get("all") == "true" and _is_staff()):
        q = q.filter(BlogPost.is_published.is_(True))
    # No explicit NULLS LAST: the helper was renamed between SQLAlchemy
    # versions, and MySQL already sorts NULLs last under DESC, so drafts
    # (published_at IS NULL) fall to the bottom anyway.
    posts = q.order_by(
        BlogPost.published_at.desc(),
        BlogPost.created_at.desc(),
    ).all()
    return jsonify([p.to_dict(include_body=False) for p in posts])


@bp.route("/<slug>", methods=["GET"])
def get_post(slug):
    post = BlogPost.query.filter_by(slug=slug).first()
    if not post or (not post.is_published and not _is_staff()):
        return jsonify({"error": "Not found"}), 404
    d = post.to_dict()
    author = User.query.get(post.author_id) if post.author_id else None
    d["author_name"] = f"{author.first_name} {author.last_name}".strip() if author else None
    return jsonify(d)


@bp.route("", methods=["POST"])
@jwt_required()
def create_post():
    user = current_user()
    if not has_staff_access(user):
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    body = (data.get("body") or "").strip()
    if not title or not body:
        return jsonify({"error": "Title and body are both required"}), 400

    published = bool(data.get("is_published", True))
    post = BlogPost(
        slug=unique_slug(slugify(data.get("slug") or title)),
        title=title[:200],
        excerpt=(data.get("excerpt") or "").strip()[:400] or None,
        body=body,
        is_published=published,
        published_at=datetime.utcnow() if published else None,
        author_id=user.id,
    )
    db.session.add(post)
    db.session.commit()
    return jsonify(post.to_dict()), 201


@bp.route("/<pid>", methods=["PUT"])
@jwt_required()
def update_post(pid):
    user = current_user()
    if not has_staff_access(user):
        return jsonify({"error": "Forbidden"}), 403

    post = BlogPost.query.get(pid)
    if not post:
        return jsonify({"error": "Not found"}), 404

    data = request.get_json() or {}
    if "title" in data:
        t = (data["title"] or "").strip()
        if not t:
            return jsonify({"error": "Title cannot be empty"}), 400
        post.title = t[:200]
    if "body" in data:
        b = (data["body"] or "").strip()
        if not b:
            return jsonify({"error": "Body cannot be empty"}), 400
        post.body = b
    if "excerpt" in data:
        post.excerpt = (data["excerpt"] or "").strip()[:400] or None
    if "slug" in data and data["slug"]:
        post.slug = unique_slug(slugify(data["slug"]), exclude_id=post.post_id)
    if "is_published" in data:
        now_published = bool(data["is_published"])
        # Stamp the publish date the first time it actually goes live, and
        # keep that original date on later edits — a post shouldn't jump to
        # the top of the blog just because a typo was fixed.
        if now_published and not post.published_at:
            post.published_at = datetime.utcnow()
        post.is_published = now_published

    db.session.commit()
    return jsonify(post.to_dict())


@bp.route("/<pid>", methods=["DELETE"])
@jwt_required()
def delete_post(pid):
    user = current_user()
    if not has_staff_access(user):
        return jsonify({"error": "Forbidden"}), 403
    post = BlogPost.query.get(pid)
    if not post:
        return jsonify({"error": "Not found"}), 404
    db.session.delete(post)
    db.session.commit()
    return jsonify({"ok": True})
