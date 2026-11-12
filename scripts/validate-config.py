#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Validation script for HiFi Music remote configuration.
Ensures JSON syntax integrity, schema conformity for radioStations,
regex adherence, uniqueness, thumbnail parity, and minimum station count.
"""

import sys
import json
import re
import os

MIN_STATIONS = 8
CONFIG_FILES = ["remote-config.json", "remote-config-dev.json"]
VID_REGEX = re.compile(r"^[A-Za-z0-9_-]{11}$")
REQUIRED_FIELDS = {"id", "title", "subtitle", "thumbnail", "videoId"}

def validate_file(filepath):
    print(f"Validating {filepath}...")
    if not os.path.exists(filepath):
        print(f"  [ERROR] File not found: {filepath}")
        return False

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"  [ERROR] Invalid JSON: {e}")
        return False

    if not isinstance(cfg, dict):
        print("  [ERROR] Root JSON must be an object")
        return False

    stations = cfg.get("radioStations")
    if not isinstance(stations, list):
        print("  [ERROR] 'radioStations' must be an array")
        return False

    if len(stations) < MIN_STATIONS:
        print(f"  [ERROR] Station count ({len(stations)}) is below MIN_STATIONS ({MIN_STATIONS})")
        return False

    seen_ids = set()
    seen_vids = set()

    for idx, st in enumerate(stations):
        if not isinstance(st, dict):
            print(f"  [ERROR] Station at index {idx} is not an object")
            return False

        missing = REQUIRED_FIELDS - set(st.keys())
        if missing:
            print(f"  [ERROR] Station at index {idx} missing fields: {missing}")
            return False

        st_id = st.get("id", "")
        vid = st.get("videoId", "")
        thumb = st.get("thumbnail", "")

        if not st_id or not isinstance(st_id, str):
            print(f"  [ERROR] Station at index {idx} has invalid 'id'")
            return False

        if st_id in seen_ids:
            print(f"  [ERROR] Duplicate station id found: '{st_id}'")
            return False
        seen_ids.add(st_id)

        if not vid or not VID_REGEX.match(vid):
            print(f"  [ERROR] Station '{st_id}' has invalid videoId: '{vid}' (must match 11-char pattern)")
            return False

        if vid in seen_vids:
            print(f"  [ERROR] Duplicate videoId found: '{vid}' across stations")
            return False
        seen_vids.add(vid)

        if vid not in thumb:
            print(f"  [ERROR] Station '{st_id}' thumbnail '{thumb}' does not match videoId '{vid}'")
            return False

    print(f"  [OK] Validated {len(stations)} stations successfully.")
    return True

def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else CONFIG_FILES
    all_ok = True
    for t in targets:
        if not validate_file(t):
            all_ok = False

    if not all_ok:
        print("\nValidation failed!")
        sys.exit(1)
    print("\nAll configurations passed validation.")
    sys.exit(0)

if __name__ == "__main__":
    main()

