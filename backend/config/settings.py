"""Django settings. All environment-specific values come from the .env file in the project root."""
import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
PROJECT_ROOT = BASE_DIR.parent  # kb-conflict-auditor/
load_dotenv(PROJECT_ROOT / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",  # refresh tokens that were logged out / rotated
    "accounts",
    "kb",
    "audit",
    "chat",
]

MIDDLEWARE = [
    "config.observability.RequestIdMiddleware",  # first: request ID + one JSON log line per request
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serves the admin's CSS/JS when DEBUG is off (Docker)
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "kb_auditor"),
        "USER": os.environ.get("POSTGRES_USER", "kb_auditor"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

# Password rules, checked when an account is created through the API (POST /api/auth/users/) and by
# `createsuperuser`: min 8 characters, not too similar to the username/name/email, not a common password,
# not only numbers. Existing passwords are not re-checked.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"  # filled by `collectstatic` (done automatically in Docker)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# User model: Django's built-in User (table auth_user). This is also Django's default; it is written here
# so it is visible. Extra user data (department) is in accounts.Profile (one-to-one); the role comes from
# Django groups ("auditor" / "employee"). Models link to users through settings.AUTH_USER_MODEL, so
# switching to a custom user model later only means changing this line (plus a data migration).
AUTH_USER_MODEL = "auth.User"

# API login with JWT: the React app sends "Authorization: Bearer <access token>" on every call (no cookies,
# so no CSRF token is needed for the API). Every API call needs a logged-in user unless a view says otherwise.
# Audit views additionally require the "auditor" role (accounts.permissions.IsAuditor).
# The Django admin page (/admin) still uses its own session-cookie login, with CSRF protection.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_RATES": {
        # Max chat questions per user per minute: protects the Bedrock quota.
        "chat": f"{os.environ.get('CHAT_RATE_LIMIT_PER_MIN', '20')}/min",
    },
}

# JWT tokens (djangorestframework-simplejwt), signed with DJANGO_SECRET_KEY.
# - access token: short-lived, sent with every API call
# - refresh token: used only to get a new access token. Each refresh returns a NEW refresh token and
#   blacklists the old one (rotation), so a stolen old refresh token cannot be reused.
# - logout blacklists the refresh token. Known limit: the access token keeps working until it expires
#   (at most JWT_ACCESS_MINUTES), because access tokens are not looked up in the database.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(os.environ.get("JWT_ACCESS_MINUTES") or "15")),
    "REFRESH_TOKEN_LIFETIME": timedelta(hours=int(os.environ.get("JWT_REFRESH_HOURS") or "8")),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# Shared cache for the rate-limit counters. gunicorn runs several processes; a cache in process memory
# would give each process its own counter, so the chat limit would be per process instead of per user.
# Docker sets REDIS_URL. Without it (laptop, one runserver process) the in-memory cache is fine.
# If Redis is down, chat requests fail instead of skipping the limit ("fail closed": protects the Bedrock bill).
REDIS_URL = os.environ.get("REDIS_URL", "")
if REDIS_URL:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Logs: one JSON object per line on the console (what `docker compose logs` shows), each with the request ID.
# See config/observability.py.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "config.observability.RequestIdFilter"}},
    "formatters": {"json": {"()": "config.observability.JsonFormatter"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json", "filters": ["request_id"]},
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO").upper()},
    "loggers": {
        # Django's own messages: warnings and errors only (errors include the stack trace).
        "django": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

# CSRF + sessions are still used by the Django admin page (/admin), not by the API.
# Where the React app is opened from: Vite dev server (5173) when running locally, nginx (8080) in Docker.
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CSRF_TRUSTED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080",
    ).split(",")
    if o.strip()
]
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8 hours (admin page login only)

# Account lockout (accounts/lockout.py): this many wrong passwords in a row lock the account for
# LOGIN_LOCK_MINUTES. An admin can also unlock it earlier (POST /api/users/<id>/unlock/).
LOGIN_MAX_FAILED_ATTEMPTS = int(os.environ.get("LOGIN_MAX_FAILED_ATTEMPTS") or "5")
LOGIN_LOCK_MINUTES = int(os.environ.get("LOGIN_LOCK_MINUTES") or "30")

# Size of the embedding vectors stored in the KB (must match EMBED_MODEL_ID).
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))

DATA_DIR = PROJECT_ROOT / "data"

# Uploaded documents (original files). In Docker this folder is the "uploads" volume (/app/uploads),
# shared by the backend (saves uploads) and the worker (processes them).
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR") or PROJECT_ROOT / "uploads")
# Max upload size. nginx (frontend/nginx.conf, client_max_body_size 2m) must allow at least this much.
UPLOAD_MAX_MB = float(os.environ.get("UPLOAD_MAX_MB") or "1")
