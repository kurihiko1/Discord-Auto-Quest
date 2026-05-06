import os
import sys
import threading
_root = os.path.dirname(os.path.abspath(__file__))
_tool_dir = os.path.join(_root, "tool")
_bot_dir = os.path.join(_root, "bot")
for _p in (_tool_dir, _bot_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)
_env_file = os.path.join(_root, ".env")
if os.path.isfile(_env_file):
    with open(_env_file, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            _k = _k.strip()
            _v = _v.strip()
            if _k and _k not in os.environ:
                os.environ[_k] = _v

def _start_flask():
    from app import app
    port = int(os.environ.get("PORT", "5000"))
    print(f"[main] Flask web tool starting on port {port}", flush=True)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

flask_thread = threading.Thread(target=_start_flask, daemon=True, name="flask-web")
flask_thread.start()

BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()

if not BOT_TOKEN:
    print(
        "[main] DISCORD_BOT_TOKEN not set — Discord bot skipped. "
        "Web tool is running.",
        flush=True,
    )
    flask_thread.join()
else:
    print("[main] Discord bot starting...", flush=True)
    import bot as _bot_module
    _bot_module.bot.run(BOT_TOKEN)
    
    
    
    
    
    
    