from datetime import datetime

from extensions import db


class Category(db.Model):
    """A listing category, managed in Admin -> Categories.

    slug is the key stored on every listing (products.category) and used in
    /category/<slug> URLs, so it never changes once created. Everything else
    (name, icon, order, shown or hidden, vehicle) can be edited. Hiding a
    category takes it out of menus and the sell form; listings already in it
    stay visible."""
    __tablename__ = "categories"
    slug = db.Column(db.String(30), primary_key=True)
    label = db.Column(db.String(60), nullable=False)
    icon = db.Column(db.String(40), nullable=False, default="fa-box-open")   # Font Awesome class
    emoji = db.Column(db.String(16), nullable=False, default="📦")
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # Cars and bikes: RC + insurance on the listing, vehicle loans (DealLock).
    is_vehicle = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {"slug": self.slug, "label": self.label, "icon": self.icon, "emoji": self.emoji,
                "sort_order": self.sort_order, "is_active": bool(self.is_active),
                "is_vehicle": bool(self.is_vehicle)}
