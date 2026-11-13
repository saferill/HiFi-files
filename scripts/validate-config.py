#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Offline schema validator for HiFi Music remote configuration.

Separates schema validity from operational health warnings:
- 0..7 stations is schema-valid (emits an operational low-count warning).
- Invalid JSON, wrong types, empty required fields, invalid 11-char videoId,
  invalid i.ytimg.com thumbnail URL, or duplicate station/video IDs fail
  with clear error messages and nonzero exit code (no tracebacks).
"""

import json
import os
import re
import sys
from urllib.parse import urlparse

OPERATIONAL_MIN_STATIONS = 8
CONFIG_FILES = ["remote-config.json", "remote-config-dev.json"]
VID_REGEX = re.compile(r"^[A-Za-z0-9_-]{11}$")
REQUIRED_FIELDS = ("id", "title", "subtitle", "thumbnail", "videoId")


def _is_non_empty_str(value):
    return isinstance(value, str) and value.strip() != ""


def validate_config_data(cfg, label="<config>", *, require_min_stations=False):
    """
    Validate an already-parsed config dictionary.
    Returns (ok: bool, errors: list[str], warnings: list[str]).
    """
    errors = []
    warnings = []
    if not isinstance(cfg, dict):
        return False, [f"{label}: Root JSON must be an object"], warnings

    stations = cfg.get("radioStations")
    if not isinstance(stations, list):
        return False, [f"{label}: 'radioStations' must be an array"], warnings

    if len(stations) < OPERATIONAL_MIN_STATIONS:
        msg = (
            f"{label}: Operational warning: station count ({len(stations)}) "
            f"is below {OPERATIONAL_MIN_STATIONS}"
        )
        if require_min_stations:
            errors.append(msg)
        else:
            warnings.append(msg)

    seen_ids = set()
    seen_vids = set()

    for idx, st in enumerate(stations):
        if not isinstance(st, dict):
            errors.append(f"{label}: Station at index {idx} must be an object")
            continue

        missing = [field for field in REQUIRED_FIELDS if field not in st]
        if missing:
            errors.append(
                f"{label}: Station at index {idx} missing required fields: {missing}"
            )
            continue

        st_id = st.get("id")
        title = st.get("title")
        subtitle = st.get("subtitle")
        vid = st.get("videoId")
        thumb = st.get("thumbnail")

        if not _is_non_empty_str(st_id):
            errors.append(f"{label}: Station at index {idx} has invalid or empty 'id'")
            continue

        if not _is_non_empty_str(title):
            errors.append(f"{label}: Station '{st_id}' has invalid or empty 'title'")

        if not _is_non_empty_str(subtitle):
            errors.append(f"{label}: Station '{st_id}' has invalid or empty 'subtitle'")

        if st_id in seen_ids:
            errors.append(f"{label}: Duplicate station id found: '{st_id}'")
        seen_ids.add(st_id)

        if not isinstance(vid, str) or not VID_REGEX.fullmatch(vid):
            errors.append(
                f"{label}: Station '{st_id}' has invalid videoId: {vid!r} (must be 11-char YouTube ID)"
            )
            continue

        if vid in seen_vids:
            errors.append(f"{label}: Duplicate videoId found: '{vid}' across stations")
        seen_vids.add(vid)

        if not _is_non_empty_str(thumb):
            errors.append(f"{label}: Station '{st_id}' has invalid or empty 'thumbnail'")
            continue

        parsed = urlparse(thumb)
        expected_path = f"/vi/{vid}/hqdefault.jpg"
        if (
            parsed.scheme != "https"
            or parsed.netloc != "i.ytimg.com"
            or parsed.path != expected_path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            errors.append(
                f"{label}: Station '{st_id}' thumbnail '{thumb}' must be "
                f"'https://i.ytimg.com{expected_path}'"
            )

    return len(errors) == 0, errors, warnings


def validate_file(filepath, *, require_min_stations=False):
    print(f"Validating {filepath}...")
    if not os.path.exists(filepath):
        print(f"  [ERROR] File not found: {filepath}")
        return False

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"  [ERROR] Invalid JSON in {filepath}: {e}")
        return False

    ok, errors, warnings = validate_config_data(
        cfg, label=filepath, require_min_stations=require_min_stations
    )
    for warn in warnings:
        print(f"  [WARN] {warn}")
    if not ok:
        for err in errors:
            print(f"  [ERROR] {err}")
        return False

    stations = cfg["radioStations"]
    print(f"  [OK] Validated {len(stations)} stations successfully.")
    return True


def main():
    args = [a for a in sys.argv[1:] if a != "--strict-min-stations"]
    strict = "--strict-min-stations" in sys.argv[1:]
    targets = args if args else CONFIG_FILES
    all_ok = True
    for target in targets:
        if not validate_file(target, require_min_stations=strict):
            all_ok = False

    if not all_ok:
        print("\nValidation failed!")
        sys.exit(1)
    print("\nAll configurations passed validation.")
    sys.exit(0)


if __name__ == "__main__":
    main()
