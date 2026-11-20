"""Geographic calculations for explicitly consented location sharing."""
import math

R = 6371008.8


def haversine(a_lat, a_lon, b_lat, b_lon):
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = p2 - p1
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(h)))


def quality(accuracy):
    if accuracy is None:
        return 0
    return int(max(0, min(100, round(100 - 40 * math.log10(max(1.0, float(accuracy)))))))


def plausible(prev, new, seconds):
    """Reject obviously impossible jumps without overstating GPS precision."""
    if prev is None:
        return True
    distance = haversine(prev['lat'], prev['lon'], new['lat'], new['lon'])
    allowance = 80 * max(1.0, seconds) + prev['acc'] + new['acc']
    return distance <= allowance


def evaluate_zones(conn, token, lat, lon):
    changed = []
    for zone in conn.execute('SELECT * FROM zones'):
        distance = haversine(lat, lon, zone['lat'], zone['lon'])
        inside = int(distance <= zone['radius'])
        old = conn.execute(
            'SELECT inside FROM zone_state WHERE token=? AND zone_id=?',
            (token, zone['id']),
        ).fetchone()
        previous = old['inside'] if old else 0
        conn.execute('''
            INSERT INTO zone_state(token,zone_id,inside) VALUES(?,?,?)
            ON CONFLICT(token,zone_id) DO UPDATE SET inside=excluded.inside
        ''', (token, zone['id'], inside))
        if previous != inside:
            changed.append((zone['label'], 'enter' if inside else 'exit', distance))
    return changed
