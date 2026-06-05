# majsoul-autopilot

一个基于 Mortal 模型和 Liqi 协议的纯 Rust 雀魂自动打牌工具。

本项目同时提供桌面 GUI 和命令行程序。程序使用邮箱账号登录雀魂，通过 Liqi websocket 协议完成匹配、进局、重连和对局操作，并由 Mortal 模型决定打牌动作。

## 功能特性

- 纯协议自动化，不依赖浏览器
- 支持四人段位场匹配
- 按账号段位自动选择目标房间
- 使用 Candle 进行 Mortal 原生推理
- 提供 Tauri 桌面 GUI，用于配置、状态、日志和牌桌查看
- 支持已有对局重连
- 支持 Mortal 立直二段决策流程
- 包含 operation 过期保护和弃牌 ACK 校验
- 不使用截图识别、视觉定位或坐标点击

## 房间策略

程序会根据账号段位自动选择房间：

| 段位 | 模式 |
| --- | --- |
| 未到雀士 | 铜之间四人东 |
| 雀士 | 银之间四人南 |
| 雀杰及以上 | 金之间四人南 |

当前不支持三人麻将入口。

## 下载

macOS Apple Silicon 预编译包可以在 GitHub Releases 下载：

[下载最新版本](https://github.com/happy-shine/majsoul-autopilot/releases/latest)

当前提供两个 macOS arm64 包：

- `majsoul-autopilot-gui-macos-arm64.zip`：桌面 App，包内同时包含 CLI 可执行文件
- `majsoul-autopilot-rs-macos-arm64.zip`：CLI-only 包，保持原有目录结构

GUI 包内包含：

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

CLI 包内包含：

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

## 快速开始

桌面版解压 `majsoul-autopilot-gui-macos-arm64.zip` 后，打开 `Majsoul Autopilot.app`。

CLI 版解压 `majsoul-autopilot-rs-macos-arm64.zip` 后进入目录：

```bash
cd majsoul-autopilot-rs-macos-arm64
cp settings.example.json settings.json
```

编辑 `settings.json`：

```json
{
  "model_path": "models/mortal-298k",
  "autoplay_account": {
    "username": "your-email@example.com",
    "password": "your-password"
  }
}
```

检查模型文件：

```bash
./majsoul-autopilot-rs --settings settings.json check-model
```

如果是从源码运行，而不是使用 release 包，先准备模型：

```bash
mkdir -p models
curl -L -o models/mortal_298k.pth \
  https://huggingface.co/VoidShine/mortal-298k/resolve/main/mortal_298k.pth
python3 tools/export_mortal.py models/mortal_298k.pth models/mortal-298k
```

检查登录状态和目标房间：

```bash
./majsoul-autopilot-rs --settings settings.json check-login
```

只运行一局：

```bash
./majsoul-autopilot-rs --settings settings.json run --max-games 1
```

持续运行：

```bash
./majsoul-autopilot-rs --settings settings.json run
```

使用 `Ctrl-C` 停止程序。

## 配置

`settings.json` 是唯一必需的运行时配置文件。

```json
{
  "model_path": "models/mortal-298k",
  "autoplay_account": {
    "username": "",
    "password": ""
  }
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `model_path` | Mortal 模型目录，目录内需要 `model.safetensors` 和 `model_config.json` |
| `autoplay_account.username` | 雀魂邮箱账号 |
| `autoplay_account.password` | 雀魂密码 |

`settings.json` 包含账号信息，默认不会提交到 git。

## 命令

```bash
majsoul-autopilot-rs --settings settings.json check-model
majsoul-autopilot-rs --settings settings.json check-login
majsoul-autopilot-rs --settings settings.json run
majsoul-autopilot-rs --settings settings.json run --max-games 1
majsoul-autopilot-rs --settings settings.json replay-fixture path/to/fixture.json
```

## 从源码构建

安装 Rust 后执行：

```bash
cargo build --release -p majsoul-autopilot-rs
```

构建产物位于：

```text
target/release/majsoul-autopilot-rs
```

构建桌面 App：

```bash
npm --prefix apps/desktop install
npm --prefix apps/desktop run tauri -- build
```

生成 macOS release 压缩包：

```bash
tools/package_macos_release.sh
```

从源码本地运行时，需要准备：

```text
settings.json
models/mortal-298k/model.safetensors
models/mortal-298k/model_config.json
```

模型权重和本地账号配置不会提交到仓库。

## 开发

运行测试：

```bash
cargo test --workspace -- --nocapture
```

运行 clippy：

```bash
cargo clippy --workspace --all-targets -- -D warnings
```

## 项目结构

```text
crates/
  autoplay/      自动打牌动作规划和 operation 保护
  cli/           命令行入口
  liqi/          protobuf 类型和 Liqi framing
  mjai/          Liqi 到 MJAI 的事件桥接
  mortal/        Mortal 推理和动作解码
  protocol/      lobby/game websocket 客户端
  riichi-core/   立直麻将状态和 observation 编码
apps/
  desktop/       Tauri 桌面 App
```

## 免责声明

本项目仅用于研究和实验。使用前请自行确认相关服务规则，并自行承担使用风险。

## 许可证

GPL-3.0-or-later。详见 [LICENSE.txt](LICENSE.txt)。
