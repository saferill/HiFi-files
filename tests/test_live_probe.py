#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Opt-in live probe against real YouTube endpoints.
Skipped unless HIFI_LIVE_PROBE=1 is set in the environment.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import radio_providers


class TestLiveProbe(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("HIFI_LIVE_PROBE") == "1", "Set HIFI_LIVE_PROBE=1 to run live network probe")
    def test_innertube_live_search_returns_candidates_with_channel_ids(self):
        res = radio_providers.search_innertube_live("lofi hip hop radio", max_pages=1, max_candidates=5)
        self.assertTrue(res.ok, res.error_msg)
        self.assertGreater(len(res.items), 0)


if __name__ == "__main__":
    unittest.main()

