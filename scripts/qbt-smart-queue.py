#!/usr/usr/bin/env python3
"""
Pravaha Smart Queue Daemon v5 — Blocklist-Aware
=================================================
Key feature: When nuking stalled torrents, delete through
the manager APIs with blocklist=true so the same dead torrent
is never re-grabbed. Falls back to direct download-client delete for orphans.

NOTE: Replace the placeholders with your actual API keys.
"""
import json
import urllib.request
import urllib.parse
import os
import time

# --- CONFIGURATION (Replace with your actual details) ---
QBIT_URL = "http://localhost:8080"
QBIT_USER = "admin"
QBIT_PASS = "YOUR_PASSWORD_HERE"
CREDS = f"username={QBIT_USER}&password={QBIT_PASS}".encode()

LOG_FILE = "/var/log/qbt-smart-queue.log"
STATE_FILE = "/tmp/qbt-stall-state.json"

SONARR_URL = "http://localhost:8989/api/v3"
SONARR_KEY = "YOUR_SONARR_API_KEY_HERE"

RADARR_URL = "http://localhost:7878/api/v3"
# You can hardcode this or read it from config.xml
RADARR_KEY_FILE = "/opt/media-stack/config/radarr/config.xml" 

STALL_TIMEOUT = 5
META_TIMEOUT  = 3
# --------------------------------------------------------

def log(msg):
    with open(LOG_FILE, 'a') as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    print(msg)

def get_cookie():
    req = urllib.request.Request(f"{QBIT_URL}/api/v2/auth/login", data=CREDS)
    resp = urllib.request.urlopen(req, timeout=10)
    cookie = resp.headers.get('set-cookie')
    return cookie.split(';')[0] if cookie else ""

def api_get(endpoint, cookie):
    req = urllib.request.Request(f"{QBIT_URL}{endpoint}")
    req.add_header("Cookie", cookie)
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read().decode())

def api_post(endpoint, cookie, data):
    encoded = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(f"{QBIT_URL}{endpoint}", data=encoded)
    req.add_header("Cookie", cookie)
    urllib.request.urlopen(req, timeout=10)

def get_usable_space():
    st = os.statvfs('/mnt/torrents')
    free_gb = (st.f_bavail * st.f_frsize) / (1024**3)
    return max(0, free_gb - 10)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def get_radarr_key():
    try:
        with open(RADARR_KEY_FILE) as f:
            import re
            m = re.search(r'<ApiKey>([^<]+)</ApiKey>', f.read())
            return m.group(1) if m else ""
    except:
        return "YOUR_RADARR_API_KEY_HERE"

def arr_api(base_url, api_key, method, endpoint, data=None):
    url = f"{base_url}{endpoint}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method,
        headers={"X-Api-Key": api_key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except:
        return None

def build_hash_to_queue_map():
    """Build a map of download hash -> (arr_type, queue_id) for smart deletion."""
    hash_map = {}

    # Sonarr queue
    sonarr_q = arr_api(SONARR_URL, SONARR_KEY, "GET", "/queue?pageSize=500")
    if sonarr_q:
        for r in sonarr_q.get("records", []):
            dl_id = r.get("downloadId", "").lower()
            if dl_id:
                hash_map[dl_id] = ("sonarr", r["id"])

    # Radarr queue
    radarr_key = get_radarr_key()
    if radarr_key:
        radarr_q = arr_api(RADARR_URL, radarr_key, "GET", "/queue?pageSize=500")
        if radarr_q:
            for r in radarr_q.get("records", []):
                dl_id = r.get("downloadId", "").lower()
                if dl_id:
                    hash_map[dl_id] = ("radarr", r["id"])

    return hash_map

def nuke_via_arr(torrent_hash, hash_map):
    """Delete torrent through API with blocklist=true.
    Returns True if handled, False if needs direct delete."""
    key = torrent_hash.lower()
    if key not in hash_map:
        return False

    arr_type, queue_id = hash_map[key]
    if arr_type == "sonarr":
        result = arr_api(SONARR_URL, SONARR_KEY, "DELETE",
            f"/queue/{queue_id}?removeFromClient=true&blocklist=true&skipRedownload=false")
    else:
        radarr_key = get_radarr_key()
        result = arr_api(RADARR_URL, radarr_key, "DELETE",
            f"/queue/{queue_id}?removeFromClient=true&blocklist=true&skipRedownload=false")

    return result is not None

def clear_sonarr_blocked():
    """Auto-clear queue items blocked by 'Not an upgrade' warnings."""
    try:
        sonarr_q = arr_api(SONARR_URL, SONARR_KEY, "GET", "/queue?pageSize=500")
        if not sonarr_q:
            return
        blocked_ids = []
        for r in sonarr_q.get("records", []):
            msgs = " ".join([m for sm in r.get("statusMessages", []) for m in sm.get("messages", [])])
            if "Not a Custom Format upgrade" in msgs or "Not an upgrade for existing" in msgs:
                blocked_ids.append(r["id"])
        if blocked_ids:
            arr_api(SONARR_URL, SONARR_KEY, "DELETE",
                "/queue/bulk?removeFromClient=true&blocklist=false",
                {"ids": blocked_ids})
            log(f"[MANAGER] Auto-cleared {len(blocked_ids)} 'not an upgrade' items.")
    except Exception as e:
        log(f"[MANAGER] Cleanup error: {e}")

def main():
    cookie = get_cookie()
    torrents = api_get("/api/v2/torrents/info", cookie)
    prev_state = load_state()
    new_state = {}
    now = time.time()
    usable_gb = get_usable_space()

    if not torrents:
        log("--- No torrents ---")
        return

    # Build hash-to-queue map ONCE for this cycle
    hash_map = build_hash_to_queue_map()

    ACTIVE_STATES  = {'downloading', 'stalledDL', 'metaDL', 'forcedDL'}
    SEEDING_STATES = {'uploading', 'stalledUP', 'pausedUP', 'queuedUP', 'stoppedUP'}
    QUEUE_STATES   = {'queuedDL'}
    IGNORE_STATES  = {'pausedDL', 'stoppedDL', 'checkingDL', 'checkingUP', 'checkingResumeData'}

    nuked = 0
    nuked_blocklisted = 0
    nuked_direct = 0
    nuke_direct_batch = []
    active_count = 0
    queued_count = 0
    survivors = []

    for t in torrents:
        hash_ = t['hash']
        name  = t['name']
        state = t['state']
        seeds = t['num_seeds']
        peers = t['num_leechs']
        speed = t['dlspeed']
        prog  = t['progress']

        if state in SEEDING_STATES or state in IGNORE_STATES:
            continue

        if state in QUEUE_STATES:
            queued_count += 1
            remaining_gb = max(0, (t['size'] - t['downloaded']) / (1024**3))
            survivors.append({'hash': hash_, 'name': name, 'score': 0, 'remaining_gb': remaining_gb, 'state': state})
            continue

        if state in ACTIVE_STATES:
            active_count += 1
            is_sick = False
            reason  = ""
            timeout = 999

            if state == 'metaDL':
                is_sick = True
                reason  = "stuck metadata"
                timeout = META_TIMEOUT
            elif state == 'stalledDL':
                is_sick = True
                reason  = "stalledDL"
                timeout = STALL_TIMEOUT
            elif state in ('downloading', 'forcedDL') and speed == 0:
                is_sick = True
                reason  = "0 B/s"
                timeout = STALL_TIMEOUT

            if is_sick:
                first_seen = prev_state.get(hash_, now)
                new_state[hash_] = first_seen
                sick_mins = (now - first_seen) / 60

                if sick_mins >= timeout:
                    log(f"[NUKE] ({reason}, {sick_mins:.0f}m, {prog*100:.0f}%) {name[:70]}")

                    # Try to delete via APIs (with blocklist)
                    if nuke_via_arr(hash_, hash_map):
                        log(f"  → Blocklisted via API")
                        nuked_blocklisted += 1
                    else:
                        # Orphan torrent — delete directly
                        nuke_direct_batch.append(hash_)
                        nuked_direct += 1

                    nuked += 1
                    continue

            remaining_gb = max(0, (t['size'] - t['downloaded']) / (1024**3))
            score = (seeds * 3) + (peers * 1) + (prog * 50)
            survivors.append({'hash': hash_, 'name': name, 'score': score, 'remaining_gb': remaining_gb, 'state': state})

    save_state(new_state)

    # Direct-delete orphan torrents
    if nuke_direct_batch:
        batch_str = "|".join(nuke_direct_batch)
        try:
            api_post("/api/v2/torrents/delete", cookie, {"hashes": batch_str, "deleteFiles": "true"})
        except Exception as e:
            log(f"Direct delete error: {e}")

    # Queue management
    target_active = 5
    if survivors:
        survivors.sort(key=lambda x: x['score'], reverse=True)
        calc_active = 0
        running_total = 0
        hashes_in_order = []
        for t in survivors:
            hashes_in_order.append(t['hash'])
            if running_total + t['remaining_gb'] <= usable_gb:
                running_total += t['remaining_gb']
                calc_active += 1
        target_active = max(1, min(5, calc_active))
        api_post("/api/v2/app/setPreferences", cookie, {"json": json.dumps({'max_active_downloads': target_active})})
        if hashes_in_order:
            api_post("/api/v2/torrents/topPrio", cookie, {"hashes": "|".join(hashes_in_order)})
        stalled = [t['hash'] for t in survivors if t['state'] == 'stalledDL']
        if stalled:
            api_post("/api/v2/torrents/reannounce", cookie, {"hashes": "|".join(stalled)})

    log(f"--- Done | Nuked: {nuked} (BL:{nuked_blocklisted} Direct:{nuked_direct}) | Active: {active_count} | Queued: {queued_count} | Slots: {target_active} | Space: {usable_gb:.0f}GB ---")

    clear_sonarr_blocked()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL: {e}")
