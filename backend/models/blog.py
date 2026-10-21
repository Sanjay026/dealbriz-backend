from datetime import datetime

from extensions import db
from .base import gen_id, iso_utc


class BlogPost(db.Model):
    """A blog post. Unlike About/Safety/Cookies (which are single editable
    documents in legal_documents), a blog needs per-post structure: its own
    title, date and URL slug."""
    __tablename__ = "blog_posts"
    post_id = db.Column(db.String(36), primary_key=True, default=gen_id)
    slug = db.Column(db.String(160), nullable=False, unique=True)
    title = db.Column(db.String(200), nullable=False)
    excerpt = db.Column(db.String(400))
    body = db.Column(db.Text, nullable=False)
    is_published = db.Column(db.Boolean, nullable=False, default=True)
    published_at = db.Column(db.DateTime)
    author_id = db.Column(db.String(36), db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self, include_body=True):
        d = {
            "post_id": self.post_id,
            "slug": self.slug,
            "title": self.title,
            "excerpt": self.excerpt,
            "is_published": bool(self.is_published),
            "published_at": iso_utc(self.published_at),
            "updated_at": iso_utc(self.updated_at),
        }
        # The list view doesn't need every post's full body — that would send
        # the entire blog down the wire just to render titles and excerpts.
        if include_body:
            d["body"] = self.body
        return d
