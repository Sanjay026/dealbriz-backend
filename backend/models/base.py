"""
Shared bits every model module needs.

Kept separate so each model file imports from here rather than from a
sibling model file — that's what keeps the package free of circular
imports no matter which order things get loaded in.
"""
import uuid


def gen_id():
    return str(uuid.uuid4())


def iso_utc(dt):
    """Serialise a stored timestamp as explicit UTC.

    Every created_at is written with datetime.utcnow(), so the value IS UTC —
    but it is naive, and .isoformat() on a naive datetime emits
    "2026-10-03T08:53:00" with no zone marker. A browser parsing that treats
    it as LOCAL time, so in IST every chat message and notification displayed
    5 hours 30 minutes earlier than it actually happened.

    Appending "Z" tells the browser the instant is UTC, and `new Date(...)`
    then converts it to whatever timezone the viewer is in. That is also what
    makes sender and receiver each see their own correct local time instead
    of one being shifted to the other's.

    Returns None for a missing timestamp so callers emit null unchanged.
    """
    if dt is None:
        return None
    # Aware datetimes already carry an offset; isoformat() keeps it.
    if dt.tzinfo is not None:
        return dt.isoformat()
    return dt.isoformat() + "Z"
