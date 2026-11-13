#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Search & resolution providers for HiFi Music live radio.

Search & pagination design adapted from Flow-Tube/Flow
(commit 4545547fbe20bcc4411c399ebdff5e6a6dd72a43, GPL-3.0):
- app/src/main/java/io/github/aedev/flow/innertube/YouTubeSearchParams.kt
- app/src/main/java/io/github/aedev/flow/innertube/pages/search/SearchPageParser.kt
- app/src/main/java/io/github/aedev/flow/data/repository/YouTubeRepository.kt
"""

from dataclasses import dataclass, field
import json
import os
import random
import re
import shutil
import socket
import subprocess
import time
from typing import Callable, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request

VID_REGEX = re.compile(r"^[A-Za-z0-9_-]{11}$")
CANONICAL_REGEX = re.compile(
    r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"'
)
OG_URL_REGEX = re.compile(
    r'<meta property="og:url" content="https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})"'
)
REDIRECT_REGEX = re.compile(r"watch\?v=([A-Za-z0-9_-]{11})")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
COOKIE_HEADER = (
    "SOCS=CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg; "
    "CONSENT=YES+cb.20210328-17-p0.en+FX+; PREF=tz=UTC&hl=en"
)

# Innertube search params for Feature.LIVE (field 8 = true -> protobuf \x10\x01\x40\x01 -> EgJAAQ==)
INNERTUBE_LIVE_FILTER_PARAMS = "EgJAAQ%3D%3D"

REQUEST_TIMEOUT_SECONDS = 8
MAX_RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 0.6
MAX_RETRY_AFTER_SECONDS = 15.0


@dataclass
class SearchCandidate:
    video_id: str
    channel_id: str = ""
    channel_title: str = ""
    title: str = ""
    source: str = "innertube_search"


@dataclass
class SearchPageResult:
    items: List[SearchCandidate] = field(default_factory=list)
    continuation: Optional[str] = None
    ok: bool = True
    error_kind: str = ""  # "" | "TRANSIENT" | "DEAD_HTTP" | "PARSE_ERROR"
    error_msg: str = ""


def is_valid_video_id(video_id) -> bool:
    return isinstance(video_id, str) and bool(VID_REGEX.fullmatch(video_id))


def _parse_retry_after(headers) -> Optional[float]:
    if not headers:
        return None
    val = headers.get("Retry-After") if hasattr(headers, "get") else None
    if not val:
        return None
    try:
        secs = float(str(val).strip())
        if secs > 0:
            return min(secs, MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        pass
    return None


def request_with_retry(
    req,
    *,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    max_attempts: int = MAX_RETRY_ATTEMPTS,
    base_delay: float = RETRY_BASE_DELAY_SECONDS,
    opener: Callable = urllib.request.urlopen,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock_fn: Callable[[], float] = time.monotonic,
    rand_fn: Callable[[float, float], float] = random.uniform,
    deadline_ts: Optional[float] = None,
    max_bytes: int = 1024 * 1024,
):
    """
    Execute urllib request with bounded retries, Retry-After support,
    exponential backoff + jitter, and overall deadline enforcement.
    Returns (ok, status_code, final_url, body_bytes, error_kind, error_msg).
    """
    last_err = ""
    retry_after_delay = None

    for attempt in range(1, max_attempts + 1):
        if deadline_ts is not None and clock_fn() >= deadline_ts:
            return False, None, "", b"", "TRANSIENT", "Overall budget exceeded"

        retry_after_delay = None
        try:
            with opener(req, timeout=timeout) as resp:
                status_code = getattr(resp, "status", 200)
                final_url = resp.geturl() if hasattr(resp, "geturl") else getattr(req, "full_url", "")
                body = resp.read(max_bytes)
                return True, status_code, final_url, body, "", ""
        except urllib.error.HTTPError as e:
            code = getattr(e, "code", None)
            if code in (400, 404, 410):
                return False, code, "", b"", "DEAD_HTTP", f"HTTP {code}"
            last_err = f"HTTP {code}"
            if code == 429:
                retry_after_delay = _parse_retry_after(getattr(e, "headers", None))
        except (TimeoutError, socket.timeout) as e:
            last_err = f"Timeout: {e}"
        except urllib.error.URLError as e:
            last_err = f"Network/DNS error: {e.reason}"
        except Exception as e:
            last_err = str(e)

        if attempt < max_attempts:
            if deadline_ts is not None and clock_fn() >= deadline_ts:
                break
            delay = (
                retry_after_delay
                if retry_after_delay is not None
                else base_delay * (2 ** (attempt - 1)) + rand_fn(0.05, 0.35)
            )
            if deadline_ts is not None:
                remaining = deadline_ts - clock_fn()
                if remaining <= 0:
                    break
                delay = min(delay, remaining)
            sleep_fn(delay)

    return False, None, "", b"", "TRANSIENT", last_err or "Transient request failure"


def _extract_runs_text(obj) -> str:
    if not isinstance(obj, dict):
        return ""
    if isinstance(obj.get("simpleText"), str):
        return obj["simpleText"]
    runs = obj.get("runs")
    if isinstance(runs, list):
        return "".join(str(r.get("text", "")) for r in runs if isinstance(r, dict))
    return ""


def _extract_channel_meta(video_renderer: dict):
    for key in ("ownerText", "longBylineText", "shortBylineText"):
        byline = video_renderer.get(key)
        if not isinstance(byline, dict):
            continue
        runs = byline.get("runs", [])
        if runs and isinstance(runs[0], dict):
            browse_id = (
                runs[0]
                .get("navigationEndpoint", {})
                .get("browseEndpoint", {})
                .get("browseId")
            )
            ch_title = str(runs[0].get("text") or "")
            if isinstance(browse_id, str) and browse_id:
                return browse_id, ch_title
    return "", ""


def _flatten_item_sections(entries) -> List[dict]:
    if not isinstance(entries, list):
        return []
    flat = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        item_sec = entry.get("itemSectionRenderer")
        if isinstance(item_sec, dict) and isinstance(item_sec.get("contents"), list):
            for child in item_sec["contents"]:
                if isinstance(child, dict):
                    flat.append(child)
        else:
            flat.append(entry)
    return flat


def parse_search_page(data: dict) -> SearchPageResult:
    """
    Flow-adapted structural search page parser:
    Walks only the search feed's own containers (page 1 or continuation),
    skips unsupported renderers safely, extracts continuation token, and deduplicates.
    """
    if not isinstance(data, dict):
        return SearchPageResult(ok=False, error_kind="PARSE_ERROR", error_msg="Root search JSON must be object")

    section_list = (
        data.get("contents", {})
        .get("twoColumnSearchResultsRenderer", {})
        .get("primaryContents", {})
        .get("sectionListRenderer", {})
    )
    if isinstance(section_list, dict) and isinstance(section_list.get("contents"), list):
        entries = _flatten_item_sections(section_list["contents"])
    else:
        entries = []
        cmds = data.get("onResponseReceivedCommands")
        if isinstance(cmds, list):
            for cmd in cmds:
                if not isinstance(cmd, dict):
                    continue
                cont_items = (
                    cmd.get("appendContinuationItemsAction", {})
                    .get("continuationItems")
                )
                entries.extend(_flatten_item_sections(cont_items))

    items: List[SearchCandidate] = []
    seen_vids = set()
    continuation = None

    for entry in entries:
        cont_renderer = entry.get("continuationItemRenderer")
        if isinstance(cont_renderer, dict):
            token = (
                cont_renderer.get("continuationEndpoint", {})
                .get("continuationCommand", {})
                .get("token")
            )
            if isinstance(token, str) and token:
                continuation = token
            continue

        vr = entry.get("videoRenderer")
        if not isinstance(vr, dict):
            continue
        vid = vr.get("videoId")
        if not is_valid_video_id(vid) or vid in seen_vids:
            continue
        seen_vids.add(vid)
        ch_id, ch_title = _extract_channel_meta(vr)
        title = _extract_runs_text(vr.get("title"))
        items.append(
            SearchCandidate(
                video_id=vid,
                channel_id=ch_id,
                channel_title=ch_title,
                title=title,
                source="innertube_search",
            )
        )

    return SearchPageResult(items=items, continuation=continuation, ok=True)


def search_innertube_live(
    query: str,
    *,
    allowed_channel_ids: Optional[List[str]] = None,
    max_pages: int = 2,
    max_candidates: int = 5,
    **req_kwargs,
) -> SearchPageResult:
    """
    Bounded Innertube search with LIVE filter and optional continuation pagination.
    Distinguishes empty results (ok=True, items=[]) from failed requests (ok=False).
    """
    if not query or max_pages < 1 or max_candidates < 1:
        return SearchPageResult(items=[], ok=True)

    url = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
    collected: List[SearchCandidate] = []
    seen = set()
    continuation: Optional[str] = None
    last_continuation: Optional[str] = None

    for page_idx in range(max_pages):
        payload: Dict[str, object] = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20250101.01.00",
                    "hl": "en",
                    "gl": "US",
                }
            }
        }
        if page_idx == 0:
            payload["query"] = query
            payload["params"] = INNERTUBE_LIVE_FILTER_PARAMS
        else:
            if not continuation:
                break
            payload["continuation"] = continuation

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        ok, _, _, body, err_kind, err_msg = request_with_retry(req, max_attempts=2, **req_kwargs)
        if not ok:
            return SearchPageResult(
                items=collected,
                continuation=continuation,
                ok=False,
                error_kind=err_kind,
                error_msg=err_msg,
            )

        try:
            data = json.loads(body.decode("utf-8", errors="ignore"))
        except Exception as e:
            return SearchPageResult(
                items=collected,
                continuation=continuation,
                ok=False,
                error_kind="PARSE_ERROR",
                error_msg=f"Invalid search JSON: {e}",
            )

        page_res = parse_search_page(data)
        if not page_res.ok:
            return page_res

        for cand in page_res.items:
            if cand.video_id in seen:
                continue
            if allowed_channel_ids is not None and cand.channel_id and cand.channel_id not in allowed_channel_ids:
                continue
            seen.add(cand.video_id)
            collected.append(cand)
            if len(collected) >= max_candidates:
                return SearchPageResult(items=collected, continuation=page_res.continuation, ok=True)

        last_continuation = page_res.continuation
        continuation = page_res.continuation
        if not continuation:
            break

    return SearchPageResult(items=collected, continuation=last_continuation, ok=True)


def search_youtube_data_api(
    query: str,
    *,
    api_key: Optional[str] = None,
    max_results: int = 5,
    **req_kwargs,
) -> SearchPageResult:
    """
    Optional YouTube Data API v3 live search provider.
    Note: Data API search costs 100 quota units/call and does NOT provide full
    player playabilityStatus checks; candidates still go through Live verifier.
    """
    key = api_key if api_key is not None else os.environ.get("YOUTUBE_API_KEY", "")
    if not key or not query:
        return SearchPageResult(items=[], ok=False, error_kind="DISABLED", error_msg="No YOUTUBE_API_KEY configured")

    params = urllib.parse.urlencode(
        {
            "part": "snippet",
            "type": "video",
            "eventType": "live",
            "q": query,
            "maxResults": max(1, min(max_results, 25)),
            "key": key,
        }
    )
    req = urllib.request.Request(
        f"https://www.googleapis.com/youtube/v3/search?{params}",
        headers={"User-Agent": USER_AGENT},
    )
    ok, _, _, body, err_kind, err_msg = request_with_retry(req, max_attempts=2, **req_kwargs)
    if not ok:
        return SearchPageResult(items=[], ok=False, error_kind=err_kind, error_msg=err_msg)

    try:
        data = json.loads(body.decode("utf-8", errors="ignore"))
    except Exception as e:
        return SearchPageResult(items=[], ok=False, error_kind="PARSE_ERROR", error_msg=str(e))

    items: List[SearchCandidate] = []
    for it in data.get("items", []):
        if not isinstance(it, dict):
            continue
        vid = it.get("id", {}).get("videoId")
        snippet = it.get("snippet", {})
        ch_id = str(snippet.get("channelId") or "")
        ch_title = str(snippet.get("channelTitle") or "")
        title = str(snippet.get("title") or "")
        if is_valid_video_id(vid):
            items.append(
                SearchCandidate(
                    video_id=vid,
                    channel_id=ch_id,
                    channel_title=ch_title,
                    title=title,
                    source="youtube_data_api",
                )
            )
    return SearchPageResult(items=items, ok=True)


def resolve_via_innertube_url(handle: str, **req_kwargs) -> Optional[str]:
    """Resolve /@handle/live via Innertube navigation/resolve_url JSON API."""
    url = "https://www.youtube.com/youtubei/v1/navigation/resolve_url?prettyPrint=false"
    payload = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": "2.20250101.01.00",
                "hl": "en",
                "gl": "US",
            }
        },
        "url": f"https://www.youtube.com/{handle}/live",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    ok, _, _, body, _, _ = request_with_retry(req, max_attempts=2, **req_kwargs)
    if ok:
        try:
            data = json.loads(body.decode("utf-8", errors="ignore"))
            vid = data.get("endpoint", {}).get("watchEndpoint", {}).get("videoId")
            if is_valid_video_id(vid):
                return vid
        except Exception:
            pass
    return None


def resolve_via_html(handle: str, is_gate_fn: Callable[[str, str], bool], **req_kwargs) -> Optional[str]:
    """Fetch /@handle/live and /@handle/streams HTML."""
    for suffix in ("live", "streams"):
        url = f"https://www.youtube.com/{handle}/{suffix}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                "Cookie": COOKIE_HEADER,
                "Cache-Control": "no-cache",
            },
        )
        ok, _, final_url, body, _, _ = request_with_retry(req, timeout=10, max_attempts=2, **req_kwargs)
        if not ok:
            continue
        html = body.decode("utf-8", errors="ignore")
        if is_gate_fn(final_url, html):
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
            matches = re.findall(
                r'"videoId":"([A-Za-z0-9_-]{11})".{0,1500}?"BADGE_STYLE_TYPE_LIVE_NOW"',
                html,
            )
            if matches:
                return matches[0]
    return None


def resolve_via_ytdlp(handle: str) -> Optional[str]:
    """Use yt-dlp --get-id if installed in environment (e.g. GitHub Actions)."""
    if not shutil.which("yt-dlp"):
        return None
    try:
        proc = subprocess.run(
            ["yt-dlp", "--get-id", "--no-playlist", "--no-warnings", f"https://www.youtube.com/{handle}/live"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            lines = [ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip()]
            if lines and is_valid_video_id(lines[0]):
                return lines[0]
    except Exception:
        pass
    return None

