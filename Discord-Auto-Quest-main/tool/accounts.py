import json
import os
from threading import Lock

PATH = os.path.join(os.path.dirname(__file__), "accounts.json")
_lock = Lock()

def _load():
    if not os.path.exists(PATH):
        return []
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []
