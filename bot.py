"""Owner-only Telegram bot for transparently consented location sessions."""
import html
import json
import os
import secrets
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone

import requests

TOKEN = os.getenv('BOT_TOKEN', '').strip()
OWNER = int(os.getenv('OWNER_ID', '0') or '0')
BASE = os.getenv('BASE_URL', '').rstrip('/')
DB = os.getenv('DB_PATH', 'data.sqlite3')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def db():
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def api(method, files=None, **params):
    if not TOKEN:
        return {'ok': False, 'description': 'BOT_TOKEN is missing'}
    try:
        if files:
            response = requests.post(f'https://api.telegram.org/bot{TOKEN}/{method}',
                                     data=params, files=files, timeout=35)
        else:
            response = requests.post(f'https://api.telegram.org/bot{TOKEN}/{method}',
                                     json=params, timeout=35)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        print('[bot] Telegram API error:', type(exc).__name__)
        return {'ok': False}


def send(message, keyboard=None):
    if not TOKEN or not OWNER:
        return
    options = {'chat_id': OWNER, 'text': message[:4000],
               'parse_mode': 'HTML', 'disable_web_page_preview': True}
    if keyboard:
        options['reply_markup'] = {'inline_keyboard': keyboard}
    api('sendMessage', **options)


def notify_first_fix(token, lat, lon, accuracy):
    if not OWNER or not TOKEN:
        return
    send(f'📍 مشاركة موقع طوعية: <code>{html.escape(token)}</code>\n'
         f'الدقة التقريبية: ±{round(accuracy)} متر',
         [[{'text': 'لوحة الموقع', 'url': f'{BASE}/admin/s/{token}'}]] if BASE else None)
    api('sendLocation', chat_id=OWNER, latitude=lat, longitude=lon,
        horizontal_accuracy=min(accuracy, 1500))


def notify_zone(token, label, action, distance):
    word = 'دخول' if action == 'enter' else 'مغادرة'
    send(f'📌 {word} منطقة <b>{html.escape(label)}</b>\n'
         f'<code>{html.escape(token)}</code> · {round(distance)} متر')


def handle(update):
    message = update.get('message') or {}
    text = (message.get('text') or '').strip()
    chat = message.get('chat', {}).get('id')
    if not text.startswith('/') or not chat:
        return
    if not OWNER or chat != OWNER:
        api('sendMessage', chat_id=chat, text='غير مصرح لك باستخدام هذا البوت.')
        return
    args = text.split()
    command = args[0].split('@')[0].lower()
    if command in ('/start', '/help'):
        send('🛰 <b>GEOHARVEST — Consent Edition</b>\n'
             '/new اسم_الجلسة — إنشاء رابط موافقة\n'
             '/sessions — الجلسات\n'
             '/live TOKEN — لوحة الخريطة\n'
             '/export TOKEN — ملف GeoJSON\n'
             '/zone اسم_المنطقة LAT LON RADIUS — سياج جغرافي\n'
             '/zones — قائمة المناطق\n'
             '/close TOKEN — إغلاق جلسة\n'
             '/wipe TOKEN — حذف الجلسة وبياناتها\n'
             f'لوحة الإدارة: {BASE}/admin')
    elif command == '/new':
        label = ' '.join(args[1:]).strip()[:80] or 'جلسة مشاركة'
        token = secrets.token_urlsafe(12)
        with db() as conn:
            conn.execute('INSERT INTO sessions(token,label,created_at) VALUES(?,?,?)',
                         (token, label, now()))
        url = f'{BASE}/t/{token}' if BASE else '(ضع BASE_URL أولاً)'
        send(f'✅ رابط جديد: <b>{html.escape(label)}</b>\n<code>{token}</code>\n{html.escape(url)}\n'
             'الموقع لا يُشارك إلا بعد موافقة الشخص والضغط على زر البدء.')
    elif command == '/sessions':
        with db() as conn:
            rows = conn.execute('SELECT token,label,state,hits FROM sessions '
                                'ORDER BY created_at DESC LIMIT 20').fetchall()
        send('\n'.join(f'• <code>{row["token"]}</code> · '
                       f'{html.escape(row["label"])} · {row["state"]} · {row["hits"]}'
                       for row in rows) or 'لا توجد جلسات.')
    elif command == '/live' and len(args) == 2:
        send(html.escape(f'{BASE}/admin/s/{args[1]}'))
    elif command == '/export' and len(args) == 2:
        with db() as conn:
            rows = conn.execute('SELECT lat,lon,acc,ts FROM hits WHERE token=? ORDER BY id',
                                (args[1],)).fetchall()
        if not rows:
            return send('لا توجد نقاط مسجلة في هذه الجلسة.')
        features = [{'type': 'Feature',
                     'geometry': {'type': 'Point', 'coordinates': [r['lon'], r['lat']]},
                     'properties': {'accuracy_m': r['acc'], 'at': r['ts']}} for r in rows]
        with tempfile.NamedTemporaryFile('w', suffix='.geojson', encoding='utf8', delete=False) as file:
            json.dump({'type': 'FeatureCollection', 'features': features}, file, ensure_ascii=False)
            path = file.name
        try:
            with open(path, 'rb') as upload:
                api('sendDocument', files={'document': upload}, chat_id=OWNER,
                    caption=f'GeoJSON · {len(rows)} points')
        finally:
            os.unlink(path)
    elif command == '/zone' and len(args) == 5:
        try:
            lat, lon, radius = map(float, args[2:])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 10 <= radius <= 100000):
                raise ValueError()
        except ValueError:
            return send('إدخال غير صالح. مثال: /zone المكتب 15.36 44.2 250')
        with db() as conn:
            conn.execute('INSERT INTO zones(label,lat,lon,radius) VALUES(?,?,?,?)',
                         (args[1][:80], lat, lon, radius))
        send('تمت إضافة السياج الجغرافي.')
    elif command == '/zones':
        with db() as conn:
            rows = conn.execute('SELECT * FROM zones ORDER BY id').fetchall()
        send('\n'.join(f'{r["id"]} · {html.escape(r["label"])} · {r["radius"]}m'
                       for r in rows) or 'لا توجد مناطق.')
    elif command == '/close' and len(args) == 2:
        with db() as conn:
            conn.execute("UPDATE sessions SET state='closed',grant_hash=NULL WHERE token=?", (args[1],))
        send('تم إغلاق الجلسة.')
    elif command == '/wipe' and len(args) == 2:
        with db() as conn:
            conn.execute('DELETE FROM sessions WHERE token=?', (args[1],))
        send('تم حذف الجلسة وجميع بياناتها.')
    else:
        send('الأمر غير معروف أو تنقصه معاملات. استخدم /help')


def loop():
    if not TOKEN or not OWNER:
        print('[bot] Disabled: BOT_TOKEN or OWNER_ID missing')
        return
    offset = 0
    print('[bot] Started owner-only polling')
    while True:
        try:
            data = requests.get(f'https://api.telegram.org/bot{TOKEN}/getUpdates',
                                params={'offset': offset, 'timeout': 45,
                                        'allowed_updates': '["message"]'}, timeout=55).json()
            for update in data.get('result', []):
                offset = update['update_id'] + 1
                try:
                    handle(update)
                except Exception as exc:
                    print('[bot] Command error:', type(exc).__name__)
        except requests.RequestException:
            time.sleep(4)


def start():
    threading.Thread(target=loop, daemon=True, name='telegram-bot').start()
