import hmac
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import abort, g, redirect, render_template, request, session, url_for
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

from core.db import get_db

MIN_PASSWORD_LENGTH = 12
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
PUBLIC_ENDPOINTS = {"login", "static"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))
_failures: dict[str, list[float]] = {}
_lock = threading.Lock()


def users():
    return get_db().users


def _norm(username: str) -> str:
    return (username or "").strip().lower()


def _fingerprint(user: dict) -> str:
    return user["password_hash"][-16:]


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")


def _update(username: str, fields: dict) -> None:
    if users().update_one({"username": _norm(username)}, {"$set": fields}).matched_count == 0:
        raise ValueError(f"No such user '{username}'.")


def create_user(username: str, password: str) -> None:
    username = _norm(username)
    if not username or len(username) > 64 or not all(c.isalnum() or c in "._-@" for c in username):
        raise ValueError("Username must be 1-64 characters: letters, digits, . _ - @")
    _check_password(password)
    try:
        users().insert_one({"username": username, "password_hash": generate_password_hash(password),
                            "active": True, "created_at": datetime.now(timezone.utc)})
    except DuplicateKeyError:
        raise ValueError(f"User '{username}' already exists.") from None


def set_password(username: str, password: str) -> None:
    _check_password(password)
    _update(username, {"password_hash": generate_password_hash(password)})


def set_active(username: str, active: bool) -> None:
    _update(username, {"active": active})


def delete_user(username: str) -> None:
    if users().delete_one({"username": _norm(username)}).deleted_count == 0:
        raise ValueError(f"No such user '{username}'.")


def list_users() -> list[dict]:
    return list(users().find({}, {"_id": 0, "password_hash": 0}).sort("username"))


def authenticate(username: str, password: str) -> dict | None:
    user = users().find_one({"username": _norm(username)})
    if user is None:
        check_password_hash(_DUMMY_HASH, password)
        return None
    if check_password_hash(user["password_hash"], password) and user.get("active", True):
        return user
    return None


def _recent(key: str, now: float) -> list[float]:
    return [t for t in _failures.get(key, []) if now - t < LOCKOUT_SECONDS]


def _locked(keys: list[str]) -> bool:
    now = time.time()
    with _lock:
        return any(len(_recent(k, now)) >= MAX_FAILURES for k in keys)


def _fail(keys: list[str]) -> None:
    now = time.time()
    with _lock:
        if len(_failures) > 10000:
            _failures.clear()
        for k in keys:
            _failures[k] = _recent(k, now) + [now]


def csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def _check_csrf() -> None:
    expected = session.get("csrf_token", "")
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    if not expected or not hmac.compare_digest(expected, sent):
        abort(400, "Invalid or missing CSRF token. Reload the page and try again.")


def _safe_next(target: str | None) -> str:
    if target and target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return url_for("landing")


def _secret_key(app) -> str:
    secret = os.environ.get("SECRET_KEY")
    if secret:
        return secret
    if not (app.debug or app.testing or os.environ.get("ALLOW_EPHEMERAL_SECRET") == "1"):
        raise RuntimeError('SECRET_KEY is not set. Generate one with: '
                           'python -c "import secrets; print(secrets.token_hex(32))"')
    app.logger.warning("SECRET_KEY not set; using a random key, sessions reset on restart.")
    return secrets.token_hex(32)


def init_app(app) -> None:
    app.config.update(
        SECRET_KEY=_secret_key(app),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "true").lower() != "false",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    try:
        users().create_index("username", unique=True)
    except Exception as exc:
        app.logger.warning("users index creation failed at startup: %s", exc)
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def _require_login():
        g.user = None
        if username := session.get("user"):
            user = users().find_one({"username": username})
            if user and user.get("active", True) and session.get("pw") == _fingerprint(user):
                g.user = username
            else:
                session.clear()
        if request.method not in SAFE_METHODS:
            _check_csrf()
        if g.user is None and request.endpoint not in PUBLIC_ENDPOINTS:
            if request.method in SAFE_METHODS and "text/html" in request.headers.get("Accept", ""):
                return redirect(url_for("login", next=request.full_path.rstrip("?")))
            abort(401)

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Content-Security-Policy",
                                "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")
        if request.is_secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if g.get("user") and "Cache-Control" not in resp.headers:
            resp.headers["Cache-Control"] = "private, no-store"
        return resp

    @app.route("/login", methods=["GET", "POST"])
    def login():
        next_url = request.values.get("next", "")
        if request.method == "GET":
            return redirect(_safe_next(next_url)) if g.user else render_template("login.html", next=next_url)
        username = _norm(request.form.get("username", ""))
        keys = [f"ip:{request.remote_addr}", f"user:{username}"]
        if _locked(keys):
            return render_template("login.html", next=next_url,
                                   error="Too many failed attempts. Try again in 15 minutes."), 429
        user = authenticate(username, request.form.get("password", ""))
        if user is None:
            _fail(keys)
            app.logger.warning("failed login for %r from %s", username, request.remote_addr)
            return render_template("login.html", next=next_url, error="Invalid username or password."), 401
        with _lock:
            for k in keys:
                _failures.pop(k, None)
        session.clear()
        session.permanent = True
        session.update(user=user["username"], pw=_fingerprint(user), csrf_token=secrets.token_urlsafe(32))
        return redirect(_safe_next(next_url))

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))
