# majsoul-autopilot

[中文文档](README.zh-CN.md)

A pure Rust Mahjong Soul autopilot powered by the Mortal model and the Liqi protocol.

The project provides a desktop GUI and a command-line tool. It logs in with an email account, joins ranked four-player rooms, connects to live games through the Liqi websocket protocol, and lets a Mortal model choose actions.

## Features

- Pure protocol automation for Mahjong Soul
- Four-player ranked matchmaking
- Automatic room selection by rank
- Native Mortal inference with Candle
- Tauri desktop GUI for settings, status, logs, and table view
- Reconnect support for active games
- Riichi declaration handling with Mortal's two-step decision flow
- Stale operation guard and discard acknowledgement checks
- No browser automation, screenshots, or coordinate clicking

## Room Policy

The runner selects a target room from the account rank:

| Rank | Mode |
| --- | --- |
| Below Adept | Bronze Room, East game |
| Adept | Silver Room, South game |
| Expert or higher | Gold Room, South game |

Three-player mode is not supported.

## Download

Prebuilt macOS Apple Silicon packages are available from GitHub Releases:

[Download the latest release](https://github.com/happy-shine/majsoul-autopilot/releases/latest)

Two macOS arm64 packages are published:

- `majsoul-autopilot-gui-macos-arm64.zip`: desktop app plus the CLI binary
- `majsoul-autopilot-rs-macos-arm64.zip`: CLI-only package with the original layout

The GUI package contains:

```text
majsoul-autopilot-gui-macos-arm64/
  Majsoul Autopilot.app
  majsoul-autopilot-rs
  settings.example.json
  README.md
  README.zh-CN.md
  models/
    mortal-298k/
      model.safetensors
      model_config.json
```

The CLI package contains:

```text
majsoul-autopilot-rs-macos-arm64/
  majsoul-autopilot-rs
  settings.example.json
  README.md
  README.zh-CN.md
  models/
    mortal-298k/
      model.safetensors
      model_config.json
```

## Quick Start

For the desktop app, unzip `majsoul-autopilot-gui-macos-arm64.zip` and open `Majsoul Autopilot.app`.

For the CLI package, unzip `majsoul-autopilot-rs-macos-arm64.zip` and enter the extracted directory:

```bash
cd majsoul-autopilot-rs-macos-arm64
cp settings.example.json settings.json
```

Edit `settings.json`:

```json
{
  "model_path": "models/mortal-298k",
  "autoplay_account": {
    "username": "your-email@example.com",
    "password": "your-password"
  }
}
```

Check the model:

```bash
./majsoul-autopilot-rs --settings settings.json check-model
```

When running from source instead of a release package, prepare the model first:

```bash
mkdir -p models
curl -L -o models/mortal_298k.pth \
  https://huggingface.co/VoidShine/mortal-298k/resolve/main/mortal_298k.pth
python3 tools/export_mortal.py models/mortal_298k.pth models/mortal-298k
```

Check login and target room:

```bash
./majsoul-autopilot-rs --settings settings.json check-login
```

Run one game:

```bash
./majsoul-autopilot-rs --settings settings.json run --max-games 1
```

Run continuously:

```bash
./majsoul-autopilot-rs --settings settings.json run
```

Stop the runner with `Ctrl-C`.

## Configuration

`settings.json` is the only required runtime configuration file.

```json
{
  "model_path": "models/mortal-298k",
  "autoplay_account": {
    "username": "",
    "password": ""
  }
}
```

Fields:

| Field | Description |
| --- | --- |
| `model_path` | Directory containing `model.safetensors` and `model_config.json` |
| `autoplay_account.username` | Mahjong Soul email account |
| `autoplay_account.password` | Mahjong Soul password |

`settings.json` is ignored by git because it contains credentials.

## Commands

```bash
majsoul-autopilot-rs --settings settings.json check-model
majsoul-autopilot-rs --settings settings.json check-login
majsoul-autopilot-rs --settings settings.json run
majsoul-autopilot-rs --settings settings.json run --max-games 1
majsoul-autopilot-rs --settings settings.json replay-fixture path/to/fixture.json
```

## Build From Source

Install Rust, then build the CLI:

```bash
cargo build --release -p majsoul-autopilot-rs
```

The binary is generated at:

```text
target/release/majsoul-autopilot-rs
```

Build the desktop app:

```bash
npm --prefix apps/desktop install
npm --prefix apps/desktop run tauri -- build
```

Create macOS release archives:

```bash
tools/package_macos_release.sh
```

For local runs from source, prepare:

```text
settings.json
models/mortal-298k/model.safetensors
models/mortal-298k/model_config.json
```

Model weights and local credentials are not committed to the repository.

## Development

Run tests:

```bash
cargo test --workspace -- --nocapture
```

Run clippy:

```bash
cargo clippy --workspace --all-targets -- -D warnings
```

## Project Layout

```text
crates/
  autoplay/      action planning and operation guards
  cli/           command-line entry point
  liqi/          protobuf types and Liqi framing
  mjai/          Liqi-to-MJAI event bridge
  mortal/        Mortal inference and action decoding
  protocol/      lobby/game websocket client
  riichi-core/   riichi state and observation encoding
apps/
  desktop/       Tauri desktop app
```

## Disclaimer

This project is for research and experimentation. Use it at your own risk and review the rules of any service you connect to.

## License

GPL-3.0-or-later. See [LICENSE.txt](LICENSE.txt).
