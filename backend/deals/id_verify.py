"""Auto-check for the buyer's Aadhaar and PAN uploads in a deal.

Uses the local OCR model in ``ModelAdharPan/`` (RapidOCR on ONNX Runtime).
The heavy imports stay lazy because loading the model takes tens of seconds;
``create_app`` warms it in a background thread instead of blocking startup.

The question asked of every file is "is this the card it claims to be,
with a valid number?" Yes: "verified", which the upload treats as approved.
No (a selfie or an Aadhaar card in the PAN slot, a card whose number can't
be read or isn't a valid PAN/Aadhaar): refused at upload, so the buyer
fixes it straight away. Nothing waits in between for a person.

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
}

KIND_NAMES = {
    "pan": "a PAN card",
    "aadhaar": "an Aadhaar card",
}

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


def check_upload(data, ext, doc_type):
    """Check one uploaded file and return only a verdict.

    Returns ``{"status": verified|unreadable|wrong|skipped|error, "detail": kind}``
    where ``kind`` is, for "wrong", the card it looks like instead ("pan" or
    "aadhaar") if it could tell, else None.
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

        status, detail = _verdict(expected, ocr_image(out))
        log.info("Aadhaar/PAN check %s: %s", doc_type, status)
        return {"status": status, "detail": detail}
    except Exception:
        log.exception("Aadhaar/PAN auto-check failed (%s)", doc_type)
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
    if result["status"] == "unreadable":
        name = {"pan": "PAN", "aadhaar": "Aadhaar"}[_EXPECTED[doc_type]]
        return (f"We couldn't read a valid {name} number on this card. Upload a clear, "
                f"straight photo of your own {name} card, or the PDF.")
    detail = result["detail"]
    looks = f" It looks like {KIND_NAMES[detail]}." if detail in KIND_NAMES else ""
    return (f"This doesn't look like the {label}.{looks} Please upload the correct "
            "card, as a clear photo or PDF.")
