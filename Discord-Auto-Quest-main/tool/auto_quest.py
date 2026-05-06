import base64
import json
import math
import random
import time
import uuid
from datetime import datetime, timezone
import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) discord/1.0.9215 Chrome/138.0.7204.251 "
    "Electron/37.6.0 Safari/537.36"
)

CLIENT_PROPS = {
    "os": "Windows",
    "browser": "Discord Client",
    "release_channel": "stable",
    "client_version": "1.0.9215",
    "os_version": "10.0.19045",
    "os_arch": "x64",
    "app_arch": "x64",
    "system_locale": "en-US",
    "has_client_mods": False,
    "client_launch_id": str(uuid.uuid4()),
    "browser_user_agent": USER_AGENT,
    "browser_version": "37.6.0",
    "os_sdk_version": "19045",
    "client_build_number": 471091,
    "native_build_number": 72186,
    "client_event_source": None,
    "launch_signature": str(uuid.uuid4()),
    "client_heartbeat_session_id": str(uuid.uuid4()),
    "client_app_state": "focused",
}
EXCLUDED_QUEST_ID = "1412491570820812933"
def _super_properties() -> str:
    return base64.b64encode(json.dumps(CLIENT_PROPS).encode("utf-8")).decode("ascii")

def _parse_iso(ts: str) -> float:
    if not ts:
        return 0.0
    try:
        s = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        try:
            return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            ).timestamp()
        except Exception:
            return 0.0
class Quest:

    TASK_ORDER = (
        "WATCH_VIDEO",
        "PLAY_ON_DESKTOP",
        "STREAM_ON_DESKTOP",
        "PLAY_ACTIVITY",
        "WATCH_VIDEO_ON_MOBILE",
    )

    def __init__(self, raw: dict):
        self.raw = raw

    @property
    def id(self) -> str:
        return self.raw.get("id", "")

    @property
    def config(self) -> dict:
        return self.raw.get("config") or {}

    @property
    def user_status(self) -> dict:
        return self.raw.get("user_status") or {}

    def refresh_status(self, status: dict | None) -> None:
        self.raw["user_status"] = status or {}

    @property
    def name(self) -> str:
        msgs = self.config.get("messages") or {}
        n = (msgs.get("quest_name") or "").strip()
        return n or self.id

    def is_expired(self) -> bool:
        exp = (self.config.get("expires_at") or "").strip()
        if not exp:
            return False
        return time.time() > _parse_iso(exp)

    def is_completed(self) -> bool:
        return bool(self.user_status.get("completed_at"))

    def is_enrolled(self) -> bool:
        return bool(self.user_status.get("enrolled_at"))

    def is_claimed(self) -> bool:
        return bool(self.user_status.get("claimed_at"))

    def detect_task_type(self) -> str | None:
        tasks = (self.config.get("task_config") or {}).get("tasks") or {}
        if not tasks:
            return None
        for t in self.TASK_ORDER:
            if tasks.get(t) is not None:
                return t
        return None

    def get_target(self) -> int:
        tt = self.detect_task_type()
        if not tt:
            return 900
        tasks = (self.config.get("task_config") or {}).get("tasks") or {}
        return int((tasks.get(tt) or {}).get("target") or 900)

    def get_progress(self) -> int:
        tt = self.detect_task_type()
        if not tt:
            return 0
        prog = (self.user_status.get("progress") or {}).get(tt) or {}
        try:
            return int(prog.get("value") or 0)
        except Exception:
            return 0

    def get_reward_label(self) -> str:
        rewards = ((self.config.get("rewards_config") or {}).get("rewards")) or []
        if not rewards:
            return "Unknown"
        first = rewards[0] or {}
        if first.get("orb_quantity"):
            return f"{first['orb_quantity']} Orbs"
        return ((first.get("messages") or {}).get("name")) or "Unknown"

class LonelyHub:
    BASE = "https://discord.com/api/v10"
    def __init__(self, token: str, status_callback=None, quest_callback=None):
        self.token = (token or "").strip()
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": self.token,
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Accept-Language": "vi",
            "Origin": "https://discord.com",
            "Pragma": "no-cache",
            "Priority": "u=1, i",
            "Referer": "https://discord.com/channels/@me",
            "Sec-Ch-Ua": '"Not)A;Brand";v="8", "Chromium";v="138"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-Debug-Options": "bugReporterEnabled",
            "X-Discord-Locale": "en-US",
            "X-Discord-Timezone": "Asia/Saigon",
            "X-Super-Properties": _super_properties(),
        })
        self.status_callback = status_callback or (lambda m: None)
        self.quest_callback = quest_callback or (lambda n, s: None)
        self.user_info: dict | None = None
        self.quests: list[Quest] = []
        self.current_quest_name = "-"
        self.stop_flag = False

    def log(self, msg: str) -> None:
        print(f"[LonelyHub] {msg}", flush=True)
        try:
            self.status_callback(msg)
        except Exception:
            pass
    def update_quest(self, name: str, status: str) -> None:
        self.current_quest_name = name
        try:
            self.quest_callback(name, status)
        except Exception:
            pass
    def stop(self) -> None:
        self.stop_flag = True
    def _sleep(self, seconds: float) -> None:
        end = time.time() + max(0.0, seconds)
        while time.time() < end:
            if self.stop_flag:
                return
            time.sleep(min(0.5, end - time.time()))
    def _get(self, path: str, **kw):
        return self.session.get(f"{self.BASE}{path}", timeout=20, **kw)
    def _post(self, path: str, body=None, **kw):
        return self.session.post(
            f"{self.BASE}{path}",
            data=json.dumps(body or {}),
            timeout=20,
            **kw,
        )

    def get_user_info(self) -> dict | None:
        try:
            r = self._get("/users/@me")
            if r.status_code == 200:
                self.user_info = r.json()
                return self.user_info
            self.log(f"Invalid token: HTTP {r.status_code}")
        except Exception as e:
            self.log(f"Connection error: {e}")
        return None

    def load_quests(self) -> list[Quest]:
        try:
            r = self._get("/quests/@me")
            if r.status_code != 200:
                self.log(f"Failed to fetch quests: HTTP {r.status_code}")
                return []
            data = r.json() or {}
            self.quests = [Quest(q) for q in (data.get("quests") or [])]
            return self.quests
        except Exception as e:
            self.log(f"Quest fetch error: {e}")
            return []

    def get_balance(self) -> dict | None:
        try:
            r = self._get("/users/@me/virtual-currency/balance")
            if r.status_code == 200:
                return r.json()
        except Exception:
            return None
        return None

    def enroll(self, quest: Quest) -> bool:
        try:
            r = self._post(
                f"/quests/{quest.id}/enroll",
                {"location": 11, "is_targeted": False, "metadata_raw": None},
            )
            if r.status_code in (200, 201, 204):
                try:
                    quest.refresh_status(r.json())
                except Exception:
                    pass
                return True
            self.log(
                f"[{quest.name}] Enroll HTTP {r.status_code}: {r.text[:160]}"
            )
            return False
        except Exception as e:
            self.log(f"[{quest.name}] Enroll error: {e}")
            return False

    def claim_reward(self, quest_id: str):
        try:
            r = self._post(f"/quests/{quest_id}/claim-reward", {})
            if r.status_code in (200, 201, 204):
                try:
                    return r.json()
                except Exception:
                    return {}
            self.log(f"Claim HTTP {r.status_code}: {r.text[:160]}")
            return None
        except Exception as e:
            self.log(f"Claim error: {e}")
            return None

    def pending(self) -> list[Quest]:
        return [
            q for q in self.quests
            if q.id != EXCLUDED_QUEST_ID and not q.is_completed() and not q.is_expired()
        ]

    def claimable(self) -> list[Quest]:
        return [q for q in self.quests if q.is_completed() and not q.is_claimed()]

    def execute(self, quest: Quest) -> bool:
        label = quest.name
        task_type = quest.detect_task_type()
        if not task_type:
            self.update_quest(label, "Unknown quest type, skipping")
            self.log(f"[{label}] No supported task type")
            return False

        self.update_quest(label, f"Starting ({task_type})")
        self.log(f"[{label}] Task type: {task_type}")

        if not quest.is_enrolled():
            if self.enroll(quest):
                self.log(f"[{label}] Enrolled")
            else:
                self.log(f"[{label}] Enroll skipped or failed")
        target = quest.get_target()
        done = quest.get_progress()
        if task_type in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
            enrolled_iso = (quest.user_status.get("enrolled_at") or "")
            enrolled_at = _parse_iso(enrolled_iso) if enrolled_iso else time.time()
            finished = False
            self.update_quest(label, f"Watching video {done}/{target}s")
            while not self.stop_flag:
                max_allowed = math.floor(time.time() - enrolled_at) + 10
                diff = max_allowed - done
                next_ts = done + 7
                if diff >= 7:
                    try:
                        body = {"timestamp": min(target, next_ts + random.random())}
                        r = self._post(f"/quests/{quest.id}/video-progress", body)
                        if r.status_code in (200, 201, 204):
                            try:
                                resp = r.json()
                                if resp.get("completed_at") is not None:
                                    finished = True
                            except Exception:
                                pass
                            done = min(target, next_ts)
                            self.update_quest(label, f"Watching video {done}/{target}s")
                            self.log(f"[{label}] video-progress {done}/{target}s")
                        else:
                            self.log(
                                f"[{label}] video-progress HTTP {r.status_code}: "
                                f"{r.text[:160]}"
                            )
                            if r.status_code in (400, 401, 403):
                                return False
                    except Exception as e:
                        self.log(f"[{label}] video-progress error: {e}")

                if next_ts >= target:
                    break
                self._sleep(1.0)

            if not finished and not self.stop_flag:
                try:
                    self._post(
                        f"/quests/{quest.id}/video-progress",
                        {"timestamp": target},
                    )
                except Exception:
                    pass
        elif task_type == "PLAY_ON_DESKTOP":
            app_id = ((quest.config.get("application") or {}).get("id")) or ""
            self.update_quest(label, f"Playing 0/{target}s")
            while not quest.is_completed() and not self.stop_flag:
                try:
                    r = self._post(
                        f"/quests/{quest.id}/heartbeat",
                        {"application_id": app_id, "terminal": False},
                    )
                    if r.status_code in (200, 201, 204):
                        try:
                            quest.refresh_status(r.json())
                        except Exception:
                            pass
                        prog = quest.get_progress()
                        self.update_quest(label, f"Playing {prog}/{target}s")
                        self.log(f"[{label}] heartbeat {prog}/{target}s")
                    else:
                        self.log(
                            f"[{label}] heartbeat HTTP {r.status_code}: "
                            f"{r.text[:160]}"
                        )
                        if r.status_code in (400, 401, 403):
                            return False
                except Exception as e:
                    self.log(f"[{label}] heartbeat error: {e}")
                self._sleep(60.0)

            if not self.stop_flag:
                try:
                    r = self._post(
                        f"/quests/{quest.id}/heartbeat",
                        {"application_id": app_id, "terminal": True},
                    )
                    if r.status_code in (200, 201, 204):
                        try:
                            quest.refresh_status(r.json())
                        except Exception:
                            pass
                except Exception:
                    pass
        else:
            self.update_quest(label, f"Task {task_type} is not supported by this engine")
            self.log(f"[{label}] Unsupported task type: {task_type}")
            return False

        if self.stop_flag:
            self.update_quest(label, "Stopped")
            return False

        self.update_quest(label, "Claiming reward...")
        result = self.claim_reward(quest.id)
        if result is not None:
            self.update_quest(label, "Reward claimed successfully")
            self.log(f"[{label}] Reward claimed - {quest.get_reward_label()}")
            return True
        self.update_quest(label, "Could not claim reward (requirements not met)")
        return False

    def run(self) -> None:
        self.log("Starting Musashi Auto Quest...")
        info = self.get_user_info()
        if not info:
            self.log("Stopping: invalid token")
            return
        self.log(f"Logged in as: {info.get('username')} ({info.get('id')})")

        self.load_quests()
        if not self.quests:
            self.log("No quests available")
            self.update_quest("-", "No quests available")
            return

        pending = self.pending()
        self.log(f"Found {len(self.quests)} quest(s), {len(pending)} pending")
        if not pending:
            for q in self.claimable():
                if self.stop_flag:
                    break
                self.update_quest(q.name, "Claiming pending reward...")
                self.claim_reward(q.id)
            self.update_quest("-", "All quests already completed")
            return
        done = 0
        for q in pending:
            if self.stop_flag:
                break
            try:
                if self.execute(q):
                    done += 1
            except Exception as e:
                self.log(f"[{q.name}] Quest error: {e}")

        self.log(f"Finished: {done}/{len(pending)} quest(s) claimed")
        self.update_quest("-", f"Finished: {done}/{len(pending)} quest(s) claimed")
class AutoQuest(LonelyHub):
    pass