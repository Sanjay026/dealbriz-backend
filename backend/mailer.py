"""
Outgoing email (used for the documents email to lenders).

Settings (.env next to docker-compose.prod.yml). Only names here, never values:

    MAIL_PROVIDER   ses | smtp | console
                    ses      Amazon SES, using the server's AWS role (no keys
                             in .env). The role needs ses:SendRawEmail, and
                             the MAIL_FROM domain must be verified in SES.
                    smtp     any SMTP mailbox (Hostinger, Zoho, Google...)
                    console  nothing is sent: the email's subject, recipient
                             and attachment names are written to the log.
                             For testing only.
                    not set  email is off. Lender emails wait (status
                             "pending") and go out once it is set.
    MAIL_FROM       e.g. DealBriz <deals@dealbriz.com>
    MAIL_REPLY_TO   optional
    SMTP_HOST, SMTP_PORT (587 = STARTTLS, 465 = SSL), SMTP_USER, SMTP_PASSWORD
    MAIL_MAX_MB     attachments per email, default 18. Larger sets are split
                    into several emails (Part 1 of 2...).
    MAIL_SAVE_DIR   optional, testing: also write each email as a .eml file.
"""
import logging
import os
import smtplib
import ssl
import uuid
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr

log = logging.getLogger("dealbridge.mailer")


class MailError(Exception):
    pass


def provider():
    return (os.environ.get("MAIL_PROVIDER") or "").strip().lower()


def mail_from():
    raw = (os.environ.get("MAIL_FROM") or "").strip()
    if raw:
        return raw
    return formataddr(("DealBriz", "no-reply@dealbriz.com"))


def is_configured():
    p = provider()
    if p == "console":
        return True
    if p == "ses":
        return bool((os.environ.get("MAIL_FROM") or "").strip())
    if p == "smtp":
        return all((os.environ.get(k) or "").strip() for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM"))
    return False


def max_attachment_bytes():
    try:
        mb = float(os.environ.get("MAIL_MAX_MB") or 18)
    except ValueError:
        mb = 18
    return int(max(1, min(mb, 35)) * 1024 * 1024)


def build(to_list, subject, text, html, attachments=()):
    """attachments: [(filename, bytes, content_type)]"""
    msg = EmailMessage()
    msg["From"] = mail_from()
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    reply_to = (os.environ.get("MAIL_REPLY_TO") or "").strip()
    if reply_to:
        msg["Reply-To"] = reply_to
    domain = (parseaddr(mail_from())[1].split("@")[-1] or "dealbriz.com")
    msg["Message-ID"] = make_msgid(domain=domain)
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    for name, data, ctype in attachments:
        maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=name)
    return msg


def send(msg):
    """Sends one EmailMessage. Raises MailError with a short reason."""
    p = provider()
    to_list = [a.strip() for a in (msg["To"] or "").split(",") if a.strip()]
    save_dir = (os.environ.get("MAIL_SAVE_DIR") or "").strip()
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        with open(os.path.join(save_dir, f"{uuid.uuid4().hex}.eml"), "wb") as fh:
            fh.write(bytes(msg))
    if p == "console":
        names = [part.get_filename() for part in msg.iter_attachments()]
        log.warning("EMAIL (console provider, not sent) to %s: %s | attachments: %s",
                    ", ".join(to_list), msg["Subject"], ", ".join(n for n in names if n) or "none")
        return
    if p == "ses":
        try:
            import boto3
            region = os.environ.get("SES_REGION") or os.environ.get("AWS_REGION", "ap-south-1")
            client = boto3.client("sesv2", region_name=region)
            client.send_email(FromEmailAddress=parseaddr(mail_from())[1],
                              Destination={"ToAddresses": to_list},
                              Content={"Raw": {"Data": bytes(msg)}})
            return
        except Exception as exc:
            raise MailError(f"SES: {exc}"[:480]) from exc
    if p == "smtp":
        host = os.environ.get("SMTP_HOST", "").strip()
        port = int(os.environ.get("SMTP_PORT") or 587)
        user = os.environ.get("SMTP_USER", "").strip()
        password = os.environ.get("SMTP_PASSWORD", "")
        try:
            context = ssl.create_default_context()
            if port == 465:
                with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as s:
                    s.login(user, password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=60) as s:
                    s.starttls(context=context)
                    s.login(user, password)
                    s.send_message(msg)
            return
        except Exception as exc:
            raise MailError(f"SMTP: {exc}"[:480]) from exc
    raise MailError("Email is not set up on the server (MAIL_PROVIDER).")
