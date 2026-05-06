import requests

class DiscordLogin:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://discord.com",
            "Referer": "https://discord.com/login",
        })

    def get_fingerprint(self):
        try:
            r = self.session.get("https://discord.com/api/v9/experiments", timeout=15)
            if r.status_code == 200:
                return r.json().get("fingerprint")
        except Exception:
            return None
        return None

    def login(self, email, password, ticket=None, code=None):
        fingerprint = self.get_fingerprint()
        if not fingerprint:
            return {"ok": False, "error": "Could not get fingerprint from Discord"}
        headers = {
            "Content-Type": "application/json",
            "X-Fingerprint": fingerprint,
        }
        if ticket and code:
            payload = {"ticket": ticket, "code": str(code), "login_source": None, "gift_code_sku_id": None}
            url = "https://discord.com/api/v9/auth/mfa/totp"
        else:
            payload = {
                "login": email,
                "password": password,
                "undelete": False,
                "captcha_key": None,
                "login_source": None,
                "gift_code_sku_id": None,
            }
            url = "https://discord.com/api/v9/auth/login"
        try:
            r = self.session.post(url, json=payload, headers=headers, timeout=20)
        except Exception as e:
            return {"ok": False, "error": f"Connection error: {e}"}
        if r.status_code == 200:
            data = r.json()
            if data.get("token"):
                return {"ok": True, "token": data["token"]}
            if data.get("mfa") and data.get("ticket"):
                return {"ok": False, "needs_mfa": True, "ticket": data["ticket"], "error": "Account has 2FA enabled, need 6-digit code"}
            return {"ok": False, "error": "Token not found in response"}
        try:
            data = r.json()
        except Exception:
            data = {}
        if data.get("captcha_key"):
            return {"ok": False, "error": "Discord requires captcha (hCaptcha). Log in via browser and paste the token on the main page."}
        if data.get("mfa") and data.get("ticket"):
            return {"ok": False, "needs_mfa": True, "ticket": data["ticket"], "error": "Account has 2FA enabled, need 6-digit code"}
        msg = data.get("message") or data.get("login") or data.get("password") or data
        return {"ok": False, "error": f"Login failed (HTTP {r.status_code}): {msg}"}

    def get_user_info(self, token):
        try:
            r = requests.get(
                "https://discord.com/api/v9/users/@me",
                headers={"Authorization": token},
                timeout=15,
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            return None
        return None
