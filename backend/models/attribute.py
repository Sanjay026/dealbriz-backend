from extensions import db


class ProductAttribute(db.Model):
    """A category-specific field on a listing (year, km_driven, colour...).

    Key/value rather than columns on `products`: cars need year and km,
    phones need colour and storage. Columns for all of them would leave
    most rows mostly NULL and need a migration per new field.

    attr_num holds the numeric form when there is one, which is what makes
    "under 1 lakh km" an indexed comparison rather than a string compare.
    """
    __tablename__ = "product_attributes"
    product_id = db.Column(db.String(36), db.ForeignKey("products.id", ondelete="CASCADE"),
                           primary_key=True)
    attr_key = db.Column(db.String(40), primary_key=True)
    attr_value = db.Column(db.String(120), nullable=False)
    attr_num = db.Column(db.Numeric(14, 2))

    # Lets Product.to_dict() include attributes without an extra query per
    # listing. selectin loads them for a whole page of results in one go.
    product = db.relationship(
        "Product",
        backref=db.backref("attributes", lazy="selectin", cascade="all, delete-orphan"),
    )

    def to_dict(self):
        return {"key": self.attr_key, "value": self.attr_value,
                "num": float(self.attr_num) if self.attr_num is not None else None}
