#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Offline schema validator for registry/player_configs.json.
Ensures valid JSON, integer schemaVersion, and non-empty players mapping.
"""

import json
import os
import sys

DEFAULT_PATH = "registry/player_configs.json"


def validate_player_config_data(data, label="<player_configs>"):
    """Validate parsed player_configs dictionary. Returns (ok: bool, errors: list[str])."""
    errors = []
    if not isinstance(data, dict):
        return False, [f"{label}: Root JSON must be an object"]

    schema_version = data.get("schemaVersion")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version < 1:
        errors.append(f"{label}: 'schemaVersion' must be a positive integer")

    players = data.get("players")
    if not isinstance(players, dict) or len(players) == 0:
        errors.append(f"{label}: 'players' must be a non-empty object")
        return False, errors

    for player_id, entry in players.items():
        if not isinstance(player_id, str) or not player_id.strip():
            errors.append(f"{label}: Invalid player key {player_id!r}")
            continue
        if not isinstance(entry, dict):
            errors.append(f"{label}: Player '{player_id}' entry must be an object")
            continue
        sts = entry.get("sts")
        if sts is not None and (not isinstance(sts, int) or isinstance(sts, bool)):
            errors.append(f"{label}: Player '{player_id}' has non-integer 'sts'")
        aliases = entry.get("aliases")
        if aliases is not None and (
            not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases)
        ):
            errors.append(f"{label}: Player '{player_id}' has invalid 'aliases'")

    return len(errors) == 0, errors


def validate_file(filepath):
    print(f"Validating {filepath}...")
    if not os.path.exists(filepath):
        print(f"  [ERROR] File not found: {filepath}")
        return False

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"  [ERROR] Invalid JSON in {filepath}: {e}")
        return False

    ok, errors = validate_player_config_data(data, label=filepath)
    if not ok:
        for err in errors:
            print(f"  [ERROR] {err}")
        return False

    print(f"  [OK] Validated {len(data['players'])} player entries (schemaVersion={data['schemaVersion']}).")
    return True


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    if not validate_file(target):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

