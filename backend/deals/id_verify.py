"""Auto-check for the buyer's Aadhaar and PAN uploads in a deal, and for the
seller's Registration Certificate (RC) on a listing.

Uses the local OCR model in ``ModelAdharPan/`` (RapidOCR on ONNX Runtime).
The heavy imports stay lazy because loading the model takes tens of seconds;
``create_app`` warms it in a background thread instead of blocking startup.

The question asked of every file is "is this the card it claims to be,
with a valid number?" Yes: "verified", which the upload treats as approved.
No (a selfie or an Aadhaar card in the PAN slot, a card whose number can't
be read or isn't a valid PAN/Aadhaar): refused at upload, so the buyer
fixes it straight away. Nothing waits in between for a person.

The RC check (see ``_rc_verdict``) asks "is this an RC?" the same way:
it reads like one ("verified", approved on the spot) or it is refused.
It does not check who owns the vehicle or that the RC is genuine.

Only the verdict is handled here. The Aadhaar/PAN number itself is never
returned, logged or stored.
"""
import logging
import os
import re
import tempfile

log = logging.getLogger("dealbridge.id_verify")

# Which card each upload slot must hold.
_EXPECTED = {
    "buyer_pan": "pan",
    "buyer_aadhaar": "aadhaar",
    "rc": "rc",
}

KIND_NAMES = {
    "pan": "a PAN card",
    "aadhaar": "an Aadhaar card",
    "insurance": "an insurance policy",
}

# Words printed on an RC (smart card, paper book or the DigiLocker /
# mParivahan PDF). Each pattern counts once.
_RC_KEYWORDS = (
    r"registration\s*certificate", r"certificate\s*of\s*registration",
    r"reg(istratio)?n\.?\s*(no|number|mark)", r"registering\s*authority",
    r"(date\s*of\s*reg|reg(istratio)?n\.?\s*(date|dt|validity|valid))",
    r"chas+is", r"engine\s*(no|number)|eng\.?\s*no",
    r"class\s*of\s*vehicle|vehicle\s*class|veh\.?\s*class", r"maker|manufacturer|mfr",
    r"fuel", r"owner", r"seating|cubic\s*cap|unladen|wheel\s*base|no\.?\s*of\s*cyl",
    r"month\s*(&|and)?\s*y(ea)?r\.?\s*of\s*m|mfg|m\.?\s*y\.?\s*of\s*mfg",
    r"hypothecat|financ(i)?er", r"form\s*23", r"motor\s*vehicles?|transport\s*dep",
    r"body\s*type|colou?r", r"emission\s*norm|bharat\s*stage|bs[\s-]?(iv|vi|4|6)",
)
# Only an RC says these; an insurance policy repeats most of the rest.
_RC_ONLY = (r"registration\s*certificate", r"certificate\s*of\s*registration",
            r"registering\s*authority", r"form\s*23")
_INSURANCE_KEYWORDS = (r"insurance", r"polic(y|ies)", r"premium", r"insured", r"insurer",
                       r"\bidv\b", r"third\s*party", r"own\s*damage", r"no\s*claim")
_STATE_CODES = frozenset(
    "AN AP AR AS BR CG CH DD DL DN GA GJ HP HR JH JK KA KL LA LD MH ML MN MP MZ NL "
    "OD OR PB PY RJ SK TG TN TR TS UK UP UA WB".split())
_REG_RE = re.compile(r"\b([A-Z]{2})[\s.-]?(\d{1,2})[\s.-]?([A-Z]{0,3})[\s.-]?(\d{4})\b")
_BH_RE = re.compile(r"\b\d{2}[\s-]?BH[\s-]?\d{4}[\s-]?[A-Z]{1,2}\b")
_VIN_RE = re.compile(r"\b(?=[A-HJ-NPR-Z0-9]*\d)(?=[A-HJ-NPR-Z0-9]*[A-Z])[A-HJ-NPR-Z0-9]{17}\b")

_WARMED = False


def enabled() -> bool:
    return os.environ.get("ID_VERIFY_ENABLED", "true").lower() in ("1", "true", "yes")


def auto_approve() -> bool:
    """A "verified" card is approved without a person. Off: every document
    waits for a superadmin or the lender, as before."""
    return os.environ.get("ID_VERIFY_AUTO_APPROVE", "true").lower() in ("1", "true", "yes")


def checks(doc_type) -> bool:
    return doc_type in _EXPECTED


def warm() -> None:
    """Load the OCR models once. Never raises (a failure must not stop the app)."""
    global _WARMED
    if _WARMED:
        return
    try:
        from ModelAdharPan.id_extractor import ocr_reader

        ocr_reader()
        _WARMED = True
        log.info("Aadhaar/PAN OCR model ready.")
    except Exception:
        log.exception("Aadhaar/PAN OCR model could not be loaded; uploads will record ERROR.")


def _pdf_first_page(path):
    """Render page 1 of a PDF to a temporary PNG (e-Aadhaar is often a PDF)."""
    import pymupdf as fitz

    doc = fitz.open(path)
    try:
        if doc.page_count == 0:
            raise ValueError("empty PDF")
        pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(2, 2))
        fd, out = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        pix.save(out)
        return out
    finally:
        doc.close()


def _verdict(expected, text):
    """verified: a valid number of the expected card. unreadable: the
    card's words but no valid number (blurry, cut off, or a sample card).
    wrong: it is not that card; the detail says which card it is instead,
    when it can tell."""
    from ModelAdharPan.id_extractor import (
        _AADHAAR_KEYWORDS, _PAN_KEYWORDS, find_aadhaar_numbers, find_pan_numbers,
    )

    numbers = {"aadhaar": bool(find_aadhaar_numbers(text)), "pan": bool(find_pan_numbers(text))}
    words = {
        "aadhaar": sum(bool(re.search(k, text, re.I)) for k in _AADHAAR_KEYWORDS),
        "pan": sum(bool(re.search(k, text, re.I)) for k in _PAN_KEYWORDS),
    }
    other = "pan" if expected == "aadhaar" else "aadhaar"

    if numbers[expected]:
        return "verified", None
    if numbers[other]:
        return "wrong", other
    if words[expected] >= 1:
        return "unreadable", None
    return "wrong", None


def _has_vehicle_number(text):
    """A registration mark (PB65AB1234, 22BH1234AA) or a 17-character chassis number."""
    up = text.upper()
    if any(m.group(1) in _STATE_CODES for m in _REG_RE.finditer(up)):
        return True
    return bool(_BH_RE.search(up) or _VIN_RE.search(up))


def _rc_verdict(text):
    """verified: reads like an RC. unreadable: a little of one (blurry or
    cropped). wrong: something else; the detail says what, when it can
    tell (an Aadhaar or PAN card, an insurance policy)."""
    from ModelAdharPan.id_extractor import find_aadhaar_numbers, find_pan_numbers

    def count(patterns):
        return sum(bool(re.search(k, text, re.I)) for k in patterns)

    words = count(_RC_KEYWORDS)
    rc_only = count(_RC_ONLY)
    number = _has_vehicle_number(text)

    # A policy names the vehicle too (number, chassis, engine), so it is
    # told apart by its own words and by lacking the RC's.
    if not rc_only and count(_INSURANCE_KEYWORDS) >= 2:
        return "wrong", "insurance"
    if words >= 4 or (words >= 2 and number) or (rc_only and words >= 2):
        return "verified", None
    if words < 2:
        if find_aadhaar_numbers(text):
            return "wrong", "aadhaar"
        if find_pan_numbers(text):
            return "wrong", "pan"
    if words >= 1 or number:
        return "unreadable", None
    return "wrong", None


def check_upload(data, ext, doc_type):
    """Check one uploaded file and return only a verdict.

    Returns ``{"status": verified|unreadable|wrong|skipped|error, "detail": kind}``
    where ``kind`` is, for "wrong", the document it looks like instead ("pan",
    "aadhaar" or "insurance") if it could tell, else None.
    """
    expected = _EXPECTED.get(doc_type)
    if expected is None:
        return {"status": "skipped", "detail": None}

    ext = (ext or "").lower()
    src = out = None
    try:
        fd, src = tempfile.mkstemp(suffix="." + (ext or "bin"))
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        out = _pdf_first_page(src) if ext == "pdf" else src

        from ModelAdharPan.id_extractor import ocr_image

        text = ocr_image(out)
        status, detail = _rc_verdict(text) if expected == "rc" else _verdict(expected, text)
        log.info("Document check %s: %s", doc_type, status)
        return {"status": status, "detail": detail}
    except Exception:
        log.exception("Document auto-check failed (%s)", doc_type)
        return {"status": "error", "detail": None}
    finally:
        for path in (src, out):
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass


def refused(result) -> bool:
    return result["status"] in ("wrong", "unreadable")


def refuse_message(doc_type, result):
    """What the buyer is told when the file is refused."""
    from . import rules

    label = rules.DOC_TYPES.get(doc_type, {}).get("label", "document")
    if _EXPECTED.get(doc_type) == "rc":
        if result["status"] == "unreadable":
            return ("We couldn't read this RC clearly. Upload a sharp, straight photo with "
                    "the whole RC in view, or its PDF from DigiLocker or mParivahan.")
        detail = result["detail"]
        looks = f" It looks like {KIND_NAMES[detail]}." if detail in KIND_NAMES else ""
        return (f"This doesn't look like the vehicle's {label}.{looks} Upload a clear, "
                "straight photo of the RC (smart card or book), or its PDF from "
                "DigiLocker or mParivahan.")
    if result["status"] == "unreadable":
        name = {"pan": "PAN", "aadhaar": "Aadhaar"}[_EXPECTED[doc_type]]
        return (f"We couldn't read a valid {name} number on this card. Upload a clear, "
                f"straight photo of your own {name} card, or the PDF.")
    detail = result["detail"]
    looks = f" It looks like {KIND_NAMES[detail]}." if detail in KIND_NAMES else ""
    return (f"This doesn't look like the {label}.{looks} Please upload the correct "
            "card, as a clear photo or PDF.")
