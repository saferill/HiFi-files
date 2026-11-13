#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Publication & 6-hour Balanced Fallback Policy (MODE SEIMBANG) for HiFi Music.

Rules:
- New candidates must pass LIVE verification + pre-publish re-check.
- Streams confirmed OFFLINE are immediately excluded.
- Previously verified streams encountering TRANSIENT errors may be kept as
  fallback ONLY for up to 6 hours measured strictly from `lastVerified`.
- Repeated failures never reset or extend the 6-hour grace period.
- Unverified defaultVid or historical records lacking verifiedChannelId /
  verificationVersion >= 2 are NOT trusted for fallback.
- Corrupted, timezone-naive, or future timestamps never grant/extend fallback.
- 0..7 active stations are valid outputs (logged as operational warning);
  dead streams are never revived to satisfy a minimum count.
"""

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from radio_providers import is_valid_video_id

TRANSIENT_GRACE_HOURS = 6.0
OPERATIONAL_LOW_STATION_THRESHOLD = 8
TELEMETRY_SCHEMA_VERSION = 2


def parse_utc_timestamp(ts_str) -> Optional[datetime]:
    """
    Parse ISO-8601 timestamp with explicit timezone into UTC datetime.
    Rejects non-strings, empty strings, malformed formats, and timezone-naive values.
    """
    if not isinstance(ts_str, str) or not ts_str.strip():
        return None
    try:
        normalized = ts_str.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def evaluate_fallback_eligibility(
    video_id: Optional[str],
    record: Optional[dict],
    allowed_channel_ids: List[str],
    now_utc: datetime,
    grace_hours: float = TRANSIENT_GRACE_HOURS,
) -> Tuple[bool, float, str]:
    """
    Determine whether `video_id` is eligible for temporary TRANSIENT fallback.
    Returns (eligible: bool, age_hours: float, reason: str).
    """
    if not is_valid_video_id(video_id):
        return False, 0.0, "Invalid or missing videoId"

    if not isinstance(record, dict):
        return False, 0.0, "No telemetry record"

    if record.get("lastGoodVideoId") != video_id:
        return False, 0.0, "Video was not the last verified good stream"

    if int(record.get("verificationVersion") or 1) < 2:
        return False, 0.0, "Historical telemetry lacks v2 identity verification proof"

    verified_ch = record.get("verifiedChannelId")
    if not isinstance(verified_ch, str) or verified_ch not in allowed_channel_ids:
        return False, 0.0, "Verified channelId missing or not in current allowlist"

    last_ver_dt = parse_utc_timestamp(record.get("lastVerified"))
    if last_ver_dt is None:
        return False, 0.0, "Missing, corrupted, or timezone-naive lastVerified timestamp"

    if last_ver_dt > now_utc:
        return False, 0.0, "Future lastVerified timestamp rejected"

    age_hours = (now_utc - last_ver_dt).total_seconds() / 3600.0
    if age_hours > grace_hours:
        return False, age_hours, f"Grace period expired ({age_hours:.1f}h > {grace_hours:.0f}h)"

    return True, age_hours, f"Within 6h grace period ({age_hours:.1f}h/{grace_hours:.0f}h)"


def detect_provider_degradation(transient_categories: List[str], total_checked: int) -> bool:
    """Flag widespread provider/bot-check outage without extending fallback grace."""
    if total_checked <= 0:
        return False
    bot_or_net = sum(1 for c in transient_categories if c in ("bot_check", "network_error"))
    threshold = max(3, (total_checked + 1) // 2)
    return bot_or_net >= threshold

