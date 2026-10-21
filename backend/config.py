import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()  # reads variables from a .env file in the same folder, if present

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-secret-key")
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "change-this-jwt-secret")

    DB_USER = os.environ.get("DB_USER", "root")
    DB_PASS = os.environ.get("DB_PASS", "")
    DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
    DB_NAME = os.environ.get("DB_NAME", "dealbridge")

    # ---- JWT in httpOnly cookies, not localStorage ----------------
    # A token in localStorage is readable by any JavaScript on the page, so
    # a single XSS hole anywhere becomes full account takeover. An httpOnly
    # cookie cannot be read by script at all, which removes that entire
    # class of risk rather than patching individual holes.
    #
    # The trade is CSRF: cookies are sent automatically, so a hostile site
    # could trigger authenticated requests. flask-jwt-extended's
    # double-submit CSRF tokens close that, which is why the frontend sends
    # an X-CSRF-TOKEN header.
    JWT_TOKEN_LOCATION = ["cookies"]
    JWT_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")
    JWT_COOKIE_SAMESITE = os.environ.get("COOKIE_SAMESITE", "Lax")
    JWT_COOKIE_CSRF_PROTECT = True
    JWT_ACCESS_COOKIE_PATH = "/api"
    JWT_REFRESH_COOKIE_PATH = "/api/auth"     # only sent to the refresh route
    JWT_CSRF_IN_COOKIES = True

    # Not set here before, so flask-jwt-extended's default of True applied and
    # every auth cookie went out with no Max-Age. A cookie without Max-Age is a
    # session cookie: Android's WebView drops it when the app process dies, so
    # the Android app asked for a login on every cold start. False gives the
    # cookies a real expiry, which the WebView persists to disk and restores on
    # the next launch.
    JWT_SESSION_COOKIE = False

    # Short access token, long refresh token. A stolen access token is
    # useless within the hour; the refresh token is revocable server-side,
    # so "log out everywhere" actually works.
    #
    # The refresh window is what decides how long someone stays signed in, since
    # the app spends the refresh cookie automatically whenever the access token
    # has lapsed. 180 days is the "six months without logging in" target.
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=int(os.environ.get("ACCESS_TOKEN_MINUTES", 30)))
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=int(os.environ.get("REFRESH_TOKEN_DAYS", 180)))

    DB_PORT = os.environ.get("DB_PORT", "3306")
    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        "?charset=utf8mb4"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Connection pooling matters on RDS. Without pre_ping, a connection
    # dropped by RDS (failover, idle timeout, maintenance) is handed to the
    # app and fails the request — the classic "MySQL server has gone away".
    # recycle keeps connections below RDS's own wait_timeout.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE", 280)),
        "pool_size": int(os.environ.get("DB_POOL_SIZE", 5)),
        "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", 10)),
        "pool_timeout": 30,
    }

    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "uploads")
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB
