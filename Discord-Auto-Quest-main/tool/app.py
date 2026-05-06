import os
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify

from auto_quest import LonelyHub
from generate_token import DiscordLogin
from accounts import _load as legacy_load

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "change-me")

@app.context_processor
def inject_globals():
    return {"imgur_url": os.environ.get("IMGUR_IMAGE_URL", "https://i.imgur.com/test.png")}

_log_fmt = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_handler = logging.StreamHandler()
_handler.setFormatter(_log_fmt)

logger = logging.getLogger("lonely_hub")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    logger.addHandler(_handler)

def _now_iso():
    return datetime.now(timezone.utc).isoformat()

STATE = {
    "running": False,
    "logs": deque(maxlen=400),
    "current_quest": "-",
    "current_status": "Idle",
    "user": None,
    "runner": None,
    "thread": None,
    "log_paused": False,
}
LOCK = threading.Lock()

def push_log(msg, level="info"):
    _lvl = level.lower()
    if _lvl == "error":
        logger.error(msg)
    elif _lvl in ("warning", "warn"):
        logger.warning(msg)
    else:
        logger.info(msg)
    with LOCK:
        if not STATE["log_paused"]:
            STATE["logs"].append({"msg": msg, "level": _lvl, "_t": _now_iso()})

def update_quest(name, status):
    with LOCK:
        STATE["current_quest"] = name
        STATE["current_status"] = status

def run_in_background(token, user_meta):
    runner = LonelyHub(token, status_callback=push_log, quest_callback=update_quest)
    with LOCK:
        STATE["runner"] = runner
        STATE["running"] = True
        STATE["current_quest"] = "-"
        STATE["current_status"] = "Starting Auto Quest..."
        STATE["logs"].clear()
        STATE["logs"].append({"msg": "Starting Auto Quest...", "level": "info"})
        STATE["user"] = user_meta if user_meta and user_meta.get("username") else None
    info = runner.get_user_info()
    if info:
        with LOCK:
            STATE["user"] = {
                "username": info.get("username"),
                "id": info.get("id"),
                "global_name": info.get("global_name") or info.get("username"),
                "avatar": info.get("avatar"),
            }
    try:
        runner.run()
    except Exception as e:
        push_log(f"Error: {e}", "error")
    finally:
        with LOCK:
            STATE["running"] = False

@app.route("/")
def index():
    logger.info("GET / — dashboard")
    return render_template("index.html")

@app.route("/dashboard")
def dashboard():
    logger.info("GET /dashboard")
    return render_template("index.html")

@app.route("/accountmanager")
def account_manager():
    return render_template("accountmanager.html")

@app.route("/logs")
def logs_page():
    return render_template("logs.html")

@app.route("/settings")
def settings_page():
    return render_template("settings.html")

@app.route("/usercheck", methods=["GET", "POST"])
def usercheck():
    if request.method == "GET":
        return render_template(
            "usercheck.html",
            error=None, token=None, user=None,
            needs_mfa=False, ticket=None, email="",
        )
    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    code = (request.form.get("code") or "").strip()
    ticket = (request.form.get("ticket") or "").strip()
    if not email or not password:
        return render_template(
            "usercheck.html",
            error="Please enter email and password",
            token=None, user=None, needs_mfa=False, ticket=None, email=email,
        )
    login = DiscordLogin()
    if ticket and code:
        res = login.login(email, password, ticket=ticket, code=code)
    else:
        res = login.login(email, password)
    if res.get("needs_mfa"):
        return render_template(
            "usercheck.html",
            error=res.get("error"), token=None, user=None,
            needs_mfa=True, ticket=res.get("ticket"), email=email,
        )
    if not res.get("ok"):
        return render_template(
            "usercheck.html",
            error=res.get("error"), token=None, user=None,
            needs_mfa=False, ticket=None, email=email,
        )
    token = res["token"]
    info = login.get_user_info(token)
    return render_template(
        "usercheck.html",
        error=None, token=token, user=info,
        needs_mfa=False, ticket=None, email=email,
    )

@app.route("/start", methods=["POST"])
def start():
    token = (request.form.get("token") or "").strip()
    if not token:
        logger.warning("POST /start — missing token")
        return jsonify({"ok": False, "error": "Missing token"}), 400
    with LOCK:
        if STATE["running"]:
            logger.warning("POST /start — run already in progress")
            return jsonify({"ok": False, "error": "A run is already in progress"}), 400
    user_meta = {
        "username": (request.form.get("username") or "").strip(),
        "id": (request.form.get("user_id") or "").strip(),
        "global_name": (request.form.get("global_name") or "").strip()
                       or (request.form.get("username") or "").strip(),
        "avatar": (request.form.get("avatar") or "").strip() or None,
    }
    logger.info("POST /start — launching thread for user=%s", user_meta.get("username") or "unknown")
    t = threading.Thread(target=run_in_background, args=(token, user_meta), daemon=True)
    with LOCK:
        STATE["thread"] = t
    t.start()
    return jsonify({"ok": True})

@app.route("/stop", methods=["POST"])
def stop():
    with LOCK:
        runner = STATE.get("runner")
    if runner:
        runner.stop()
        push_log("Stop requested", "warning")
        logger.warning("POST /stop — stop signal sent")
    else:
        logger.info("POST /stop — no active runner")
    return jsonify({"ok": True})

@app.route("/status")
def status():
    with LOCK:
        payload = {
            "running": STATE["running"],
            "user": STATE["user"],
            "current_quest": STATE["current_quest"],
            "current_status": STATE["current_status"],
            "logs": list(STATE["logs"]),
            "log_paused": STATE["log_paused"],
        }
    logger.debug("GET /status — running=%s logs=%d", payload["running"], len(payload["logs"]))
    return jsonify(payload)

@app.route("/api/logs/clear", methods=["POST"])
def api_logs_clear():
    with LOCK:
        STATE["logs"].clear()
    logger.info("POST /api/logs/clear — logs cleared")
    return jsonify({"ok": True})

@app.route("/api/logs/pause", methods=["POST"])
def api_logs_pause():
    with LOCK:
        STATE["log_paused"] = True
    logger.info("POST /api/logs/pause — log stream paused")
    return jsonify({"ok": True})

@app.route("/api/logs/resume", methods=["POST"])
def api_logs_resume():
    with LOCK:
        STATE["log_paused"] = False
    logger.info("POST /api/logs/resume — log stream resumed")
    return jsonify({"ok": True})

@app.route("/api/login", methods=["POST"])
def api_login():
    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    code = (request.form.get("code") or "").strip()
    ticket = (request.form.get("ticket") or "").strip()
    if not email or not password:
        logger.warning("POST /api/login — missing email or password")
        return jsonify({"ok": False, "error": "Email and password are required"}), 400
    logger.info("POST /api/login — attempt for email=%s", email)
    login = DiscordLogin()
    if ticket and code:
        res = login.login(email, password, ticket=ticket, code=code)
    else:
        res = login.login(email, password)
    if res.get("needs_mfa"):
        logger.info("POST /api/login — MFA required for email=%s", email)
        return jsonify({
            "ok": False, "needs_mfa": True, "ticket": res.get("ticket"),
            "error": res.get("error") or "2FA required",
        })
    if not res.get("ok"):
        logger.error("POST /api/login — failed for email=%s: %s", email, res.get("error"))
        return jsonify({"ok": False, "error": res.get("error") or "Login failed"})
    token = res["token"]
    info = login.get_user_info(token) or {}
    logger.info("POST /api/login — success for email=%s username=%s", email, info.get("username"))
    return jsonify({
        "ok": True,
        "token": token,
        "user": {
            "id": info.get("id"),
            "username": info.get("username"),
            "global_name": info.get("global_name") or info.get("username"),
            "avatar": info.get("avatar"),
            "email": email,
        },
    })

@app.route("/api/validate-token", methods=["POST"])
def api_validate_token():
    token = (request.form.get("token") or "").strip()
    if not token:
        logger.warning("POST /api/validate-token — token missing")
        return jsonify({"ok": False, "error": "Token is required"}), 400
    logger.info("POST /api/validate-token — validating token")
    login = DiscordLogin()
    info = login.get_user_info(token)
    if not info:
        logger.error("POST /api/validate-token — token invalid or expired")
        return jsonify({"ok": False, "error": "Token is invalid or expired"})
    logger.info("POST /api/validate-token — valid for username=%s", info.get("username"))
    return jsonify({
        "ok": True,
        "user": {
            "id": info.get("id"),
            "username": info.get("username"),
            "global_name": info.get("global_name") or info.get("username"),
            "avatar": info.get("avatar"),
            "email": info.get("email"),
        },
    })

@app.route("/api/legacy-accounts")
def api_legacy_accounts():
    logger.info("GET /api/legacy-accounts")
    try:
        raw = legacy_load()
    except Exception as exc:
        logger.error("GET /api/legacy-accounts — load failed: %s", exc)
        raw = []
    out = []
    for a in raw:
        if not a.get("token"):
            continue
        out.append({
            "username": a.get("username"),
            "global_name": a.get("global_name") or a.get("username"),
            "user_id": a.get("user_id"),
            "token": a.get("token"),
            "avatar": a.get("avatar"),
            "email": a.get("email"),
        })
    logger.info("GET /api/legacy-accounts — returning %d accounts", len(out))
    return jsonify({"accounts": out})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)