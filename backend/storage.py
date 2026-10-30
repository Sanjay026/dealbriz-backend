"""File storage — local disk or S3.

Why this exists: uploads were written to a folder inside the container.
That works on one box and breaks on AWS the moment there is more than one
task behind a load balancer — a photo uploaded through container A is a 404
from container B, and every upload is lost when a task is replaced during a
deploy. On ECS/Fargate that is not an edge case, it is the normal lifecycle.

Set STORAGE_BACKEND=s3 in production. Local stays the default so nothing
changes for development.
"""
import logging
import mimetypes
import os
import uuid

log = logging.getLogger("dealbriz.storage")

STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local").lower()
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "uploads")

S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_REGION = os.environ.get("AWS_REGION", "ap-south-1")
S3_PREFIX = os.environ.get("S3_PREFIX", "listings/")
# Serve through CloudFront when it is in front of the bucket, so images are
# cached at the edge and the bucket itself can stay private.
CDN_DOMAIN = os.environ.get("CDN_DOMAIN", "")

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        import boto3
        # No explicit credentials: on ECS/EC2 the task role supplies them.
        # Baking keys into the image or .env is what leaks them.
        _s3 = boto3.client("s3", region_name=S3_REGION)
    return _s3


def is_s3():
    return STORAGE_BACKEND == "s3" and bool(S3_BUCKET)


IMAGE_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}


def check_image(file_storage, allowed=("jpg", "png", "webp", "gif")):
    """What a photo really is, from its first bytes. Returns (ext,
    content_type, None) or (None, None, error message).

    The name and the type the browser claims are both chosen by whoever
    sends the request, so neither decides: an SVG with script named x.png
    and sent as image/svg+xml used to be stored, and served from S3 with
    that type. Only real JPG/PNG/WEBP/GIF bytes are accepted, and the
    stored type comes from those bytes."""
    if not file_storage or not file_storage.filename:
        return None, None, "No file selected"
    head = file_storage.stream.read(16)
    file_storage.stream.seek(0)
    kind = None
    if head.startswith(b"\xff\xd8\xff"):
        kind = "jpg"
    elif head.startswith(b"\x89PNG\r\n\x1a\n"):
        kind = "png"
    elif head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        kind = "webp"
    elif head[:6] in (b"GIF87a", b"GIF89a"):
        kind = "gif"
    elif head[4:8] == b"ftyp":
        return None, None, ("This photo is in HEIC format. Please upload it as JPG "
                            "(or take a screenshot of it and upload that).")
    if kind is None or kind not in allowed:
        names = ", ".join(a.upper() for a in allowed[:-1]) + " or " + allowed[-1].upper()
        return None, None, f"Only {names} images are allowed"
    return kind, IMAGE_TYPES[kind], None


def save_upload(file_storage, filename=None, content_type=None):
    """Stores an uploaded file and returns the public URL path.

    Returns a path like "/uploads/abc.jpg" for local, or a full URL for S3.
    Callers store whatever comes back, so switching backends does not
    require rewriting existing rows.
    """
    name = filename or f"{uuid.uuid4().hex}{os.path.splitext(file_storage.filename or '')[1].lower()}"

    if not is_s3():
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        path = os.path.join(UPLOAD_FOLDER, name)
        file_storage.save(path)
        return f"/uploads/{name}"

    key = f"{S3_PREFIX}{name}"
    # The caller's checked type (check_image) wins over the browser's claim.
    content_type = (content_type
                    or mimetypes.guess_type(name)[0]
                    or "application/octet-stream")
    try:
        file_storage.stream.seek(0)
        _client().upload_fileobj(
            file_storage.stream, S3_BUCKET, key,
            ExtraArgs={
                "ContentType": content_type,
                # Listing photos never change once written, so a long cache
                # is safe and keeps them off the origin entirely.
                "CacheControl": "public, max-age=31536000, immutable",
            },
        )
    except Exception as exc:
        log.error("S3 upload failed for %s: %s", key, exc, exc_info=True)
        raise

    if CDN_DOMAIN:
        return f"https://{CDN_DOMAIN.rstrip('/')}/{key}"
    return f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{key}"


def delete_upload(url_or_path):
    """Best-effort removal. A failure here must never block deleting the
    listing itself — an orphaned object costs pennies, a listing that
    refuses to delete is a support ticket."""
    if not url_or_path:
        return
    try:
        if is_s3():
            key = url_or_path.split(".amazonaws.com/")[-1]
            if CDN_DOMAIN and CDN_DOMAIN in url_or_path:
                key = url_or_path.split(CDN_DOMAIN, 1)[-1].lstrip("/")
            if key and not key.startswith("http"):
                _client().delete_object(Bucket=S3_BUCKET, Key=key)
        else:
            name = os.path.basename(url_or_path)
            path = os.path.join(UPLOAD_FOLDER, name)
            if os.path.exists(path):
                os.remove(path)
    except Exception as exc:
        log.warning("Could not delete upload %s: %s", url_or_path, exc)


def health():
    """Reports whether storage is actually reachable, for the health check.
    A container that cannot write uploads is not healthy, even if it serves
    pages fine."""
    if not is_s3():
        try:
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            return {"backend": "local", "ok": os.access(UPLOAD_FOLDER, os.W_OK)}
        except OSError:
            return {"backend": "local", "ok": False}
    try:
        _client().head_bucket(Bucket=S3_BUCKET)
        return {"backend": "s3", "bucket": S3_BUCKET, "ok": True}
    except Exception as exc:
        log.error("S3 health check failed: %s", exc)
        return {"backend": "s3", "bucket": S3_BUCKET, "ok": False}
