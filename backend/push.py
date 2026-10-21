"""
Push notifications via OneSignal's REST API.

Only the server sends pushes: the REST API key can send a notification to
ANY subscriber of the app, so it must never be in the Android/web bundle.

Configuration (in ./.env next to docker-compose.prod.yml):
    ONESIGNAL_REST_API_KEY   required - OneSignal dashboard -> Settings -> Keys & IDs
    ONESIGNAL_APP_ID         optional - defaults to the DealBriz app id below

If the key is missing, pushes are skipped with a single warning in the log;
chat itself keeps working either way.

Design notes
- Sent on a background thread AFTER the message is committed. A slow or
  failing OneSignal call must never delay or fail sending a chat message.
- Uses urllib from the standard library, so requirements.txt is unchanged.
- Subscription ids OneSignal reports as invalid (app uninstalled, data
  cleared) are deleted, so dead devices don't accumulate.
"""
import json
import logging
import os
import threading
import urllib.error
import urllib.request

from flask import current_app

log = logging.getLogger("dealbridge.push")

ONESIGNAL_URL = "https://api.onesignal.com/notifications?c=push"
DEFAULT_APP_ID = "a58e0f68-26db-4c02-a227-669ecba34afc"

# Must match OFFER_PREFIX in the app's src/services/chatBridge.ts.
OFFER_PREFIX = "[offer]"
PREVIEW_CHARS = 140

_warned_missing_key = False


def _config():
    key = os.environ.get("ONESIGNAL_REST_API_KEY", "").strip()
    app_id = os.environ.get("ONESIGNAL_APP_ID", "").strip() or DEFAULT_APP_ID
    return key, app_id


def _preview(body):
    """Readable notification text, including for offer messages."""
    body = body or ""
    if body.startswith(OFFER_PREFIX):
        rest = body[len(OFFER_PREFIX):]
        amount, sep, text = rest.partition("|")
        if sep and amount.strip().isdigit():
            line = f"Made an offer: \u20b9{int(amount):,}"
            text = text.strip()
            body = f"{line} \u2014 {text}" if text else line
        else:
            body = rest
    body = " ".join(body.split())
    if len(body) > PREVIEW_CHARS:
        body = body[: PREVIEW_CHARS - 1].rstrip() + "\u2026"
    return body


def _post(payload, key):
    req = urllib.request.Request(
        ONESIGNAL_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Key {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            data = {}
        return e.code, data


def _invalid_ids(response, sent_ids):
    """Subscription ids OneSignal says it can't deliver to."""
    errors = response.get("errors") if isinstance(response, dict) else None
    if not isinstance(errors, dict):
        return []
    sent = set(sent_ids)
    bad = []
    for name, value in errors.items():
        if name.startswith("invalid") and isinstance(value, list):
            bad.extend(v for v in value if isinstance(v, str) and v in sent)
    return bad


def _send_and_prune(app, payload, key, player_ids):
    try:
        status, data = _post(payload, key)
    except Exception:
        log.warning("OneSignal request failed", exc_info=True)
        return

    if status in (401, 403):
        log.error("OneSignal rejected the REST API key (HTTP %s). "
                  "Check ONESIGNAL_REST_API_KEY.", status)
        return
    if status >= 400:
        log.warning("OneSignal returned HTTP %s: %s", status, data)

    bad = _invalid_ids(data, player_ids)
    if not bad:
        return
    try:
        with app.app_context():
            from extensions import db
            from models import PushDevice
            PushDevice.query.filter(PushDevice.player_id.in_(bad)).delete(
                synchronize_session=False)
            db.session.commit()
            log.info("Removed %d dead push subscription(s)", len(bad))
    except Exception:
        log.warning("Could not prune dead push subscriptions", exc_info=True)


def send_chat_push(message, sender, product=None):
    """Notify the receiver of a new chat message. Never raises.

    The data block is what the app reads when the notification is tapped:
    {type: 'chat', product_id, sender_id} opens that exact conversation.
    """
    global _warned_missing_key
    try:
        key, app_id = _config()
        if not key:
            if not _warned_missing_key:
                log.warning("ONESIGNAL_REST_API_KEY is not set - chat push notifications are off.")
                _warned_missing_key = True
            return

        from models import PushDevice
        player_ids = [
            row.player_id
            for row in PushDevice.query.filter_by(user_id=message.receiver_id).all()
        ]
        if not player_ids:
            return

        sender_name = f"{sender.first_name or ''} {sender.last_name or ''}".strip() or "New message"
        heading = sender_name
        if product is not None and getattr(product, "title", None):
            heading = f"{sender_name} \u00b7 {product.title}"[:120]

        payload = {
            "app_id": app_id,
            "target_channel": "push",
            "include_subscription_ids": player_ids,
            "headings": {"en": heading},
            "contents": {"en": _preview(message.body)},
            "data": {
                "type": "chat",
                "product_id": str(message.product_id),
                "sender_id": str(message.sender_id),
            },
            # Messages from the same conversation stack together on the phone.
            "android_group": f"chat_{message.product_id}_{message.sender_id}",
            "priority": 10,
        }
        avatar = getattr(sender, "avatar_url", None)
        if avatar and str(avatar).startswith("https://"):
            payload["large_icon"] = avatar

        app = current_app._get_current_object()
        threading.Thread(
            target=_send_and_prune, args=(app, payload, key, player_ids), daemon=True
        ).start()
    except Exception:
        log.warning("Could not queue chat push", exc_info=True)


def send_deal_push(recipient_id, heading, text, product_id, other_user_id):
    """Notify one user about a DealLock event (offer, counter, lock, release).
    Never raises.

    The data block uses type 'chat' with the other party as sender_id, so
    tapping it opens that conversation, where the offer card and deal status
    live. That already works in the current app build, which only knows
    'chat' pushes.
    """
    global _warned_missing_key
    try:
        key, app_id = _config()
        if not key:
            if not _warned_missing_key:
                log.warning("ONESIGNAL_REST_API_KEY is not set - push notifications are off.")
                _warned_missing_key = True
            return None

        from models import PushDevice
        player_ids = [
            row.player_id
            for row in PushDevice.query.filter_by(user_id=recipient_id).all()
        ]
        if not player_ids:
            return None

        payload = {
            "app_id": app_id,
            "target_channel": "push",
            "include_subscription_ids": player_ids,
            "headings": {"en": (heading or "DealBriz")[:120]},
            "contents": {"en": _preview(text)},
            "data": {
                "type": "chat",
                "product_id": str(product_id),
                "sender_id": str(other_user_id),
            },
            "android_group": f"chat_{product_id}_{other_user_id}",
            "priority": 10,
        }

        app = current_app._get_current_object()
        thread = threading.Thread(
            target=_send_and_prune, args=(app, payload, key, player_ids), daemon=True
        )
        thread.start()
        # Returned so a short-lived process (the timer job) can wait for the
        # send before exiting. Request handlers just ignore it.
        return thread
    except Exception:
        log.warning("Could not queue deal push", exc_info=True)
    return None
