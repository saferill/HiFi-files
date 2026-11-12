#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auto-Heal Live Radio Synchronization for HiFi Music.

Strict 5-Stage Verification:
1. Canonical watch?v= ID extraction matching ^[A-Za-z0-9_-]{11}$
2. Page contains "isLiveNow":true and lacks "isUpcoming":true / LIVE_STREAM_OFFLINE
3. playabilityStatus.status == "OK" (rejects LOGIN_REQUIRED, UNPLAYABLE, ERROR, age-gate)
4. YouTube oEmbed endpoint returns HTTP 200
5. YouTube hqdefault thumbnail returns HTTP 200

Fallback & Stale Pruning:
- Candidate ID only replaces existing ID if all 5 checks pass.
- If checks fail, existing videoId is retained up to STALE_HOURS (72 hours).
- If failing for > 72h, station is excluded from remote-config.json (auto-pruned).
- Diagnostics are saved to registry/radio-status.json (never in remote-config.json).
"""

import sys
import os
import re
import json
import time
import random
import urllib.request
import urllib.error
from datetime import datetime, timezone

# Ensure real-time unbuffered output in terminal and workflows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

STALE_HOURS = 72
CONFIG_FILES = ["remote-config.json", "remote-config-dev.json"]
STATUS_REGISTRY = "registry/radio-status.json"

STATION_CHANNELS = [
    {
        "id": "soft-feelings",
        "title": "Soft Feelings Top Hits",
        "subtitle": "Easy Listening & Viral • LIVE",
        "handle": "@soft_feelings-25",
        "backupHandles": ["@liveradiomix"],
        "defaultVid": "GwWxRgH4nnA"
    },
    {
        "id": "live-radio-mix",
        "title": "Radio 2000s Pop Hits",
        "subtitle": "Nostalgia 2000s • LIVE",
        "handle": "@liveradiomix",
        "backupHandles": ["@soft_feelings-25"],
        "defaultVid": "0C4kZlNrhbI"
    },
    {
        "id": "the-good-life-radio",
        "title": "The Good Life Radio",
        "subtitle": "Deep House & Summer Chill • LIVE",
        "handle": "@TheGoodLifeRadio",
        "backupHandles": ["@liveradiomix"],
        "defaultVid": "IkmLXvBfVv0"
    },
    {
        "id": "lofi-girl",
        "title": "Lofi Hip Hop Radio",
        "subtitle": "Lofi Girl • LIVE",
        "handle": "@LofiGirl",
        "backupHandles": ["@ChillhopMusic"],
        "defaultVid": "4t4AQ15yj1M"
    },
    {
        "id": "chillhop-music",
        "title": "Chillhop Essentials",
        "subtitle": "Jazzy Beats • LIVE",
        "handle": "@ChillhopMusic",
        "backupHandles": ["@LofiGirl"],
        "defaultVid": "jiua2V9q9V0"
    },
    {
        "id": "cafe-jazz-tokyo",
        "title": "Tokyo Cafe Jazz BGM",
        "subtitle": "Cafe Music • LIVE",
        "handle": "@CafeMusicBGMchannel",
        "backupHandles": ["@YellowBrickCinema"],
        "defaultVid": "4REA-0zfAQ8"
    },
    {
        "id": "yellow-brick-cinema",
        "title": "Deep Sleep & Relax Piano",
        "subtitle": "Sleep & Meditation • LIVE",
        "handle": "@YellowBrickCinema",
        "backupHandles": ["@CafeMusicBGMchannel"],
        "defaultVid": "x1ij7uqBC7E"
    },
    {
        "id": "nasa-space-live",
        "title": "NASA Earth & Space Views",
        "subtitle": "ISS Space Live • LIVE",
        "handle": "@NASA",
        "backupHandles": ["@SkyNews"],
        "defaultVid": "ROboUHfPUrU"
    },
    {
        "id": "aljazeera-english",
        "title": "Al Jazeera English",
        "subtitle": "Global News • LIVE",
        "handle": "@AlJazeeraEnglish",
        "backupHandles": ["@SkyNews"],
        "defaultVid": "gCNeDWCI0vo"
    },
    {
        "id": "sky-news-live",
        "title": "Sky News UK & World",
        "subtitle": "Breaking News • LIVE",
        "handle": "@SkyNews",
        "backupHandles": ["@euronews"],
        "defaultVid": "23jDRUNoia4"
    },
    {
        "id": "abc-news-live",
        "title": "ABC News Live (USA)",
        "subtitle": "US & World News • LIVE",
        "handle": "@ABCNews",
        "backupHandles": ["@SkyNews"],
        "defaultVid": "x1t_803BM7Y"
    },
    {
        "id": "euronews-english",
        "title": "Euronews English",
        "subtitle": "European News • LIVE",
        "handle": "@euronews",
        "backupHandles": ["@AlJazeeraEnglish"],
        "defaultVid": "pykpO5kQJ98"
    },
    {
        "id": "makkah-live",
        "title": "Makkah Live & Al-Qur'an",
        "subtitle": "Masjidil Haram • LIVE",
        "handle": "@MakkahLiveHD",
        "backupHandles": ["@kompastv"],
        "defaultVid": "HMOPLGDCUyM"
    },
    {
        "id": "kompas-tv-live",
        "title": "Kompas TV Berita 24 Jam",
        "subtitle": "Berita Indonesia • LIVE",
        "handle": "@kompastv",
        "backupHandles": ["@MakkahLiveHD"],
        "defaultVid": "C1uJzmnkp3w"
    }
]

VID_REGEX = re.compile(r"^[A-Za-z0-9_-]{11}$")
CANONICAL_REGEX = re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"')
OG_URL_REGEX = re.compile(r'<meta property="og:url" content="https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"')
REDIRECT_REGEX = re.compile(r"watch\?v=([A-Za-z0-9_-]{11})")
PLAYABILITY_STATUS_REGEX = re.compile(r'"playabilityStatus":\s*\{[^}]*?"status":\s*"([^"]+)"')

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
COOKIE_HEADER = "SOCS=CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg; CONSENT=YES+cb.20210328-17-p0.en+FX+; PREF=tz=UTC&hl=en"

def is_transient_failure(url, html):
    """Detect bot challenges, consent redirects, or CAPTCHA gates."""
    url_lower = url.lower()
    if "consent.youtube.com" in url_lower or "consent.google.com" in url_lower or "before you continue to youtube" in html.lower():
        return True, "Redirected to Google/YouTube consent gate"
    if "/sorry/index" in url_lower or "action=\"captcharedirect\"" in html.lower() or "id=\"captcha-form\"" in html.lower():
        return True, "Encountered Google/YouTube CAPTCHA challenge"
    if "our systems have detected unusual traffic" in html.lower():
        return True, "Encountered unusual traffic block"
    if "ytInitialPlayerResponse" not in html and "ytInitialData" not in html:
        return True, "HTML missing player data structures (likely blocked or rate-limited)"
    return False, ""

def check_oembed(video_id):
    """Check if YouTube oEmbed endpoint returns HTTP 200."""
    url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cookie": COOKIE_HEADER})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception:
        return False

def check_thumbnail(video_id):
    """Check if YouTube hqdefault thumbnail returns HTTP 200."""
    url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception:
        return False

def resolve_live_id(handle, max_retries=2):
    """
    Fetch /live page and apply strict 5-stage validation.
    Returns (success: bool, video_id: str or None, reason: str)
    """
    url = f"https://www.youtube.com/{handle}/live"

    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                "Cookie": COOKIE_HEADER,
                "Cache-Control": "no-cache"
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                final_url = resp.geturl()
                # Read up to 1MB of HTML to capture header & player JSON without hanging on stream
                html = resp.read(1024 * 1024).decode("utf-8", errors="ignore")

            # Check transient failure
            is_transient, trans_reason = is_transient_failure(final_url, html)
            if is_transient:
                if attempt < max_retries:
                    wait_sec = 2.0 * (attempt + 1)
                    print(f"  [WARN] {handle}: {trans_reason}. Retrying in {wait_sec}s (attempt {attempt + 1}/{max_retries})...", flush=True)
                    time.sleep(wait_sec)
                    continue
                return False, None, f"Transient failure: {trans_reason}"

            # 1. Canonical ID extraction
            candidate_id = None
            m_canon = CANONICAL_REGEX.search(html)
            if m_canon:
                candidate_id = m_canon.group(1)
            else:
                m_og = OG_URL_REGEX.search(html)
                if m_og:
                    candidate_id = m_og.group(1)
                else:
                    m_furl = REDIRECT_REGEX.search(final_url)
                    if m_furl:
                        candidate_id = m_furl.group(1)
                    else:
                        m_vdetails = re.search(r'"videoDetails":\s*\{[^}]*?"videoId":\s*"([A-Za-z0-9_-]{11})"[^}]*?"isLive":\s*true', html)
                        if m_vdetails:
                            candidate_id = m_vdetails.group(1)

            if not candidate_id or not VID_REGEX.match(candidate_id):
                return False, None, "No canonical watch?v= link matching 11-char pattern found"

            # 2. Live Now & not upcoming/offline check
            has_live_now = ('"isLiveNow":true' in html) or ('"isLiveNow": true' in html)
            if not has_live_now:
                return False, candidate_id, "Missing 'isLiveNow':true in page content"

            has_upcoming = ('"isUpcoming":true' in html) or ('"isUpcoming": true' in html)
            has_offline = ("LIVE_STREAM_OFFLINE" in html)
            if has_upcoming or has_offline:
                return False, candidate_id, "Stream is marked as upcoming or offline"

            # 3. Playability Status check (status must be OK)
            m_play = PLAYABILITY_STATUS_REGEX.search(html)
            if not m_play:
                return False, candidate_id, "Missing playabilityStatus block in page"
            play_status = m_play.group(1)
            if play_status != "OK":
                return False, candidate_id, f"playabilityStatus is '{play_status}' (expected OK)"

            # 4. oEmbed check
            if not check_oembed(candidate_id):
                return False, candidate_id, "oEmbed endpoint returned non-200"

            # 5. Thumbnail check
            if not check_thumbnail(candidate_id):
                return False, candidate_id, "Thumbnail endpoint returned non-200"

            return True, candidate_id, "OK"

        except urllib.error.HTTPError as e:
            if attempt < max_retries and e.code in (429, 500, 502, 503):
                time.sleep(2.0 * (attempt + 1))
                continue
            return False, None, f"HTTP Error {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            if attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return False, None, f"Network error: {e.reason}"
        except Exception as e:
            if attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return False, None, f"Unexpected error: {str(e)}"

    return False, None, "Max retries exceeded"

def load_existing_video_ids():
    """Load existing video IDs from remote-config.json to prevent fallback to stale seeds."""
    target = CONFIG_FILES[0]
    if os.path.exists(target):
        try:
            with open(target, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return {st["id"]: st.get("videoId") for st in cfg.get("radioStations", []) if "id" in st}
        except Exception as e:
            print(f"[WARN] Failed to load existing video IDs from {target}: {e}", flush=True)
    return {}

def load_status_registry():
    """Load registry/radio-status.json."""
    if os.path.exists(STATUS_REGISTRY):
        try:
            with open(STATUS_REGISTRY, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] Failed to read {STATUS_REGISTRY}: {e}", flush=True)
    return {"lastUpdated": None, "stations": {}}

def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("=== DRY RUN MODE: No files will be modified ===", flush=True)

    print("Starting Hardened Auto-Heal Live Radio Synchronization...", flush=True)

    existing_vids = load_existing_video_ids()
    status_data = load_status_registry()
    station_records = status_data.setdefault("stations", {})

    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    active_stations = []
    summary_rows = []

    for item in STATION_CHANNELS:
        st_id = item["id"]
        primary_handle = item["handle"]
        current_cfg_vid = existing_vids.get(st_id) or item.get("defaultVid")
        record = station_records.get(st_id, {})

        handles_to_try = [primary_handle] + item.get("backupHandles", [])
        success = False
        candidate_vid = None
        reason = ""
        resolved_handle = primary_handle

        for h in handles_to_try:
            print(f"\nResolving [{st_id}] ({h})...", flush=True)
            success, candidate_vid, reason = resolve_live_id(h)
            time.sleep(random.uniform(0.8, 1.5))
            if success:
                resolved_handle = h
                break
            else:
                print(f"  [INFO] Handle {h} not live: {reason}. Trying next if available...", flush=True)

        if success:
            final_vid = candidate_vid
            passed_str = "PASS"
            status_desc = "OK"

            # Update diagnostics
            record["lastVerified"] = now_iso
            record["lastGoodVideoId"] = final_vid
            record["lastStatus"] = "OK"
            record["consecutiveFailures"] = 0
            record["lastChecked"] = now_iso
            station_records[st_id] = record

            active_stations.append({
                "id": st_id,
                "title": item["title"],
                "subtitle": item["subtitle"],
                "thumbnail": f"https://i.ytimg.com/vi/{final_vid}/hqdefault.jpg",
                "videoId": final_vid
            })
            summary_rows.append((st_id, current_cfg_vid, final_vid, passed_str, status_desc))
            print(f"  ✓ Verified LIVE: {final_vid}", flush=True)

        else:
            passed_str = "REJECT"
            last_ver_str = record.get("lastVerified")
            if last_ver_str:
                try:
                    last_ver_dt = datetime.fromisoformat(last_ver_str.replace("Z", "+00:00"))
                except Exception:
                    last_ver_dt = now_utc
            else:
                # If first time tracking and existing config has a station, seed grace period from now
                last_ver_dt = now_utc
                record["lastVerified"] = now_iso

            unverified_hours = (now_utc - last_ver_dt).total_seconds() / 3600.0
            failures = record.get("consecutiveFailures", 0) + 1
            record["consecutiveFailures"] = failures
            record["lastChecked"] = now_iso

            if unverified_hours > STALE_HOURS:
                status_desc = f"PRUNED (> {STALE_HOURS}h stale): {reason}"
                record["lastStatus"] = status_desc
                station_records[st_id] = record
                summary_rows.append((st_id, current_cfg_vid, "-", "PRUNED", status_desc))
                print(f"  ✗ Pruned station: unverified for {unverified_hours:.1f}h (> {STALE_HOURS}h). Reason: {reason}", flush=True)
            else:
                fallback_vid = current_cfg_vid or record.get("lastGoodVideoId") or item.get("defaultVid")
                status_desc = f"KEPT_FALLBACK ({unverified_hours:.1f}h/{STALE_HOURS}h): {reason}"
                record["lastStatus"] = status_desc
                station_records[st_id] = record

                active_stations.append({
                    "id": st_id,
                    "title": item["title"],
                    "subtitle": item["subtitle"],
                    "thumbnail": f"https://i.ytimg.com/vi/{fallback_vid}/hqdefault.jpg",
                    "videoId": fallback_vid
                })
                summary_rows.append((st_id, current_cfg_vid, fallback_vid, passed_str, status_desc))
                print(f"  ⚠ Verification failed ({reason}). Retained fallback: {fallback_vid} (stale {unverified_hours:.1f}h)", flush=True)

    # Print Summary Table
    print("\n" + "=" * 115, flush=True)
    print(f"{'STATION ID':<22} | {'OLD VIDEO ID':<13} | {'NEW VIDEO ID':<13} | {'STATUS':<7} | {'REASON'}", flush=True)
    print("=" * 115, flush=True)
    for sid, old_v, new_v, status_val, rsn in summary_rows:
        print(f"{sid:<22} | {str(old_v):<13} | {str(new_v):<13} | {status_val:<7} | {rsn}", flush=True)
    print("=" * 115, flush=True)
    print(f"Active Stations Count: {len(active_stations)} / {len(STATION_CHANNELS)}", flush=True)

    if not dry_run:
        # Save Status Registry
        status_data["lastUpdated"] = now_iso
        os.makedirs(os.path.dirname(STATUS_REGISTRY), exist_ok=True)
        with open(STATUS_REGISTRY, "w", encoding="utf-8") as f:
            json.dump(status_data, f, indent=2, ensure_ascii=False)
        print(f"Saved diagnostic telemetry to {STATUS_REGISTRY}", flush=True)

        # Update Remote Configs
        for cfg_path in CONFIG_FILES:
            if not os.path.exists(cfg_path):
                continue
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                cfg["radioStations"] = active_stations
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2, ensure_ascii=False)
                print(f"Successfully updated {cfg_path} with {len(active_stations)} stations.", flush=True)
            except Exception as e:
                print(f"[ERROR] Failed to update {cfg_path}: {e}", flush=True)
    else:
        print("[DRY RUN] File write skipped.", flush=True)

    # Script exits with error code ONLY if ALL stations failed (zero active stations)
    if len(active_stations) == 0:
        print("\n[CRITICAL] All stations failed validation or were pruned!", flush=True)
        sys.exit(1)

    print("\nSynchronization completed successfully.", flush=True)
    sys.exit(0)

if __name__ == "__main__":
    main()
