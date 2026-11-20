# GEOHARVEST — Consent Edition

A Flask/SQLite + Telegram location-sharing application that works **only after explicit user consent**. This is a standalone, runnable safety-focused reimplementation; it is **not** a verbatim copy of the provided GEOHARVEST v2. The original deceptive lures, hidden fingerprinting, IP/WebRTC inference, contacts/clipboard/camera extraction, and auto-resume are intentionally excluded.

## Files

```text
geoharvest-consent/
├── app.py                  # Flask web app, APIs, dashboard, exports
├── bot.py                  # Telegram bot, owner-only commands
├── geo.py                  # Geographic calculations and geofence evaluation
├── schema.sql              # SQLite database schema
├── requirements.txt
├── Dockerfile
├── Caddyfile
├── Procfile
├── render.yaml
├── .replit
├── replit.nix
├── .env.example
├── .gitignore
├── README.md
├── tests/
│   └── test_geo.py
└── templates/
    ├── capture.html        # Explicit opt-in location sharing
    ├── lure.html           # Transparent invitation / no spoofing
    ├── live.html           # Admin live map
    ├── admin.html          # Admin sessions
    ├── login.html          # Admin login
    ├── decoy.html          # Neutral completion page / no redirect trick
    └── sw.js               # No tracking or background work
```

## Replit: complete setup (Arabic)

1. Extract this ZIP on your own device. Open your Python Replit project. Upload the extracted project **files and folders** into the project root. Alternatively, upload the ZIP and use the Replit Shell command `unzip geoharvest-consent.zip` then move the files to your Repl root, or import them through a GitHub repository.
2. In Replit, open **Tools → Secrets** (sometimes called **Environment Variables**) and add one entry for each of the following exact keys:

   | Key | Value / how to get it |
   |---|---|
   | `BOT_TOKEN` | Obtain a **new** token using the official Telegram `@BotFather`. Never reuse a token exposed in a chat. |
   | `OWNER_ID` | Your own numeric Telegram user/chat ID, discoverable using a bot such as `@userinfobot` or Telegram `getUpdates` after sending the bot `/start`. |
   | `ADMIN_PASS` | A newly chosen password with at least 12 characters. |
   | `APP_SECRET` | A random secret of at least 32 characters. Generate via `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
   | `START_BOT` | `1` enables Telegram long-polling; `0` runs web only. |
   | `BASE_URL` | Your public **HTTPS** Replit web address, without a trailing slash. |
   | `DB_PATH` | Optional: `data.sqlite3`. |
   | `PORT` | Optional: `8080` (use provided platform port when deployed). |

3. In the Shell install the dependencies: `pip install -r requirements.txt`.
4. Click **Run**, or run `python app.py`. The entrypoint is set in `.replit`.
5. Open `https://YOUR-URL/healthz`. You should see JSON showing `"ok": true`.
6. In Telegram, open **your newly created bot**, send `/start`. The bot responds only to the account matching `OWNER_ID`. Use `/new test` to create a link.
7. On a new browser open `/admin/login`, enter `ADMIN_PASS`, and manage sessions and exports. The bot’s `/new` link must be opened by the consenting visitor, who clicks **أوافق وأبدأ مشاركة موقعي** and then grants geolocation permission.
8. Use the **إيقاف المشاركة** button to stop tracking; it also stops when the page is hidden, closed, or navigated away. Session owners can close and delete records.

**Important:** Browser GPS generally requires HTTPS, and obtaining precise location is never guaranteed. The Replit service may sleep. Local SQLite on ephemeral hosting may be lost after redeployment; use a persistent volume or backed-up database for production.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # Windows: copy .env.example .env
# Set ADMIN_PASS and APP_SECRET (mandatory); add BOT_TOKEN, OWNER_ID, BASE_URL as needed.
python app.py
```

Visit `http://127.0.0.1:8080/admin/login`. On localhost, the browser may allow geolocation without HTTPS; public deployments should always use HTTPS.

## Telegram commands

- `/help` — available commands
- `/new label` — new explicit-consent sharing link
- `/sessions` — list sharing sessions
- `/live TOKEN` — admin map
- `/export TOKEN` — GeoJSON file
- `/zone label latitude longitude radius_m` — create a geofence
- `/zones` — list geofences
- `/close TOKEN` — prevent further sharing
- `/wipe TOKEN` — delete the session and its data

## Security and privacy

Only record user-authorized GPS coordinates and session consent events; do not collect fingerprinting, contacts, clipboard, camera media, or IP intel. Inform visitors about recipients, storage, and geofence processing. Use a unique administrator password, protect Secrets, retain only necessary data, and delete it upon request.

The SQLite database is not pre-populated. All credentials are intentionally excluded from the archive. The Telegram bot is **optional**; leaving `START_BOT=0` still allows a local web app with admin-generated sessions.

## Testing

```bash
python -m unittest discover -s tests -v
```

## Troubleshooting

- `RuntimeError: Set ADMIN_PASS` / `Set APP_SECRET`: configure mandatory Secrets and restart.
- Bot does not reply: confirm `BOT_TOKEN` and `OWNER_ID`, send `/start` from the owner account, and ensure `START_BOT=1`.
- Location permission fails: use an HTTPS public URL, enable device location, and allow location access in browser site settings.
- `404` on a sharing link: generate a new session using `/new` or the admin dashboard first.
- Blank map: OpenStreetMap/Leaflet external resources may be blocked by your network.
