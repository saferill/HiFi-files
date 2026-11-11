# HiFi Remote Config & Assets (`HiFi-files`)

Centralized remote configuration, dynamic player auto-heal client fallbacks, live radio feeds, and precompiled offline language dictionaries for **[HiFi Music](https://github.com/saferill/HiFi)**.

This repository decouples runtime app behavior from APK release cycles. Changes pushed to `remote-config.json` take effect immediately on user devices without requiring an application update or rebuild.

---

## Repository Structure

```
.
├── .github/workflows/
│   ├── sync-radios.yml               # Daily health-check & auto-heal for 24/7 radio streams
│   └── update-player-configs.yml     # 6-hour sync for YouTube player client fallbacks
├── scripts/
│   └── sync-radios.py                # Channel /live resolver script
├── remote-config.json                # Production configuration read by release builds
├── remote-config-dev.json            # Staging configuration for debug/canary builds
└── README.md
```

---

## Remote Configuration Schema

### `remote-config.json`

```json
{
  "tidalClientId": "...",
  "tidalClientSecret": "...",
  "playerClients": [
    "WEB_REMIX",
    "TVHTML5",
    "IOS",
    "ANDROID"
  ],
  "greeting": {
    "dates": {}
  },
  "radioStations": [
    {
      "id": "lofi-girl",
      "title": "Lofi Hip Hop Radio",
      "subtitle": "Lofi Girl • LIVE",
      "thumbnail": "https://i.ytimg.com/vi/4t4AQ15yj1M/hqdefault.jpg",
      "videoId": "4t4AQ15yj1M"
    }
  ],
  "promos": []
}
```

### Key Components

* **`playerClients`**: Ordered fallback list used by HiFi's stream resolver. When YouTube restricts or changes signature cipher algorithms on one client (e.g., `WEB_REMIX`), the app automatically cascades down to `TVHTML5`, `IOS`, or `ANDROID` without app crashes.
* **`radioStations`**: Curated 24/7 live audio streams displayed in the home carousel. Video IDs are kept current by the auto-heal workflow.
* **`greeting`**: Dynamic holiday and national date overrides displayed on the Home screen header.
* **Release Assets (`tag: abc`)**: Contains `kuromoji-ipadic-0.9.0-dict.tar.gz`, downloaded on demand by Android clients to provide offline Japanese Romaji phonetic transcriptions for lyrics.

---

## Automation Workflows

| Workflow | Frequency | Responsibility |
| :--- | :--- | :--- |
| `sync-radios.yml` | Daily (`03:00 UTC`) | Resolves YouTube channel `/live` endpoints, detects restarted streams, and commits new video IDs. |
| `update-player-configs.yml` | Every 6 hours | Validates player client availability and keeps stream extraction reliable. |

---

## License

This project is open-source under the [GPL-3.0 License](https://www.gnu.org/licenses/gpl-3.0.html).
