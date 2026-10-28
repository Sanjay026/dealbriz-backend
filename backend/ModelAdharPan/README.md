# Aadhaar & PAN card identification

Two layers that work together:

| Layer | File | What it does |
|---|---|---|
| CNN classifier | `train_classifier.py` | MobileNetV2 transfer learning: image → `aadhaar` / `pan` / `other` |
| OCR + rules | `id_extractor.py` | RapidOCR (one pass, ONNX), keyword detection, Aadhaar **Verhoeff checksum**, PAN format + holder type, name/DOB/gender extraction, Aadhaar masking |
| CLI | `predict.py` | Combines both; works with OCR rules alone until you train the CNN |

## Setup
1. `pip install -r requirements.txt`
2. RapidOCR's models ship inside the pip package; nothing else to install or download.

## Web app
```
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```
Open http://127.0.0.1:8000 and upload a card image.

## Use without training (OCR rules only)
```
python predict.py card.jpg --no-model
```

## Train the CNN
Put images in `data/aadhaar`, `data/pan`, `data/other` (≥150 per class is a good start;
include front/back, phone photos, scans, e-Aadhaar PDFs rendered to images).
```
python train_classifier.py --data data --epochs 10 --fine-tune-epochs 5
python predict.py card.jpg
```

## Tests
```
python -m pytest test_id_extractor.py
```

## Notes
- Aadhaar numbers are masked by default (UIDAI guidance); `--show-full` disables it.
- Checksum/format validation proves a number is well-formed, not that it is genuine.
  Real verification needs UIDAI / NSDL-Protean APIs via a licensed KYC provider.
- Store and process card images in line with India's DPDP Act 2023.
