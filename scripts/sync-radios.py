#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hardened Auto-Heal Live Radio Synchronization for HiFi Music.

Multi-Method Resilient Resolution (works on both local & datacenter/CI IPs):
1. Innertube navigation/resolve_url API for /@handle/live
2. Direct HTML /@handle/live & /@handle/streams extraction
3. yt-dlp --get-id fallback (when installed, e.g. in GitHub Actions)
4. Innertube youtubei/v1/search with Live filter (EgJAAQ%3D%3D)

Strict Live Verification:
- Must match ^[A-Za-z0-9_-]{11}$ and not duplicate another station's videoId
- Verified live via watch HTML ("isLiveNow":true, playabilityStatus == "OK")
  OR Innertube player API (videoDetails.isLive == true)
- oEmbed endpoint returns HTTP 200
- Thumbnail hqdefault.jpg returns HTTP 200

Dead-Stream Rejection:
- If primary/backup handles fail, explicitly verifies if existing videoId is STILL live.
- If existing videoId is confirmed ended/dead (isLive == False), it is NOT kept for 72h;
  it is pruned immediately unless a live replacement is found.
"""

import sys
import os
import re
import json
import time
import random
import shutil
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

TRANSIENT_STALE_HOURS = 6
CONFIG_FILES = ["remote-config.json", "remote-config-dev.json"]
STATUS_REGISTRY = "registry/radio-status.json"

STATION_CHANNELS = [
    {
        "id": "soft-feelings",
        "title": "Soft Feelings Top Hits",
        "subtitle": "Easy Listening & Viral • LIVE",
        "handle": "@soft_feelings-25",
        "searchQuery": "Soft Feelings live radio",
        "backupHandles": ["@liveradiomix"],
        "defaultVid": "GwWxRgH4nnA"
    },
    {
        "id": "live-radio-mix",
        "title": "Radio 2000s Pop Hits",
        "subtitle": "Nostalgia 2000s • LIVE",
        "handle": "@liveradiomix",
        "searchQuery": "Live Radio Mix 2000s pop hits live",
        "backupHandles": ["@soft_feelings-25"],
        "defaultVid": "0C4kZlNrhbI"
    },
    {
        "id": "the-good-life-radio",
        "title": "The Good Life Radio",
        "subtitle": "Deep House & Summer Chill • LIVE",
        "handle": "@TheGoodLifeRadio",
        "searchQuery": "The Good Life Radio x Sensual Musique live",
        "backupHandles": ["@liveradiomix"],
        "defaultVid": "IkmLXvBfVv0"
    },
    {
        "id": "lofi-girl",
        "title": "Lofi Hip Hop Radio",
        "subtitle": "Lofi Girl • LIVE",
        "handle": "@LofiGirl",
        "searchQuery": "Lofi Girl beats to relax study to live",
        "backupHandles": ["@ChillhopMusic"],
        "defaultVid": "4t4AQ15yj1M"
    },
    {
        "id": "chillhop-music",
        "title": "Chillhop Essentials",
        "subtitle": "Jazzy Beats • LIVE",
        "handle": "@ChillhopMusic",
        "searchQuery": "Chillhop Radio jazzy lofi hip hop beats live",
        "backupHandles": ["@LofiGirl"],
        "defaultVid": "jiua2V9q9V0"
    },
    {
        "id": "cafe-jazz-tokyo",
        "title": "Tokyo Cafe Jazz BGM",
        "subtitle": "Cafe Music • LIVE",
        "handle": "@CafeMusicBGMchannel",
        "searchQuery": "Cafe Music BGM channel jazz live",
        "backupHandles": ["@YellowBrickCinema"],
        "defaultVid": "WMi37od24aw"
    },
    {
        "id": "yellow-brick-cinema",
        "title": "Deep Sleep & Relax Piano",
        "subtitle": "Sleep & Meditation • LIVE",
        "handle": "@YellowBrickCinema",
        "searchQuery": "Yellow Brick Cinema relaxing sleep music live",
        "backupHandles": ["@CafeMusicBGMchannel"],
        "defaultVid": "ZQVu-nzhPT8"
    },
    {
        "id": "nasa-space-live",
        "title": "NASA Earth & Space Views",
        "subtitle": "ISS Space Live • LIVE",
        "handle": "@NASA",
        "searchQuery": "NASA Live Earth from Space ISS",
        "backupHandles": ["@SpaceVideos"],
        "defaultVid": "ROboUHfPUrU"
    },
    {
        "id": "aljazeera-english",
        "title": "Al Jazeera English",
        "subtitle": "Global News • LIVE",
        "handle": "@AlJazeeraEnglish",
        "searchQuery": "Al Jazeera English Live",
        "backupHandles": ["@SkyNews"],
        "defaultVid": "gCNeDWCI0vo"
    },
    {
        "id": "sky-news-live",
        "title": "Sky News UK & World",
        "subtitle": "Breaking News • LIVE",
        "handle": "@SkyNews",
        "searchQuery": "Sky News Live",
        "backupHandles": ["@euronews"],
        "defaultVid": "umLWFz4izTM"
    },
    {
        "id": "abc-news-live",
        "title": "ABC News Live (USA)",
        "subtitle": "US & World News • LIVE",
        "handle": "@ABCNews",
        "searchQuery": "ABC News Live 24/7",
        "backupHandles": ["@SkyNews"],
        "defaultVid": "8JK_3tFkXe0"
    },
    {
        "id": "euronews-english",
        "title": "Euronews English",
        "subtitle": "European News • LIVE",
        "handle": "@euronews",
        "searchQuery": "Euronews English Live",
        "backupHandles": ["@AlJazeeraEnglish"],
        "defaultVid": "pykpO5kQJ98"
    },
    {
        "id": "makkah-live",
        "title": "Makkah Live & Al-Qur'an",
        "subtitle": "Masjidil Haram • LIVE",
        "handle": "@MakkahLiveHD",
        "searchQuery": "Makkah Live HD Masjidil Haram",
        "backupHandles": ["@HaramainInfo"],
        "defaultVid": "HMOPLGDCUyM"
    },
    {
        "id": "kompas-tv-live",
        "title": "Kompas TV Berita 24 Jam",
        "subtitle": "Berita Indonesia • LIVE",
        "handle": "@kompastv",
        "searchQuery": "LIVE STREAMING 24 JAM KOMPASTV",
        "backupHandles": ["@CNNIndonesia"],
        "defaultVid": "DOOrIxw5xOw"
    }
]

VID_REGEX = re.compile(r"^[A-Za-z0-9_-]{11}$")
CANONICAL_REGEX = re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"')
OG_URL_REGEX = re.compile(r'<meta property="og:url" content="https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"')
REDIRECT_REGEX = re.compile(r"watch\?v=([A-Za-z0-9_-]{11})")
PLAYABILITY_STATUS_REGEX = re.compile(r'"playabilityStatus":\s*\{[^}]*?"status":\s*"([^"]+)"')

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
COOKIE_HEADER = "SOCS=CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg; CONSENT=YES+cb.20210328-17-p0.en+FX+; PREF=tz=UTC&hl=en"


def is_transient_failure(url, html):
    url_lower = url.lower()
    html_lower = html.lower()
    if "consent.youtube.com" in url_lower or "consent.google.com" in url_lower or "before you continue to youtube" in html_lower:
        return True, "Consent gate"
    if "/sorry/index" in url_lower or 'action="captcharedirect"' in html_lower or 'id="captcha-form"' in html_lower:
        return True, "CAPTCHA challenge"
    if "our systems have detected unusual traffic" in html_lower:
        return True, "Unusual traffic block"
    if "ytInitialPlayerResponse" not in html and "ytInitialData" not in html:
        return True, "Missing player data structures"
    return False, ""


def check_oembed(video_id):
    url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception:
        return False


def check_thumbnail(video_id):
    url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception:
        return False


def verify_video_is_live(video_id):
    """
    Strictly verify that a given 11-char videoId is currently LIVE and playable.
    Returns (status: 'LIVE' | 'DEAD' | 'TRANSIENT', reason: str)
    """
    if not video_id or not VID_REGEX.match(video_id):
        return "DEAD", "Invalid videoId format"

    # 1. Check Innertube player API (works even when watch HTML is stripped on datacenter IPs)
    player_is_live = None
    player_reason = ""
    try:
        url = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"
        payload = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20250101.01.00",
                    "hl": "en",
                    "gl": "US"
                }
            },
            "videoId": video_id
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.load(resp)
        vd = data.get("videoDetails", {})
        ps = data.get("playabilityStatus", {}).get("status", "")
        if ps == "ERROR":
            return "DEAD", f"Player status {ps}"
        if "isLive" in vd:
            player_is_live = bool(vd.get("isLive"))
            if not player_is_live:
                return "DEAD", "Stream has ended (videoDetails.isLive=False)"
        # Note: ps == "LOGIN_REQUIRED" is a datacenter IP bot-check on /player,
        # NOT a dead stream; fall through to Innertube /next & watch HTML.
    except Exception as e:
        player_reason = str(e)

    # 1b. Check Innertube /next API (works reliably on datacenter IPs without LOGIN_REQUIRED)
    try:
        next_url = "https://www.youtube.com/youtubei/v1/next?prettyPrint=false"
        next_payload = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20250101.01.00",
                    "hl": "en",
                    "gl": "US"
                }
            },
            "videoId": video_id
        }
        next_req = urllib.request.Request(
            next_url,
            data=json.dumps(next_payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(next_req, timeout=8) as resp:
            next_txt = resp.read().decode("utf-8", errors="ignore")
        next_lower = next_txt.lower()
        if '"islive":true' in next_lower or "watching now" in next_lower:
            if check_oembed(video_id) and check_thumbnail(video_id):
                return "LIVE", "OK (verified via Innertube next + oEmbed)"
            return "DEAD", "oEmbed or thumbnail check failed"
        if "streamed live" in next_lower or '"islive":false' in next_lower:
            return "DEAD", "Stream has ended (Innertube next)"
    except Exception as e:
        if not player_reason:
            player_reason = str(e)

    # 2. Check watch HTML for isLiveNow & playabilityStatus
    try:
        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        req = urllib.request.Request(watch_url, headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Cookie": COOKIE_HEADER,
            "Cache-Control": "no-cache"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            final_url = resp.geturl()
            html = resp.read(1024 * 1024).decode("utf-8", errors="ignore")

        is_trans, trans_reason = is_transient_failure(final_url, html)
        if not is_trans:
            has_live_now = ('"isLiveNow":true' in html) or ('"isLiveNow": true' in html)
            has_upcoming = ('"isUpcoming":true' in html) or ('"isUpcoming": true' in html)
            has_offline = ("LIVE_STREAM_OFFLINE" in html)
            m_play = PLAYABILITY_STATUS_REGEX.search(html)
            play_status = m_play.group(1) if m_play else ""

            if has_upcoming or has_offline or not has_live_now:
                return "DEAD", "Watch page indicates stream is not live (ended/upcoming/offline)"
            if play_status and play_status != "OK":
                return "DEAD", f"playabilityStatus={play_status}"
            if check_oembed(video_id) and check_thumbnail(video_id):
                return "LIVE", "OK"
            return "DEAD", "oEmbed or thumbnail check failed"
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return "DEAD", f"HTTP {e.code}"
    except Exception:
        pass

    # If watch HTML hit a transient gate on CI, trust player_is_live + oEmbed + thumbnail
    if player_is_live is True and check_oembed(video_id) and check_thumbnail(video_id):
        return "LIVE", "OK (verified via Innertube player + oEmbed)"

    return "TRANSIENT", player_reason or "Transient verification failure"


def resolve_via_innertube_url(handle):
    """Method 1: Resolve /@handle/live via Innertube navigation/resolve_url JSON API."""
    url = "https://www.youtube.com/youtubei/v1/navigation/resolve_url?prettyPrint=false"
    payload = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": "2.20250101.01.00",
                "hl": "en",
                "gl": "US"
            }
        },
        "url": f"https://www.youtube.com/{handle}/live"
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.load(resp)
            vid = data.get("endpoint", {}).get("watchEndpoint", {}).get("videoId")
            if vid and VID_REGEX.match(vid):
                return vid
    except Exception:
        pass
    return None


def resolve_via_html(handle):
    """Method 2: Fetch /@handle/live and /@handle/streams HTML."""
    for suffix in ("live", "streams"):
        url = f"https://www.youtube.com/{handle}/{suffix}"
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Cookie": COOKIE_HEADER,
            "Cache-Control": "no-cache"
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                final_url = resp.geturl()
                html = resp.read(1024 * 1024).decode("utf-8", errors="ignore")
            is_trans, _ = is_transient_failure(final_url, html)
            if is_trans:
                continue
            if suffix == "live":
                for rx in (CANONICAL_REGEX, OG_URL_REGEX):
                    m = rx.search(html)
                    if m:
                        return m.group(1)
                m_f = REDIRECT_REGEX.search(final_url)
                if m_f:
                    return m_f.group(1)
            else:
                # Look for live badge near videoId in /streams
                matches = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})".{0,1500}?"BADGE_STYLE_TYPE_LIVE_NOW"', html)
                if matches:
                    return matches[0]
        except Exception:
            continue
    return None


def resolve_via_ytdlp(handle):
    """Method 3: Use yt-dlp --get-id if installed in environment (e.g. GitHub Actions)."""
    if not shutil.which("yt-dlp"):
        return None
    try:
        proc = subprocess.run(
            ["yt-dlp", "--get-id", "--no-playlist", "--no-warnings", f"https://www.youtube.com/{handle}/live"],
            capture_output=True,
            text=True,
            timeout=15
        )
        if proc.returncode == 0:
            vid = proc.stdout.strip().splitlines()[0].strip()
            if VID_REGEX.match(vid):
                return vid
    except Exception:
        pass
    return None


def resolve_via_search(query):
    """Method 4: Innertube search with Live filter (EgJAAQ%3D%3D)."""
    if not query:
        return []
    url = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
    payload = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": "2.20250101.01.00",
                "hl": "en",
                "gl": "US"
            }
        },
        "query": query,
        "params": "EgJAAQ%3D%3D"
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}
    )
    candidates = []
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.load(resp)
        contents = data.get("contents", {}).get("twoColumnSearchResultsRenderer", {}).get("primaryContents", {}).get("sectionListRenderer", {}).get("contents", [])
        for sec in contents:
            for it in sec.get("itemSectionRenderer", {}).get("contents", []):
                vr = it.get("videoRenderer")
                if not vr:
                    continue
                vid = vr.get("videoId")
                badges = str(vr.get("badges", [])) + str(vr.get("thumbnailOverlays", []))
                if vid and VID_REGEX.match(vid) and "LIVE" in badges:
                    candidates.append(vid)
                    if len(candidates) >= 3:
                        return candidates
    except Exception:
        pass
    return candidates


def find_live_id_for_station(item, used_vids):
    """Try all resolution methods for a station and return (success, vid, reason)."""
    handles = [item["handle"]] + item.get("backupHandles", [])

    # 1. Try handle-based resolvers first (most accurate for exact channel)
    for h in handles:
        candidates = []
        for resolver_name, resolver_fn in (
            ("Innertube resolve_url", resolve_via_innertube_url),
            ("HTML /live+/streams", resolve_via_html),
            ("yt-dlp", resolve_via_ytdlp),
        ):
            vid = resolver_fn(h)
            if vid and vid not in candidates and vid not in used_vids:
                candidates.append(vid)
                status, reason = verify_video_is_live(vid)
                if status == "LIVE":
                    return True, vid, f"OK ({h} via {resolver_name})"
                else:
                    print(f"  [INFO] {h} candidate {vid} via {resolver_name} rejected: {reason}", flush=True)

    # 2. Fallback to Innertube Live Search for the station
    search_q = item.get("searchQuery")
    if search_q:
        for vid in resolve_via_search(search_q):
            if vid in used_vids:
                continue
            status, reason = verify_video_is_live(vid)
            if status == "LIVE":
                return True, vid, "OK (via Live Search)"

    return False, None, "All live resolution methods returned no active live stream"


def load_existing_video_ids():
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
    used_vids = set()
    summary_rows = []

    for item in STATION_CHANNELS:
        st_id = item["id"]
        current_cfg_vid = existing_vids.get(st_id) or item.get("defaultVid")
        record = station_records.get(st_id, {})

        print(f"\nResolving [{st_id}] ({item['handle']})...", flush=True)
        success, candidate_vid, reason = find_live_id_for_station(item, used_vids)
        time.sleep(random.uniform(0.5, 1.0))

        if success:
            used_vids.add(candidate_vid)
            record["lastVerified"] = now_iso
            record["lastGoodVideoId"] = candidate_vid
            record["lastStatus"] = reason
            record["consecutiveFailures"] = 0
            record["lastChecked"] = now_iso
            station_records[st_id] = record

            active_stations.append({
                "id": st_id,
                "title": item["title"],
                "subtitle": item["subtitle"],
                "thumbnail": f"https://i.ytimg.com/vi/{candidate_vid}/hqdefault.jpg",
                "videoId": candidate_vid
            })
            summary_rows.append((st_id, current_cfg_vid, candidate_vid, "PASS", reason))
            print(f"  ✓ Verified LIVE: {candidate_vid} ({reason})", flush=True)

        else:
            # Explicitly check whether the existing videoId is STILL live or confirmed dead
            existing_status, existing_reason = (
                verify_video_is_live(current_cfg_vid)
                if current_cfg_vid and current_cfg_vid not in used_vids
                else ("DEAD", "Duplicate or missing ID")
            )

            if existing_status == "LIVE":
                used_vids.add(current_cfg_vid)
                record["lastVerified"] = now_iso
                record["lastGoodVideoId"] = current_cfg_vid
                record["lastStatus"] = f"KEPT_LIVE: Existing stream still live ({existing_reason})"
                record["consecutiveFailures"] = 0
                record["lastChecked"] = now_iso
                station_records[st_id] = record

                active_stations.append({
                    "id": st_id,
                    "title": item["title"],
                    "subtitle": item["subtitle"],
                    "thumbnail": f"https://i.ytimg.com/vi/{current_cfg_vid}/hqdefault.jpg",
                    "videoId": current_cfg_vid
                })
                summary_rows.append((st_id, current_cfg_vid, current_cfg_vid, "PASS", "Existing stream verified LIVE"))
                print(f"  ✓ Existing videoId still LIVE: {current_cfg_vid}", flush=True)

            elif existing_status == "DEAD":
                # Confirmed dead! Do NOT keep dead streams in remote-config.json!
                failures = record.get("consecutiveFailures", 0) + 1
                record["consecutiveFailures"] = failures
                record["lastChecked"] = now_iso
                status_desc = f"PRUNED_DEAD: {existing_reason}"
                record["lastStatus"] = status_desc
                station_records[st_id] = record
                summary_rows.append((st_id, current_cfg_vid, "-", "PRUNED", status_desc))
                print(f"  ✗ Confirmed DEAD ({existing_reason}). Removed from active list!", flush=True)

            else:
                # Transient network failure: only keep up to TRANSIENT_STALE_HOURS (6h)
                last_ver_str = record.get("lastVerified")
                try:
                    last_ver_dt = datetime.fromisoformat(last_ver_str.replace("Z", "+00:00")) if last_ver_str else now_utc
                except Exception:
                    last_ver_dt = now_utc

                unverified_hours = (now_utc - last_ver_dt).total_seconds() / 3600.0
                failures = record.get("consecutiveFailures", 0) + 1
                record["consecutiveFailures"] = failures
                record["lastChecked"] = now_iso

                if unverified_hours > TRANSIENT_STALE_HOURS:
                    status_desc = f"PRUNED_STALE ({unverified_hours:.1f}h/{TRANSIENT_STALE_HOURS}h): {reason}"
                    record["lastStatus"] = status_desc
                    station_records[st_id] = record
                    summary_rows.append((st_id, current_cfg_vid, "-", "PRUNED", status_desc))
                    print(f"  ✗ Pruned stale station ({unverified_hours:.1f}h).", flush=True)
                else:
                    used_vids.add(current_cfg_vid)
                    status_desc = f"KEPT_TRANSIENT ({unverified_hours:.1f}h/{TRANSIENT_STALE_HOURS}h): {existing_reason}"
                    record["lastStatus"] = status_desc
                    station_records[st_id] = record

                    active_stations.append({
                        "id": st_id,
                        "title": item["title"],
                        "subtitle": item["subtitle"],
                        "thumbnail": f"https://i.ytimg.com/vi/{current_cfg_vid}/hqdefault.jpg",
                        "videoId": current_cfg_vid
                    })
                    summary_rows.append((st_id, current_cfg_vid, current_cfg_vid, "WARN", status_desc))
                    print(f"  ⚠ Transient check ({existing_reason}). Kept temporarily ({unverified_hours:.1f}h).", flush=True)

    print("\n" + "=" * 120, flush=True)
    print(f"{'STATION ID':<22} | {'OLD VIDEO ID':<13} | {'NEW VIDEO ID':<13} | {'STATUS':<7} | {'REASON'}", flush=True)
    print("=" * 120, flush=True)
    for sid, old_v, new_v, status_val, rsn in summary_rows:
        print(f"{sid:<22} | {str(old_v):<13} | {str(new_v):<13} | {status_val:<7} | {rsn}", flush=True)
    print("=" * 120, flush=True)
    print(f"Active Stations Count: {len(active_stations)} / {len(STATION_CHANNELS)}", flush=True)

    if not dry_run:
        status_data["lastUpdated"] = now_iso
        os.makedirs(os.path.dirname(STATUS_REGISTRY), exist_ok=True)
        with open(STATUS_REGISTRY, "w", encoding="utf-8") as f:
            json.dump(status_data, f, indent=2, ensure_ascii=False)
        print(f"Saved diagnostic telemetry to {STATUS_REGISTRY}", flush=True)

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

    if len(active_stations) == 0:
        print("\n[CRITICAL] All stations failed validation or were pruned!", flush=True)
        sys.exit(1)

    print("\nSynchronization completed successfully.", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
