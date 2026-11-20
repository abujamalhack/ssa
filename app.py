#!/usr/bin/env python3
"""GEOHARVEST Consent Edition: user-controlled location sharing platform."""
import csv
import hashlib
import io
import math
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import (Flask, abort, jsonify, make_response, redirect, render_template,
                   request, send_from_directory, session, url_for)

load_dotenv()

import bot
import geo
DB_PATH = os.environ.get('DB_PATH', 'data.sqlite3')
BASE_URL = os.environ.get('BASE_URL', '').strip().rstrip('/')
PORT = int(os.environ.get('PORT', '8080'))
ADMIN_PASS = os.environ.get('ADMIN_PASS', '')
APP_SECRET = os.environ.get('APP_SECRET', '')
START_BOT = os.environ.get('START_BOT', '1') == '1'

if not ADMIN_PASS or len(ADMIN_PASS) < 12:
    raise RuntimeError('Set ADMIN_PASS (at least 12 characters) using environment secrets.')
if not APP_SECRET or len(APP_SECRET) < 32:
    raise RuntimeError('Set APP_SECRET (at least 32 characters) using environment secrets.')

app = Flask(__name__)
app.secret_key = APP_SECRET
app.config.update(
    MAX_CONTENT_LENGTH=8192,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=BASE_URL.startswith('https://'),
    PERMANENT_SESSION_LIFETIME=3600 * 8,
)
bot.DB = DB_PATH
bot.BASE = BASE_URL


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


@contextmanager
def db():
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    parent = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(parent, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        with open(os.path.join(os.path.dirname(__file__), 'schema.sql'), encoding='utf8') as schema:
            connection.executescript(schema.read())


def log_event(conn, token, kind, message=''):
    conn.execute('INSERT INTO events(token,kind,body,ts) VALUES(?,?,?,?)',
                 (token, kind, message[:250], now()))


def is_admin():
    return session.get('admin') is True


def require_admin():
    if not is_admin():
        abort(403)
    if request.method == 'POST':
        expected = session.get('csrf', '')
        supplied = request.form.get('csrf', '')
        if not expected or not supplied or not secrets.compare_digest(expected, supplied):
            abort(403)


def require_json():
    if not request.is_json:
        abort(415)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    return data


def authorized_grant(conn, token, grant):
    row = conn.execute('SELECT state,grant_hash FROM sessions WHERE token=?', (token,)).fetchone()
    if not row or row['state'] != 'active' or not isinstance(grant, str):
        return False
    hashed = hashlib.sha256(grant.encode()).hexdigest()
    return bool(row['grant_hash'] and secrets.compare_digest(row['grant_hash'], hashed))


def public_link(token):
    return f'{BASE_URL or request.host_url.rstrip("/")}/t/{token}'


@app.get('/healthz')
def health():
    return {'ok': True, 'service': 'geoharvest-consent', 'time': now()}


@app.get('/')
def home():
    return render_template('capture.html', token=None)


@app.get('/t/<token>')
def share_page(token):
    with db() as conn:
        row = conn.execute('SELECT state FROM sessions WHERE token=?', (token,)).fetchone()
    if not row:
        abort(404)
    return render_template('capture.html', token=token, closed=row['state'] == 'closed')


@app.get('/l/<token>')
def explanation_page(token):
    with db() as conn:
        row = conn.execute('SELECT label FROM sessions WHERE token=?', (token,)).fetchone()
    if not row:
        abort(404)
    return render_template('lure.html', token=token, label=row['label'])


@app.get('/sw.js')
def service_worker():
    response = make_response(send_from_directory('templates', 'sw.js'))
    response.headers['Content-Type'] = 'application/javascript; charset=utf-8'
    response.headers['Service-Worker-Allowed'] = '/'
    return response


@app.get('/decoy')
def completed():
    return render_template('decoy.html')


@app.post('/api/consent/start')
def consent_start():
    data = require_json()
    token = str(data.get('token') or '')[:64]
    grant = secrets.token_urlsafe(32)
    with db() as conn:
        row = conn.execute('SELECT state FROM sessions WHERE token=?', (token,)).fetchone()
        if not row or row['state'] == 'closed':
            abort(404)
        conn.execute('''UPDATE sessions
                        SET state='active',consent_at=?,grant_hash=?
                        WHERE token=?''',
                     (now(), hashlib.sha256(grant.encode()).hexdigest(), token))
        log_event(conn, token, 'consent_started', 'User clicked Start location sharing')
    return jsonify({'ok': True, 'grant': grant})


@app.post('/api/consent/stop')
def consent_stop():
    data = require_json()
    token, grant = str(data.get('token') or '')[:64], data.get('grant')
    with db() as conn:
        if not authorized_grant(conn, token, grant):
            return jsonify({'ok': False, 'error': 'not_active'}), 403
        conn.execute("UPDATE sessions SET state='stopped',grant_hash=NULL WHERE token=?", (token,))
        log_event(conn, token, 'consent_stopped', 'Sharing stopped by visitor')
    return jsonify({'ok': True})


@app.post('/api/hit')
def collect_position():
    data = require_json()
    token, grant = str(data.get('token') or '')[:64], data.get('grant')
    try:
        lat, lon, acc = float(data['lat']), float(data['lon']), float(data['acc'])
    except (KeyError, ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'invalid_coordinates'}), 400
    if not (math.isfinite(lat) and math.isfinite(lon) and math.isfinite(acc)
            and -90 <= lat <= 90 and -180 <= lon <= 180 and 0 < acc < 100000):
        return jsonify({'ok': False, 'error': 'out_of_range'}), 400

    first_position = False
    with db() as conn:
        if not authorized_grant(conn, token, grant):
            return jsonify({'ok': False, 'error': 'not_active'}), 403
        prev = conn.execute('SELECT lat,lon,acc,ts FROM hits WHERE token=? '
                            'ORDER BY id DESC LIMIT 1', (token,)).fetchone()
        if prev:
            dt = max(1., (datetime.fromisoformat(now()) - datetime.fromisoformat(prev['ts'])).total_seconds())
            if not geo.plausible(prev, {'lat': lat, 'lon': lon, 'acc': acc}, dt):
                return jsonify({'ok': False, 'error': 'implausible_jump'}), 409
        q = geo.quality(acc)
        conn.execute('INSERT INTO hits(token,stage,lat,lon,acc,quality,ts) VALUES(?,?,?,?,?,?,?)',
                     (token, 'gps', lat, lon, acc, q, now()))
        old = conn.execute('SELECT best_acc,hits FROM sessions WHERE token=?', (token,)).fetchone()
        first_position = old['hits'] == 0
        conn.execute('UPDATE sessions SET hits=hits+1,last_seen=? WHERE token=?', (now(), token))
        if old['best_acc'] is None or acc < old['best_acc']:
            conn.execute('UPDATE sessions SET best_lat=?,best_lon=?,best_acc=?,best_at=? '
                         'WHERE token=?', (lat, lon, acc, now(), token))
        changes = geo.evaluate_zones(conn, token, lat, lon)
        for label, action, distance in changes:
            log_event(conn, token, 'geofence_' + action, f'{label} · {round(distance)}m')

    # Notifications occur only after explicit visitor consent.
    if first_position:
        threading.Thread(target=bot.notify_first_fix, args=(token, lat, lon, acc), daemon=True).start()
    for label, action, distance in changes:
        threading.Thread(target=bot.notify_zone,
                         args=(token, label, action, distance), daemon=True).start()
    return jsonify({'ok': True, 'quality': q})


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = ''
    if request.method == 'POST':
        password = request.form.get('p', '')
        if secrets.compare_digest(password, ADMIN_PASS):
            session.clear()
            session['admin'] = True
            session['csrf'] = secrets.token_urlsafe(24)
            session.permanent = True
            return redirect(url_for('admin_home'))
        time.sleep(.3)
        error = 'كلمة المرور غير صحيحة.'
    return render_template('login.html', err=error)


@app.post('/admin/logout')
def admin_logout():
    require_admin()
    session.clear()
    return redirect(url_for('admin_login'))


@app.get('/admin')
def admin_home():
    if not is_admin():
        return redirect(url_for('admin_login'))
    with db() as conn:
        sessions = [dict(r) for r in conn.execute('SELECT * FROM sessions ORDER BY created_at DESC LIMIT 100')]
        events = [dict(r) for r in conn.execute('SELECT * FROM events ORDER BY id DESC LIMIT 30')]
    return render_template('admin.html', sessions=sessions, events=events, base=BASE_URL, csrf=session['csrf'])


@app.post('/admin/new')
def admin_new():
    require_admin()
    label = request.form.get('label', '').strip()[:80] or 'جلسة مشاركة'
    token = secrets.token_urlsafe(12)
    with db() as conn:
        conn.execute('INSERT INTO sessions(token,label,created_at) VALUES(?,?,?)', (token, label, now()))
    return redirect(url_for('admin_home'))


@app.post('/admin/close/<token>')
def close_session(token):
    require_admin()
    with db() as conn:
        conn.execute("UPDATE sessions SET state='closed',grant_hash=NULL WHERE token=?", (token,))
        log_event(conn, token, 'closed_by_admin')
    return redirect(url_for('admin_home'))


@app.post('/admin/wipe/<token>')
def delete_session(token):
    require_admin()
    with db() as conn:
        conn.execute('DELETE FROM sessions WHERE token=?', (token,))
    return redirect(url_for('admin_home'))


@app.get('/admin/s/<token>')
def admin_session(token):
    if not is_admin():
        return redirect(url_for('admin_login'))
    with db() as conn:
        record = conn.execute('SELECT * FROM sessions WHERE token=?', (token,)).fetchone()
        if not record:
            abort(404)
        events = [dict(r) for r in conn.execute('SELECT id,kind,body,ts FROM events '
                                                  'WHERE token=? ORDER BY id DESC LIMIT 100', (token,))]
    return render_template('live.html', item=dict(record), events=events,
                           last_event_id=max((e['id'] for e in events), default=0))


@app.get('/api/live/<token>')
def live_api(token):
    require_admin()
    try:
        hit_after = max(0, int(request.args.get('hit_after', 0)))
        event_after = max(0, int(request.args.get('event_after', 0)))
    except ValueError:
        abort(400)
    with db() as conn:
        hits = [dict(r) for r in conn.execute(
            'SELECT id,lat,lon,acc,quality,ts FROM hits WHERE token=? AND id>? ORDER BY id LIMIT 250',
            (token, hit_after))]
        events = [dict(r) for r in conn.execute(
            'SELECT id,kind,body,ts FROM events WHERE token=? AND id>? ORDER BY id LIMIT 150',
            (token, event_after))]
    return jsonify({'hits': hits, 'events': events})


@app.get('/api/trail/<token>')
def trail_api(token):
    require_admin()
    with db() as conn:
        hits = [dict(r) for r in conn.execute(
            'SELECT id,lon,lat,acc,ts FROM hits WHERE token=? ORDER BY id LIMIT 10000', (token,))]
    return jsonify(hits)


@app.get('/admin/export/<token>/<format>')
def export_session(token, format):
    require_admin()
    if format not in ('csv', 'geojson'):
        abort(404)
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            'SELECT lat,lon,acc,ts FROM hits WHERE token=? ORDER BY id', (token,))]
    if format == 'geojson':
        obj = {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [r['lon'], r['lat']]},
             'properties': {'accuracy_m': r['acc'], 'at': r['ts']}} for r in rows]}
        response = jsonify(obj)
        response.headers['Content-Disposition'] = f'attachment; filename="{token}.geojson"'
        return response
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=['lat', 'lon', 'acc', 'ts'])
    writer.writeheader()
    writer.writerows(rows)
    response = make_response(out.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename="{token}.csv"'
    return response


@app.after_request
def response_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Permissions-Policy'] = 'geolocation=(self), camera=(), microphone=()'
    response.headers['Cache-Control'] = 'no-store'
    if request.is_secure:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000'
    return response


init_db()
if START_BOT:
    bot.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
