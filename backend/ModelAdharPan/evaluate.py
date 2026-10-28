"""
Measure how reliable pan_card_verification.py is on labelled images, with 95% confidence intervals.

Test folder layout (images NOT used for training):
    test_data/
      aadhaar/   valid Aadhaar cards           -> expected "AADHAAR VERIFIED"
      pan/       valid PAN cards               -> expected "PAN VERIFIED"
      other/     other docs, fakes, bad photos -> expected "INVALID..."

    python evaluate.py test_data
"""
import argparse
import math
import time
from pathlib import Path

from pan_card_verification import verify_image

EXPECTED = {"aadhaar": "AADHAAR VERIFIED", "pan": "PAN VERIFIED", "other": "INVALID"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion; behaves well for small n and p near 0/1."""
    if n == 0:
        return 0.0, 0.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def normalise(status: str) -> str:
    return "INVALID" if status.startswith("INVALID") else status


def run(root: Path) -> tuple[list[tuple[str, str, str]], list[float]]:
    rows, seconds = [], []
    for label in EXPECTED:
        for img in sorted((root / label).glob("*")):
            if img.suffix.lower() in IMAGE_EXT:
                t = time.perf_counter()
                status = verify_image(str(img))["status"]
                seconds.append(time.perf_counter() - t)
                rows.append((str(img), label, normalise(status)))
    return rows, seconds


def latency_report(seconds: list[float]) -> None:
    if not seconds:
        return
    s = sorted(seconds)
    p95 = s[min(len(s) - 1, math.ceil(0.95 * len(s)) - 1)]
    print(f"\nLatency per image: median {s[len(s) // 2]:.2f} s, p95 {p95:.2f} s, max {s[-1]:.2f} s")


def fmt(k: int, n: int) -> str:
    lo, hi = wilson_ci(k, n)
    return f"{k}/{n} = {k / n:6.1%}   95% CI [{lo:.1%}, {hi:.1%}]" if n else "n/a (no images)"


def report(rows: list[tuple[str, str, str]]) -> None:
    preds = sorted(set(EXPECTED.values()))
    print("\nConfusion matrix (rows = true class, cols = prediction)")
    print(f"{'':10}" + "".join(f"{p:>18}" for p in preds))
    for label in EXPECTED:
        counts = [sum(1 for _, l, p in rows if l == label and p == pr) for pr in preds]
        print(f"{label:10}" + "".join(f"{c:>18}" for c in counts))

    correct = sum(EXPECTED[l] == p for _, l, p in rows)
    print(f"\nAccuracy              {fmt(correct, len(rows))}")
    for label, verdict in EXPECTED.items():
        if verdict == "INVALID":
            continue
        tp = sum(l == label and p == verdict for _, l, p in rows)
        n_true = sum(l == label for _, l, _ in rows)
        n_pred = sum(p == verdict for _, _, p in rows)
        print(f"{label:7} recall        {fmt(tp, n_true)}   (real cards that got VERIFIED)")
        print(f"{label:7} precision     {fmt(tp, n_pred)}   (VERIFIED results that were right)")
    n_other = sum(l == "other" for _, l, _ in rows)
    fa = sum(l == "other" and p != "INVALID" for _, l, p in rows)
    print(f"False accept rate     {fmt(fa, n_other)}   (non-cards wrongly VERIFIED)")

    wrong = [(f, l, p) for f, l, p in rows if EXPECTED[l] != p]
    if wrong:
        print("\nMistakes:")
        for f, l, p in wrong:
            print(f"  {f}: expected {EXPECTED[l]}, got {p}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="test_data")
    args = ap.parse_args()

    from id_extractor import ocr_reader
    ocr_reader()  # load the model first so it doesn't count toward per-image latency

    rows, seconds = run(Path(args.root))
    report(rows)
    latency_report(seconds)


if __name__ == "__main__":
    main()
