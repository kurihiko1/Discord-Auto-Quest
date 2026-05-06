## Discord Auto Quest

This project is a tool for automating and managing Discord quests through a simple web dashboard. It allows you to handle multiple accounts, monitor activity, and control how the automation runs.

Made with help With AI.

---

## How it works

The application is built around a Flask-based web interface combined with a backend automation engine.

There are two main parts:

### Web dashboard

https://raw.githubusercontent.com/kurihiko1/Discord-Auto-Quest/main/dashboard.webp

The dashboard is where you interact with everything.

* Add accounts using a token or login
* Start or stop automation
* View logs in real time
* Adjust settings like delays and behavior

The goal is to keep everything simple so you can control multiple accounts without needing to touch the code.

---

### Automation engine

The backend handles the actual quest automation.

* Detects available quests
* Performs the required actions
* Applies delays and randomized timing

The delays are important to reduce the risk of hitting rate limits and to behave more like a normal user.

---

## Project structure

```
Discord-Auto-Quest/
├── main.py
├── requirements.txt
├── README.md
│
├── bot/
│   ├── bot.py
│   ├── auto_quest.py
│   └── account_store.py
│
└── tool/
    ├── app.py
    ├── auto_quest.py
    ├── accounts.py
    └── templates/
        ├── index.html
        ├── logs.html
        ├── settings.html
        └── _base_style.html
```

---

## Token storage

When you add an account through the web interface, the token is stored locally in your browser using `localStorage`.

The main keys used are:

* `lonely_hub_accounts_v1` → stores account data (including tokens)
* `lonely_hub_settings_v1` → stores settings

This means:

* Tokens are not stored on a remote server by default
* Data stays in your browser unless you remove it
* Clearing your browser storage will remove everything

---

## Managing and deleting tokens

You have full control over your data.

### From the UI

* Open the Account Manager

https://raw.githubusercontent.com/kurihiko1/Discord-Auto-Quest/main/accounts.webp

* Remove individual accounts directly

### Full reset

* Go to Settings
* Click **Clear All Data**
* This removes all accounts, settings, and cached data

### Manual removal

You can also delete everything manually from the browser.

Open Developer Tools (F12), then run:

```javascript
localStorage.removeItem('lonely_hub_accounts_v1');
localStorage.removeItem('lonely_hub_settings_v1');
```

Or clear all storage:

```javascript
localStorage.clear();
```

---

## Setup

Make sure you have Python 3.12 or newer installed.

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the app:

```bash
python main.py
```

Then open:

```
http://localhost:5000
```

---

## Notes
* been used for over 2 weeks no ban

---

## Disclaimer

This tool is intended for educational purposes.

Automating actions on Discord may violate their Terms of Service. Use it at your own risk.
