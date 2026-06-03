[中文文档](README_ZH.md)

# Majsoul Autopilot

> **Disclaimer**: This project is for educational and research purposes only. The author is not responsible for any consequences, including account bans, resulting from the use of this tool.

A full-auto Majsoul mahjong player powered by [Mortal](https://github.com/Equim-chan/Mortal). It logs in, selects the correct ranked room, queues, plays, recovers from reconnects, and repeats unattended.

Based on [Akagi](https://github.com/shinkuan/Akagi) by [shinkuan](https://github.com/shinkuan).

## Current Architecture

Majsoul's current web client is Unity WebGL. The old browser-side Laya globals (`uiscript`, `app.NetAgent`, `GameMgr`) are no longer available, so the default autopilot no longer drives the game with browser JS, screenshots, or coordinate clicks.

The current path is protocol-first:

```
run_autoplay.py
    -> Liqi protocol client (login, route discovery, match queue, game reconnect)
    -> Liqi game socket (FastTest auth/sync/inputOperation)
    -> Majsoul bridge (Liqi broadcasts -> MJAI events)
    -> Mortal model (MJAI decision)
    -> Liqi operation RPCs

Jsonl logs -> WebUI watcher -> React WebUI dashboard
MITM proxy -> protocol/state bridge and compatibility stream
```

The legacy Playwright browser automation code is still in the tree for reference, but the default runner uses `autoplay/protocol_automation.py`.

## Features

- **Pure protocol autoplay**: password login, route discovery, match queue, game auth/sync, and action execution through Liqi WebSocket RPCs.
- **Mortal AI decisions**: game broadcasts are translated to MJAI and sent to the Mortal model.
- **Correct riichi handling**: when Mortal chooses riichi, the runner feeds the `reach` event back to the model before selecting the declaration discard.
- **Rank-aware room switching**: below 雀士 plays bronze 4-player east; 雀士 plays silver 4-player south; 雀杰 and above plays gold 4-player south.
- **Recovery logic**: reconnects existing games, avoids duplicate queueing when the account is busy, handles stale queue/low-coin `1304` by claiming revive coins when available, and recovers from failed action windows.
- **WebUI dashboard**: real-time game state and inference viewer at `http://localhost:3002`.

## Requirements

- Python >= 3.12
- Node.js, only if rebuilding the WebUI
- Mortal model weights, for example [`VoidShine/mortal-298k`](https://huggingface.co/VoidShine/mortal-298k)

## Installation

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd majsoul-autopilot

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

`playwright install chromium` is not needed for the default protocol runner. Install it only if you are intentionally experimenting with the legacy browser automation path.

### 2. Configure settings

```bash
cp settings/settings.json.example settings/settings.json
```

Edit `settings/settings.json`:

```json
{
  "model_path": "mjai_bot/mortal/mortal.pth",
  "autoplay_account": {
    "username": "your_email@example.com",
    "password": "your_password"
  },
  "autoplay_mode": {
    "type": "4p_south",
    "room": "silver"
  },
  "webui_port": 3002
}
```

Important settings:

| Field | Description | Notes |
|-------|-------------|-------|
| `autoplay_account.username` | Majsoul email/account login | Stored only in local `settings/settings.json`, which is gitignored |
| `autoplay_account.password` | Majsoul password | Hashed client-side before the login RPC |
| `model_path` | Mortal model file | Absolute path or relative to project root |
| `autoplay_mode.type` / `room` | Initial mode hint | The protocol runner refreshes the 4-player rank after login and may override this automatically |
| `webui_port` | WebUI server port | Default: `3002` |
| `mitm.host` / `mitm.port` | Local MITM compatibility bridge | Default: `127.0.0.1:7880` |

Automatic ranked target rules:

| 4-player rank | Target mode |
|---------------|-------------|
| Below 雀士 | `4p_east` / `bronze` |
| 雀士 | `4p_south` / `silver` |
| 雀杰 and above | `4p_south` / `gold` |

### 3. Place model weights

Download the Mortal model file and place it at `mjai_bot/mortal/mortal.pth`, or update `model_path`.

Model download: [VoidShine/mortal-298k](https://huggingface.co/VoidShine/mortal-298k)

### 4. Build WebUI assets

Pre-built assets are included. Rebuild only after modifying the frontend:

```bash
cd webui
npm install
npm run build
```

## Usage

```bash
python run_autoplay.py
```

The runner will:

1. Start the WebUI at `http://localhost:3002`.
2. Start the local MITM compatibility bridge.
3. Log in to Majsoul through the Liqi protocol.
4. Refresh the account's 4-player rank and choose the target room.
5. Queue through `startUnifiedMatch`.
6. Connect to the game socket, sync the round, send MJAI events to Mortal, and execute actions through Liqi RPCs.
7. Reconnect or recover when the server reports an active game, a failed operation, a stale queue, or a temporary cooldown.

Open `http://localhost:3002` to view real-time game state. Press `Ctrl+C` once to request shutdown; press it again to force exit.

To run WebUI only:

```bash
python run_webui.py
```

## Operational Notes

- Do not log into the same Majsoul account in a browser while the runner is playing. If the account is taken over elsewhere, the runner will try to reconnect, but it may temporarily stop acting.
- A `1304` response from `startUnifiedMatch` can mean the account needs revive coins, not only a stale queue. The runner checks `fetchReviveCoinInfo`, calls `gainReviveCoin` when available, and retries queueing.
- Route selection is fetched from Majsoul route/config endpoints when available and falls back to known route domains.
- The WebUI watches JSONL streams and resets cached state when logs are rewritten or truncated.

## Project Structure

```
majsoul-autopilot/
├── run_autoplay.py                 # Main protocol autoplay loop
├── settings/                       # Local configuration schema/example
├── autoplay/
│   ├── protocol_automation.py      # Liqi login, match, reconnect, and operation RPCs
│   └── majsoul_automation.py       # Legacy Playwright path kept for reference
├── mjai_bot/                       # Mortal model integration
├── mitm/                           # Liqi parser, bridge, and compatibility MITM stream
├── akagi/                          # Game state processing and WebUI server
├── webui/                          # React frontend source
└── tests/                          # Protocol recovery and WebUI watcher tests
```

## Tests

Run the focused regression tests:

```bash
python -m unittest tests.test_protocol_recovery tests.test_webui_watcher
```

## Acknowledgements

This project is built upon [Akagi](https://github.com/shinkuan/Akagi) by [shinkuan](https://github.com/shinkuan), which provides the original protocol bridge and Mortal bot integration. Majsoul Autopilot now uses a protocol-first runner plus a React WebUI dashboard.

## License

This project is licensed under the **GNU Affero General Public License v3 with Commons Clause**, the same license as the original Akagi project. See [LICENSE.txt](LICENSE.txt) for details.

This project is for educational and research purposes only.
