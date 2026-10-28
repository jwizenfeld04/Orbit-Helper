#!/usr/bin/env python3
"""Does for the Orbit launcher what a Fire TV app isn't allowed to do, over ADB from a home server.

Fire OS only lets Amazon-signed apps (or ADB) switch inputs or take over the Home button, so Orbit's HDMI
tiles POST here and this sends the trusted key over ADB. It also keeps homewatch.sh running on the TV.

Routes (requests are accepted only from TV_IP):
  POST /hdmi/<1-4>     switch input (wakes the PS4 first if it's on PS4_HDMI)
  GET  /glance         the one game the Orbit home screen should show (via Sports Remote)
  POST /glance/launch  {"gameId": ...} — open that game on the TV (via Sports Remote)
"""
import http.server
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PORT = int(os.environ.get("PORT", "8788"))
ROUTE = re.compile(r"^/hdmi/([1-4])$")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def adb(*args, timeout=5):
    return subprocess.run(["adb", "-s", SERIAL, *args], capture_output=True, text=True, timeout=timeout)


def load_env(path):
    """KEY=VALUE lines from the private .env next to this file (PS4 pairing data)."""
    env = {}
    try:
        with open(path) as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    env[k] = v
    except FileNotFoundError:
        pass
    return env


ENV = load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def setting(key, default=None):
    """The environment first, then .env (see .env.example)."""
    return os.environ.get(key) or ENV.get(key) or default


# The TV this helper controls and the only client it answers. Required: set TV_IP in .env.
TV_IP = setting("TV_IP")
if not TV_IP:
    raise SystemExit("TV_IP is not set: add TV_IP=<the TV's IP> to .env (see .env.example)")
SERIAL = f"{TV_IP}:5555"
BIND = setting("BIND", "0.0.0.0")  # an address of this machine to listen on; requests from anyone but the TV are refused anyway
PS4_HOST = ENV.get("PS4_HOST")
PS4_CREDENTIAL = ENV.get("PS4_CREDENTIAL")  # from Remote Play pairing (pyremoteplay format_regist_key)
PS4_HDMI = setting("PS4_HDMI", "3")  # the HDMI port the PS4 is on: woken before switching to it
DDP_PORT = 987


def ps4_status():
    """'on', 'standby' or None (no answer). PS4 Device Discovery Protocol, UDP 987."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1.0)
    try:
        for _ in range(2):
            s.sendto(b"SRCH * HTTP/1.1\ndevice-discovery-protocol-version:00030010\n", (PS4_HOST, DDP_PORT))
            try:
                data, _ = s.recvfrom(2048)
            except socket.timeout:
                continue
            first = data.split(b"\n", 1)[0]
            return "on" if b" 200 " in first else "standby" if b" 620 " in first else None
        return None
    finally:
        s.close()


def ps4_wake():
    msg = ("WAKEUP * HTTP/1.1\n"
           f"user-credential:{PS4_CREDENTIAL}\nclient-type:vr\nauth-type:R\nmodel:w\napp-type:r\n"
           "device-discovery-protocol-version:00030010\n").encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for _ in range(3):  # UDP; repeat in case the resting Wi-Fi misses one
            s.sendto(msg, (PS4_HOST, DDP_PORT))
            time.sleep(0.2)
    finally:
        s.close()


def _wake_ps4_until_on(timeout=25.0):
    """Resting PS4 Wi-Fi dozes and drops packets, so keep sending WAKEUP until it reports 'on'."""
    start = time.time()
    while time.time() - start < timeout:
        ps4_wake()
        if ps4_status() == "on":
            logging.info("PS4 on after %.1fs", time.time() - start)
            return
        time.sleep(0.5)
    logging.warning("PS4 did not report 'on' within %.0fs", timeout)


def wake_console(port: str):
    """Runs in the background so the HDMI switch isn't delayed."""
    if port == PS4_HDMI and PS4_HOST and PS4_CREDENTIAL:
        def run():
            state = ps4_status()
            logging.info("PS4 was %s", state or "unreachable (Wi-Fi dozing)")
            if state != "on":
                _wake_ps4_until_on()
        threading.Thread(target=run, daemon=True).start()


def switch(port: str) -> bool:
    key = f"KEYCODE_TV_INPUT_HDMI_{port}"
    for attempt in (1, 2):
        try:
            r = adb("shell", "input", "keyevent", key)
            if r.returncode == 0:
                return True
            logging.warning("adb failed (attempt %d): %s", attempt, r.stderr.strip())
        except subprocess.TimeoutExpired:
            logging.warning("adb timed out (attempt %d)", attempt)
        subprocess.run(["adb", "connect", SERIAL], capture_output=True, text=True, timeout=5)
    return False


# ---------- Game line for the Orbit home screen ----------
# The launcher asks GET /glance for the one game worth showing and POST /glance/launch to start it.
# Sports Remote does the real work; this keeps its sign-in (the household PIN, in .env) off the TV.

SR_URL = setting("SPORTS_REMOTE_URL", "http://127.0.0.1:8787")
SR_PIN = ENV.get("SPORTS_REMOTE_PIN")
# "Tonight" / "Today" and the day names follow this machine's time zone (TZ=… in the service environment to override).
LOCAL_TZ = ZoneInfo(setting("TZ")) if setting("TZ") else datetime.now().astimezone().tzinfo
LEAGUE_ORDER = {"nfl": 0, "mlb": 1, "nhl": 2, "nba": 3}
BIG_NETS = {"abc", "cbs", "fox", "nbc", "espn", "tnt", "tbs", "prime", "netflix", "peacock", "apple", "youtube"}
# Local teams ("league:ABBR", comma-separated) used only when Sports Remote doesn't report the household's market.
LOCAL_TEAMS = [t.strip() for t in (setting("LOCAL_TEAMS", "") or "").split(",") if t.strip()]
_sr = {"cookie": None, "lock": threading.Lock(), "glance": None, "at": 0.0}


def _sr_request(method, path, body=None):
    """One call to Sports Remote, signing in with the PIN first if needed. Returns (status, json).
    status 0 = Sports Remote couldn't be reached (or answered something that isn't JSON)."""
    def call(cookie):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(SR_URL + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
            req.add_header("Origin", SR_URL)
        if cookie:
            req.add_header("Cookie", cookie)
        try:
            with urllib.request.urlopen(req, timeout=60 if path == "/api/launch" else 8) as r:
                return r.status, r.headers, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.headers, json.loads(e.read() or b"null")
            except ValueError:
                return e.code, e.headers, None
        except (OSError, ValueError) as e:  # connection refused, timeout, bad JSON
            logging.warning("Sports Remote %s %s failed: %s", method, path, e)
            return 0, None, None

    with _sr["lock"]:
        status, _, data = call(_sr["cookie"]) if _sr["cookie"] else (401, None, None)
        if status in (401, 403) and SR_PIN:
            s, headers, _ = call_login()
            if s == 200:
                status, _, data = call(_sr["cookie"])
        return status, data


def call_login():
    req = urllib.request.Request(SR_URL + "/api/auth/login", data=json.dumps({"pin": SR_PIN}).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Origin", SR_URL)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            for h in r.headers.get_all("Set-Cookie") or []:
                if h.startswith("sr_session="):
                    _sr["cookie"] = h.split(";", 1)[0]
            logging.info("signed in to Sports Remote")
            return r.status, r.headers, None
    except urllib.error.HTTPError as e:
        logging.warning("Sports Remote sign-in failed: %s", e.code)
        return e.code, e.headers, None
    except OSError as e:
        logging.warning("Sports Remote sign-in failed: %s", e)
        return 0, None, None


def _key(league, team):
    return f"{league}:{team['abbr'].upper()}"


def _postseason(league, when):
    m, d = when.month, when.day
    return {"mlb": m >= 10, "nfl": m in (1, 2), "nba": (m == 4 and d >= 18) or m in (5, 6),
            "nhl": (m == 4 and d >= 18) or m in (5, 6)}.get(league, False)


def _is_big(g, when):
    national = [b for b in g.get("broadcasts", []) if b.get("scope") == "national" and b.get("network") in BIG_NETS]
    if not national:
        return False
    if _postseason(g["league"], when):
        return True
    if g["league"] == "nfl":  # Thursday, Sunday and Monday night, plus Saturdays and holidays
        return when.hour >= 19 or when.weekday() not in (6,)
    return when.hour >= 19 and g["league"] in ("nba", "nhl") and any(b["network"] in ("abc", "espn", "tnt", "nbc", "prime", "peacock") for b in national)


def _close_late(g):
    h, a = g["home"].get("score"), g["away"].get("score")
    if g["state"] != "in" or h is None or a is None:
        return False
    p, margin = g.get("period") or 0, abs(h - a)
    return {"nfl": p >= 4 and margin <= 8, "nhl": p >= 3 and margin <= 2, "nba": p >= 4 and margin <= 6}.get(g["league"], p >= 9 and margin <= 2)


def _net_label(g):
    bs = g.get("broadcasts", [])
    pick = next((b for b in bs if b.get("scope") == "national" and not b.get("radio")), None) or next((b for b in bs if not b.get("radio")), None)
    return (pick or {}).get("raw", "")


def _describe(g, when, now, fav_keys, tag_big):
    live = g["state"] == "in"
    away, home = g["away"], g["home"]
    if live:
        title = f"{away['name']} {away.get('score', 0)} – {home.get('score', 0)} {home['name']}"
        detail = g.get("detail", "")
    else:
        title = f"{away['name']} at {home['name']}"
        day = "Tonight" if when.date() == now.date() and when.hour >= 17 else "Today" if when.date() == now.date() else when.strftime("%a")
        detail = f"{day} {when.strftime('%-I:%M')}"
    tag = g["league"].upper()
    if _postseason(g["league"], when):
        tag = {"mlb": "WORLD SERIES" if when.month >= 10 and when.day >= 20 else "MLB PLAYOFFS", "nfl": "NFL PLAYOFFS"}.get(g["league"], f"{tag} PLAYOFFS")
    fav = _key(g["league"], away) in fav_keys or _key(g["league"], home) in fav_keys
    side = lambda t: {"name": t["name"], "abbr": t["abbr"], "score": t.get("score")}
    return {"id": g["id"], "live": live, "fav": fav, "tag": tag, "title": title, "detail": detail, "net": _net_label(g),
            "away": side(away), "home": side(home)}


def pick_games(data):
    """Favorite > big national game > local team; live before later today; then NFL > MLB > NHL > NBA."""
    now = datetime.now(LOCAL_TZ)
    games = [g for g in data.get("games", []) if g.get("league") in LEAGUE_ORDER and g.get("state") in ("in", "pre")]
    favs = set((data.get("settings") or {}).get("favorites") or [])
    local = set(((data.get("household") or {}).get("market") or {}).get("localTeams") or LOCAL_TEAMS)

    def keys(g):
        return {_key(g["league"], g["home"]), _key(g["league"], g["away"])}

    ranked = []
    for g in games:
        when = datetime.fromisoformat(g["startTime"].replace("Z", "+00:00")).astimezone(LOCAL_TZ)
        live = g["state"] == "in"
        today = live or when.date() == now.date() or (when - now).total_seconds() < 6 * 3600
        if not live and when < now - timedelta(hours=1):
            continue
        k = keys(g)
        if k & favs:
            tier = 0 if live else 1 if today else 6
        elif _is_big(g, when):
            tier = 2 if live else 3 if today else None
        elif k & local:
            tier = 4 if live else 5 if today else None
        else:
            tier = None
        if tier is None:
            continue
        if tier == 6 and (when - now).days > 7:
            continue
        ranked.append(((tier, 0 if _close_late(g) else 1, LEAGUE_ORDER[g["league"]], when.timestamp()), g, when))
    ranked.sort(key=lambda r: r[0])
    if not ranked:
        return {"game": None, "also": None, "alsoGame": None}
    _, g, when = ranked[0]
    main = _describe(g, when, now, favs, True)
    also, also_game = None, None
    for r in ranked[1:]:
        if r[0][0] <= 5:
            also_game = _describe(r[1], r[2], now, favs, True)
            also = f"Also · {also_game['title']} · {also_game['detail']}" + (f" · {also_game['net']}" if also_game["net"] else "")
            break
    return {"game": main, "also": also, "alsoGame": also_game}


def glance(leagues):
    if not SR_PIN:
        return {"game": None, "also": None, "error": "no_pin"}
    if _sr["glance"] is None or time.time() - _sr["at"] >= 15:  # Sports Remote data, cached 15 s (live scores)
        status, data = _sr_request("GET", "/api/bootstrap")
        if status != 200 or not isinstance(data, dict) or not data.get("authed"):
            logging.warning("Sports Remote bootstrap failed: %s", status)
            return {"game": None, "also": None, "error": "unavailable"}
        _sr["glance"], _sr["at"] = data, time.time()
    data = dict(_sr["glance"])
    data["games"] = [g for g in data.get("games", []) if g.get("league") in leagues]
    try:
        return pick_games(data)
    except (KeyError, TypeError, ValueError) as e:
        logging.warning("could not pick a game: %s", e)
        return {"game": None, "also": None, "error": "bad_data"}


def launch_game(game_id):
    if not SR_PIN or not re.match(r"^[a-z]{2,5}:[0-9A-Za-z_-]{1,40}$", game_id or ""):
        return False, "bad request"
    status, data = _sr_request("POST", "/api/launch", {"gameId": game_id})
    ok = status == 200 and isinstance(data, dict) and data.get("ok") is not False and data.get("outcome") != "failed"
    msg = (data or {}).get("message") or (data or {}).get("error") or str(status)
    logging.info("launch %s -> %s (%s)", game_id, "ok" if ok else "failed", msg)
    return ok, msg


class Handler(http.server.BaseHTTPRequestHandler):
    def _reply(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.client_address[0] != TV_IP:
            logging.warning("rejected request from %s", self.client_address[0])
            return self._reply(403, b"forbidden\n")
        if self.path == "/glance/launch":
            try:
                n = max(0, min(int(self.headers.get("Content-Length") or 0), 2048))
                game_id = json.loads(self.rfile.read(n) or b"{}").get("gameId")
            except (ValueError, AttributeError):
                return self._json(400, {"ok": False, "message": "bad request"})
            ok, msg = launch_game(game_id)
            return self._json(200 if ok else 502, {"ok": ok, "message": msg})
        m = ROUTE.match(self.path)
        if not m:
            return self._reply(404, b"not found\n")
        wake_console(m.group(1))
        ok = switch(m.group(1))
        logging.info("HDMI %s -> %s", m.group(1), "ok" if ok else "FAILED")
        self._reply(200 if ok else 502, b"ok\n" if ok else b"adb failed\n")

    def do_GET(self):
        if self.client_address[0] != TV_IP:
            return self._reply(403, b"forbidden\n")
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/glance":
            asked = (urllib.parse.parse_qs(url.query).get("leagues") or ["nfl,mlb,nhl,nba"])[0]
            leagues = {l for l in asked.split(",") if l in LEAGUE_ORDER}
            return self._json(200, glance(leagues))  # Orbit fetches its own weather now
        self._reply(405, b"POST /hdmi/<1-4>\n")

    def log_message(self, *_):
        pass  # one line per switch is logged above


HOMEWATCH_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "homewatch.sh")
HOMEWATCH_TV = "/data/local/tmp/homewatch.sh"
HOMEWATCH = setting("HOMEWATCH", "1") != "0"  # HOMEWATCH=0: don't push the Fire TV watcher (Android TV / Google TV)


def keep_homewatch_running():
    """Restart the on-TV Home-button watcher (homewatch.sh) after a TV reboot or ADB reconnect."""
    while True:
        try:
            if adb("shell", "pgrep", "-f", "[h]omewatch.sh").returncode != 0:
                subprocess.run(["adb", "connect", SERIAL], capture_output=True, text=True, timeout=5)
                if adb("shell", "pgrep", "-f", "[h]omewatch.sh").returncode != 0:
                    adb("push", HOMEWATCH_LOCAL, HOMEWATCH_TV, timeout=15)
                    adb("shell", "chmod", "755", HOMEWATCH_TV)
                    adb("shell", f"nohup {HOMEWATCH_TV} >/dev/null 2>&1 &")
                    logging.info("started homewatch on the TV")
        except (subprocess.TimeoutExpired, OSError) as e:
            logging.warning("homewatch check failed: %s", e)
        time.sleep(30)


if __name__ == "__main__":
    if HOMEWATCH:
        threading.Thread(target=keep_homewatch_running, daemon=True).start()
    logging.info("listening on %s:%d, accepting only %s", BIND, PORT, TV_IP)
    http.server.ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
