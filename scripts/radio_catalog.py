#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Station catalog & broad discovery configuration for HiFi Music live radio.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class StationSpec:
    id: str
    title: str
    subtitle: str
    category: str
    handle: str
    allowed_channel_ids: List[str]
    search_query: str = ""
    backup_handles: List[str] = field(default_factory=list)
    preferred_vid: Optional[str] = None
    default_vid: Optional[str] = None
    enabled: bool = True


# Verified UC... channel IDs resolved from each station's official YouTube channel.
# Cross-brand and 404 backupHandles were audited and removed so a station never
# silently plays another brand under the original station name.
STATION_CATALOG: List[StationSpec] = [
    StationSpec(
        id="soft-feelings",
        title="Soft Feelings Top Hits",
        subtitle="Easy Listening & Viral • LIVE",
        category="pop-easy-listening",
        handle="@soft_feelings-25",
        allowed_channel_ids=["UCh_4VN7jDxDi1WNJ7xgcQZA"],
        search_query="Soft Feelings live radio",
        default_vid="GwWxRgH4nnA",
    ),
    StationSpec(
        id="live-radio-mix",
        title="Best Radio 1 POP Hits",
        subtitle="Pop Hits 2026 • LIVE",
        category="pop-hits",
        handle="@liveradiomix",
        allowed_channel_ids=["UCCTVoz8uUBKn4TUKntJacng"],
        search_query="Best Radio 1 POP Hits live",
        preferred_vid="b-bK2Vn3D38",
        default_vid="b-bK2Vn3D38",
    ),
    StationSpec(
        id="the-good-life-radio",
        title="The Good Life Radio",
        subtitle="Deep House & Summer Chill • LIVE",
        category="electronic-chill",
        handle="@TheGoodLifeRadio",
        allowed_channel_ids=["UChs0pSaEoNLV4mevBFGaoKA"],
        search_query="The Good Life Radio x Sensual Musique live",
        default_vid="IkmLXvBfVv0",
    ),
    StationSpec(
        id="lofi-girl",
        title="Lofi Hip Hop Radio",
        subtitle="Lofi Girl • LIVE",
        category="lofi",
        handle="@LofiGirl",
        allowed_channel_ids=["UCSJ4gkVC6NrvII8umztf0Ow"],
        search_query="Lofi Girl beats to relax study to live",
        default_vid="4t4AQ15yj1M",
    ),
    StationSpec(
        id="chillhop-music",
        title="Chillhop Essentials",
        subtitle="Jazzy Beats • LIVE",
        category="lofi",
        handle="@ChillhopMusic",
        allowed_channel_ids=["UCOxqgCwgOqC2lMqC5PYz_Dg"],
        search_query="Chillhop Radio jazzy lofi hip hop beats live",
        default_vid="jiua2V9q9V0",
    ),
    StationSpec(
        id="cafe-jazz-tokyo",
        title="Tokyo Cafe Jazz BGM",
        subtitle="Cafe Music • LIVE",
        category="jazz",
        handle="@CafeMusicBGMchannel",
        allowed_channel_ids=["UCJhjE7wbdYAae1G25m0tHAA"],
        search_query="Cafe Music BGM channel jazz live",
        default_vid="WMi37od24aw",
    ),
    StationSpec(
        id="yellow-brick-cinema",
        title="Deep Sleep & Relax Piano",
        subtitle="Sleep & Meditation • LIVE",
        category="ambient-sleep",
        handle="@YellowBrickCinema",
        allowed_channel_ids=["UCwobzUc3z-0PrFpoRxNszXQ"],
        search_query="Yellow Brick Cinema relaxing sleep music live",
        default_vid="ZQVu-nzhPT8",
    ),
    StationSpec(
        id="nasa-space-live",
        title="NASA Earth & Space Views",
        subtitle="ISS Space Live • LIVE",
        category="science-ambient",
        handle="@NASA",
        allowed_channel_ids=["UCLA_DiR1FfKNvjuUpBHmylQ"],
        search_query="NASA Live Earth from Space ISS",
        default_vid="ROboUHfPUrU",
    ),
    StationSpec(
        id="aljazeera-english",
        title="Al Jazeera English",
        subtitle="Global News • LIVE",
        category="news-global",
        handle="@AlJazeeraEnglish",
        allowed_channel_ids=["UCNye-wNBqNL5ZzHSJj3l8Bg"],
        search_query="Al Jazeera English Live",
        default_vid="gCNeDWCI0vo",
    ),
    StationSpec(
        id="sky-news-live",
        title="Sky News UK & World",
        subtitle="Breaking News • LIVE",
        category="news-global",
        handle="@SkyNews",
        allowed_channel_ids=["UCoMdktPbSTixAyNGwb-UYkQ"],
        search_query="Sky News Live",
        default_vid="umLWFz4izTM",
    ),
    StationSpec(
        id="abc-news-live",
        title="ABC News Live (USA)",
        subtitle="US & World News • LIVE",
        category="news-global",
        handle="@ABCNews",
        allowed_channel_ids=["UCBi2mrWuNuyYy4gbM6fU18Q"],
        search_query="ABC News Live 24/7",
        default_vid="8JK_3tFkXe0",
    ),
    StationSpec(
        id="euronews-english",
        title="Euronews English",
        subtitle="European News • LIVE",
        category="news-global",
        handle="@euronews",
        allowed_channel_ids=["UCSrZ3UV4jOidv8ppoVuvW9Q"],
        search_query="Euronews English Live",
        default_vid="pykpO5kQJ98",
    ),
    StationSpec(
        id="makkah-live",
        title="Makkah Live & Al-Qur'an",
        subtitle="Masjidil Haram • LIVE",
        category="spiritual",
        handle="@MakkahLiveHD",
        allowed_channel_ids=["UCEOEGI67vgRhtyxAImuvIAA"],
        search_query="Makkah Live HD Masjidil Haram",
        default_vid="HMOPLGDCUyM",
    ),
    StationSpec(
        id="kompas-tv-live",
        title="Kompas TV Berita 24 Jam",
        subtitle="Berita Indonesia • LIVE",
        category="news-indonesia",
        handle="@kompastv",
        allowed_channel_ids=["UC5BMIWZe9isJXLZZWPWvBlg"],
        search_query="LIVE STREAMING 24 JAM KOMPASTV",
        default_vid="DOOrIxw5xOw",
    ),
]

# Broad discovery queries (never auto-published; saved to registry/discovery-candidates.json for review)
DISCOVERY_QUERIES = [
    {"category": "lofi", "query": "lofi hip hop radio 24/7 live"},
    {"category": "jazz", "query": "cafe jazz music live radio 24/7"},
    {"category": "indonesia-radio", "query": "radio musik indonesia live 24 jam"},
    {"category": "electronic-chill", "query": "deep house chillout radio live 24/7"},
]

