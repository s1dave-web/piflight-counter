"""piFlight counter: how many piFlights are online. Python standard library only.

  POST /checkin   {"id": "<32 hex>", "version": "0.5.3", "style": "modern", "planes": 1234}
                  -> {"online": <devices seen in the last 48 hours>, "planes": <all devices' planes>}
  GET  /stats     -> {"online": n, "week": n, "month": n, "planes": n, "versions": {...}, "styles": {...}}
  GET  /          -> a tiny HTML page with the numbers

Stored per device: a salted hash of its random ID, first and last check-in time, version, style and the
number of planes it has shown in all ("planes", optional: sent only by devices whose owner agreed to it).
IP addresses are never stored or logged. They're used, in memory only, to stop one address from
inventing more than NEW_PER_IP_PER_HOUR new devices an hour.

Each check-in prints one line (new or known device, version, style, planes, and the totals), with nothing
that identifies a device or where it connected from: no IP, no ID, no hash.
"""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.environ.get("COUNTER_DATA", "/data")
DB = os.path.join(DATA, "counter.db")
PORT = int(os.environ.get("COUNTER_PORT", "8080"))
ONLINE_SECONDS = 48 * 3600
KEEP_SECONDS = 400 * 86400
NEW_PER_IP_PER_HOUR = 20
MAX_BODY = 1024
MAX_PLANES = 10_000_000      # far more than one device could show in years

_ID = re.compile(r"^[0-9a-f]{32}$")
_VERSION = re.compile(r"^[0-9A-Za-z.+\-]{1,20}$")
STYLES = {"modern", "classic"}

_lock = threading.Lock()
_new_by_ip = defaultdict(list)       # ip -> [times a new device appeared]; memory only


def _salt():
    path = os.path.join(DATA, "salt")
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        s = secrets.token_hex(32)
        with open(path, "w") as f:
            f.write(s)
        os.chmod(path, 0o600)
        return s


def connect():
    os.makedirs(DATA, exist_ok=True)
    db = sqlite3.connect(DB, check_same_thread=False)
    db.execute("CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, first INTEGER, last INTEGER,"
               " version TEXT, style TEXT)")
    cols = {r[1] for r in db.execute("PRAGMA table_info(devices)")}
    if "planes" not in cols:                       # added with app 0.5.3
        db.execute("ALTER TABLE devices ADD COLUMN planes INTEGER NOT NULL DEFAULT 0")
    db.commit()
    return db


DBC = None
SALT = None


def log(msg, now=None):
    t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() if now is None else now))
    print(f"{t} {msg}", flush=True)


def checkin(ip, payload, now=None):
    """Record a check-in. Returns (http_status, reply_dict)."""
    now = int(time.time() if now is None else now)
    if not isinstance(payload, dict):
        log("check-in refused: not a check-in", now)
        return 400, {"error": "bad request"}
    did, version, style = payload.get("id"), payload.get("version"), payload.get("style")
    if not (isinstance(did, str) and _ID.match(did)):
        log("check-in refused: bad id", now)
        return 400, {"error": "bad id"}
    version = version if isinstance(version, str) and _VERSION.match(version) else "?"
    style = style if style in STYLES else "other"
    planes = payload.get("planes")
    planes = planes if isinstance(planes, int) and not isinstance(planes, bool) and 0 <= planes <= MAX_PLANES else 0
    key = hashlib.sha256((SALT + did).encode()).hexdigest()
    with _lock:
        known = DBC.execute("SELECT 1 FROM devices WHERE id = ?", (key,)).fetchone()
        if not known:
            recent = [t for t in _new_by_ip[ip] if now - t < 3600]
            if len(recent) >= NEW_PER_IP_PER_HOUR:
                log("check-in refused: too many new devices from one address", now)
                return 429, {"error": "slow down"}
            recent.append(now)
            _new_by_ip[ip] = recent
            DBC.execute("INSERT INTO devices (id, first, last, version, style, planes) VALUES (?, ?, ?, ?, ?, ?)",
                        (key, now, now, version, style, planes))
        else:                                       # a lifetime total only goes up
            DBC.execute("UPDATE devices SET last = ?, version = ?, style = ?, planes = MAX(planes, ?) WHERE id = ?",
                        (now, version, style, planes, key))
        DBC.execute("DELETE FROM devices WHERE last < ?", (now - KEEP_SECONDS,))
        DBC.commit()
        reply = {"online": online(now), "planes": planes_total()}
    log(f"check-in: {'known' if known else 'NEW'} device · {version} · {style} · {planes:,} planes"
        f" → {reply['online']} online, {reply['planes']:,} planes in all", now)
    return 200, reply


def planes_total():
    return DBC.execute("SELECT COALESCE(SUM(planes), 0) FROM devices").fetchone()[0]


def online(now=None):
    now = int(time.time() if now is None else now)
    return DBC.execute("SELECT COUNT(*) FROM devices WHERE last >= ?", (now - ONLINE_SECONDS,)).fetchone()[0]


def stats(now=None):
    now = int(time.time() if now is None else now)

    def count(seconds):
        return DBC.execute("SELECT COUNT(*) FROM devices WHERE last >= ?", (now - seconds,)).fetchone()[0]

    def by(col):
        rows = DBC.execute(f"SELECT {col}, COUNT(*) FROM devices WHERE last >= ? GROUP BY {col} ORDER BY 2 DESC",
                           (now - 30 * 86400,)).fetchall()
        return {k: n for k, n in rows}

    return {"online": count(ONLINE_SECONDS), "week": count(7 * 86400), "month": count(30 * 86400),
            "planes": planes_total(), "versions": by("version"), "styles": by("style")}


PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>piFlights online</title><style>body{{font-family:system-ui,sans-serif;background:#121212;color:#ececec;margin:0;
display:grid;place-items:center;min-height:100vh}}main{{text-align:center;padding:16px}}b{{font-size:4rem;color:#ff6600;display:block}}
p{{color:#9a9a9a}}</style><main><b>{online}</b>piFlights online now<p>{week} this week &middot; {month} this month<br>{planes:,} flights tracked</p></main></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "piflight-counter"
    sys_version = ""

    def log_message(self, *args):          # no access log: it would hold IP addresses
        pass

    def _client_ip(self):
        """For the in-memory new-device limit only. Behind Cloudflare every request comes from a Cloudflare
        address, so use the visitor's address Cloudflare passes on; else the proxy's X-Forwarded-For."""
        cf = self.headers.get("CF-Connecting-IP", "").strip()
        if cf:
            return cf
        fwd = self.headers.get("X-Forwarded-For", "")
        return fwd.split(",")[0].strip() or self.client_address[0]

    def _send(self, status, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/checkin":
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_BODY:
            return self._send(400, {"error": "bad request"})
        try:
            payload = json.loads(self.rfile.read(length).decode())
        except (ValueError, UnicodeDecodeError):
            return self._send(400, {"error": "bad request"})
        status, reply = checkin(self._client_ip(), payload)
        self._send(status, reply)

    def do_HEAD(self):                     # uptime checkers and Cloudflare use HEAD
        self.send_response(200 if self.path in ("/", "/stats", "/health") else 404)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self):
        if self.path == "/stats":
            with _lock:
                return self._send(200, stats())
        if self.path == "/":
            with _lock:
                s = stats()
            return self._send(200, PAGE.format(**s).encode(), "text/html; charset=utf-8")
        if self.path == "/health":
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})


def main():
    global DBC, SALT
    DBC, SALT = connect(), _salt()
    print(f"piflight-counter listening on {PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
