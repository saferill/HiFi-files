#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

validate_config = __import__("validate-config")
validate_player_config = __import__("validate-player-config")


def make_valid_station(idx):
    vid = f"vidTest{idx:04d}"
    return {
        "id": f"station-{idx}",
        "title": f"Station {idx}",
        "subtitle": "Live",
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "videoId": vid,
    }


def make_valid_config(count=8):
    return {
        "tidalClientId": "keep-me",
        "promos": [{"id": "custom-promo"}],
        "radioStations": [make_valid_station(i) for i in range(count)],
    }


class TestValidateConfig(unittest.TestCase):
    def test_valid_config_passes(self):
        cfg = make_valid_config(8)
        ok, errors, warnings = validate_config.validate_config_data(cfg)
        self.assertTrue(ok, errors)
        self.assertEqual(warnings, [])

    def test_low_station_counts_0_1_7_are_schema_valid_with_warning(self):
        for count in (0, 1, 7):
            cfg = make_valid_config(count)
            ok, errors, warnings = validate_config.validate_config_data(cfg)
            self.assertTrue(ok, errors)
            self.assertEqual(len(warnings), 1)

    def test_26_wrong_types_and_empty_fields_fail_without_traceback(self):
        base = make_valid_config(8)

        c1 = copy.deepcopy(base)
        c1["radioStations"][0]["videoId"] = 12345678901
        ok, errs, _ = validate_config.validate_config_data(c1)
        self.assertFalse(ok)
        self.assertTrue(errs)

        c2 = copy.deepcopy(base)
        c2["radioStations"][0]["videoId"] = None
        ok, _, _ = validate_config.validate_config_data(c2)
        self.assertFalse(ok)

        c3 = copy.deepcopy(base)
        c3["radioStations"][0]["thumbnail"] = 999
        ok, _, _ = validate_config.validate_config_data(c3)
        self.assertFalse(ok)

        c4 = copy.deepcopy(base)
        c4["radioStations"][0]["title"] = "   "
        ok, _, _ = validate_config.validate_config_data(c4)
        self.assertFalse(ok)

    def test_27_duplicate_station_id_and_video_id_rejected(self):
        base = make_valid_config(8)

        c1 = copy.deepcopy(base)
        c1["radioStations"][1]["id"] = c1["radioStations"][0]["id"]
        ok, _, _ = validate_config.validate_config_data(c1)
        self.assertFalse(ok)

        c2 = copy.deepcopy(base)
        dup_vid = c2["radioStations"][0]["videoId"]
        c2["radioStations"][1]["videoId"] = dup_vid
        c2["radioStations"][1]["thumbnail"] = f"https://i.ytimg.com/vi/{dup_vid}/hqdefault.jpg"
        ok, _, _ = validate_config.validate_config_data(c2)
        self.assertFalse(ok)

    def test_28_thumbnail_wrong_host_or_path_rejected(self):
        base = make_valid_config(8)
        vid0 = base["radioStations"][0]["videoId"]

        c1 = copy.deepcopy(base)
        c1["radioStations"][0]["thumbnail"] = f"https://evil.example.com/vi/{vid0}/hqdefault.jpg"
        ok, _, _ = validate_config.validate_config_data(c1)
        self.assertFalse(ok)

        c2 = copy.deepcopy(base)
        c2["radioStations"][0]["thumbnail"] = f"https://i.ytimg.com/vi/{vid0}/maxresdefault.jpg"
        ok, _, _ = validate_config.validate_config_data(c2)
        self.assertFalse(ok)

        c3 = copy.deepcopy(base)
        c3["radioStations"][0]["thumbnail"] = f"http://i.ytimg.com/vi/{vid0}/hqdefault.jpg"
        ok, _, _ = validate_config.validate_config_data(c3)
        self.assertFalse(ok)

    def test_cli_exits_nonzero_without_traceback_on_invalid_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = os.path.join(tmp, "bad.json")
            cfg = make_valid_config(8)
            cfg["radioStations"][0]["videoId"] = 12345
            with open(bad_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f)

            proc = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS_DIR, "validate-config.py"), bad_path],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertNotIn("Traceback", proc.stderr)
            self.assertNotIn("Traceback", proc.stdout)

    def test_34_player_config_validator_rejects_broken_and_accepts_valid(self):
        ok, _ = validate_player_config.validate_player_config_data(
            {"schemaVersion": 1, "players": {"123": {"sts": 20634, "aliases": ["abc"]}}}
        )
        self.assertTrue(ok)

        ok, _ = validate_player_config.validate_player_config_data({"schemaVersion": 1, "players": {}})
        self.assertFalse(ok)

        ok, _ = validate_player_config.validate_player_config_data(
            {"schemaVersion": "1", "players": {"123": {"sts": "bad"}}}
        )
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
