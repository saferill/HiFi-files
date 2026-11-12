# HiFi Remote Config & Assets (`HiFi-files`)

Centralized remote configuration, dynamic player auto-heal client fallbacks, live radio feeds, and precompiled offline language dictionaries for **[HiFi Music](https://github.com/saferill/HiFi)**.

This repository decouples runtime app behavior from APK release cycles. Changes pushed to `remote-config.json` take effect immediately on user devices without requiring an application update or rebuild.

---

## Repository Structure

```
.
├── .github/workflows/
│   ├── sync-radios.yml               # 2-hour auto-heal sync for 24/7 live radio streams
│   └── update-player-configs.yml     # 6-hour sync for YouTube player client fallbacks
├── registry/
│   ├── radio-status.json             # Diagnostic telemetry & health tracking for radio stations
│   └── player_configs.json           # Cached player configurations and client mappings
├── scripts/
│   ├── sync-radios.py                # Live stream resolver with 5-stage verification & auto-pruning
│   └── validate-config.py            # Pre-commit JSON schema & station constraint validator
├── remote-config.json                # Production configuration read by release builds
├── remote-config-dev.json            # Staging configuration for debug/canary builds
└── README.md
```

---

## Live Radio Auto-Heal & Validation Rules

HiFi's live radio carousel displays active 24/7 audio streams. To prevent broken playback, static video replays, upcoming placeholders, or geo-restricted streams, `scripts/sync-radios.py` enforces a **strict 5-stage verification pipeline**:

1. **Canonical Video ID Pattern**:
   The candidate ID must strictly match `^[A-Za-z0-9_-]{11}$` and originate from the canonical link (`<link rel="canonical" href="...">`, `<meta property="og:url" ...>`, or direct redirect URL). Ambiguous generic `videoId` regex matches are rejected.
2. **Active Live Stream Verification**:
   The channel page must explicitly contain `"isLiveNow":true` and must **not** contain upcoming or offline markers (`"isUpcoming":true`, `LIVE_STREAM_OFFLINE`).
3. **Playability Status Check**:
   The internal YouTube player response (`playabilityStatus.status`) must equal `"OK"`. Any streams flagged as `LOGIN_REQUIRED`, `UNPLAYABLE`, `ERROR`, age-restricted, or region-blocked are rejected immediately.
4. **oEmbed Endpoint Verification**:
   `https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={ID}&format=json` must return HTTP `200`.
5. **HQ Thumbnail Availability**:
   `https://i.ytimg.com/vi/{ID}/hqdefault.jpg` must return HTTP `200`.

### Transient Bot-Check Detection & Resilience
If YouTube redirects to `consent.youtube.com`, displays bot-detection warnings, or returns HTML lacking player data structures, the failure is flagged as transient rather than an offline stream. The script retries twice with exponential backoff and injects random jitter between requests to avoid rate limits.

### Configuration Fallback & 72-Hour Auto-Pruning
* **Strict Replacement**: The stored `videoId` in `remote-config.json` is **only** replaced when a new stream ID passes all 5 verification stages.
* **Grace Period**: If verification fails for a previously working station, its existing `videoId` is retained as a fallback.
* **Auto-Pruning (> 72 Hours)**: If a station consistently fails verification for longer than 72 hours (`STALE_HOURS = 72`), it is automatically excluded from `radioStations` in `remote-config.json` to keep the user carousel clean.
* **Auto-Recovery**: As soon as the channel resumes an active, valid live stream, it is automatically re-admitted to the carousel.

### Diagnostic Telemetry (`registry/radio-status.json`)
All health tracking metadata—such as `lastVerified`, `lastGoodVideoId`, `lastStatus`, and `consecutiveFailures`—is persisted exclusively in `registry/radio-status.json`. This ensures `remote-config.json` remains strictly compliant with the client app's expected schema.

---

## How to Add New Radio Stations

To add a new live stream channel, append a dictionary entry to `STATION_CHANNELS` in `scripts/sync-radios.py`:

```python
{
    "id": "my-channel-id",                  # Unique lowercase slug
    "title": "Station Display Name",        # Carousel title in app
    "subtitle": "Genre or Description • LIVE", # Subtitle in app
    "handle": "@ChannelHandle",             # Primary YouTube channel handle
    "backupHandles": ["@BackupChannel"],    # Optional fallback channels if primary is offline
    "defaultVid": "xxxxxxxxxxx"             # Initial seed video ID (11 chars)
}
```

The next run of the sync script will resolve and verify the channel's live stream.

---

## Automation Workflows

| Workflow | Frequency | Responsibility |
| :--- | :--- | :--- |
| `sync-radios.yml` | Every 2 hours (`0 */2 * * *` UTC) | Resolves `/live` endpoints, validates streams, prunes stale stations, runs `validate-config.py`, and commits changes. Features concurrency control (`sync-radios`) and rebase pulls. |
| `update-player-configs.yml` | Every 6 hours | Validates player client availability and keeps stream extraction reliable. |

---

## Security & Credential Hygiene

> [!WARNING]
> Remote configuration files (`remote-config.json`, `remote-config-dev.json`) may reference client keys (such as `tidalClientId` and `tidalClientSecret`) read at runtime by the mobile app.

### Removing Secrets from Git History
If production secrets need to be scrubbed from git history after rotation, use [`git-filter-repo`](https://github.com/newren/git-filter-repo) or [BFG Repo-Cleaner](https://rtyley.github.io/bfg-repo-cleaner/):

```bash
# Using git-filter-repo to replace secret occurrences across all commits:
git filter-repo --replace-text <(echo 'OLD_SECRET==>REDACTED')

# Force push the sanitized history:
git push origin --force --all
git push origin --force --tags
```

---

## License

This project is open-source under the [GPL-3.0 License](https://www.gnu.org/licenses/gpl-3.0.html).
