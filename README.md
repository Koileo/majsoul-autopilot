# Majsoul Autopilot

> **Disclaimer**: This project is for educational and research purposes only. The author is not responsible for any consequences such as account bans resulting from the use of this tool. **Not recommended for ranked play.**

A full-auto Majsoul mahjong player powered by [Mortal](https://github.com/Equim-chan/Mortal) AI. Automatically logs in, queues for matches, plays games, and repeats — completely unattended.

Based on [Akagi](https://github.com/shinkuan/Akagi) by [shinkuan](https://github.com/shinkuan).

## Features

- **Full automation**: login → match → play → repeat
- **Mortal AI**: Uses pre-trained Mortal model (currently 4-player south only)
- **WebUI dashboard**: Real-time game state viewer at `http://localhost:3002`
- **Periodic restart**: Soft-restarts browser every N games for stability
- **Error recovery**: Auto-recovers from stuck states and connection issues

## Requirements

- Python >= 3.12
- Node.js (for building WebUI, optional if using pre-built assets)
- Mortal model weights (`mortal.pth`)

## Installation

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd majsoul-autopilot

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium
```

### 2. Configure settings

```bash
cp settings/settings.json.example settings/settings.json
```

Edit `settings/settings.json`:

```json
{
  "autoplay_account": {
    "username": "your_email@example.com",
    "password": "your_password"
  },
  "autoplay_mode": {
    "type": "4p_south",
    "room": "gold"
  }
}
```

Key settings:

| Field | Description | Options |
|-------|-------------|---------|
| `autoplay_mode.type` | Game type | Currently only `4p_south` |
| `autoplay_mode.room` | Room tier | `bronze`, `silver`, `gold`, `jade`, `throne` |
| `autoplay_headless` | Hide browser window | `true` / `false` |
| `autoplay_time` | Action delays (seconds) | Timing config for human-like play |
| `webui_port` | WebUI server port | Default: `3002` |
| `model_path` | Mortal model file | Absolute path or relative to project root |

### 3. Place model weights

Download the Mortal model file, place it at `mjai_bot/mortal/mortal.pth` (default), and set `model_path` in `settings.json` accordingly.

Model download: [VoidShine/mortal-298k](https://huggingface.co/VoidShine/mortal-298k)

### 4. Build WebUI (optional)

Pre-built assets are included. To rebuild after modifying the frontend:

```bash
cd webui
npm install
npm run build
```

## Usage

```bash
python run_autoplay.py
```

The program will:
1. Start the MITM proxy (intercepts game protocol)
2. Launch the WebUI at `http://localhost:3002`
3. Open a Chromium browser and navigate to Majsoul
4. Log in with your configured account
5. Start queuing for matches and playing automatically

Open `http://localhost:3002` to view real-time game state. Press `Ctrl+C` to stop.

To run WebUI only (no autoplay):

```bash
python run_webui.py
```

## Architecture

```
Browser (Playwright/Chromium)
    ↕ WebSocket (via MITM proxy)
MITM Proxy (mitmproxy)
    ↕ Liqi Protocol → MJAI format
Game Loop Thread
    ↕ MJAI messages
Mortal AI Model
    ↕ Action recommendation
Browser Automation (JS injection)
    → Execute action in game
```

## Project Structure

```
majsoul-autopilot/
├── run_autoplay.py          # Main entry point
├── settings/                # Configuration
├── mjai_bot/                # AI models & inference
│   ├── mortal/              # 4-player model + libriichi
│   └── mortal3p/            # 3-player model + libriichi
├── mitm/                    # MITM proxy & protocol bridges
├── autoplay/                # Browser automation & action execution
├── akagi/                   # Game state processing & WebUI server
└── webui/                   # React frontend source
```

## Acknowledgements

This project is built upon [Akagi](https://github.com/shinkuan/Akagi) by [shinkuan](https://github.com/shinkuan), which provides the MITM proxy, protocol bridges, and Mortal bot integration. Majsoul Autopilot replaces Akagi's Textual TUI with Playwright-based browser automation and a React WebUI dashboard.

## License

This project is licensed under the **GNU Affero General Public License v3 with Commons Clause**, the same license as the original Akagi project. See [LICENSE.txt](LICENSE.txt) for details.

This project is for educational and research purposes only.
