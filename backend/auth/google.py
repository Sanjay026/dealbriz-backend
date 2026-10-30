"""Sign in / sign up with Google (Google Identity Services, ID-token flow).

The website (and later the Android app) gets a signed ID token from Google
and posts it to POST /api/auth/google. Here the token is checked against
Google's public keys: signature, our client id as the audience, Google as
the issuer, not expired, and a verified email. Only then is anyone signed
in. This flow needs the Client ID only. The client secret is not used and
does not need to be on the server.

Settings (names only; values live in ~/deal_briz/.env):
  GOOGLE_CLIENT_ID          the Web client id from Google Cloud Console.
                            Empty = Google sign-in is switched off.
  GOOGLE_EXTRA_CLIENT_IDS   optional, comma-separated: other client ids whose
                            tokens are accepted (e.g. the Android app's, if it
                            doesn't use the web id as its server client id).
"""
import logging
import os
import threading

log = logging.getLogger("dealbridge.auth.google")

GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")

_jwks = None
_jwks_lock = threading.Lock()


class GoogleTokenError(Exception):
    pass


def web_client_id():
    return os.environ.get("GOOGLE_CLIENT_ID", "").strip()


def accepted_client_ids():
    ids = [web_client_id()] + os.environ.get("GOOGLE_EXTRA_CLIENT_IDS", "").split(",")
    return [i.strip() for i in ids if i.strip()]


def _jwk_client():
    """Google's signing keys, fetched once and cached (they rotate rarely;
    PyJWKClient refetches when it meets a key id it doesn't know)."""
    global _jwks
    with _jwks_lock:
        if _jwks is None:
            from jwt import PyJWKClient
            try:
                _jwks = PyJWKClient(GOOGLE_CERTS_URL, cache_keys=True, lifespan=3600, timeout=5)
            except TypeError:   # an older PyJWT without the timeout argument
                _jwks = PyJWKClient(GOOGLE_CERTS_URL, cache_keys=True, lifespan=3600)
        return _jwks


def verify_id_token(token):
    """The token's claims, or GoogleTokenError. Every check is done here:
    a token that fails any of them signs nobody in."""
    import jwt
    audiences = accepted_client_ids()
    if not audiences:
        raise GoogleTokenError("Google sign-in isn't set up.")
    if not token or not isinstance(token, str) or len(token) > 8000:
        raise GoogleTokenError("Google sign-in failed. Please try again.")
    try:
        key = _jwk_client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token, key, algorithms=["RS256"], audience=audiences, leeway=60,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWKClientError as exc:
        log.error("Could not fetch Google's signing keys: %s", exc)
        raise GoogleTokenError("Google sign-in is unavailable right now. Please try again.")
    except jwt.InvalidTokenError as exc:
        log.info("Google ID token refused: %s", exc)
        raise GoogleTokenError("Google sign-in failed. Please try again.")
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise GoogleTokenError("Google sign-in failed. Please try again.")
    if claims.get("email_verified") is not True or not claims.get("email"):
        raise GoogleTokenError("Your Google account's email isn't verified, so it can't be used here.")
    return claims
