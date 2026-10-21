"""
Private storage for DealLock documents (RC, PAN, masked Aadhaar...): the
seller's on the listing (key deal-docs/listings/<listing id>/...) and the
buyer's in the deal (key deal-docs/<deal id>/...).

These are identity documents, so unlike listing photos they are never given
a public URL:

  S3     stored under DEAL_DOCS_PREFIX (default "deal-docs/") in
         DEAL_DOCS_BUCKET (default: the same S3_BUCKET as listing photos),
         encrypted at rest (SSE-S3), no public cache headers. Viewing goes
         through the backend, which checks who is asking and then redirects
         to a link that works for 5 minutes.
         IMPORTANT: if the bucket policy makes the whole bucket public
         ("Resource": "arn:aws:s3:::<bucket>/*"), these files would be public
         too. Limit the public rule to listings/* or use a separate private
         bucket in DEAL_DOCS_BUCKET.
  local  stored in PRIVATE_UPLOAD_FOLDER (default "private_uploads"), which
         nothing serves directly. Development only.
"""
import logging
import os
import uuid

log = logging.getLogger("dealbridge.deals.docs")

STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local").lower()
BUCKET = os.environ.get("DEAL_DOCS_BUCKET") or os.environ.get("S3_BUCKET", "")
REGION = os.environ.get("AWS_REGION", "ap-south-1")
PREFIX = os.environ.get("DEAL_DOCS_PREFIX", "deal-docs/")
LOCAL_FOLDER = os.environ.get("PRIVATE_UPLOAD_FOLDER", "private_uploads")
LINK_SECONDS = 300

_s3 = None


def _client():
    global _s3
    if _s3 is None:
        import boto3
        from botocore.config import Config
        # Signature v4 and the regional endpoint, so the 5-minute links
        # work in ap-south-1 (which accepts v4 only) without a redirect.
        _s3 = boto3.client("s3", region_name=REGION,
                           endpoint_url=f"https://s3.{REGION}.amazonaws.com",
                           config=Config(signature_version="s3v4",
                                         s3={"addressing_style": "virtual"}))
    return _s3


def is_s3():
    return STORAGE_BACKEND == "s3" and bool(BUCKET)


def _local_path(key):
    # Keys are made here, never from user input, but stay inside the folder
    # regardless.
    base = os.path.abspath(LOCAL_FOLDER)
    path = os.path.abspath(os.path.join(base, key))
    if not path.startswith(base + os.sep):
        raise ValueError("bad document key")
    return path


def save(file_storage, deal_id, doc_type, ext, content_type):
    """Stores the file and returns its private key."""
    key = f"{PREFIX}{deal_id}/{doc_type}-{uuid.uuid4().hex}.{ext}"
    file_storage.stream.seek(0)
    if is_s3():
        _client().upload_fileobj(
            file_storage.stream, BUCKET, key,
            ExtraArgs={"ContentType": content_type,
                       "ServerSideEncryption": "AES256",
                       "CacheControl": "private, no-store"})
    else:
        path = _local_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        file_storage.save(path)
    return key


def delete(key):
    """Best effort. A file that can't be deleted is logged, not fatal."""
    if not key:
        return
    try:
        if is_s3():
            _client().delete_object(Bucket=BUCKET, Key=key)
        else:
            path = _local_path(key)
            if os.path.exists(path):
                os.remove(path)
    except Exception as exc:
        log.warning("Could not delete deal document %s: %s", key, exc)


def view_link(key, file_name, content_type):
    """S3: a 5-minute signed link. Local: None (the caller streams the file
    with local_path())."""
    if not is_s3():
        return None
    safe_name = "".join(ch for ch in (file_name or "document") if ch.isalnum() or ch in "._- ")[:100]
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET, "Key": key,
                "ResponseContentType": content_type or "application/octet-stream",
                "ResponseContentDisposition": f'inline; filename="{safe_name or "document"}"',
                "ResponseCacheControl": "private, no-store"},
        ExpiresIn=LINK_SECONDS)


def local_path(key):
    return _local_path(key)


def _sniff(head):
    """What the file really is, from its first bytes: jpg, png, webp, pdf,
    heic, or None."""
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"):
        return "heic"
    return None


def check_upload(file_storage, extensions, max_bytes):
    """The checks every document upload gets (deal or listing): a real
    JPG/PNG/WEBP/PDF, not empty, not too big. Returns (ext, content_type,
    size, None) or (None, None, None, error message).

    The type comes from the file's first bytes, not its name: phones often
    hand over "IMG_0412.JPG" that is really a PNG, or a name with no
    extension at all. Only the content decides."""
    if not file_storage or not file_storage.filename:
        return None, None, None, "Choose a file to upload."
    head = file_storage.stream.read(16)
    file_storage.stream.seek(0, 2)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size <= 0:
        return None, None, None, "That file is empty."
    kind = _sniff(head)
    if kind == "heic":
        return None, None, None, ("This photo is in HEIC format, which can't be opened by the lender. "
                                  "Upload it as JPG (or take a screenshot of it and upload that).")
    if kind is None or kind not in extensions:
        return None, None, None, "Upload a photo (JPG, PNG or WEBP) or a PDF."
    if size > max_bytes:
        return None, None, None, f"That file is too big. Files can be up to {max_bytes // (1024 * 1024)} MB."
    return kind, extensions[kind], size, None
