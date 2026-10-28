"""
OCR + rule-based identification and field extraction for Aadhaar and PAN cards.

- Aadhaar number: 12 digits, first digit 2-9, last digit is a Verhoeff checksum.
- PAN: AAAAA9999A; the 4th letter encodes the holder type (P = individual, C = company...).

The validation helpers (verhoeff_*, is_valid_pan, mask_aadhaar, detect_doc_type)
have no OCR dependency, so they can be used on text from any OCR engine.
"""
import re
from functools import lru_cache

# ---------------------------------------------------------------- Verhoeff ---
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_INV = [0, 4, 3, 2, 1, 5, 9, 8, 7, 6]


def verhoeff_check_digit(number: str) -> int:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return _INV[c]


def verhoeff_validate(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


# ------------------------------------------------------------------ Aadhaar ---
# Separators are spaces/dashes only: a newline must not join or block a number,
# e.g. the "1947" helpline printed on the line right below the Aadhaar number.
# OCR often puts 2-3 spaces between the 4-digit groups.
_AADHAAR_RE = re.compile(
    r"(?<![0-9])([2-9][0-9]{3})[ \t-]{0,3}([0-9]{4})[ \t-]{0,3}([0-9]{4})(?![ \t-]{0,3}[0-9])"
)
# Hindi OCR models may return Devanagari digits (४९००) for printed ASCII ones.
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def normalize_digits(text: str) -> str:
    return text.translate(_DEVANAGARI_DIGITS)


def is_valid_aadhaar(number: str) -> bool:
    digits = re.sub(r"[^0-9]", "", normalize_digits(number))
    return len(digits) == 12 and digits[0] not in "01" and verhoeff_validate(digits)


def find_aadhaar_numbers(text: str) -> list[str]:
    found = []
    for m in _AADHAAR_RE.finditer(normalize_digits(text)):
        num = "".join(m.groups())
        if is_valid_aadhaar(num) and num not in found:
            found.append(num)
    return found


def mask_aadhaar(number: str) -> str:
    """UIDAI-style masking: only the last 4 digits are shown."""
    digits = re.sub(r"\D", "", number)
    return "XXXX XXXX " + digits[-4:]


# ---------------------------------------------------------------------- PAN ---
_PAN_RE = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b")
PAN_HOLDER_TYPES = {
    "P": "Individual",
    "C": "Company",
    "H": "Hindu Undivided Family",
    "F": "Firm / LLP",
    "A": "Association of Persons",
    "T": "Trust",
    "B": "Body of Individuals",
    "L": "Local Authority",
    "J": "Artificial Juridical Person",
    "G": "Government",
}


def is_valid_pan(pan: str) -> bool:
    pan = pan.strip().upper()
    return bool(re.fullmatch(r"[A-Z]{3}[ABCFGHJLPT][A-Z][0-9]{4}[A-Z]", pan))


def _fix_pan_ocr(token: str) -> str:
    """Repair common OCR confusions using the fixed PAN letter/digit layout."""
    to_letter = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G"}
    to_digit = {"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6"}
    chars = list(token)
    for i, ch in enumerate(chars):
        if i in (5, 6, 7, 8):
            chars[i] = to_digit.get(ch, ch)
        else:
            chars[i] = to_letter.get(ch, ch)
    return "".join(chars)


def find_pan_numbers(text: str) -> list[str]:
    found = []
    upper = normalize_digits(text).upper()
    candidates = _PAN_RE.findall(upper)
    # Also try OCR repair on any 10-char alphanumeric token.
    candidates += [_fix_pan_ocr(t) for t in re.findall(r"\b[A-Z0-9]{10}\b", upper)]
    for c in candidates:
        if is_valid_pan(c) and c not in found:
            found.append(c)
    return found


# ------------------------------------------------------------ Other fields ---
_DOB_RE = re.compile(r"\b(\d{2})[/\-.](\d{2})[/\-.](\d{4})\b")
_YOB_RE = re.compile(r"(?:Year of Birth|YoB)\s*[:\-]?\s*(\d{4})", re.I)


def find_dob(text: str) -> str | None:
    text = normalize_digits(text)
    m = _DOB_RE.search(text)
    if m:
        return "/".join(m.groups())
    m = _YOB_RE.search(text)
    return m.group(1) if m else None


def find_gender(text: str) -> str | None:
    t = text.upper()
    if re.search(r"\bFEMALE\b|महिला", t):
        return "Female"
    if re.search(r"\bMALE\b|पुरुष", t):
        return "Male"
    if re.search(r"\bTRANSGENDER\b", t):
        return "Transgender"
    return None


def find_pan_name(text: str) -> dict:
    """PAN layout: name line follows 'Name' / the dept header; father's name follows it."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out = {}
    for i, line in enumerate(lines):
        low = line.lower()
        if "father" in low and i + 1 < len(lines):
            out["father_name"] = lines[i + 1]
        elif re.fullmatch(r"(name|नाम\s*/\s*name)", low) and i + 1 < len(lines):
            out.setdefault("name", lines[i + 1])
    return out


def find_aadhaar_name(text: str) -> str | None:
    """On Aadhaar fronts the English name is usually the line just above the DOB line."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        if (_DOB_RE.search(line) or _YOB_RE.search(line)) and i > 0:
            cand = lines[i - 1]
            if re.fullmatch(r"[A-Za-z .]{3,}", cand):
                return cand
    return None


# -------------------------------------------------------- Doc type by text ---
# Regex patterns, matched case-insensitively against the OCR text.
_AADHAAR_KEYWORDS = [
    r"aadha+r", r"unique identification", r"\buidai\b", r"आधार",
    r"\bvid\b", r"enrol?ment", r"government of india",
]
_PAN_KEYWORDS = [
    r"income\s*tax\s*department", r"permanent\s*account\s*number", r"आयकर विभाग",
    r"govt\.? of india", r"\bpan\b", r"father'?s name",
]


def detect_doc_type(text: str) -> tuple[str, float]:
    """Rule-based classification from OCR text. Returns (label, score in [0,1])."""
    a = sum(bool(re.search(k, text, re.I)) for k in _AADHAAR_KEYWORDS)
    p = sum(bool(re.search(k, text, re.I)) for k in _PAN_KEYWORDS)
    a += 3 * bool(find_aadhaar_numbers(text))
    p += 3 * bool(find_pan_numbers(text))
    if a == 0 and p == 0:
        return "other", 0.0
    label = "aadhaar" if a >= p else "pan"
    return label, max(a, p) / (a + p)


# ---------------------------------------------------------------------- OCR ---
@lru_cache(maxsize=1)
def ocr_reader():
    """Load RapidOCR once (PP-OCR models on ONNX Runtime; models ship inside the pip package)."""
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


# Card text stays readable at this width, and OCR time grows with pixel count.
OCR_WIDTH = 960


def _boxes_to_lines(results) -> str:
    """OCR returns text boxes; group boxes on the same row into text lines, top to bottom."""
    if not results:
        return ""
    items = []
    for box, text, _conf in results:
        ys = [p[1] for p in box]
        items.append(((min(ys) + max(ys)) / 2, max(ys) - min(ys), min(p[0] for p in box), text))
    items.sort()
    tol = 0.5 * sorted(h for _, h, _, _ in items)[len(items) // 2]
    lines, current = [], [items[0]]
    for it in items[1:]:
        if abs(it[0] - current[-1][0]) <= tol:
            current.append(it)
        else:
            lines.append(current)
            current = [it]
    lines.append(current)
    return "\n".join(" ".join(t for *_, t in sorted(line, key=lambda i: i[2])) for line in lines)


def ocr_image(path: str) -> str:
    """Read the text on an image in a single RapidOCR pass, returned as lines top to bottom."""
    import cv2

    img = cv2.imread(path)
    if img is None:
        raise FileNotFoundError(path)
    w = img.shape[1]
    if w != OCR_WIDTH:
        interp = cv2.INTER_AREA if w > OCR_WIDTH else cv2.INTER_CUBIC
        img = cv2.resize(img, None, fx=OCR_WIDTH / w, fy=OCR_WIDTH / w, interpolation=interp)

    result, _elapsed = ocr_reader()(img)
    return _boxes_to_lines(result)


def extract_fields(text: str, doc_type: str) -> dict:
    if doc_type == "aadhaar":
        nums = find_aadhaar_numbers(text)
        return {
            "aadhaar_number": nums[0] if nums else None,
            "name": find_aadhaar_name(text),
            "dob": find_dob(text),
            "gender": find_gender(text),
        }
    if doc_type == "pan":
        pans = find_pan_numbers(text)
        fields = {"pan_number": pans[0] if pans else None, "dob": find_dob(text)}
        if pans:
            fields["holder_type"] = PAN_HOLDER_TYPES.get(pans[0][3])
        fields.update(find_pan_name(text))
        return fields
    return {}
