import discord
from discord.ext import commands
from discord import app_commands
import os
import json
import threading
import asyncio
from datetime import datetime, timezone
from collections import deque

from generate_token import DiscordLogin
from auto_quest import LonelyHub
from account_store import AccountStore

BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0")) if os.environ.get("DISCORD_GUILD_ID") else None

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# key: (discord_uid, account_user_id, quest_index) -> state dict
# quest_index là số thứ tự quest (0, 1, 2, ...) để 1 acc chạy nhiều quest cùng lúc
GLOBAL_STATE: dict[tuple, dict] = {}
GLOBAL_LOCK = threading.Lock()


def _make_slot_key(uid: int, account_user_id: str, quest_idx: int) -> tuple:
    return (uid, account_user_id, quest_idx)


def _next_slot(uid: int, account_user_id: str) -> int:
    with GLOBAL_LOCK:
        idx = 0
        while (uid, account_user_id, idx) in GLOBAL_STATE and GLOBAL_STATE[(uid, account_user_id, idx)].get("running"):
            idx += 1
        return idx


def get_slot_state(uid: int, account_user_id: str, quest_idx: int) -> dict:
    key = _make_slot_key(uid, account_user_id, quest_idx)
    with GLOBAL_LOCK:
        if key not in GLOBAL_STATE:
            GLOBAL_STATE[key] = {
                "running": False,
                "logs": deque(maxlen=400),
                "current_quest": "-",
                "current_status": "Idle",
                "runner": None,
                "quest_idx": quest_idx,
                "_last_sent_log_idx": 0,
            }
        return GLOBAL_STATE[key]


def get_running_slots(uid: int, account_user_id: str) -> list[tuple[int, dict]]:
    with GLOBAL_LOCK:
        result = []
        for key, state in GLOBAL_STATE.items():
            if key[0] == uid and key[1] == account_user_id and state.get("running"):
                result.append((key[2], state))
        return sorted(result, key=lambda x: x[0])


def get_all_running(uid: int) -> list[tuple[str, int, dict]]:
    with GLOBAL_LOCK:
        result = []
        for key, state in GLOBAL_STATE.items():
            if key[0] == uid and state.get("running"):
                result.append((key[1], key[2], state))
        return sorted(result, key=lambda x: (x[0], x[1]))


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _embed_base(title: str, color: int = 0x5865F2) -> discord.Embed:
    e = discord.Embed(title=title, color=color)
    e.set_footer(text="Lonely Hub")
    e.timestamp = datetime.now(timezone.utc)
    return e


def _code_block(val: str) -> str:
    return f"```\n{val}\n```"


def _avatar_url(user_id: str, avatar_hash: str) -> str | None:
    if user_id and avatar_hash:
        ext = "gif" if avatar_hash.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{ext}?size=128"
    return None


def _nitro_type(t: int) -> str:
    return {0: "None", 1: "Nitro Classic", 2: "Nitro", 3: "Nitro Basic"}.get(t, f"Unknown ({t})")


def _created_at_from_id(user_id: str) -> str:
    try:
        ts = ((int(user_id) >> 22) + 1420070400000) / 1000
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%d/%m/%Y %H:%M UTC")
    except Exception:
        return "-"


# ─── Confirm Add View ─────────────────────────────────────────────────────────

class ConfirmAddView(discord.ui.View):
    def __init__(self, account: dict, store: AccountStore, dm_user: discord.User):
        super().__init__(timeout=60)
        self.account = account
        self.store = store
        self.dm_user = dm_user

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="confirm_add_yes")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.store.add(self.account)
        for item in self.children:
            item.disabled = True

        e = _embed_base("Account Added", 0x57F287)
        e.add_field(name="Username:", value=_code_block(self.account.get("global_name") or self.account.get("username") or "-"), inline=False)
        e.add_field(name="ID:", value=_code_block(self.account.get("user_id") or "-"), inline=False)
        avatar = _avatar_url(self.account.get("user_id", ""), self.account.get("avatar", ""))
        if avatar:
            e.set_thumbnail(url=avatar)
        await interaction.response.edit_message(embed=e, view=self)

        try:
            dm = await self.dm_user.create_dm()
            dm_e = _embed_base("Account Added Successfully", 0x57F287)
            dm_e.add_field(name="Username:", value=_code_block(self.account.get("global_name") or self.account.get("username") or "-"), inline=False)
            dm_e.add_field(name="Email:", value=_code_block(self.account.get("email") or "-"), inline=False)
            dm_e.add_field(name="Status:", value=_code_block("Saved"), inline=False)
            if avatar:
                dm_e.set_thumbnail(url=avatar)
            await dm.send(embed=dm_e)
        except Exception:
            pass

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="confirm_add_no")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        e = _embed_base("Cancelled", 0xED4245)
        e.add_field(name="Status:", value=_code_block("Account was not saved."), inline=False)
        await interaction.response.edit_message(embed=e, view=self)


def _confirm_embed(account: dict) -> discord.Embed:
    name = account.get("global_name") or account.get("username") or "Unknown"
    username = account.get("username") or "-"
    uid_val = account.get("user_id") or "-"
    email = account.get("email") or "-"
    avatar = _avatar_url(account.get("user_id", ""), account.get("avatar", ""))
    nitro = _nitro_type(account.get("premium_type") or 0)
    created = _created_at_from_id(uid_val)
    phone = "✅" if account.get("phone") else "❌"
    mfa = "✅" if account.get("mfa_enabled") else "❌"
    verified_email = "✅" if account.get("verified") else "❌"
    locale = account.get("locale") or "-"

    e = _embed_base("Confirm Add Account", 0xFEE75C)
    e.description = "Do you want to add this account?"
    e.add_field(name="Display Name:", value=_code_block(name), inline=True)
    e.add_field(name="Username:", value=_code_block(username), inline=True)
    e.add_field(name="User ID:", value=_code_block(uid_val), inline=False)
    e.add_field(name="Email:", value=_code_block(email), inline=True)
    e.add_field(name="Email Verified:", value=_code_block(verified_email), inline=True)
    e.add_field(name="Phone:", value=_code_block(phone), inline=True)
    e.add_field(name="Nitro:", value=_code_block(nitro), inline=True)
    e.add_field(name="2FA:", value=_code_block(mfa), inline=True)
    e.add_field(name="Locale:", value=_code_block(locale), inline=True)
    e.add_field(name="Account Created:", value=_code_block(created), inline=False)
    if avatar:
        e.set_thumbnail(url=avatar)
    return e


# ─── Modals ───────────────────────────────────────────────────────────────────

class EmailPasswordModal(discord.ui.Modal, title="Add Account - Email & Password"):
    email = discord.ui.TextInput(label="Email", placeholder="your@email.com", required=True)
    password = discord.ui.TextInput(label="Password", placeholder="Password", required=True, style=discord.TextStyle.short)
    mfa_code = discord.ui.TextInput(label="2FA Code (if enabled)", placeholder="123456", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id
        loop = asyncio.get_event_loop()

        def do_login():
            login = DiscordLogin()
            if self.mfa_code.value.strip():
                return None, None, "Provide email+password first, then 2FA on prompt."
            res = login.login(self.email.value.strip(), self.password.value)
            if res.get("needs_mfa"):
                return None, res.get("ticket"), res.get("error")
            if not res.get("ok"):
                return None, None, res.get("error") or "Login failed"
            token = res["token"]
            info = login.get_user_info(token) or {}
            return token, info, None

        token, info_or_ticket, err = await loop.run_in_executor(None, do_login)

        if err and info_or_ticket and isinstance(info_or_ticket, str):
            await interaction.followup.send(
                embed=_make_mfa_embed(err),
                view=MFAView(self.email.value.strip(), self.password.value, info_or_ticket),
                ephemeral=True,
            )
            return

        if err:
            e = _embed_base("Add Account Failed", 0xED4245)
            e.add_field(name="Error:", value=_code_block(err), inline=False)
            await interaction.followup.send(embed=e, ephemeral=True)
            return

        info = info_or_ticket
        store = AccountStore(uid)
        account = {
            "token": token,
            "email": self.email.value.strip(),
            "username": info.get("username", ""),
            "global_name": info.get("global_name") or info.get("username", ""),
            "user_id": info.get("id", ""),
            "avatar": info.get("avatar", ""),
            "premium_type": info.get("premium_type", 0),
            "phone": info.get("phone") or "",
            "mfa_enabled": info.get("mfa_enabled", False),
            "verified": info.get("verified", False),
            "locale": info.get("locale", ""),
        }
        await interaction.followup.send(
            embed=_confirm_embed(account),
            view=ConfirmAddView(account, store, interaction.user),
            ephemeral=True,
        )


class TokenModal(discord.ui.Modal, title="Add Account - Token"):
    token_input = discord.ui.TextInput(label="Discord Token", placeholder="Paste your token here", required=True, style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id
        token = self.token_input.value.strip()
        loop = asyncio.get_event_loop()

        def do_validate():
            login = DiscordLogin()
            return login.get_user_info(token)

        info = await loop.run_in_executor(None, do_validate)

        if not info:
            e = _embed_base("Invalid Token", 0xED4245)
            e.add_field(name="Error:", value=_code_block("Token is invalid or expired"), inline=False)
            await interaction.followup.send(embed=e, ephemeral=True)
            return

        store = AccountStore(uid)
        account = {
            "token": token,
            "email": info.get("email", ""),
            "username": info.get("username", ""),
            "global_name": info.get("global_name") or info.get("username", ""),
            "user_id": info.get("id", ""),
            "avatar": info.get("avatar", ""),
            "premium_type": info.get("premium_type", 0),
            "phone": info.get("phone") or "",
            "mfa_enabled": info.get("mfa_enabled", False),
            "verified": info.get("verified", False),
            "locale": info.get("locale", ""),
        }
        await interaction.followup.send(
            embed=_confirm_embed(account),
            view=ConfirmAddView(account, store, interaction.user),
            ephemeral=True,
        )


class MFAView(discord.ui.View):
    def __init__(self, email: str, password: str, ticket: str):
        super().__init__(timeout=180)
        self.email = email
        self.password = password
        self.ticket = ticket

    @discord.ui.button(label="Enter 2FA Code", style=discord.ButtonStyle.primary)
    async def enter_mfa(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(MFAModal(self.email, self.password, self.ticket))


class MFAModal(discord.ui.Modal, title="2FA Code"):
    code = discord.ui.TextInput(label="6-digit code", placeholder="123456", required=True, max_length=8)

    def __init__(self, email: str, password: str, ticket: str):
        super().__init__()
        self.email = email
        self.password = password
        self.ticket = ticket

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id
        loop = asyncio.get_event_loop()

        def do_mfa():
            login = DiscordLogin()
            res = login.login(self.email, self.password, ticket=self.ticket, code=self.code.value.strip())
            if not res.get("ok"):
                return None, None, res.get("error") or "2FA failed"
            token = res["token"]
            info = login.get_user_info(token) or {}
            return token, info, None

        token, info, err = await loop.run_in_executor(None, do_mfa)

        if err:
            e = _embed_base("2FA Failed", 0xED4245)
            e.add_field(name="Error:", value=_code_block(err), inline=False)
            await interaction.followup.send(embed=e, ephemeral=True)
            return

        store = AccountStore(uid)
        account = {
            "token": token,
            "email": self.email,
            "username": info.get("username", ""),
            "global_name": info.get("global_name") or info.get("username", ""),
            "user_id": info.get("id", ""),
            "avatar": info.get("avatar", ""),
            "premium_type": info.get("premium_type", 0),
            "phone": info.get("phone") or "",
            "mfa_enabled": info.get("mfa_enabled", False),
            "verified": info.get("verified", False),
            "locale": info.get("locale", ""),
        }
        await interaction.followup.send(
            embed=_confirm_embed(account),
            view=ConfirmAddView(account, store, interaction.user),
            ephemeral=True,
        )


def _make_mfa_embed(msg: str) -> discord.Embed:
    e = _embed_base("2FA Required", 0xFEE75C)
    e.add_field(name="Info:", value=_code_block(msg or "Account has 2FA enabled"), inline=False)
    return e


# ─── Account Select Menus ─────────────────────────────────────────────────────

class AccountSelectMenu(discord.ui.Select):
    def __init__(self, accounts: list[dict], action: str):
        self.action = action
        opts = []
        for i, a in enumerate(accounts[:25]):
            label = a.get("global_name") or a.get("username") or f"Account {i+1}"
            desc = a.get("email") or a.get("user_id") or ""
            opts.append(discord.SelectOption(label=label[:100], description=desc[:100], value=str(i)))
        super().__init__(placeholder="Select an account...", options=opts, min_values=1, max_values=1)
        self.accounts = accounts

    async def callback(self, interaction: discord.Interaction):
        idx = int(self.values[0])
        account = self.accounts[idx]
        uid = interaction.user.id

        if self.action == "delete":
            store = AccountStore(uid)
            store.remove_by_index(idx)
            e = _embed_base("Account Removed", 0xED4245)
            e.add_field(name="Removed:", value=_code_block(account.get("global_name") or account.get("username") or "Unknown"), inline=False)
            await interaction.response.edit_message(embed=e, view=None)

        elif self.action == "info":
            e = _embed_base("Account Info", 0x5865F2)
            e.add_field(name="Username:", value=_code_block(account.get("global_name") or account.get("username") or "-"), inline=False)
            e.add_field(name="User ID:", value=_code_block(account.get("user_id") or "-"), inline=False)
            e.add_field(name="Email:", value=_code_block(account.get("email") or "-"), inline=False)
            await interaction.response.edit_message(embed=e, view=None)


class StopQuestSelectMenu(discord.ui.Select):
    """Dropdown để chọn account, sau đó chọn quest slot cần dừng."""

    def __init__(self, uid: int, accounts: list[dict]):
        self._uid = uid
        opts = []
        for i, a in enumerate(accounts[:25]):
            label = a.get("global_name") or a.get("username") or f"Account {i+1}"
            desc = a.get("email") or a.get("user_id") or ""
            opts.append(discord.SelectOption(label=label[:100], description=desc[:100], value=str(i)))
        super().__init__(placeholder="Select account...", options=opts, min_values=1, max_values=1)
        self.accounts = accounts

    async def callback(self, interaction: discord.Interaction):
        idx = int(self.values[0])
        account = self.accounts[idx]
        acc_uid = account.get("user_id", "")
        display = account.get("global_name") or account.get("username") or "Unknown"

        running = get_running_slots(self._uid, acc_uid)
        if not running:
            e = _embed_base("Stop Auto Quest", 0xFEE75C)
            e.add_field(name="Status:", value=_code_block(f"No quest running for {display}."), inline=False)
            await interaction.response.edit_message(embed=e, view=None)
            return

        if len(running) == 1:
            quest_idx, state = running[0]
            runner = state.get("runner")
            if runner and hasattr(runner, "stop"):
                runner.stop()
            state["running"] = False
            state["runner"] = None
            e = _embed_base("Auto Quest Stopped", 0xED4245)
            e.add_field(name="Account:", value=_code_block(display), inline=False)
            e.add_field(name="Quest Slot:", value=_code_block(f"#{quest_idx + 1}"), inline=True)
            e.add_field(name="Status:", value=_code_block("Stopped"), inline=True)
            await interaction.response.edit_message(embed=e, view=None)
            return

        view = discord.ui.View(timeout=60)
        view.add_item(StopSlotSelectMenu(self._uid, acc_uid, display, running))
        e = _embed_base("Stop Auto Quest", 0xED4245)
        e.add_field(name="Account:", value=_code_block(display), inline=False)
        e.add_field(name="Running Quests:", value=_code_block(f"{len(running)} quests running"), inline=False)
        e.add_field(name="Select slot to stop:", value=_code_block("Choose from dropdown below"), inline=False)
        await interaction.response.edit_message(embed=e, view=view)


class StopSlotSelectMenu(discord.ui.Select):
    """Dropdown chọn quest slot cụ thể để dừng."""

    def __init__(self, uid: int, acc_uid: str, display: str, running: list[tuple[int, dict]]):
        self._uid = uid
        self._acc_uid = acc_uid
        self._display = display
        opts = []
        for quest_idx, state in running:
            qname = state.get("current_quest") or "-"
            status = state.get("current_status") or "Running"
            opts.append(discord.SelectOption(
                label=f"Quest #{quest_idx + 1}",
                description=f"{qname} | {status}"[:100],
                value=str(quest_idx),
            ))
        opts.append(discord.SelectOption(label="⛔ Stop ALL quests for this account", value="all"))
        super().__init__(placeholder="Select quest slot to stop...", options=opts, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        if choice == "all":
            stopped = 0
            running = get_running_slots(self._uid, self._acc_uid)
            for quest_idx, state in running:
                runner = state.get("runner")
                if runner and hasattr(runner, "stop"):
                    runner.stop()
                state["running"] = False
                state["runner"] = None
                stopped += 1
            e = _embed_base("Auto Quest Stopped", 0xED4245)
            e.add_field(name="Account:", value=_code_block(self._display), inline=False)
            e.add_field(name="Stopped:", value=_code_block(f"{stopped} quest(s)"), inline=False)
            await interaction.response.edit_message(embed=e, view=None)
        else:
            quest_idx = int(choice)
            key = _make_slot_key(self._uid, self._acc_uid, quest_idx)
            with GLOBAL_LOCK:
                state = GLOBAL_STATE.get(key)
            if state:
                runner = state.get("runner")
                if runner and hasattr(runner, "stop"):
                    runner.stop()
                state["running"] = False
                state["runner"] = None
            e = _embed_base("Auto Quest Stopped", 0xED4245)
            e.add_field(name="Account:", value=_code_block(self._display), inline=False)
            e.add_field(name="Quest Slot:", value=_code_block(f"#{quest_idx + 1}"), inline=True)
            e.add_field(name="Status:", value=_code_block("Stopped"), inline=True)
            await interaction.response.edit_message(embed=e, view=None)


class AccountsView(discord.ui.View):
    def __init__(self, accounts: list[dict]):
        super().__init__(timeout=120)
        self.accounts = accounts
        self.add_item(AccountSelectMenu(accounts, "info"))

    @discord.ui.button(label="Delete Account", style=discord.ButtonStyle.danger, row=1)
    async def delete_account(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.accounts:
            await interaction.response.send_message("No accounts.", ephemeral=True)
            return
        view = discord.ui.View(timeout=60)
        view.add_item(AccountSelectMenu(self.accounts, "delete"))
        e = _embed_base("Delete Account", 0xED4245)
        e.add_field(name="Select account to delete:", value=_code_block("Choose from the dropdown below"), inline=False)
        await interaction.response.send_message(embed=e, view=view, ephemeral=True)


# ─── JSON Import Modal ─────────────────────────────────────────────────────────

class AddAccountJsonModal(discord.ui.Modal, title="Import Accounts from JSON"):
    json_input = discord.ui.TextInput(
        label="Paste JSON array",
        style=discord.TextStyle.paragraph,
        placeholder='[{"token":"...","username":"..."}]',
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id
        raw = self.json_input.value.strip()
        try:
            data = json.loads(raw)
        except Exception as ex:
            e = _embed_base("Import Failed", 0xED4245)
            e.add_field(name="Error:", value=_code_block(f"Invalid JSON: {ex}"), inline=False)
            await interaction.followup.send(embed=e, ephemeral=True)
            return

        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            e = _embed_base("Import Failed", 0xED4245)
            e.add_field(name="Error:", value=_code_block("JSON must be an array or object"), inline=False)
            await interaction.followup.send(embed=e, ephemeral=True)
            return

        store = AccountStore(uid)
        added = 0
        skipped = 0
        loop = asyncio.get_event_loop()
        login = DiscordLogin()

        for item in data:
            if not isinstance(item, dict):
                skipped += 1
                continue
            token = (item.get("token") or "").strip()
            if not token:
                skipped += 1
                continue

            info = await loop.run_in_executor(None, lambda t=token: login.get_user_info(t))
            account = {
                "token": token,
                "email": item.get("email") or (info.get("email") if info else "") or "",
                "username": item.get("username") or (info.get("username") if info else "") or "",
                "global_name": item.get("global_name") or (info.get("global_name") if info else "") or item.get("username") or "",
                "user_id": item.get("user_id") or item.get("id") or (info.get("id") if info else "") or "",
                "avatar": item.get("avatar") or (info.get("avatar") if info else "") or "",
            }
            store.add(account)
            added += 1

        e = _embed_base("Import Complete", 0x57F287)
        e.add_field(name="Added:", value=_code_block(str(added)), inline=True)
        e.add_field(name="Skipped:", value=_code_block(str(skipped)), inline=True)
        await interaction.followup.send(embed=e, ephemeral=True)


# ─── Add Account Views ─────────────────────────────────────────────────────────

class AddAccountView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="Email & Password", style=discord.ButtonStyle.primary, custom_id="add_email_pw")
    async def email_pw(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.send_modal(EmailPasswordModal())
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="Token", style=discord.ButtonStyle.secondary, custom_id="add_token")
    async def token_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.send_modal(TokenModal())
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass


class AddAccountWithJsonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="Email & Password", style=discord.ButtonStyle.primary, custom_id="add_email_pw2")
    async def email_pw(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.send_modal(EmailPasswordModal())
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="Token", style=discord.ButtonStyle.secondary, custom_id="add_token2")
    async def token_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.send_modal(TokenModal())
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="Import JSON", style=discord.ButtonStyle.success, custom_id="add_json2")
    async def json_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.send_modal(AddAccountJsonModal())
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass


# ─── Slash Commands ────────────────────────────────────────────────────────────

@tree.command(name="add-account", description="Add a Discord account to Lonely Hub")
async def add_account(interaction: discord.Interaction):
    e = _embed_base("Add Account")
    e.add_field(name="Method:", value=_code_block("Choose how to add your account"), inline=False)
    await interaction.response.send_message(embed=e, view=AddAccountWithJsonView(), ephemeral=True)


@tree.command(name="accounts", description="View and manage your saved accounts")
async def accounts_cmd(interaction: discord.Interaction):
    uid = interaction.user.id
    store = AccountStore(uid)
    accs = store.load_all()

    if not accs:
        e = _embed_base("Accounts", 0xFEE75C)
        e.add_field(name="Status:", value=_code_block("No accounts saved. Use /add-account to add one."), inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)
        return

    e = _embed_base("Accounts")
    e.add_field(name="Total:", value=_code_block(str(len(accs))), inline=False)
    for i, a in enumerate(accs[:10]):
        label = a.get("global_name") or a.get("username") or f"Account {i+1}"
        uid_val = a.get("user_id") or "-"
        running_count = len(get_running_slots(uid, a.get("user_id", "")))
        running_tag = f" ▶ {running_count} running" if running_count else ""
        e.add_field(name=f"{i+1}. {label}{running_tag}", value=_code_block(uid_val), inline=False)
    if len(accs) > 10:
        e.add_field(name="Note:", value=_code_block(f"...and {len(accs) - 10} more"), inline=False)

    await interaction.response.send_message(embed=e, view=AccountsView(accs), ephemeral=True)


@tree.command(name="auto-quest", description="Start auto quest for a saved account (multiple allowed)")
@app_commands.describe(account="Username or index of the account to use")
async def auto_quest_cmd(interaction: discord.Interaction, account: str):
    uid = interaction.user.id
    store = AccountStore(uid)
    accs = store.load_all()

    if not accs:
        e = _embed_base("Auto Quest", 0xED4245)
        e.add_field(name="Error:", value=_code_block("No accounts saved. Use /add-account first."), inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)
        return

    selected = None
    if account.isdigit():
        idx = int(account) - 1
        if 0 <= idx < len(accs):
            selected = accs[idx]
    else:
        for a in accs:
            name = (a.get("global_name") or a.get("username") or "").lower()
            if name == account.lower() or a.get("user_id") == account:
                selected = a
                break

    if not selected:
        e = _embed_base("Auto Quest", 0xED4245)
        e.add_field(name="Error:", value=_code_block(f"Account not found: {account}"), inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)
        return

    token = selected["token"]
    acc_uid = selected.get("user_id", "")
    display = selected.get("global_name") or selected.get("username") or "Unknown"

    quest_idx = _next_slot(uid, acc_uid)
    state = get_slot_state(uid, acc_uid, quest_idx)

    e = _embed_base("Auto Quest Started", 0x57F287)
    e.add_field(name="Account:", value=_code_block(display), inline=False)
    e.add_field(name="Quest Slot:", value=_code_block(f"#{quest_idx + 1}"), inline=True)
    e.add_field(name="Status:", value=_code_block("Running..."), inline=True)
    await interaction.response.send_message(embed=e, ephemeral=True)

    async def run_quest():
        loop = asyncio.get_event_loop()
        dm_channel = None
        try:
            dm_channel = await interaction.user.create_dm()
        except Exception:
            pass

        def push_log(msg: str, level: str = "info"):
            state["logs"].append({"msg": msg, "level": level.lower(), "_t": _now_iso()})

        def quest_cb(name: str, status: str):
            state["current_quest"] = name
            state["current_status"] = status

        runner = LonelyHub(token, status_callback=push_log, quest_callback=quest_cb)
        state["runner"] = runner
        state["running"] = True
        state["logs"].clear()
        state["_last_sent_log_idx"] = 0

        def do_run():
            return runner.run()

        async def _send_log_snapshot(dm: discord.DMChannel, label: str, slot: int, final: bool = False):
            all_logs = list(state["logs"])
            last_idx = state.get("_last_sent_log_idx", 0)
            new_logs = all_logs[last_idx:]
            if not new_logs and not final:
                return
            state["_last_sent_log_idx"] = len(all_logs)

            title = "Auto Quest - Log Update" if not final else "Auto Quest Finished"
            color = 0x5865F2 if not final else 0x57F287
            e = _embed_base(title, color)
            e.add_field(name="Account:", value=_code_block(label), inline=True)
            e.add_field(name="Quest Slot:", value=_code_block(f"#{slot + 1}"), inline=True)
            e.add_field(name="Status:", value=_code_block(state.get("current_status") or "-"), inline=True)
            e.add_field(name="Current Quest:", value=_code_block(state.get("current_quest") or "-"), inline=False)

            if new_logs:
                info_lines = [l["msg"] for l in new_logs if l["level"] == "info"]
                warn_lines = [l["msg"] for l in new_logs if l["level"] in ("warning", "warn")]
                err_lines  = [l["msg"] for l in new_logs if l["level"] == "error"]
                if info_lines:
                    e.add_field(name="Info:", value=_code_block("\n".join(info_lines[-10:])[:900]), inline=False)
                if warn_lines:
                    e.add_field(name="Warning:", value=_code_block("\n".join(warn_lines[-5:])[:900]), inline=False)
                if err_lines:
                    e.add_field(name="Error:", value=_code_block("\n".join(err_lines[-5:])[:900]), inline=False)
            elif final:
                e.add_field(name="Logs:", value=_code_block("No new logs."), inline=False)

            try:
                await dm.send(embed=e)
            except Exception:
                pass

        async def _log_broadcast_loop(dm: discord.DMChannel, label: str, slot: int, interval: float = 10.0):
            while state.get("running"):
                await asyncio.sleep(interval)
                if not state.get("running"):
                    break
                await _send_log_snapshot(dm, label, slot, final=False)

        broadcast_task = None
        if dm_channel:
            broadcast_task = asyncio.create_task(_log_broadcast_loop(dm_channel, display, quest_idx))

        try:
            await loop.run_in_executor(None, do_run)
        except Exception as ex:
            push_log(f"Fatal error: {ex}", "error")
        finally:
            state["running"] = False
            state["runner"] = None

        if broadcast_task is not None:
            broadcast_task.cancel()
            try:
                await broadcast_task
            except asyncio.CancelledError:
                pass

        if dm_channel:
            await _send_log_snapshot(dm_channel, display, quest_idx, final=True)

    asyncio.create_task(run_quest())


@auto_quest_cmd.autocomplete("account")
async def auto_quest_autocomplete(interaction: discord.Interaction, current: str):
    uid = interaction.user.id
    store = AccountStore(uid)
    accs = store.load_all()
    results = []
    for i, a in enumerate(accs):
        name = a.get("global_name") or a.get("username") or f"Account {i+1}"
        if current.lower() in name.lower() or current == "":
            results.append(app_commands.Choice(name=name, value=name))
    return results[:25]


@tree.command(name="stop-autoquest", description="Stop running auto quest(s) for a selected account")
async def stop_autoquest_cmd(interaction: discord.Interaction):
    uid = interaction.user.id
    store = AccountStore(uid)
    accs = store.load_all()

    if not accs:
        e = _embed_base("Stop Auto Quest", 0xFEE75C)
        e.add_field(name="Status:", value=_code_block("No accounts saved."), inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)
        return

    all_running = get_all_running(uid)
    if not all_running:
        e = _embed_base("Stop Auto Quest", 0xFEE75C)
        e.add_field(name="Status:", value=_code_block("No quests are currently running."), inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)
        return

    running_acc_ids = {r[0] for r in all_running}
    running_accs = [a for a in accs if a.get("user_id", "") in running_acc_ids]

    view = discord.ui.View(timeout=60)
    view.add_item(StopQuestSelectMenu(uid, running_accs))

    total = len(all_running)
    e = _embed_base("Stop Auto Quest", 0xED4245)
    e.add_field(name="Running Quests:", value=_code_block(f"{total} quest(s) running across {len(running_accs)} account(s)"), inline=False)
    e.add_field(name="Select account:", value=_code_block("Choose from dropdown below"), inline=False)
    await interaction.response.send_message(embed=e, view=view, ephemeral=True)


@tree.command(name="logs", description="View recent logs for a running quest")
@app_commands.describe(account="Username or index of the account", level="Filter by log level")
@app_commands.choices(level=[
    app_commands.Choice(name="All", value="all"),
    app_commands.Choice(name="Info", value="info"),
    app_commands.Choice(name="Warning", value="warning"),
    app_commands.Choice(name="Error", value="error"),
])
async def logs_cmd(interaction: discord.Interaction, account: str = "", level: str = "all"):
    uid = interaction.user.id
    store = AccountStore(uid)
    accs = store.load_all()

    all_running = get_all_running(uid)

    if not all_running:
        e = _embed_base("Logs", 0xFEE75C)
        e.add_field(name="Status:", value=_code_block("No quests running."), inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)
        return

    selected_acc = None
    if account:
        for a in accs:
            name = (a.get("global_name") or a.get("username") or "").lower()
            if name == account.lower() or a.get("user_id") == account or account.isdigit() and accs.index(a) == int(account) - 1:
                selected_acc = a
                break

    if selected_acc:
        acc_uid = selected_acc.get("user_id", "")
        slots = get_running_slots(uid, acc_uid)
    else:
        acc_uid = all_running[0][0]
        slots = [(all_running[0][1], all_running[0][2])]

    e = _embed_base("Logs")
    e.add_field(name="Running Slots:", value=_code_block(str(len(slots))), inline=True)

    for quest_idx, state in slots[:3]:
        all_logs = list(state["logs"])
        if level != "all":
            all_logs = [l for l in all_logs if l["level"] == level]
        recent = all_logs[-20:]

        e.add_field(
            name=f"── Quest Slot #{quest_idx + 1} ──",
            value=_code_block(f"Status: {state.get('current_status') or 'Idle'} | Quest: {state.get('current_quest') or '-'}"),
            inline=False,
        )
        if not recent:
            e.add_field(name="Logs:", value=_code_block("No logs."), inline=False)
        else:
            info_lines = [l["msg"] for l in recent if l["level"] == "info"]
            warn_lines = [l["msg"] for l in recent if l["level"] in ("warning", "warn")]
            err_lines = [l["msg"] for l in recent if l["level"] == "error"]
            if info_lines:
                e.add_field(name="Info:", value=_code_block("\n".join(info_lines[-8:])[:900]), inline=False)
            if warn_lines:
                e.add_field(name="Warning:", value=_code_block("\n".join(warn_lines[-4:])[:900]), inline=False)
            if err_lines:
                e.add_field(name="Error:", value=_code_block("\n".join(err_lines[-4:])[:900]), inline=False)

    await interaction.response.send_message(embed=e, ephemeral=True)


# ─── Get Token ────────────────────────────────────────────────────────────────

class GetTokenModal(discord.ui.Modal, title="Get Token - Email & Password"):
    email = discord.ui.TextInput(label="Email", placeholder="your@email.com", required=True)
    password = discord.ui.TextInput(label="Password", placeholder="Password", required=True, style=discord.TextStyle.short)
    mfa_code = discord.ui.TextInput(label="2FA Code (if enabled)", placeholder="123456", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        loop = asyncio.get_event_loop()

        def do_login():
            login = DiscordLogin()
            res = login.login(self.email.value.strip(), self.password.value)
            if res.get("needs_mfa"):
                return None, res.get("ticket"), res.get("error")
            if not res.get("ok"):
                return None, None, res.get("error") or "Login failed"
            token = res["token"]
            info = login.get_user_info(token) or {}
            return token, info, None

        token, info_or_ticket, err = await loop.run_in_executor(None, do_login)

        if err and info_or_ticket and isinstance(info_or_ticket, str):
            await interaction.followup.send(
                embed=_make_mfa_embed(err),
                view=GetTokenMFAView(self.email.value.strip(), self.password.value, info_or_ticket),
                ephemeral=True,
            )
            return

        if err:
            e = _embed_base("Get Token Failed", 0xED4245)
            e.add_field(name="Error:", value=_code_block(err), inline=False)
            await interaction.followup.send(embed=e, ephemeral=True)
            return

        info = info_or_ticket
        account = {
            "token": token,
            "email": self.email.value.strip(),
            "username": info.get("username", ""),
            "global_name": info.get("global_name") or info.get("username", ""),
            "user_id": info.get("id", ""),
            "avatar": info.get("avatar", ""),
            "premium_type": info.get("premium_type", 0),
            "phone": info.get("phone") or "",
            "mfa_enabled": info.get("mfa_enabled", False),
            "verified": info.get("verified", False),
            "locale": info.get("locale", ""),
        }
        await interaction.followup.send(embed=_token_info_embed(account), ephemeral=True)


class GetTokenMFAView(discord.ui.View):
    def __init__(self, email: str, password: str, ticket: str):
        super().__init__(timeout=180)
        self.email = email
        self.password = password
        self.ticket = ticket

    @discord.ui.button(label="Enter 2FA Code", style=discord.ButtonStyle.primary)
    async def enter_mfa(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GetTokenMFAModal(self.email, self.password, self.ticket))


class GetTokenMFAModal(discord.ui.Modal, title="2FA Code"):
    code = discord.ui.TextInput(label="6-digit code", placeholder="123456", required=True, max_length=8)

    def __init__(self, email: str, password: str, ticket: str):
        super().__init__()
        self.email = email
        self.password = password
        self.ticket = ticket

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        loop = asyncio.get_event_loop()

        def do_mfa():
            login = DiscordLogin()
            res = login.login(self.email, self.password, ticket=self.ticket, code=self.code.value.strip())
            if not res.get("ok"):
                return None, None, res.get("error") or "2FA failed"
            token = res["token"]
            info = login.get_user_info(token) or {}
            return token, info, None

        token, info, err = await loop.run_in_executor(None, do_mfa)

        if err:
            e = _embed_base("2FA Failed", 0xED4245)
            e.add_field(name="Error:", value=_code_block(err), inline=False)
            await interaction.followup.send(embed=e, ephemeral=True)
            return

        account = {
            "token": token,
            "email": self.email,
            "username": info.get("username", ""),
            "global_name": info.get("global_name") or info.get("username", ""),
            "user_id": info.get("id", ""),
            "avatar": info.get("avatar", ""),
            "premium_type": info.get("premium_type", 0),
            "phone": info.get("phone") or "",
            "mfa_enabled": info.get("mfa_enabled", False),
            "verified": info.get("verified", False),
            "locale": info.get("locale", ""),
        }
        await interaction.followup.send(embed=_token_info_embed(account), ephemeral=True)


def _token_info_embed(account: dict) -> discord.Embed:
    name = account.get("global_name") or account.get("username") or "Unknown"
    username = account.get("username") or "-"
    uid_val = account.get("user_id") or "-"
    email = account.get("email") or "-"
    token = account.get("token") or "-"
    avatar = _avatar_url(account.get("user_id", ""), account.get("avatar", ""))
    nitro = _nitro_type(account.get("premium_type") or 0)
    created = _created_at_from_id(uid_val)
    phone = "✅" if account.get("phone") else "❌"
    mfa = "✅" if account.get("mfa_enabled") else "❌"
    verified_email = "✅" if account.get("verified") else "❌"
    locale = account.get("locale") or "-"

    e = _embed_base("Token Info", 0x5865F2)
    e.add_field(name="Display Name:", value=_code_block(name), inline=True)
    e.add_field(name="Username:", value=_code_block(username), inline=True)
    e.add_field(name="User ID:", value=_code_block(uid_val), inline=False)
    e.add_field(name="Email:", value=_code_block(email), inline=True)
    e.add_field(name="Email Verified:", value=_code_block(verified_email), inline=True)
    e.add_field(name="Phone:", value=_code_block(phone), inline=True)
    e.add_field(name="Nitro:", value=_code_block(nitro), inline=True)
    e.add_field(name="2FA:", value=_code_block(mfa), inline=True)
    e.add_field(name="Locale:", value=_code_block(locale), inline=True)
    e.add_field(name="Account Created:", value=_code_block(created), inline=False)
    e.add_field(name="Token:", value=f"```\n||{token}||\n```", inline=False)
    e.add_field(name="⚠️ Do not share your token!", value="```\nAnyone with your token has full access to your account.\n```", inline=False)
    if avatar:
        e.set_thumbnail(url=avatar)
    return e


@tree.command(name="get-token", description="Login and retrieve your Discord token")
async def get_token_cmd(interaction: discord.Interaction):
    await interaction.response.send_modal(GetTokenModal())


# ─── Help ─────────────────────────────────────────────────────────────────────

@tree.command(name="help", description="Show all available commands")
async def help_cmd(interaction: discord.Interaction):
    e = _embed_base("Lonely Hub - Help", 0x5865F2)
    e.description = "List of all available commands:"
    e.add_field(
        name="👤 Account Management",
        value=(
            "`/add-account` — Add a Discord account (Email/Password or Token or JSON)\n"
            "`/accounts` — View and manage saved accounts\n"
            "`/get-token` — Login and retrieve your Discord token"
        ),
        inline=False,
    )
    e.add_field(
        name="⚔️ Auto Quest",
        value=(
            "`/auto-quest <account>` — Start auto quest (multiple quests per account supported)\n"
            "`/stop-autoquest` — Stop one or all running quests for an account"
        ),
        inline=False,
    )
    e.add_field(
        name="📋 Logs",
        value="`/logs [account] [level]` — View recent logs (all / info / warning / error)",
        inline=False,
    )
    e.add_field(
        name="❓ Other",
        value="`/help` — Show this help menu",
        inline=False,
    )
    e.set_footer(text="Lonely Hub • All commands are ephemeral (only visible to you)")
    await interaction.response.send_message(embed=e, ephemeral=True)


# ─── Events ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    print(f"Logged in as {bot.user} | Commands synced")


if __name__ == "__main__":
    bot.run(BOT_TOKEN)