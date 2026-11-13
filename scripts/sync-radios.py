#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hardened Auto-Heal Live Radio Synchronization & Discovery CLI for HiFi Music.

Modes:
- health-check (default): Updates existing enabled stations from approved
  allowedChannelIds, enforces 6-hour balanced fallback from `lastVerified`,
  re-verifies LIVE candidates before publication, preserves promos/other fields,
  and publishes valid 0..N station lists atomically per file.
- discovery: Runs bounded broad search and queues new channel candidates in
  registry/discovery-candidates.json for manual review (never auto-publishes).
"""

import argparse
from datetime import datetime, timezone
import json
import os
import random
import sys
import tempfile
import time
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from radio_catalog import STATION_CATALOG, StationSpec
from radio_discovery import DISCOVERY_REGISTRY, run_discovery
from radio_policy import (
    OPERATIONAL_LOW_STATION_THRESHOLD,
    TELEMETRY_SCHEMA_VERSION,
    TRANSIENT_GRACE_HOURS,
    detect_provider_degradation,
    evaluate_fallback_eligibility,
)
from radio_providers import (
    is_valid_video_id,
    resolve_via_html,
    resolve_via_innertube_url,
    resolve_via_ytdlp,
    search_innertube_live,
)
from radio_verifier import (
    STATUS_DISABLED,
    STATUS_LIVE,
    STATUS_OFFLINE,
    STATUS_TRANSIENT,
    VerificationResult,
    is_transient_html_gate,
    verify_video_is_live,
)

validate_config_mod = __import__("validate-config")
validate_config_data = validate_config_mod.validate_config_data

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

CONFIG_FILES = ["remote-config.json", "remote-config-dev.json"]
STATUS_REGISTRY = "registry/radio-status.json"
MAX_RUN_DURATION_SECONDS = 300.0


def atomic_write_json(filepath: str, data: dict):
    """Write JSON atomically to a single file using a sibling temporary file + os.replace."""
    parent = os.path.dirname(os.path.abspath(filepath))
    os.makedirs(parent, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=f".{os.path.basename(filepath)}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = tmp.name
            json.dump(data, tmp, indent=2, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, filepath)
        tmp_path = None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def find_live_id_for_station(
    item: StationSpec,
    used_vids: set,
    *,
    verify_fn=verify_video_is_live,
    search_fn=search_innertube_live,
    cache=None,
    **req_kwargs,
):
    """Try approved resolvers and channel-restricted search for an existing station."""
    allowed_ids = list(item.allowed_channel_ids)
    if not allowed_ids:
        return False, None, None, "Station has no allowedChannelIds configured"

    if item.preferred_vid and item.preferred_vid not in used_vids:
        res = verify_fn(item.preferred_vid, allowed_channel_ids=allowed_ids, cache=cache, **req_kwargs)
        if res.status == STATUS_LIVE:
            return True, item.preferred_vid, res, f"OK (preferredVid {item.preferred_vid})"
        print(f"  [INFO] preferredVid {item.preferred_vid} rejected ({res.reason}), trying resolvers", flush=True)

    handles = [item.handle] + list(item.backup_handles)
    tried_vids = set()

    for h in handles:
        for resolver_name, resolver_fn in (
            ("Innertube resolve_url", lambda handle: resolve_via_innertube_url(handle, **req_kwargs)),
            ("HTML /live+/streams", lambda handle: resolve_via_html(handle, lambda u, ht: is_transient_html_gate(u, ht)[0], **req_kwargs)),
            ("yt-dlp", resolve_via_ytdlp),
        ):
            vid = resolver_fn(h)
            if vid and vid not in tried_vids and vid not in used_vids:
                tried_vids.add(vid)
                res = verify_fn(vid, allowed_channel_ids=allowed_ids, cache=cache, **req_kwargs)
                if res.status == STATUS_LIVE:
                    return True, vid, res, f"OK ({h} via {resolver_name})"
                print(f"  [INFO] {h} candidate {vid} via {resolver_name} rejected: {res.reason}", flush=True)

    if item.search_query:
        page_res = search_fn(
            item.search_query,
            allowed_channel_ids=allowed_ids,
            max_pages=2,
            max_candidates=5,
            **req_kwargs,
        )
        if page_res.ok:
            for cand in page_res.items:
                if cand.video_id in used_vids or cand.video_id in tried_vids:
                    continue
                tried_vids.add(cand.video_id)
                res = verify_fn(cand.video_id, allowed_channel_ids=allowed_ids, cache=cache, **req_kwargs)
                if res.status == STATUS_LIVE:
                    return True, cand.video_id, res, "OK (via channel-filtered Live Search)"
        else:
            print(f"  [WARN] Search provider failed for {item.id}: {page_res.error_msg}", flush=True)

    return False, None, None, "No active LIVE stream found from approved channels"


def load_existing_video_ids(config_path: str) -> dict:
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return {
                st["id"]: st.get("videoId")
                for st in cfg.get("radioStations", [])
                if isinstance(st, dict) and isinstance(st.get("id"), str)
            }
        except Exception as e:
            print(f"[WARN] Failed to load existing video IDs from {config_path}: {e}", flush=True)
    return {}


def load_status_registry(registry_path: str = STATUS_REGISTRY) -> dict:
    if os.path.exists(registry_path):
        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("stations", {})
                return data
        except Exception as e:
            print(f"[WARN] Failed to read {registry_path}: {e}", flush=True)
    return {"schemaVersion": TELEMETRY_SCHEMA_VERSION, "lastUpdated": None, "stations": {}}


def _station_entry(item: StationSpec, vid: str) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "subtitle": item.subtitle,
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "videoId": vid,
    }


def _coerce_station(raw) -> StationSpec:
    if isinstance(raw, StationSpec):
        return raw
    return StationSpec(
        id=raw["id"],
        title=raw["title"],
        subtitle=raw["subtitle"],
        category=raw.get("category", "general"),
        handle=raw["handle"],
        allowed_channel_ids=list(raw.get("allowedChannelIds") or raw.get("allowed_channel_ids") or []),
        search_query=raw.get("searchQuery") or raw.get("search_query", ""),
        backup_handles=list(raw.get("backupHandles") or raw.get("backup_handles") or []),
        preferred_vid=raw.get("preferredVid") or raw.get("preferred_vid"),
        default_vid=raw.get("defaultVid") or raw.get("default_vid"),
        enabled=raw.get("enabled", True),
    )


def run_sync(
    *,
    dry_run: bool = False,
    config_files=None,
    status_registry_path: str = STATUS_REGISTRY,
    stations=None,
    now_fn=None,
    sleep_fn=time.sleep,
    clock_fn=time.monotonic,
    rand_fn=random.uniform,
    opener=urllib.request.urlopen,
    verify_fn=verify_video_is_live,
    find_fn=find_live_id_for_station,
    search_fn=search_innertube_live,
) -> int:
    """
    Execute balanced 6-hour auto-heal radio synchronization.
    Returns 0 on valid publication (including valid 0..7 station lists),
    or 1 on internal/read/validation/write failure.
    """
    config_files = list(config_files or CONFIG_FILES)
    catalog = [_coerce_station(s) for s in (stations if stations is not None else STATION_CATALOG)]
    now_utc = (now_fn() if now_fn else datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    deadline_ts = clock_fn() + MAX_RUN_DURATION_SECONDS

    if dry_run:
        print("=== DRY RUN MODE: No files will be modified ===", flush=True)
    print("Starting Balanced Auto-Heal Live Radio Synchronization (6h grace policy)...", flush=True)

    existing_vids = load_existing_video_ids(config_files[0] if config_files else "")
    status_data = load_status_registry(status_registry_path)
    status_data["schemaVersion"] = TELEMETRY_SCHEMA_VERSION
    station_records = status_data.setdefault("stations", {})

    verification_cache = {}
    req_kwargs = {
        "opener": opener,
        "sleep_fn": sleep_fn,
        "clock_fn": clock_fn,
        "rand_fn": rand_fn,
        "deadline_ts": deadline_ts,
    }

    selected_candidates = []  # (StationSpec, vid, is_new_or_live_checked, reason, ver_result)
    used_vids = set()
    summary_rows = []
    transient_categories = []
    counts = {"LIVE": 0, "FALLBACK": 0, "OFFLINE": 0, "TRANSIENT_EXCLUDED": 0, "DISABLED": 0}

    for item in catalog:
        st_id = item.id
        record = dict(station_records.get(st_id, {}))

        if not item.enabled:
            record["lastChecked"] = now_iso
            record["verificationState"] = STATUS_DISABLED
            record["lastStatus"] = "DISABLED in station catalog"
            station_records[st_id] = record
            counts["DISABLED"] += 1
            summary_rows.append((st_id, existing_vids.get(st_id), "-", STATUS_DISABLED, "Disabled in catalog"))
            continue

        cfg_vid = existing_vids.get(st_id)
        allowed_ids = list(item.allowed_channel_ids)
        print(f"\nResolving [{st_id}] ({item.handle})...", flush=True)

        success, candidate_vid, ver_res, reason = find_fn(
            item,
            used_vids,
            verify_fn=verify_fn,
            search_fn=search_fn,
            cache=verification_cache,
            **req_kwargs,
        )
        sleep_fn(rand_fn(0.2, 0.5))

        if success and candidate_vid:
            used_vids.add(candidate_vid)
            selected_candidates.append((item, candidate_vid, True, reason, ver_res))
            continue

        # No new LIVE stream found; evaluate existing config videoId (never unverified defaultVid)
        if is_valid_video_id(cfg_vid) and cfg_vid not in used_vids:
            ex_res = verify_fn(cfg_vid, allowed_channel_ids=allowed_ids, cache=verification_cache, **req_kwargs)
        else:
            ex_res = VerificationResult(
                STATUS_OFFLINE,
                "Missing, invalid, or duplicate existing videoId",
                category="invalid_input",
                video_id=str(cfg_vid or ""),
            )

        if ex_res.status == STATUS_LIVE:
            used_vids.add(cfg_vid)
            selected_candidates.append((item, cfg_vid, True, f"KEPT_LIVE: {ex_res.reason}", ex_res))
        elif ex_res.status == STATUS_OFFLINE:
            record["consecutiveFailures"] = int(record.get("consecutiveFailures") or 0) + 1
            record["lastChecked"] = now_iso
            record["lastFailureCategory"] = ex_res.category
            record["lastStatus"] = f"OFFLINE: {ex_res.reason}"
            record["verificationState"] = STATUS_OFFLINE
            station_records[st_id] = record
            counts["OFFLINE"] += 1
            summary_rows.append((st_id, cfg_vid, "-", STATUS_OFFLINE, record["lastStatus"]))
            print(f"  ✗ Confirmed OFFLINE ({ex_res.reason}). Excluded.", flush=True)
        else:
            transient_categories.append(ex_res.category)
            eligible, age_hours, fb_reason = evaluate_fallback_eligibility(
                cfg_vid, record, allowed_ids, now_utc, grace_hours=TRANSIENT_GRACE_HOURS
            )
            record["consecutiveFailures"] = int(record.get("consecutiveFailures") or 0) + 1
            record["lastChecked"] = now_iso
            record["lastFailureCategory"] = ex_res.category

            if eligible:
                used_vids.add(cfg_vid)
                record["lastStatus"] = f"TRANSIENT_FALLBACK ({age_hours:.1f}h/{TRANSIENT_GRACE_HOURS:.0f}h): {ex_res.reason}"
                record["verificationState"] = "TRANSIENT_FALLBACK"
                # IMPORTANT: Never update lastVerified when keeping fallback!
                station_records[st_id] = record
                selected_candidates.append((item, cfg_vid, False, record["lastStatus"], ex_res))
                counts["FALLBACK"] += 1
                summary_rows.append((st_id, cfg_vid, cfg_vid, "FALLBACK", record["lastStatus"]))
                print(f"  ⚠ Transient ({ex_res.reason}). Kept verified fallback ({age_hours:.1f}h/6h).", flush=True)
            else:
                record["lastStatus"] = f"EXCLUDED_TRANSIENT ({fb_reason}): {ex_res.reason}"
                record["verificationState"] = STATUS_TRANSIENT
                station_records[st_id] = record
                counts["TRANSIENT_EXCLUDED"] += 1
                summary_rows.append((st_id, cfg_vid, "-", "TRANSIENT", record["lastStatus"]))
                print(f"  ✗ Transient excluded ({fb_reason}).", flush=True)

    # Pre-publication re-verification (force_refresh=True) for all LIVE candidates
    active_stations = []
    for item, vid, needs_recheck, reason, prev_res in selected_candidates:
        st_id = item.id
        old_vid = existing_vids.get(st_id)
        record = dict(station_records.get(st_id, {}))

        if not needs_recheck:
            active_stations.append(_station_entry(item, vid))
            continue

        recheck = verify_fn(
            vid,
            allowed_channel_ids=list(item.allowed_channel_ids),
            cache=verification_cache,
            force_refresh=True,
            **req_kwargs,
        )
        if recheck.status == STATUS_LIVE:
            record["verificationVersion"] = TELEMETRY_SCHEMA_VERSION
            record["lastVerified"] = now_iso
            record["lastGoodVideoId"] = vid
            record["verifiedChannelId"] = recheck.channel_id or (prev_res.channel_id if prev_res else "")
            record["lastStatus"] = reason
            record["lastFailureCategory"] = ""
            record["consecutiveFailures"] = 0
            record["lastChecked"] = now_iso
            record["verificationState"] = STATUS_LIVE
            station_records[st_id] = record
            active_stations.append(_station_entry(item, vid))
            counts["LIVE"] += 1
            summary_rows.append((st_id, old_vid, vid, STATUS_LIVE, reason))
            print(f"  ✓ Verified LIVE (re-checked): {vid} ({reason})", flush=True)
        elif recheck.status == STATUS_TRANSIENT and vid == old_vid:
            transient_categories.append(recheck.category)
            eligible, age_hours, fb_reason = evaluate_fallback_eligibility(
                vid, record, list(item.allowed_channel_ids), now_utc, grace_hours=TRANSIENT_GRACE_HOURS
            )
            record["consecutiveFailures"] = int(record.get("consecutiveFailures") or 0) + 1
            record["lastChecked"] = now_iso
            record["lastFailureCategory"] = recheck.category
            if eligible:
                record["lastStatus"] = f"TRANSIENT_FALLBACK ({age_hours:.1f}h/{TRANSIENT_GRACE_HOURS:.0f}h): {recheck.reason}"
                record["verificationState"] = "TRANSIENT_FALLBACK"
                station_records[st_id] = record
                active_stations.append(_station_entry(item, vid))
                counts["FALLBACK"] += 1
                summary_rows.append((st_id, old_vid, vid, "FALLBACK", record["lastStatus"]))
            else:
                record["lastStatus"] = f"REJECTED_PREPUBLISH ({fb_reason}): {recheck.reason}"
                record["verificationState"] = STATUS_TRANSIENT
                station_records[st_id] = record
                counts["TRANSIENT_EXCLUDED"] += 1
                summary_rows.append((st_id, old_vid, "-", "TRANSIENT", record["lastStatus"]))
        else:
            record["consecutiveFailures"] = int(record.get("consecutiveFailures") or 0) + 1
            record["lastChecked"] = now_iso
            record["lastFailureCategory"] = recheck.category
            record["lastStatus"] = f"REJECTED_PREPUBLISH ({recheck.status}): {recheck.reason}"
            record["verificationState"] = recheck.status
            station_records[st_id] = record
            if recheck.status == STATUS_OFFLINE:
                counts["OFFLINE"] += 1
            else:
                counts["TRANSIENT_EXCLUDED"] += 1
            summary_rows.append((st_id, old_vid, "-", recheck.status, record["lastStatus"]))
            print(f"  ✗ Pre-publish re-check failed for {vid}: {recheck.reason}", flush=True)

    enabled_total = sum(1 for s in catalog if s.enabled)
    provider_degraded = detect_provider_degradation(transient_categories, enabled_total)
    status_data["providerDegraded"] = provider_degraded

    print("\n" + "=" * 120, flush=True)
    print(f"{'STATION ID':<22} | {'OLD VIDEO ID':<13} | {'NEW VIDEO ID':<13} | {'STATUS':<9} | {'REASON'}", flush=True)
    print("=" * 120, flush=True)
    for sid, old_v, new_v, status_val, rsn in summary_rows:
        print(f"{sid:<22} | {str(old_v):<13} | {str(new_v):<13} | {status_val:<9} | {rsn}", flush=True)
    print("=" * 120, flush=True)
    print(
        f"Summary -> Active: {len(active_stations)}/{enabled_total} | "
        f"LIVE: {counts['LIVE']} | FALLBACK: {counts['FALLBACK']} | "
        f"OFFLINE: {counts['OFFLINE']} | TRANSIENT_EXCLUDED: {counts['TRANSIENT_EXCLUDED']} | "
        f"DISABLED: {counts['DISABLED']} | providerDegraded: {provider_degraded}",
        flush=True,
    )

    if len(active_stations) < OPERATIONAL_LOW_STATION_THRESHOLD:
        print(
            f"[WARN] Operational alert: active station count ({len(active_stations)}) "
            f"< {OPERATIONAL_LOW_STATION_THRESHOLD}. Publishing valid verified/fallback output without reviving dead streams.",
            flush=True,
        )

    # Prepare and validate ALL candidate configs in memory BEFORE writing any file
    prepared_configs = []
    for cfg_path in config_files:
        if not os.path.exists(cfg_path):
            print(f"[ERROR] Required config file missing: {cfg_path}", flush=True)
            return 1
        try:
            with open(cfg_path, "rb") as f:
                original_bytes = f.read()
            cfg = json.loads(original_bytes.decode("utf-8"))
        except Exception as e:
            print(f"[ERROR] Failed to read/parse {cfg_path}: {e}", flush=True)
            return 1

        if not isinstance(cfg, dict):
            print(f"[ERROR] {cfg_path} root must be a JSON object", flush=True)
            return 1

        # Preserve promos and all other non-radio fields!
        cfg["radioStations"] = active_stations
        ok, errors, warnings = validate_config_data(cfg, label=cfg_path, require_min_stations=False)
        for warn in warnings:
            print(f"  [WARN] {warn}", flush=True)
        if not ok:
            print(f"[ERROR] Candidate validation failed for {cfg_path}:", flush=True)
            for err in errors:
                print(f"  - {err}", flush=True)
            return 1
        prepared_configs.append((cfg_path, original_bytes, cfg))

    if dry_run:
        print("[DRY RUN] Candidate configs validated in memory. Zero files modified.", flush=True)
        return 0

    # Publish per-file atomically; rollback already-updated configs on partial failure
    written_backups = []
    try:
        for cfg_path, original_bytes, cfg_dict in prepared_configs:
            atomic_write_json(cfg_path, cfg_dict)
            written_backups.append((cfg_path, original_bytes))
            print(f"Successfully updated {cfg_path} with {len(active_stations)} stations.", flush=True)
    except Exception as e:
        print(f"[ERROR] Publication failed: {e}", flush=True)
        for prev_path, prev_bytes in reversed(written_backups):
            try:
                atomic_write_json(prev_path, json.loads(prev_bytes.decode("utf-8")))
                print(f"[ROLLBACK] Restored {prev_path}", flush=True)
            except Exception as rb_err:
                print(f"[ERROR] Rollback failed for {prev_path}: {rb_err}", flush=True)
        return 1

    # Record publication & telemetry ONLY after config publication succeeds
    status_data["lastUpdated"] = now_iso
    status_data["lastPublished"] = now_iso
    status_data["lastPublishedCount"] = len(active_stations)
    try:
        atomic_write_json(status_registry_path, status_data)
        print(f"Saved diagnostic telemetry to {status_registry_path}", flush=True)
    except Exception as e:
        print(f"[ERROR] Failed to write telemetry {status_registry_path}: {e}", flush=True)
        return 1

    print("\nSynchronization completed successfully.", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description="HiFi Live Radio Sync & Discovery")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing any files")
    parser.add_argument(
        "--mode",
        choices=("health-check", "discovery"),
        default="health-check",
        help="health-check (default) or broad discovery",
    )
    args = parser.parse_args()

    if args.mode == "discovery":
        rc = run_discovery(
            dry_run=args.dry_run,
            registry_path=DISCOVERY_REGISTRY,
            atomic_write_fn=atomic_write_json,
        )
    else:
        rc = run_sync(dry_run=args.dry_run)
    sys.exit(rc)


if __name__ == "__main__":
    main()
