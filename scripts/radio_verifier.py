#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Structured Live Stream Verifier for HiFi Music.

Status values:
- LIVE: Target videoId verified active live, playable in check context, and
  owned by an allowed channel.
- OFFLINE: Definitive evidence target is ended, VOD/regular video, upcoming,
  outside allowedChannelIds, or permanently unavailable/restricted.
- TRANSIENT: Network error, timeout, HTTP 429/5xx, bot-check, consent gate, or
  ambiguous/incomplete player data.
- DISABLED: Station disabled in catalog.

Note: Playability ("OK") applies to the current check context (runner IP,
client, and region) and is not a guarantee across all devices or regions.
"""

from dataclasses import dataclass
import json
import re
from typing import Dict, List, Optional, Tuple
import urllib.request

from radio_providers import (
    COOKIE_HEADER,
    USER_AGENT,
    is_valid_video_id,
    request_with_retry,
)

STATUS_LIVE = "LIVE"
STATUS_OFFLINE = "OFFLINE"
STATUS_TRANSIENT = "TRANSIENT"
STATUS_DISABLED = "DISABLED"

PLAYER_RESPONSE_MARKER_REGEX = re.compile(
    r'(?:var\s+ytInitialPlayerResponse|window\["ytInitialPlayerResponse"\]|ytInitialPlayerResponse)\s*=\s*\{'
)


@dataclass
class VerificationResult:
    status: str  # LIVE | OFFLINE | TRANSIENT | DISABLED
    reason: str
    category: str = ""  # ended_or_vod | upcoming | channel_mismatch | playback_restriction | bot_check | network_error | ambiguous | metadata_mismatch | invalid_input
    video_id: str = ""
    channel_id: str = ""
    channel_title: str = ""
    title: str = ""
    source: str = ""
    playable_context: str = "Checked in current runner client/IP/region context only"


def is_transient_html_gate(url: str, html: str) -> Tuple[bool, str]:
    url_lower = (url or "").lower()
    html_lower = (html or "").lower()
    if (
        "consent.youtube.com" in url_lower
        or "consent.google.com" in url_lower
        or "before you continue to youtube" in html_lower
    ):
        return True, "Consent gate"
    if (
        "/sorry/index" in url_lower
        or 'action="captcharedirect"' in html_lower
        or 'id="captcha-form"' in html_lower
    ):
        return True, "CAPTCHA challenge"
    if "our systems have detected unusual traffic" in html_lower:
        return True, "Unusual traffic block"
    if "ytInitialPlayerResponse" not in (html or ""):
        return True, "Missing ytInitialPlayerResponse"
    return False, ""


def extract_yt_initial_player_response(html: str) -> Optional[dict]:
    """Safely extract and JSON-decode ytInitialPlayerResponse using raw_decode."""
    if not isinstance(html, str) or not html:
        return None
    decoder = json.JSONDecoder()
    for match in PLAYER_RESPONSE_MARKER_REGEX.finditer(html):
        brace_idx = match.end() - 1
        try:
            obj, _ = decoder.raw_decode(html, idx=brace_idx)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def _is_transient_player_reason(reason_text: str) -> bool:
    lower = (reason_text or "").lower()
    markers = (
        "not a bot",
        "confirm you",
        "unusual traffic",
        "captcha",
        "sign in",
        "page needs to be reloaded",
    )
    return any(m in lower for m in markers)


def evaluate_player_response(
    data: dict,
    target_video_id: str,
    allowed_channel_ids: Optional[List[str]] = None,
    *,
    require_channel_id: bool = True,
    source: str = "player",
) -> VerificationResult:
    """
    Evaluate structured YouTube player JSON (Innertube /player or ytInitialPlayerResponse).
    """
    if not is_valid_video_id(target_video_id):
        return VerificationResult(
            STATUS_OFFLINE,
            "Invalid videoId format",
            category="invalid_input",
            video_id=str(target_video_id),
            source=source,
        )

    if not isinstance(data, dict):
        return VerificationResult(
            STATUS_TRANSIENT,
            "Missing or invalid player JSON",
            category="ambiguous",
            video_id=target_video_id,
            source=source,
        )

    playability = data.get("playabilityStatus")
    if not isinstance(playability, dict) or not isinstance(playability.get("status"), str):
        return VerificationResult(
            STATUS_TRANSIENT,
            "Missing playabilityStatus",
            category="ambiguous",
            video_id=target_video_id,
            source=source,
        )

    ps_status = playability.get("status", "").strip()
    ps_reason = str(playability.get("reason") or "")
    if not ps_reason:
        subreasons = (
            playability.get("errorScreen", {})
            .get("playerErrorMessageRenderer", {})
            .get("subreason", {})
        )
        if isinstance(subreasons, dict):
            ps_reason = str(subreasons.get("simpleText") or "")

    if ps_status == "LOGIN_REQUIRED":
        lower_reason = ps_reason.lower()
        if "private" in lower_reason or "members-only" in lower_reason:
            return VerificationResult(
                STATUS_OFFLINE,
                f"playabilityStatus=LOGIN_REQUIRED ({ps_reason})",
                category="playback_restriction",
                video_id=target_video_id,
                source=source,
            )
        return VerificationResult(
            STATUS_TRANSIENT,
            f"LOGIN_REQUIRED bot/auth gate ({ps_reason or 'no reason'})",
            category="bot_check",
            video_id=target_video_id,
            source=source,
        )

    if ps_status in ("ERROR", "UNPLAYABLE", "CONTENT_CHECK_REQUIRED", "AGE_VERIFICATION_REQUIRED", "LIVE_STREAM_OFFLINE"):
        if _is_transient_player_reason(ps_reason):
            return VerificationResult(
                STATUS_TRANSIENT,
                f"Bot/reload gate ({ps_reason})",
                category="bot_check",
                video_id=target_video_id,
                source=source,
            )
        return VerificationResult(
            STATUS_OFFLINE,
            f"playabilityStatus={ps_status}" + (f" ({ps_reason})" if ps_reason else ""),
            category="playback_restriction",
            video_id=target_video_id,
            source=source,
        )

    if ps_status != "OK":
        return VerificationResult(
            STATUS_TRANSIENT,
            f"Ambiguous playabilityStatus={ps_status}",
            category="ambiguous",
            video_id=target_video_id,
            source=source,
        )

    video_details = data.get("videoDetails")
    if not isinstance(video_details, dict):
        return VerificationResult(
            STATUS_TRANSIENT,
            "Missing videoDetails",
            category="ambiguous",
            video_id=target_video_id,
            source=source,
        )

    meta_vid = video_details.get("videoId")
    if meta_vid != target_video_id:
        return VerificationResult(
            STATUS_OFFLINE,
            f"Metadata videoId mismatch (expected {target_video_id}, got {meta_vid!r})",
            category="metadata_mismatch",
            video_id=target_video_id,
            source=source,
        )

    channel_id = str(video_details.get("channelId") or "")
    channel_title = str(video_details.get("author") or "")
    title = str(video_details.get("title") or "")

    if require_channel_id and not channel_id.startswith("UC"):
        return VerificationResult(
            STATUS_OFFLINE,
            f"Missing/invalid channelId {channel_id!r}",
            category="channel_mismatch",
            video_id=target_video_id,
            channel_id=channel_id,
            channel_title=channel_title,
            title=title,
            source=source,
        )

    if allowed_channel_ids is not None:
        if not channel_id or channel_id not in allowed_channel_ids:
            return VerificationResult(
                STATUS_OFFLINE,
                f"channelId {channel_id!r} not in allowedChannelIds {list(allowed_channel_ids)}",
                category="channel_mismatch",
                video_id=target_video_id,
                channel_id=channel_id,
                channel_title=channel_title,
                title=title,
                source=source,
            )

    if video_details.get("isUpcoming") is True:
        return VerificationResult(
            STATUS_OFFLINE,
            "Upcoming stream/premiere (isUpcoming=True)",
            category="upcoming",
            video_id=target_video_id,
            channel_id=channel_id,
            channel_title=channel_title,
            title=title,
            source=source,
        )

    microformat = data.get("microformat", {})
    lbd = None
    if isinstance(microformat, dict):
        pmr = microformat.get("playerMicroformatRenderer", {})
        if isinstance(pmr, dict):
            lbd = pmr.get("liveBroadcastDetails")

    if isinstance(lbd, dict):
        if lbd.get("endTimestamp"):
            return VerificationResult(
                STATUS_OFFLINE,
                "Live broadcast has ended (endTimestamp present)",
                category="ended_or_vod",
                video_id=target_video_id,
                channel_id=channel_id,
                channel_title=channel_title,
                title=title,
                source=source,
            )
        if lbd.get("isLiveNow") is False:
            return VerificationResult(
                STATUS_OFFLINE,
                "Live broadcast is not active (isLiveNow=False)",
                category="ended_or_vod",
                video_id=target_video_id,
                channel_id=channel_id,
                channel_title=channel_title,
                title=title,
                source=source,
            )

    live_streamability = playability.get("liveStreamability", {})
    if isinstance(live_streamability, dict):
        lsr = live_streamability.get("liveStreamabilityRenderer", {})
        if isinstance(lsr, dict) and "offlineSlate" in lsr and video_details.get("isLive") is not True:
            return VerificationResult(
                STATUS_OFFLINE,
                "Stream offline slate present",
                category="ended_or_vod",
                video_id=target_video_id,
                channel_id=channel_id,
                channel_title=channel_title,
                title=title,
                source=source,
            )

    is_live = video_details.get("isLive")
    is_live_content = video_details.get("isLiveContent")

    if is_live is False:
        return VerificationResult(
            STATUS_OFFLINE,
            "Stream is not live (videoDetails.isLive=False)",
            category="ended_or_vod",
            video_id=target_video_id,
            channel_id=channel_id,
            channel_title=channel_title,
            title=title,
            source=source,
        )

    if is_live is not True:
        if is_live_content is True:
            return VerificationResult(
                STATUS_OFFLINE,
                "Recorded live VOD (isLiveContent=True without active isLive=True)",
                category="ended_or_vod",
                video_id=target_video_id,
                channel_id=channel_id,
                channel_title=channel_title,
                title=title,
                source=source,
            )
        return VerificationResult(
            STATUS_TRANSIENT,
            "Missing active live indicator (videoDetails.isLive absent)",
            category="ambiguous",
            video_id=target_video_id,
            channel_id=channel_id,
            channel_title=channel_title,
            title=title,
            source=source,
        )

    return VerificationResult(
        status=STATUS_LIVE,
        reason=f"OK ({source})",
        category="live",
        video_id=target_video_id,
        channel_id=channel_id,
        channel_title=channel_title,
        title=title,
        source=source,
    )


def check_oembed(video_id: str, **req_kwargs) -> Tuple[str, str]:
    """Supporting check only: returns ('OK'|'OFFLINE'|'TRANSIENT', reason)."""
    url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    ok, code, _, _, err_kind, err_msg = request_with_retry(req, **req_kwargs)
    if ok and code == 200:
        return "OK", ""
    if err_kind == "DEAD_HTTP":
        return STATUS_OFFLINE, err_msg
    return STATUS_TRANSIENT, err_msg or f"oEmbed status {code}"


def check_thumbnail(video_id: str, **req_kwargs) -> Tuple[str, str]:
    """Supporting check only: returns ('OK'|'OFFLINE'|'TRANSIENT', reason)."""
    url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    ok, code, _, _, err_kind, err_msg = request_with_retry(req, **req_kwargs)
    if ok and code == 200:
        return "OK", ""
    if err_kind == "DEAD_HTTP":
        return STATUS_OFFLINE, err_msg
    return STATUS_TRANSIENT, err_msg or f"Thumbnail status {code}"


def verify_video_is_live(
    video_id: str,
    allowed_channel_ids: Optional[List[str]] = None,
    *,
    cache: Optional[Dict[Tuple, VerificationResult]] = None,
    force_refresh: bool = False,
    **req_kwargs,
) -> VerificationResult:
    """
    Strictly verify target videoId is currently LIVE, playable in check context,
    and matches allowed_channel_ids (or has valid UC... channelId for discovery).
    """
    if not is_valid_video_id(video_id):
        return VerificationResult(
            STATUS_OFFLINE,
            "Invalid videoId format",
            category="invalid_input",
            video_id=str(video_id),
        )

    cache_key = (
        video_id,
        tuple(sorted(allowed_channel_ids)) if allowed_channel_ids is not None else None,
    )
    if cache is not None and not force_refresh and cache_key in cache:
        return cache[cache_key]

    def _store(res: VerificationResult) -> VerificationResult:
        if cache is not None:
            cache[cache_key] = res
        return res

    # 1. Innertube /youtubei/v1/player
    player_res: Optional[VerificationResult] = None
    player_url = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"
    payload = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": "2.20250101.01.00",
                "hl": "en",
                "gl": "US",
            }
        },
        "videoId": video_id,
    }
    player_req = urllib.request.Request(
        player_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    ok, _, _, body, err_kind, err_msg = request_with_retry(player_req, **req_kwargs)
    if ok:
        try:
            player_json = json.loads(body.decode("utf-8", errors="ignore"))
            player_res = evaluate_player_response(
                player_json,
                video_id,
                allowed_channel_ids=allowed_channel_ids,
                source="innertube_player",
            )
            if player_res.status == STATUS_OFFLINE:
                return _store(player_res)
        except Exception as e:
            player_res = VerificationResult(
                STATUS_TRANSIENT,
                f"Player JSON parse error: {e}",
                category="ambiguous",
                video_id=video_id,
                source="innertube_player",
            )
    elif err_kind == "DEAD_HTTP":
        return _store(
            VerificationResult(
                STATUS_OFFLINE,
                err_msg,
                category="playback_restriction",
                video_id=video_id,
                source="innertube_player",
            )
        )
    else:
        player_res = VerificationResult(
            STATUS_TRANSIENT,
            err_msg,
            category="network_error",
            video_id=video_id,
            source="innertube_player",
        )

    # 2. Watch HTML ytInitialPlayerResponse (independent structured check)
    watch_res: Optional[VerificationResult] = None
    if player_res is None or player_res.status != STATUS_LIVE:
        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        watch_req = urllib.request.Request(
            watch_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                "Cookie": COOKIE_HEADER,
                "Cache-Control": "no-cache",
            },
        )
        ok_w, _, final_url, body_w, err_kind_w, err_msg_w = request_with_retry(
            watch_req, timeout=10, **req_kwargs
        )
        if ok_w:
            html = body_w.decode("utf-8", errors="ignore")
            is_gate, gate_reason = is_transient_html_gate(final_url, html)
            if is_gate:
                watch_res = VerificationResult(
                    STATUS_TRANSIENT,
                    gate_reason,
                    category="bot_check",
                    video_id=video_id,
                    source="watch_html",
                )
            else:
                pr_json = extract_yt_initial_player_response(html)
                if pr_json is None:
                    watch_res = VerificationResult(
                        STATUS_TRANSIENT,
                        "Failed to parse ytInitialPlayerResponse",
                        category="ambiguous",
                        video_id=video_id,
                        source="watch_html",
                    )
                else:
                    watch_res = evaluate_player_response(
                        pr_json,
                        video_id,
                        allowed_channel_ids=allowed_channel_ids,
                        source="watch_html",
                    )
                    if watch_res.status == STATUS_OFFLINE:
                        return _store(watch_res)
        elif err_kind_w == "DEAD_HTTP":
            return _store(
                VerificationResult(
                    STATUS_OFFLINE,
                    err_msg_w,
                    category="playback_restriction",
                    video_id=video_id,
                    source="watch_html",
                )
            )
        else:
            watch_res = VerificationResult(
                STATUS_TRANSIENT,
                err_msg_w,
                category="network_error",
                video_id=video_id,
                source="watch_html",
            )

    live_evidence = (
        player_res
        if (player_res and player_res.status == STATUS_LIVE)
        else (watch_res if (watch_res and watch_res.status == STATUS_LIVE) else None)
    )
    if live_evidence is None:
        chosen = watch_res or player_res
        return _store(
            VerificationResult(
                STATUS_TRANSIENT,
                chosen.reason if chosen else "Transient verification failure",
                category=chosen.category if chosen else "ambiguous",
                video_id=video_id,
                source="verify",
            )
        )

    # 3. Supporting checks (oEmbed + thumbnail) - never proof of live, and transient
    #    failures here yield TRANSIENT, never OFFLINE.
    oe_status, oe_reason = check_oembed(video_id, **req_kwargs)
    if oe_status != "OK":
        return _store(
            VerificationResult(
                STATUS_TRANSIENT if oe_status == STATUS_TRANSIENT else STATUS_OFFLINE,
                f"oEmbed {oe_status.lower()} ({oe_reason})",
                category="network_error" if oe_status == STATUS_TRANSIENT else "playback_restriction",
                video_id=video_id,
                channel_id=live_evidence.channel_id,
                channel_title=live_evidence.channel_title,
                title=live_evidence.title,
                source="oembed",
            )
        )

    th_status, th_reason = check_thumbnail(video_id, **req_kwargs)
    if th_status != "OK":
        return _store(
            VerificationResult(
                STATUS_TRANSIENT if th_status == STATUS_TRANSIENT else STATUS_OFFLINE,
                f"Thumbnail {th_status.lower()} ({th_reason})",
                category="network_error" if th_status == STATUS_TRANSIENT else "playback_restriction",
                video_id=video_id,
                channel_id=live_evidence.channel_id,
                channel_title=live_evidence.channel_title,
                title=live_evidence.title,
                source="thumbnail",
            )
        )

    return _store(live_evidence)

