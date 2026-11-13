# HiFi Remote Config & Assets (`HiFi-files`)

Centralized remote configuration, dynamic player auto-heal client fallbacks, live radio feeds, and assets for **[HiFi Music](https://github.com/saferill/HiFi)**.

---

## Architecture & Flow Design Reference

The radio synchronization pipeline is organized into focused standard-library modules under `scripts/`:

- **`scripts/radio_catalog.py` (Station Catalog)**: Defines `StationSpec` (`id`, `title`, `subtitle`, `category`, `enabled`, `handle`, `allowed_channel_ids`, `backup_handles`, `preferred_vid`, `default_vid`) and `DISCOVERY_QUERIES`.
- **`scripts/radio_providers.py` (Search & Resolution Providers)**: Bounded HTTP retries with `Retry-After`, exponential backoff + jitter, Innertube `navigation/resolve_url`, `/@handle/live` + `/streams` HTML, `yt-dlp`, optional `YOUTUBE_API_KEY` Data API v3 search, and structured Innertube LIVE search with continuation pagination.
  - **Design Reference**: Search parameter encoding, bounded container-only feed parsing, continuation pagination, and separation of empty results vs failed requests are adapted from **[Flow-Tube/Flow](https://github.com/Flow-Tube/Flow)** (GPL-3.0, commit `4545547fbe20bcc4411c399ebdff5e6a6dd72a43`):
    - `app/src/main/java/io/github/aedev/flow/innertube/YouTubeSearchParams.kt`
    - `app/src/main/java/io/github/aedev/flow/innertube/pages/search/SearchPageParser.kt`
    - `app/src/main/java/io/github/aedev/flow/data/repository/YouTubeRepository.kt`
    - `app/src/test/java/io/github/aedev/flow/data/repository/SearchVideosLiveProbeTest.kt`
- **`scripts/radio_verifier.py` (Live Verifier)**: Evaluates target `videoId` via Innertube `/youtubei/v1/player` JSON and `json.JSONDecoder.raw_decode`-parsed `ytInitialPlayerResponse` from watch HTML. Never uses `/next` recommendations or global page text as live proof.
- **`scripts/radio_policy.py` (Publication Policy)**: Enforces the **6-hour balanced fallback** measured strictly from `lastVerified` (`MODE SEIMBANG`), identity/version checks on telemetry, and widespread bot-check flagging (`providerDegraded`).
- **`scripts/radio_discovery.py` (Broad Discovery)**: Runs separately (`--mode discovery`) with bounded queries, pages, and candidates, queuing new channels in `registry/discovery-candidates.json` (`reviewStatus: "PENDING_REVIEW"`) without modifying production configs.
- **`scripts/sync-radios.py` (Publisher & Telemetry)**: Validates candidate configs in memory (preserving `promos` and all non-radio fields), writes per-file atomically with rollback on partial write failure, and records telemetry in `registry/radio-status.json`.

---

## Status Definitions & 6-Hour Balanced Fallback Policy

- **`LIVE`**: Target `videoId` matches `^[A-Za-z0-9_-]{11}$`, metadata `videoId` matches target, `channelId` is in `allowedChannelIds`, `playabilityStatus.status == "OK"`, `videoDetails.isLive == True` (not merely `isLiveContent`), no `isUpcoming`/`endTimestamp`/`isLiveNow == False`, and oEmbed/thumbnail checks succeed, followed by a fresh pre-publish `force_refresh=True` re-check.
- **`OFFLINE`**: Definitive evidence that the stream has ended, is upcoming/premiere, is a regular video or past-live VOD, belongs to a channel outside `allowedChannelIds`, or is permanently unavailable/restricted. Excluded immediately.
- **`TRANSIENT`**: Network timeout, DNS/socket error, HTTP `429`/`5xx`, consent/CAPTCHA gate, `LOGIN_REQUIRED` bot-check, `"The page needs to be reloaded."`, missing/ambiguous player data, or transient oEmbed/thumbnail failure.
- **`DISABLED`**: Station disabled (`enabled=False`) in `STATION_CATALOG`.

### Fallback Rules
- **6-Hour Grace Limit**: Previously verified streams (`verificationVersion >= 2`, `verifiedChannelId` in `allowedChannelIds`, valid `lastVerified` UTC timestamp) encountering `TRANSIENT` errors may be retained as `TRANSIENT_FALLBACK` for up to **6 hours from `lastVerified`**.
- Repeated failures **never** reset or extend `lastVerified` or the 6-hour limit.
- Unverified `defaultVid` seeds, corrupted/naive/future timestamps, and historical v1 records without verified channel identity receive **no** fallback.
- **0–7 Stations / Empty List**: Schema-valid outputs with `< 8` stations emit an operational warning and are still published cleanly (dead streams are never revived to satisfy a minimum count). In the HiFi app (`composeApp`), `SmartGreetingAndRadio.kt` (`parseRemoteRadioStations`) returns `emptyList()` for `"[]"` and `HomeScreen.kt` (`radioStations.isNotEmpty()`) skips rendering the radio carousel without crashing.

> **Limitations**: Playability (`"OK"`) reflects the check context (runner IP, client, and region) at check time. It does **not** guarantee unlimited YouTube access, immunity from bot-checks, uninterrupted live status until the next run, or playback across all devices/countries.

---

## Trusted Station Updates vs Broad Discovery

- **Existing Station Updates (`--mode health-check`)**: Only accepts streams from `allowedChannelIds` (`UC...`). Cross-brand `backupHandles` were removed. To add a station, append a `StationSpec` with verified `UC...` IDs in `scripts/radio_catalog.py`.
- **Broad Discovery (`--mode discovery`)**: Searches `DISCOVERY_QUERIES` and writes new channel candidates to `registry/discovery-candidates.json` for manual review. `LIVE` status does not guarantee genre accuracy, safety, or official status.

---

## Local Commands (Tests, Validators, Dry-Run, Live Probe)

```bash
# Run offline unit tests
python -m unittest discover -s tests -p "test_*.py" -v

# Validate remote-config.json and remote-config-dev.json (offline)
python scripts/validate-config.py

# Validate registry/player_configs.json (offline)
python scripts/validate-player-config.py

# Dry-run health check without modifying any files
python scripts/sync-radios.py --mode health-check --dry-run

# Dry-run broad discovery without modifying any files
python scripts/sync-radios.py --mode discovery --dry-run

# Opt-in live network probe (not run in offline CI)
HIFI_LIVE_PROBE=1 python -m unittest tests/test_live_probe.py -v
```

---

## Automation Workflows & GitHub Permissions

| Workflow | Schedule (Best-Effort) | Permissions | Output Files |
| :--- | :--- | :--- | :--- |
| `sync-radios.yml` | Every 1 hour (`0 * * * *` UTC) | `contents: write` | `remote-config.json`, `remote-config-dev.json`, `registry/radio-status.json` |
| `discover-radios.yml` | Twice daily (`0 3,15 * * *` UTC) | `contents: write` | `registry/discovery-candidates.json` |
| `update-player-configs.yml` | Every 2 hours (`0 */2 * * *` UTC) | `contents: write` | `registry/player_configs.json` |
| `ci.yml` | `pull_request` to `main` | `contents: read` | None (read-only tests & validators) |

- All writer workflows share concurrency group `hifi-files-auto-publish` (`cancel-in-progress: false`), explicitly check out `main` with `fetch-depth: 0`, rebase safely before pushing (never force-pushing), and re-validate after rebase.
- GitHub Actions cron schedules are best-effort, not real-time. If hourly runs increase rate limiting, reduce frequency in `.github/workflows/sync-radios.yml`.
- **Branch Protection**: Direct push requires `GITHUB_TOKEN` `contents: write` and permission for Actions to push to `main`. If branch protection blocks direct pushes, switch the commit/push step to open a Pull Request instead.

---

## License

Open-source under the [GPL-3.0 License](https://www.gnu.org/licenses/gpl-3.0.html).
