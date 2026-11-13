#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Broad Live Radio Discovery (Review-Only) for HiFi Music.

Searches configurable categories/keywords with bounded queries, pagination,
and candidate limits. Verified candidates from new channels are saved to
`registry/discovery-candidates.json` with `reviewStatus: "PENDING_REVIEW"`.
Never auto-publishes to `remote-config*.json` and never alters existing stations.
"""

from datetime import datetime, timezone
import json
import os
import random
import time
from typing import Callable, Dict, List, Optional
import urllib.request

from radio_catalog import DISCOVERY_QUERIES, STATION_CATALOG
from radio_providers import search_innertube_live
from radio_verifier import STATUS_LIVE, verify_video_is_live

DISCOVERY_REGISTRY = "registry/discovery-candidates.json"
DEFAULT_MAX_QUERIES = 4
DEFAULT_MAX_PAGES = 2
DEFAULT_MAX_CANDIDATES_PER_QUERY = 5
DEFAULT_MAX_RUN_SECONDS = 120.0


def load_discovery_registry(path: str = DISCOVERY_REGISTRY) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("candidates"), list):
                return data
        except Exception:
            pass
    return {"schemaVersion": 1, "lastUpdated": None, "candidates": []}


def run_discovery(
    *,
    dry_run: bool = False,
    registry_path: str = DISCOVERY_REGISTRY,
    queries: Optional[List[dict]] = None,
    catalog=None,
    max_queries: int = DEFAULT_MAX_QUERIES,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_candidates_per_query: int = DEFAULT_MAX_CANDIDATES_PER_QUERY,
    max_run_seconds: float = DEFAULT_MAX_RUN_SECONDS,
    now_fn: Optional[Callable[[], datetime]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock_fn: Callable[[], float] = time.monotonic,
    rand_fn: Callable[[float, float], float] = random.uniform,
    opener: Callable = urllib.request.urlopen,
    search_fn: Callable = search_innertube_live,
    verify_fn: Callable = verify_video_is_live,
    atomic_write_fn: Optional[Callable[[str, dict], None]] = None,
) -> int:
    queries = list(queries if queries is not None else DISCOVERY_QUERIES)[:max_queries]
    catalog = list(catalog if catalog is not None else STATION_CATALOG)
    now_utc = (now_fn() if now_fn else datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    deadline_ts = clock_fn() + max_run_seconds

    known_channel_ids = {
        ch_id for st in catalog for ch_id in st.allowed_channel_ids
    }

    existing_data = load_discovery_registry(registry_path)
    by_video_id: Dict[str, dict] = {
        c["videoId"]: c
        for c in existing_data.get("candidates", [])
        if isinstance(c, dict) and isinstance(c.get("videoId"), str)
    }

    cache = {}
    req_kwargs = {
        "opener": opener,
        "sleep_fn": sleep_fn,
        "clock_fn": clock_fn,
        "rand_fn": rand_fn,
        "deadline_ts": deadline_ts,
    }

    discovered_count = 0
    for q_spec in queries:
        if clock_fn() >= deadline_ts:
            print("[WARN] Discovery time budget reached.", flush=True)
            break

        category = q_spec.get("category", "general")
        query_text = q_spec.get("query", "")
        if not query_text:
            continue

        page_res = search_fn(
            query_text,
            allowed_channel_ids=None,
            max_pages=max_pages,
            max_candidates=max_candidates_per_query,
            **req_kwargs,
        )
        if not page_res.ok:
            print(f"[WARN] Discovery query '{query_text}' failed: {page_res.error_msg}", flush=True)
            continue

        for cand in page_res.items:
            if clock_fn() >= deadline_ts:
                break
            if cand.video_id in by_video_id:
                continue
            if cand.channel_id and cand.channel_id in known_channel_ids:
                continue

            ver = verify_fn(
                cand.video_id,
                allowed_channel_ids=None,
                cache=cache,
                **req_kwargs,
            )
            if ver.status != STATUS_LIVE:
                continue
            if not ver.channel_id or ver.channel_id in known_channel_ids:
                continue

            by_video_id[cand.video_id] = {
                "videoId": cand.video_id,
                "channelId": ver.channel_id,
                "channelTitle": ver.channel_title or cand.channel_title,
                "title": ver.title or cand.title,
                "category": category,
                "sourceQuery": query_text,
                "discoveredAt": now_iso,
                "verificationStatus": ver.status,
                "verificationReason": ver.reason,
                "reviewStatus": "PENDING_REVIEW",
            }
            discovered_count += 1
            sleep_fn(rand_fn(0.2, 0.5))

    print(f"Discovery completed: {discovered_count} new candidate(s) queued for manual review.", flush=True)

    if dry_run:
        print("[DRY RUN] Discovery registry write skipped.", flush=True)
        return 0

    if atomic_write_fn is None:
        raise ValueError("atomic_write_fn is required when dry_run=False")

    output = {
        "schemaVersion": 1,
        "lastUpdated": now_iso,
        "note": "Candidates require manual review before adding to STATION_CATALOG. LIVE status does not guarantee genre/safety/official status.",
        "candidates": list(by_video_id.values()),
    }
    try:
        atomic_write_fn(registry_path, output)
        print(f"Saved discovery candidates to {registry_path}", flush=True)
        return 0
    except Exception as e:
        print(f"[ERROR] Failed to write {registry_path}: {e}", flush=True)
        return 1

