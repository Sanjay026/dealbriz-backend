from datetime import datetime

from extensions import db
from .base import iso_utc


class SellOptionList(db.Model):
    """One editable list of the sell form (Admin -> Categories -> Sell form
    lists): the brands of one category, or the choices of one dropdown
    field such as Colour or Fuel type.

    list_key is "brands:<category slug>" or "attr:<field key>".
    values_json is a JSON array of strings, in the order they're shown.

    No row means "use the default list in attributes.py", so a list nobody
    has edited keeps following the code, and "Reset to default" just
    deletes the row.
    """
    __tablename__ = "sell_option_lists"
    list_key = db.Column(db.String(60), primary_key=True)
    values_json = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.String(36))   # the admin's user id (no FK, see the migration)

    def to_dict(self):
        return {"key": self.list_key, "updated_at": iso_utc(self.updated_at)}
