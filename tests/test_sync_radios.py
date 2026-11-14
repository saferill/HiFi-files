#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from datetime import datetime, timedelta, timezone
import io
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

sync_radios = __import__("sync-radios")
import radio_catalog
import radio_discovery
import radio_policy
import radio_providers
import radio_verifier

GOOD_VID = "abcDEF12345"
GOOD_CH = "UC1111111111111111111111"
OTHER_CH = "UC9999999999999999999999"


class FakeHTTPResponse:
    def __init__(self, body_bytes, status=200, url="https://www.youtube.com/watch?v=abcDEF12345"):
        self._buf = io.BytesIO(body_bytes)
        self.status = status
        self._url = url

    def read(self, n=-1):
        return self._buf.read(n)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def make_player_payload(
    *,
    status="OK",
    video_id=GOOD_VID,
    channel_id=GOOD_CH,
    is_live=True,
    is_live_content=True,
    is_upcoming=False,
    is_live_now=True,
    end_timestamp=None,
    reason="",
):
    lbd = {"isLiveNow": is_live_now}
    if end_timestamp is not None:
        lbd["endTimestamp"] = end_timestamp
    vd = {
        "videoId": video_id,
        "channelId": channel_id,
        "author": "Test Channel",
        "title": "Test Live Stream",
        "isLiveContent": is_live_content,
        "isUpcoming": is_upcoming,
    }
    if is_live is not None:
        vd["isLive"] = is_live
    ps = {"status": status}
    if reason:
        ps["reason"] = reason
    return {
        "playabilityStatus": ps,
        "videoDetails": vd,
        "microformat": {"playerMicroformatRenderer": {"liveBroadcastDetails": lbd}},
    }


def make_opener(routes):
    calls = []

    def opener(req, timeout=8):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        calls.append(url)
        for key, handler in routes.items():
            if key in url:
                val = handler(req) if callable(handler) else handler
                if isinstance(val, Exception):
                    raise val
                return val
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    opener.calls = calls
    return opener


def make_stations(count=8):
    return [
        radio_catalog.StationSpec(
            id=f"st-{i}",
            title=f"Station {i}",
            subtitle="Live",
            category="lofi",
            handle=f"@st{i}",
            allowed_channel_ids=[f"UC{i:022d}"],
            search_query=f"st{i} live",
            default_vid=f"defVid{i:05d}",
        )
        for i in range(count)
    ]


class TestVerificationAndNetwork(unittest.TestCase):
    def test_01_unplayable_rejected(self):
        payload = make_player_payload(status="UNPLAYABLE", reason="Video unavailable")
        res = radio_verifier.evaluate_player_response(payload, GOOD_VID, [GOOD_CH])
        self.assertEqual(res.status, radio_verifier.STATUS_OFFLINE)

    def test_02_missing_playability_status_rejected_as_transient(self):
        payload = {"videoDetails": {"videoId": GOOD_VID, "channelId": GOOD_CH, "isLive": True}}
        res = radio_verifier.evaluate_player_response(payload, GOOD_VID, [GOOD_CH])
        self.assertEqual(res.status, radio_verifier.STATUS_TRANSIENT)

    def test_03_recommended_live_video_does_not_make_dead_target_live(self):
        target_pr = json.dumps(make_player_payload(is_live=False, is_live_now=False))
        html = (
            f'<html><script>var ytInitialPlayerResponse = {target_pr};</script>'
            f'<script>var ytInitialData = {{"recommended": {{"videoId": "other123456", "isLiveNow": true}} }};</script></html>'
        )
        opener = make_opener({
            "youtubei/v1/player": lambda _: FakeHTTPResponse(target_pr.encode()),
            "watch?v=": lambda _: FakeHTTPResponse(html.encode()),
        })
        res = radio_verifier.verify_video_is_live(GOOD_VID, [GOOD_CH], opener=opener, sleep_fn=lambda _: None)
        self.assertEqual(res.status, radio_verifier.STATUS_OFFLINE)

    def test_04_metadata_videoid_mismatch_rejected(self):
        payload = make_player_payload(video_id="wrongVid123")
        res = radio_verifier.evaluate_player_response(payload, GOOD_VID, [GOOD_CH])
        self.assertEqual(res.status, radio_verifier.STATUS_OFFLINE)

    def test_05_channel_outside_allowlist_rejected(self):
        payload = make_player_payload(channel_id=OTHER_CH)
        res = radio_verifier.evaluate_player_response(payload, GOOD_VID, [GOOD_CH])
        self.assertEqual(res.status, radio_verifier.STATUS_OFFLINE)

    def test_06_recorded_past_live_rejected(self):
        payload = make_player_payload(is_live=None, is_live_content=True, is_live_now=False)
        res = radio_verifier.evaluate_player_response(payload, GOOD_VID, [GOOD_CH])
        self.assertEqual(res.status, radio_verifier.STATUS_OFFLINE)

    def test_07_upcoming_premiere_and_regular_video_rejected(self):
        upcoming = make_player_payload(is_live=False, is_upcoming=True, is_live_now=False)
        self.assertEqual(radio_verifier.evaluate_player_response(upcoming, GOOD_VID, [GOOD_CH]).status, radio_verifier.STATUS_OFFLINE)

        regular = make_player_payload(is_live=False, is_live_content=False, is_live_now=False)
        self.assertEqual(radio_verifier.evaluate_player_response(regular, GOOD_VID, [GOOD_CH]).status, radio_verifier.STATUS_OFFLINE)

    def test_08_valid_active_live_accepted(self):
        pr = json.dumps(make_player_payload()).encode()
        opener = make_opener({
            "youtubei/v1/player": lambda _: FakeHTTPResponse(pr),
            "oembed": lambda _: FakeHTTPResponse(b'{"title":"Live"}'),
            "hqdefault.jpg": lambda _: FakeHTTPResponse(b"jpgbytes"),
        })
        res = radio_verifier.verify_video_is_live(GOOD_VID, [GOOD_CH], opener=opener, sleep_fn=lambda _: None)
        self.assertEqual(res.status, radio_verifier.STATUS_LIVE)

    def test_09_ambiguous_response_returns_transient(self):
        opener = make_opener({
            "youtubei/v1/player": lambda _: FakeHTTPResponse(b"{}"),
            "watch?v=": lambda _: FakeHTTPResponse(b"<html><body>No player</body></html>"),
        })
        res = radio_verifier.verify_video_is_live(GOOD_VID, [GOOD_CH], opener=opener, sleep_fn=lambda _: None)
        self.assertEqual(res.status, radio_verifier.STATUS_TRANSIENT)

    def test_11_timeout_429_5xx_are_transient(self):
        for err in (
            socket.timeout("timed out"),
            urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None),
            urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None),
        ):
            opener = make_opener({"youtubei/v1/player": err, "watch?v=": err})
            res = radio_verifier.verify_video_is_live(GOOD_VID, [GOOD_CH], opener=opener, sleep_fn=lambda _: None)
            self.assertEqual(res.status, radio_verifier.STATUS_TRANSIENT)

    def test_12_oembed_or_thumbnail_transient_failure_is_not_offline(self):
        pr = json.dumps(make_player_payload()).encode()
        opener = make_opener({
            "youtubei/v1/player": lambda _: FakeHTTPResponse(pr),
            "oembed": lambda _: FakeHTTPResponse(b'{"title":"Live"}'),
            "hqdefault.jpg": socket.timeout("thumb timeout"),
        })
        res = radio_verifier.verify_video_is_live(GOOD_VID, [GOOD_CH], opener=opener, sleep_fn=lambda _: None)
        self.assertEqual(res.status, radio_verifier.STATUS_TRANSIENT)

    def test_13_and_14_retry_pagination_duration_bounds_and_empty_vs_failed_search(self):
        # Empty search (ok=True) vs failed search (ok=False)
        empty_page = json.dumps({"contents": {"twoColumnSearchResultsRenderer": {"primaryContents": {"sectionListRenderer": {"contents": []}}}}}).encode()
        res_empty = radio_providers.search_innertube_live(
            "q", opener=make_opener({"youtubei/v1/search": lambda _: FakeHTTPResponse(empty_page)}), sleep_fn=lambda _: None
        )
        self.assertTrue(res_empty.ok)
        self.assertEqual(res_empty.items, [])

        res_fail = radio_providers.search_innertube_live(
            "q", opener=make_opener({"youtubei/v1/search": socket.timeout("timeout")}), sleep_fn=lambda _: None
        )
        self.assertFalse(res_fail.ok)

        # Pagination stops at max_pages=2 even if continuation token keeps appearing
        def paged_search(req):
            body = json.loads(req.data.decode())
            vid = "page1Vid001" if "continuation" not in body else "page2Vid002"
            payload = {
                "contents": {
                    "twoColumnSearchResultsRenderer": {
                        "primaryContents": {
                            "sectionListRenderer": {
                                "contents": [
                                    {"itemSectionRenderer": {"contents": [{"videoRenderer": {"videoId": vid}}]}},
                                    {"continuationItemRenderer": {"continuationEndpoint": {"continuationCommand": {"token": "next-tok"}}}},
                                ]
                            }
                        }
                    }
                }
            } if "continuation" not in body else {
                "onResponseReceivedCommands": [
                    {
                        "appendContinuationItemsAction": {
                            "continuationItems": [
                                {"videoRenderer": {"videoId": vid}},
                                {"continuationItemRenderer": {"continuationEndpoint": {"continuationCommand": {"token": "tok-3"}}}},
                            ]
                        }
                    }
                ]
            }
            return FakeHTTPResponse(json.dumps(payload).encode())

        opener = make_opener({"youtubei/v1/search": paged_search})
        paged_res = radio_providers.search_innertube_live("q", max_pages=2, max_candidates=10, opener=opener, sleep_fn=lambda _: None)
        self.assertTrue(paged_res.ok)
        self.assertEqual(len(opener.calls), 2)
        self.assertEqual([c.video_id for c in paged_res.items], ["page1Vid001", "page2Vid002"])


class TestFallbackPolicyAndOrchestration(unittest.TestCase):
    def _write_configs(self, tmp, stations_cfg, extras=None):
        paths = [os.path.join(tmp, "remote-config.json"), os.path.join(tmp, "remote-config-dev.json")]
        for p in paths:
            data = {"promos": [{"id": "keep-promo"}], "tidalClientId": "abc", "radioStations": stations_cfg}
            if extras:
                data.update(extras)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f)
        return paths

    def test_15_to_21_six_hour_grace_from_last_verified_and_timestamp_guards(self):
        now = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
        ch = ["UC0000000000000000000000"]

        # 15 & 16: Within 6h from lastVerified -> eligible
        rec_ok = {
            "verificationVersion": 2,
            "lastGoodVideoId": GOOD_VID,
            "verifiedChannelId": ch[0],
            "lastVerified": "2026-11-13T07:00:00Z",  # 5h ago
        }
        elig, age, _ = radio_policy.evaluate_fallback_eligibility(GOOD_VID, rec_ok, ch, now)
        self.assertTrue(elig)
        self.assertAlmostEqual(age, 5.0, places=2)

        # 17: Repeated failures do NOT extend beyond 6h from lastVerified
        rec_expired = {
            "verificationVersion": 2,
            "lastGoodVideoId": GOOD_VID,
            "verifiedChannelId": ch[0],
            "lastVerified": "2026-11-13T05:00:00Z",  # 7h ago
            "consecutiveFailures": 3,
        }
        elig, _, _ = radio_policy.evaluate_fallback_eligibility(GOOD_VID, rec_expired, ch, now)
        self.assertFalse(elig)

        # 18: Unverified defaultVid (no record / wrong videoId) gets NO fallback
        elig, _, _ = radio_policy.evaluate_fallback_eligibility("defVid00000", {}, ch, now)
        self.assertFalse(elig)

        # 20: Corrupted, naive, and future timestamps rejected
        for bad_ts in ("not-a-date", "2026-11-13T11:00:00", "2026-11-14T12:00:00Z"):
            rec_bad = dict(rec_ok, lastVerified=bad_ts)
            elig, _, _ = radio_policy.evaluate_fallback_eligibility(GOOD_VID, rec_bad, ch, now)
            self.assertFalse(elig, f"Should reject timestamp {bad_ts!r}")

        # 21: Historical v1 telemetry without verifiedChannelId not automatically trusted
        rec_v1 = {"lastGoodVideoId": GOOD_VID, "lastVerified": "2026-11-13T11:00:00Z"}
        elig, _, _ = radio_policy.evaluate_fallback_eligibility(GOOD_VID, rec_v1, ch, now)
        self.assertFalse(elig)

    def test_10_19_29_30_31_35_recheck_offline_pruned_promos_preserved_and_low_count_published(self):
        stations = make_stations(8)
        initial_cfg = [
            {
                "id": st.id,
                "title": st.title,
                "subtitle": st.subtitle,
                "thumbnail": f"https://i.ytimg.com/vi/oldVid{i:05d}/hqdefault.jpg",
                "videoId": f"oldVid{i:05d}",
            }
            for i, st in enumerate(stations)
        ]

        # Test 1 station live, 7 offline -> valid 1-station output published (30, 31, 35)
        with tempfile.TemporaryDirectory() as tmp:
            cfg_paths = self._write_configs(tmp, initial_cfg, extras={"extraField": 42})
            reg_path = os.path.join(tmp, "radio-status.json")

            def fake_find(item, used, **kw):
                if item.id == "st-0":
                    return True, "newVid00000", radio_verifier.VerificationResult("LIVE", "OK", video_id="newVid00000", channel_id=item.allowed_channel_ids[0]), "OK"
                return False, None, None, "Offline"

            def fake_verify(vid, allowed_channel_ids=None, force_refresh=False, **kw):
                if vid == "newVid00000":
                    return radio_verifier.VerificationResult("LIVE", "OK", video_id=vid, channel_id="UC0000000000000000000000")
                return radio_verifier.VerificationResult("OFFLINE", "Ended", category="ended_or_vod", video_id=vid)

            rc = sync_radios.run_sync(
                config_files=cfg_paths,
                status_registry_path=reg_path,
                stations=stations,
                sleep_fn=lambda _: None,
                find_fn=fake_find,
                verify_fn=fake_verify,
            )
            self.assertEqual(rc, 0)
            with open(cfg_paths[0], "r", encoding="utf-8") as f:
                out = json.load(f)
            self.assertEqual(len(out["radioStations"]), 1)
            self.assertEqual(out["radioStations"][0]["videoId"], "newVid00000")
            self.assertEqual(out["promos"], [{"id": "keep-promo"}])
            self.assertEqual(out["extraField"], 42)

        # Test 0 stations live -> valid empty list published cleanly (30)
        with tempfile.TemporaryDirectory() as tmp:
            cfg_paths = self._write_configs(tmp, initial_cfg)
            reg_path = os.path.join(tmp, "radio-status.json")
            rc = sync_radios.run_sync(
                config_files=cfg_paths,
                status_registry_path=reg_path,
                stations=stations,
                sleep_fn=lambda _: None,
                find_fn=lambda *a, **kw: (False, None, None, "Offline"),
                verify_fn=lambda vid, **kw: radio_verifier.VerificationResult("OFFLINE", "Ended", category="ended_or_vod", video_id=vid),
            )
            self.assertEqual(rc, 0)
            with open(cfg_paths[0], "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["radioStations"], [])

        # Test 10: Pre-publish recheck bypasses cache and rejects newly-offline candidate
        with tempfile.TemporaryDirectory() as tmp:
            cfg_paths = self._write_configs(tmp, initial_cfg)
            reg_path = os.path.join(tmp, "radio-status.json")
            rechecks = []

            def fake_verify_recheck(vid, allowed_channel_ids=None, force_refresh=False, **kw):
                if force_refresh:
                    rechecks.append(vid)
                    if vid == "newVid00000":
                        return radio_verifier.VerificationResult("OFFLINE", "Ended before publish", category="ended_or_vod", video_id=vid)
                return radio_verifier.VerificationResult("LIVE", "OK", video_id=vid, channel_id=(allowed_channel_ids or [""])[0])

            rc = sync_radios.run_sync(
                config_files=cfg_paths,
                status_registry_path=reg_path,
                stations=stations,
                sleep_fn=lambda _: None,
                find_fn=lambda item, used, **kw: (True, f"newVid{int(item.id.split('-')[1]):05d}", None, "OK"),
                verify_fn=fake_verify_recheck,
            )
            self.assertEqual(rc, 0)
            self.assertEqual(len(rechecks), 8)
            with open(cfg_paths[0], "r", encoding="utf-8") as f:
                self.assertEqual(len(json.load(f)["radioStations"]), 7)

    def test_32_write_failure_returns_nonzero_and_rolls_back(self):
        stations = make_stations(8)
        old_cfg = [
            {
                "id": st.id,
                "title": st.title,
                "subtitle": st.subtitle,
                "thumbnail": f"https://i.ytimg.com/vi/oldVid{i:05d}/hqdefault.jpg",
                "videoId": f"oldVid{i:05d}",
            }
            for i, st in enumerate(stations)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            cfg_paths = self._write_configs(tmp, old_cfg)
            reg_path = os.path.join(tmp, "radio-status.json")
            real_atomic = sync_radios.atomic_write_json
            calls = {"n": 0}

            def flaky_atomic(path, data):
                calls["n"] += 1
                if calls["n"] == 2:  # Fail on second config write
                    raise OSError("Disk full")
                return real_atomic(path, data)

            with mock.patch.object(sync_radios, "atomic_write_json", side_effect=flaky_atomic):
                rc = sync_radios.run_sync(
                    config_files=cfg_paths,
                    status_registry_path=reg_path,
                    stations=stations,
                    sleep_fn=lambda _: None,
                    find_fn=lambda item, used, **kw: (True, f"newVid{int(item.id.split('-')[1]):05d}", None, "OK"),
                    verify_fn=lambda vid, allowed_channel_ids=None, **kw: radio_verifier.VerificationResult("LIVE", "OK", video_id=vid, channel_id=(allowed_channel_ids or [""])[0]),
                )
            self.assertEqual(rc, 1)
            with open(cfg_paths[0], "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["radioStations"][0]["videoId"], "oldVid00000")

    def test_33_dry_run_modifies_zero_files(self):
        stations = make_stations(8)
        old_cfg = [
            {
                "id": st.id,
                "title": st.title,
                "subtitle": st.subtitle,
                "thumbnail": f"https://i.ytimg.com/vi/oldVid{i:05d}/hqdefault.jpg",
                "videoId": f"oldVid{i:05d}",
            }
            for i, st in enumerate(stations)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            cfg_paths = self._write_configs(tmp, old_cfg)
            reg_path = os.path.join(tmp, "radio-status.json")
            with open(reg_path, "w", encoding="utf-8") as f:
                json.dump({"lastUpdated": "orig", "stations": {}}, f)

            rc = sync_radios.run_sync(
                dry_run=True,
                config_files=cfg_paths,
                status_registry_path=reg_path,
                stations=stations,
                sleep_fn=lambda _: None,
                find_fn=lambda item, used, **kw: (True, f"newVid{int(item.id.split('-')[1]):05d}", None, "OK"),
                verify_fn=lambda vid, allowed_channel_ids=None, **kw: radio_verifier.VerificationResult("LIVE", "OK", video_id=vid, channel_id=(allowed_channel_ids or [""])[0]),
            )
            self.assertEqual(rc, 0)
            with open(cfg_paths[0], "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["radioStations"][0]["videoId"], "oldVid00000")
            with open(reg_path, "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["lastUpdated"], "orig")


class TestDiscovery(unittest.TestCase):
    def test_22_to_25_discovery_dedup_review_only_and_never_touches_configs(self):
        catalog = make_stations(2)
        with tempfile.TemporaryDirectory() as tmp:
            disc_path = os.path.join(tmp, "discovery-candidates.json")

            def fake_search(query, **kw):
                return radio_providers.SearchPageResult(
                    items=[
                        radio_providers.SearchCandidate("newDiscVid1", "UCnewChannel111111111111", "New Radio", "24/7 Lofi"),
                        radio_providers.SearchCandidate("newDiscVid1", "UCnewChannel111111111111", "New Radio", "24/7 Lofi"),
                        radio_providers.SearchCandidate("knownVid001", "UC0000000000000000000000", "Known", "Known"),
                    ],
                    ok=True,
                )

            def fake_verify(vid, allowed_channel_ids=None, **kw):
                if vid == "newDiscVid1":
                    return radio_verifier.VerificationResult("LIVE", "OK", video_id=vid, channel_id="UCnewChannel111111111111", channel_title="New Radio", title="24/7 Lofi")
                return radio_verifier.VerificationResult("LIVE", "OK", video_id=vid, channel_id="UC0000000000000000000000")

            rc = radio_discovery.run_discovery(
                dry_run=False,
                registry_path=disc_path,
                queries=[{"category": "lofi", "query": "lofi live"}],
                catalog=catalog,
                sleep_fn=lambda _: None,
                search_fn=fake_search,
                verify_fn=fake_verify,
                atomic_write_fn=sync_radios.atomic_write_json,
            )
            self.assertEqual(rc, 0)
            with open(disc_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(len(saved["candidates"]), 1)
            self.assertEqual(saved["candidates"][0]["videoId"], "newDiscVid1")
            self.assertEqual(saved["candidates"][0]["reviewStatus"], "PENDING_REVIEW")

    def test_34_verify_video_is_live_force_refresh_bypasses_cache(self):
        vid = "liveVid0001"
        allowed = ["UC0000000000000000000000"]
        live_payload = json.dumps({
            "playabilityStatus": {"status": "OK"},
            "videoDetails": {
                "videoId": vid,
                "channelId": allowed[0],
                "author": "Test Channel",
                "title": "Live Radio",
                "isLive": True,
                "isLiveContent": True,
            },
            "microformat": {
                "playerMicroformatRenderer": {
                    "liveBroadcastDetails": {
                        "isLiveNow": True,
                        "startTimestamp": "2026-11-13T00:00:00+00:00",
                    }
                }
            },
        }).encode("utf-8")
        ended_payload = json.dumps({
            "playabilityStatus": {"status": "OK"},
            "videoDetails": {
                "videoId": vid,
                "channelId": allowed[0],
                "author": "Test Channel",
                "title": "Ended Radio",
                "isLive": False,
                "isLiveContent": True,
            },
            "microformat": {
                "playerMicroformatRenderer": {
                    "liveBroadcastDetails": {
                        "isLiveNow": False,
                        "endTimestamp": "2026-11-13T01:00:00+00:00",
                    }
                }
            },
        }).encode("utf-8")

        class FakeResp:
            def __init__(self, body, status=200, url="https://www.youtube.com"):
                self._body = body
                self.status = status
                self._url = url
            def read(self, n=-1):
                return self._body
            def geturl(self):
                return self._url
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        state = {"player_calls": 0, "payload": live_payload}

        def fake_opener(req, timeout=8):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "/youtubei/v1/player" in url:
                state["player_calls"] += 1
                return FakeResp(state["payload"], url=url)
            if "oembed" in url:
                return FakeResp(b'{"title":"Live"}', url=url)
            if "i.ytimg.com" in url:
                return FakeResp(b"JPEG", url=url)
            raise AssertionError(f"Unexpected URL: {url}")

        cache = {}
        r1 = radio_verifier.verify_video_is_live(
            vid, allowed_channel_ids=allowed, cache=cache, opener=fake_opener, sleep_fn=lambda _: None
        )
        self.assertEqual(r1.status, "LIVE")
        self.assertEqual(state["player_calls"], 1)

        # Cached call with force_refresh=False should NOT re-hit opener
        state["payload"] = ended_payload
        r2 = radio_verifier.verify_video_is_live(
            vid, allowed_channel_ids=allowed, cache=cache, force_refresh=False, opener=fake_opener, sleep_fn=lambda _: None
        )
        self.assertEqual(r2.status, "LIVE")
        self.assertEqual(state["player_calls"], 1)

        # force_refresh=True must bypass cache and see the ended payload
        r3 = radio_verifier.verify_video_is_live(
            vid, allowed_channel_ids=allowed, cache=cache, force_refresh=True, opener=fake_opener, sleep_fn=lambda _: None
        )
        self.assertEqual(r3.status, "OFFLINE")
        self.assertEqual(state["player_calls"], 2)

    def test_35_request_with_retry_bounds_attempts_and_deadline(self):
        attempts = {"n": 0}
        sleeps = []

        def failing_opener(req, timeout=8):
            attempts["n"] += 1
            raise TimeoutError("timed out")

        req = radio_providers.urllib.request.Request("https://example.com")
        ok, code, url, body, err_kind, err_msg = radio_providers.request_with_retry(
            req,
            max_attempts=3,
            opener=failing_opener,
            sleep_fn=sleeps.append,
            rand_fn=lambda a, b: 0.1,
        )
        self.assertFalse(ok)
        self.assertEqual(err_kind, "TRANSIENT")
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(len(sleeps), 2)

        # Deadline already exceeded stops before any network call
        attempts["n"] = 0
        ok2, _, _, _, err_kind2, err_msg2 = radio_providers.request_with_retry(
            req,
            max_attempts=5,
            opener=failing_opener,
            clock_fn=lambda: 100.0,
            deadline_ts=100.0,
        )
        self.assertFalse(ok2)
        self.assertEqual(err_kind2, "TRANSIENT")
        self.assertIn("budget", err_msg2.lower())
        self.assertEqual(attempts["n"], 0)

    def test_36_recheck_transient_on_new_candidate_not_published(self):
        stations = make_stations(8)
        initial_cfg = []
        with tempfile.TemporaryDirectory() as tmp:
            cfg_paths = [os.path.join(tmp, "remote-config.json"), os.path.join(tmp, "remote-config-dev.json")]
            for p in cfg_paths:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"radioStations": initial_cfg}, f)
            reg_path = os.path.join(tmp, "radio-status.json")

            def fake_verify(vid, allowed_channel_ids=None, force_refresh=False, **kw):
                if force_refresh and vid == "newVid00000":
                    return radio_verifier.VerificationResult("TRANSIENT", "Network blip on recheck", category="network_error", video_id=vid)
                return radio_verifier.VerificationResult("LIVE", "OK", video_id=vid, channel_id=(allowed_channel_ids or [""])[0])

            rc = sync_radios.run_sync(
                config_files=cfg_paths,
                status_registry_path=reg_path,
                stations=stations,
                sleep_fn=lambda _: None,
                find_fn=lambda item, used, **kw: (True, f"newVid{int(item.id.split('-')[1]):05d}", None, "OK"),
                verify_fn=fake_verify,
            )
            self.assertEqual(rc, 0)
            with open(cfg_paths[0], "r", encoding="utf-8") as f:
                published_ids = [s["id"] for s in json.load(f)["radioStations"]]
            self.assertNotIn("station-0", published_ids)
            self.assertEqual(len(published_ids), 7)

    def test_37_discovery_never_modifies_remote_configs(self):
        catalog = make_stations(2)
        with tempfile.TemporaryDirectory() as tmp:
            disc_path = os.path.join(tmp, "discovery-candidates.json")
            rc_main = os.path.join(tmp, "remote-config.json")
            rc_dev = os.path.join(tmp, "remote-config-dev.json")
            sentinel = '{"radioStations": [{"id": "keep", "videoId": "oldVid00000"}]}\n'
            for p in (rc_main, rc_dev):
                with open(p, "w", encoding="utf-8") as f:
                    f.write(sentinel)

            rc = radio_discovery.run_discovery(
                dry_run=False,
                registry_path=disc_path,
                queries=[{"category": "lofi", "query": "lofi live"}],
                catalog=catalog,
                sleep_fn=lambda _: None,
                search_fn=lambda q, **kw: radio_providers.SearchPageResult(
                    items=[radio_providers.SearchCandidate("newDiscVid2", "UCnewChannel222222222222", "New", "Live")],
                    ok=True,
                ),
                verify_fn=lambda vid, **kw: radio_verifier.VerificationResult("LIVE", "OK", video_id=vid, channel_id="UCnewChannel222222222222"),
                atomic_write_fn=sync_radios.atomic_write_json,
            )
            self.assertEqual(rc, 0)
            for p in (rc_main, rc_dev):
                with open(p, "r", encoding="utf-8") as f:
                    self.assertEqual(f.read(), sentinel)

    def test_38_provider_degraded_bot_check_preserves_existing_configs(self):
        stations = make_stations(4)
        initial_cfg = [
            {
                "id": st.id,
                "title": st.title,
                "subtitle": st.subtitle,
                "thumbnail": f"https://i.ytimg.com/vi/oldVid{i:05d}/hqdefault.jpg",
                "videoId": f"oldVid{i:05d}",
            }
            for i, st in enumerate(stations)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            rc_main = os.path.join(tmp, "remote-config.json")
            rc_dev = os.path.join(tmp, "remote-config-dev.json")
            reg_path = os.path.join(tmp, "radio-status.json")
            for p in (rc_main, rc_dev):
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"radioStations": initial_cfg}, f)

            # Expired grace (>6h) + all stations hitting YouTube bot_check
            expired_iso = "2020-01-01T00:00:00Z"
            with open(reg_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "stations": {
                            st.id: {
                                "verificationVersion": 2,
                                "lastVerified": expired_iso,
                                "lastGoodVideoId": f"oldVid{i:05d}",
                                "verifiedChannelId": st.allowed_channel_ids[0],
                            }
                            for i, st in enumerate(stations)
                        }
                    },
                    f,
                )

            rc = sync_radios.run_sync(
                config_files=[rc_main, rc_dev],
                status_registry_path=reg_path,
                stations=stations,
                sleep_fn=lambda _: None,
                find_fn=lambda item, used, **kw: (False, None, None, "bot_check"),
                verify_fn=lambda vid, **kw: radio_verifier.VerificationResult(
                    "TRANSIENT", "LOGIN_REQUIRED bot/auth gate", category="bot_check", video_id=vid
                ),
            )
            self.assertEqual(rc, 1)
            for p in (rc_main, rc_dev):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.assertEqual(len(data["radioStations"]), 4)
                self.assertEqual(data["radioStations"][0]["videoId"], "oldVid00000")
            with open(reg_path, "r", encoding="utf-8") as f:
                reg = json.load(f)
            self.assertTrue(reg.get("providerDegraded"))


if __name__ == "__main__":
    unittest.main()
