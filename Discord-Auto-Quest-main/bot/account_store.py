import json
import os
from threading import Lock

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "accounts")
_LOCK = Lock()


def _user_path(uid: int) -> str:
    os.makedirs(_DATA_DIR, exist_ok=True)
    return os.path.join(_DATA_DIR, f"{uid}.json")


class AccountStore:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.path = _user_path(user_id)

    def load_all(self) -> list[dict]:
        with _LOCK:
            return self._load_unlocked()

    def _load_unlocked(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                return []
        except Exception:
            return []

    def _save(self, accounts: list[dict]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(accounts, f, ensure_ascii=False, indent=2)

    def add(self, account: dict) -> None:
        with _LOCK:
            accounts = self._load_unlocked()
            for i, a in enumerate(accounts):
                if a.get("user_id") and a.get("user_id") == account.get("user_id"):
                    accounts[i] = account
                    self._save(accounts)
                    return
                if a.get("token") and a.get("token") == account.get("token"):
                    accounts[i] = account
                    self._save(accounts)
                    return
            accounts.append(account)
            self._save(accounts)

    def remove_by_index(self, idx: int) -> bool:
        with _LOCK:
            accounts = self._load_unlocked()
            if 0 <= idx < len(accounts):
                accounts.pop(idx)
                self._save(accounts)
                return True
            return False

    def remove_by_user_id(self, user_id: str) -> bool:
        with _LOCK:
            accounts = self._load_unlocked()
            before = len(accounts)
            accounts = [a for a in accounts if a.get("user_id") != user_id]
            if len(accounts) < before:
                self._save(accounts)
                return True
            return False
