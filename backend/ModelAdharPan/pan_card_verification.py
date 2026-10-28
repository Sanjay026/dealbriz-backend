"""
Print VERIFIED / INVALID for Aadhaar and PAN card images. Everything runs locally.

    python pan_card_verification.py card.jpg [more.jpg ...]

Output is only AADHAAR VERIFIED, PAN VERIFIED or INVALID; card data is never printed.
"VERIFIED" means the number on the card is well-formed:
  - Aadhaar: 12 digits, first digit 2-9, Verhoeff checksum correct
  - PAN:     AAAAA9999A with a valid holder-type letter (PAN has no public checksum)
It does NOT prove the card is genuine; that needs UIDAI / Protean (NSDL) APIs.
"""
import sys

from id_extractor import find_aadhaar_numbers, find_pan_numbers, ocr_image


def verify_text(text: str) -> str:
    """Decide the verdict from OCR text: AADHAAR VERIFIED, PAN VERIFIED or INVALID.

    The number is only checked (Aadhaar checksum / PAN format), never returned.
    """
    if find_aadhaar_numbers(text):
        return "AADHAAR VERIFIED"
    if find_pan_numbers(text):
        return "PAN VERIFIED"
    return "INVALID"


def verify_image(path: str) -> dict:
    """One OCR pass, no retries, so latency stays predictable. Returns only the verdict."""
    return {"file": path, "status": verify_text(ocr_image(path))}


def main() -> None:
    for path in sys.argv[1:]:
        print(f"{path}: {verify_image(path)['status']}")


if __name__ == "__main__":
    main()
