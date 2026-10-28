"""
Web API + upload page for Aadhaar / PAN verification. Runs fully on this machine.

    python -m uvicorn app:app --host 127.0.0.1 --port 8000
    open http://127.0.0.1:8000

The OCR model loads once at startup, so each request is a single OCR pass.
"""
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from id_extractor import ocr_reader
from pan_card_verification import verify_image

BASE = Path(__file__).parent
MAX_BYTES = 10 * 1024 * 1024
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm up so the first user doesn't wait for model loading.
    ocr_reader()
    yield


app = FastAPI(title="Aadhaar / PAN Verify", lifespan=lifespan)


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/verify")
def verify(file: UploadFile = File(...)):
    # Plain `def` so FastAPI runs this blocking OCR work in its thread pool.
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(415, "Only JPG, PNG, WEBP or BMP images are accepted.")
    data = file.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Image is larger than 10 MB.")

    suffix = Path(file.filename or "upload.jpg").suffix.lower() or ".jpg"
    fd, path = tempfile.mkstemp(suffix=suffix)
    started = time.perf_counter()
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        result = verify_image(path)
    except FileNotFoundError:  # OpenCV could not decode the upload
        raise HTTPException(400, "Could not read this image file.")
    finally:
        os.remove(path)  # never keep ID images on disk

    # Only the verdict goes back to the client; no number or other card data.
    return {
        "status": result["status"],
        "verified": result["status"] != "INVALID",
        "seconds": round(time.perf_counter() - started, 2),
    }
