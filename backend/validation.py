"""Server-side checks for what users type: listings, profiles and staff edits.

The website escapes what it shows, but the API is also used by the Android
app and by anyone with curl, so the rules live here too. Three ideas:

- Short display fields (names, brand, model, place) never need "<" or ">",
  so they are refused there. Long free text (title, description) may hold
  them legitimately ("<5000 km"), so it is only length-capped; showing it
  safely is the page's job.
- Image addresses must point at our own storage (what upload-image gave
  back). Anything else could be a tracking pixel, or carry a quote that
  breaks out of an <img src="..."> attribute. An address already stored on
  the listing is kept, so editing an old listing never fails over its photos.
- Numbers are numbers, in range.
"""
import os
import re

TITLE_MAX = 200          # products.title
DESCRIPTION_MAX = 5000
PLACE_MAX = 200          # products.location / landmark
BRAND_MAX = 100          # products.brand / model
NAME_MAX = 80            # same as signup
PRICE_MAX = 99999999
MAX_EXTRA_IMAGES = 20
LISTING_STATUSES = ("active", "sold", "removed", "reserved")

_ANGLE = re.compile(r"[<>]")
# A key-like value: category and condition are short lowercase codes.
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
# Characters a storage URL can hold. No quotes, spaces, <>, backslash or
# comma (extra_images is stored comma-separated).
_URL_CHARS = re.compile(r"^[A-Za-z0-9\-._~:/?#\[\]@!$&()*+;=%]+$")
_PHONE_CHARS = re.compile(r"^[0-9+\-\s()]{10,20}$")
_USER_CITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .\-]{0,9}$")


class Invalid(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def short_text(value, label, max_len, required=False):
    """Name-like text: trimmed, capped, no < or >."""
    text = str(value if value is not None else "").strip()
    if not text:
        if required:
            raise Invalid(f"Please add the {label}.")
        return ""
    if len(text) > max_len:
        raise Invalid(f"The {label} is too long (max {max_len} characters).")
    if _ANGLE.search(text):
        raise Invalid(f"The {label} can't contain < or >.")
    return text


def long_text(value, label, max_len, required=False):
    text = str(value if value is not None else "").strip()
    if required and not text:
        raise Invalid(f"Please add a {label}.")
    if len(text) > max_len:
        raise Invalid(f"The {label} is too long (max {max_len:,} characters).")
    return text


def code(value, label):
    text = str(value or "").strip().lower()
    if not _CODE.match(text):
        raise Invalid(f"That isn't a valid {label}.")
    return text


def money(value, label, required=True):
    if value in (None, ""):
        if required:
            raise Invalid(f"Please add the {label}.")
        return None
    if isinstance(value, bool):
        raise Invalid(f"The {label} must be a number.")
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise Invalid(f"The {label} must be a number.")
    if n != n or n < 0:
        raise Invalid(f"The {label} can't be negative.")
    if n > PRICE_MAX:
        raise Invalid(f"The {label} is too large.")
    return n


def flag(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def pincode(value, required=True):
    text = str(value or "").strip()
    if not text:
        if required:
            raise Invalid("Please add the pincode.")
        return ""
    if len(text) > 10 or _ANGLE.search(text) or '"' in text or "'" in text:
        raise Invalid("That isn't a valid pincode.")
    return text


# ---------------------------------------------------------------- images

def _image_prefixes():
    from storage import CDN_DOMAIN, S3_BUCKET, S3_REGION
    out = ["/uploads/"]
    if S3_BUCKET:
        out += [f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/",
                f"https://{S3_BUCKET}.s3.amazonaws.com/"]
    if CDN_DOMAIN:
        out.append(f"https://{CDN_DOMAIN.strip().rstrip('/')}/")
    for host in [os.environ.get("PRIMARY_DOMAIN", "")] + os.environ.get("ALT_DOMAINS", "").split(","):
        host = host.strip().lower()
        if host:
            out.append(f"https://{host}/uploads/")
    return out


def image_url(value, already=()):
    """One photo address: ours, or one the listing already had."""
    url = str(value or "").strip()
    if not url:
        return None
    if url in already:
        return url
    if len(url) > 1000 or not _URL_CHARS.match(url) or "/../" in url:
        raise Invalid("One of the photos has an address that isn't valid. Please upload it again.")
    if not any(url.startswith(p) for p in _image_prefixes()):
        raise Invalid("Photos must be uploaded through DealBriz. Please upload it again.")
    return url


def image_list(value, already=()):
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        value = [v for v in value.split(",") if v.strip()]
    if not isinstance(value, (list, tuple)):
        raise Invalid("The photos list isn't valid.")
    if len(value) > MAX_EXTRA_IMAGES:
        raise Invalid(f"Please add at most {MAX_EXTRA_IMAGES + 1} photos.")
    return [u for u in (image_url(v, already) for v in value) if u]


def existing_images(product):
    if product is None:
        return set()
    out = {product.image_url} if product.image_url else set()
    out.update(u for u in (product.extra_images or "").split(",") if u)
    return out


# --------------------------------------------------------------- listings

def clean_listing(data, product=None):
    """Checks the listing fields present in `data` and returns them cleaned.

    For a new listing (product=None) the required fields must be there.
    For an edit only the fields sent are checked, and the listing's current
    photo addresses count as allowed. Raises Invalid with a message the user
    can act on."""
    creating = product is None
    old = existing_images(product)
    out = {}

    def has(k):
        return creating or k in data

    if has("title"):
        out["title"] = long_text(data.get("title"), "title", TITLE_MAX, required=True)
        if _ANGLE.search(out["title"]):
            raise Invalid("The title can't contain < or >.")
    if has("category"):
        out["category"] = code(data.get("category"), "category")
    if has("condition"):
        out["condition"] = code(data.get("condition"), "condition")
    if has("price"):
        out["price"] = money(data.get("price"), "price")
    if "original_price" in data:
        out["original_price"] = money(data.get("original_price"), "original price", required=False)
    if has("description"):
        out["description"] = long_text(data.get("description"), "description",
                                       DESCRIPTION_MAX, required=True)
    if has("city"):
        out["city"] = pincode(data.get("city"))
    for key, label, cap in (("location", "location", PLACE_MAX), ("landmark", "landmark", PLACE_MAX),
                            ("brand", "brand", BRAND_MAX), ("model", "model", BRAND_MAX)):
        if key in data:
            out[key] = short_text(data.get(key), label, cap)
    for key in ("negotiable", "emi_eligible"):
        if key in data:
            out[key] = flag(data.get(key))
    if "image_url" in data:
        out["image_url"] = image_url(data.get("image_url"), old)
    if "extra_images" in data:
        out["extra_images"] = image_list(data.get("extra_images"), old)
    if "status" in data and data.get("status") not in LISTING_STATUSES:
        raise Invalid("That isn't a listing status.")
    return out


# ---------------------------------------------------------------- people

def phone(value):
    text = str(value or "").strip()
    digits = re.sub(r"\D", "", text)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if (not _PHONE_CHARS.match(text) or len(digits) != 10
            or digits[0] not in "6789" or digits == digits[0] * 10):
        raise Invalid("Please enter a valid 10-digit mobile number.")
    return text


def user_city(value):
    text = str(value or "").strip()
    if not text:
        raise Invalid("Please add your city or pincode.")
    if not _USER_CITY.match(text):
        raise Invalid("Please enter a valid city or pincode (max 10 characters).")
    return text


def clean_profile(data):
    """first_name, last_name, phone, city — whichever are present."""
    out = {}
    if "first_name" in data:
        out["first_name"] = short_text(data.get("first_name"), "first name", NAME_MAX, required=True)
    if "last_name" in data:
        out["last_name"] = short_text(data.get("last_name"), "last name", NAME_MAX)
    if "phone" in data:
        out["phone"] = phone(data.get("phone"))
    if "city" in data:
        out["city"] = user_city(data.get("city"))
    return out
