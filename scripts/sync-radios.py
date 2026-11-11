# -*- coding: utf-8 -*-
import urllib.request, re, json, os

CONFIG_FILES = ["remote-config.json", "remote-config-dev.json"]

STATION_CHANNELS = [
    {
        "id": "soft-feelings",
        "title": "Soft Feelings Top Hits",
        "subtitle": "Easy Listening & Viral • LIVE",
        "handle": "@soft_feelings-25",
        "defaultVid": "GwWxRgH4nnA"
    },
    {
        "id": "live-radio-mix",
        "title": "Radio 2000s Pop Hits",
        "subtitle": "Nostalgia 2000s • LIVE",
        "handle": "@liveradiomix",
        "defaultVid": "0C4kZlNrhbI"
    },
    {
        "id": "the-good-life-radio",
        "title": "The Good Life Radio",
        "subtitle": "Deep House & Summer Chill • LIVE",
        "handle": "@TheGoodLifeRadio",
        "defaultVid": "IkmLXvBfVv0"
    },
    {
        "id": "lofi-girl",
        "title": "Lofi Hip Hop Radio",
        "subtitle": "Lofi Girl • LIVE",
        "handle": "@LofiGirl",
        "defaultVid": "4t4AQ15yj1M"
    },
    {
        "id": "chillhop-music",
        "title": "Chillhop Essentials",
        "subtitle": "Jazzy Beats • LIVE",
        "handle": "@ChillhopMusic",
        "defaultVid": "jiua2V9q9V0"
    },
    {
        "id": "cafe-jazz-tokyo",
        "title": "Tokyo Cafe Jazz BGM",
        "subtitle": "Cafe Music • LIVE",
        "handle": "@CafeMusicBGMchannel",
        "defaultVid": "4REA-0zfAQ8"
    },
    {
        "id": "yellow-brick-cinema",
        "title": "Deep Sleep & Relax Piano",
        "subtitle": "Sleep & Meditation • LIVE",
        "handle": "@YellowBrickCinema",
        "defaultVid": "x1ij7uqBC7E"
    },
    {
        "id": "nasa-space-live",
        "title": "NASA Earth & Space Views",
        "subtitle": "ISS Space Live • LIVE",
        "handle": "@NASA",
        "defaultVid": "ROboUHfPUrU"
    },
    {
        "id": "aljazeera-english",
        "title": "Al Jazeera English",
        "subtitle": "Global News • LIVE",
        "handle": "@AlJazeeraEnglish",
        "defaultVid": "gCNeDWCI0vo"
    },
    {
        "id": "sky-news-live",
        "title": "Sky News UK & World",
        "subtitle": "Breaking News • LIVE",
        "handle": "@SkyNews",
        "defaultVid": "23jDRUNoia4"
    },
    {
        "id": "abc-news-live",
        "title": "ABC News Live (USA)",
        "subtitle": "US & World News • LIVE",
        "handle": "@ABCNews",
        "defaultVid": "x1t_803BM7Y"
    },
    {
        "id": "euronews-english",
        "title": "Euronews English",
        "subtitle": "European News • LIVE",
        "handle": "@euronews",
        "defaultVid": "pykpO5kQJ98"
    },
    {
        "id": "makkah-live",
        "title": "Makkah Live & Al-Qur'an",
        "subtitle": "Masjidil Haram • LIVE",
        "handle": "@MakkahLiveHD",
        "defaultVid": "HMOPLGDCUyM"
    },
    {
        "id": "kompas-tv-live",
        "title": "Kompas TV Berita 24 Jam",
        "subtitle": "Berita Indonesia • LIVE",
        "handle": "@kompastv",
        "defaultVid": "C1uJzmnkp3w"
    }
]

def resolve_live_id(handle, fallback):
    url = f"https://www.youtube.com/{handle}/live"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            furl = resp.geturl()
            m = re.search(r"watch\?v=([a-zA-Z0-9_-]{11})", furl)
            if m:
                return m.group(1)
            html = resp.read().decode('utf-8', errors='ignore')
            m2 = re.search(r'<meta property="og:url" content="https://www.youtube.com/watch\?v=([a-zA-Z0-9_-]{11})">', html)
            if m2:
                return m2.group(1)
            m3 = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
            if m3:
                return m3.group(1)
    except Exception as e:
        print(f"[{handle}] Fallback to {fallback} due to: {e}")
    return fallback

print("Starting Auto-Heal Live Radio Sync...")
active_stations = []
for item in STATION_CHANNELS:
    vid = resolve_live_id(item["handle"], item["defaultVid"])
    print(f"✓ {item['title']} ({item['handle']}) -> {vid}")
    active_stations.append({
        "id": item["id"],
        "title": item["title"],
        "subtitle": item["subtitle"],
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "videoId": vid
    })

for target_file in CONFIG_FILES:
    if not os.path.exists(target_file):
        continue
    try:
        with open(target_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["radioStations"] = active_stations
        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        print(f"Updated {target_file} with {len(active_stations)} active stations.")
    except Exception as e:
        print(f"Error updating {target_file}: {e}")

print("Auto-Heal Live Radio Sync Finished Successfully!")
