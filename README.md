# majsoul-autopilot-rs

纯 Rust 版雀魂自动打牌运行时。

这个分支只保留 Rust runtime：不使用浏览器、不走视觉识别、不依赖 Python 运行自动打牌。程序通过 Liqi 协议登录、匹配、进局、重连，并使用导出的 Mortal 模型进行四人麻将决策。

## 当前能力

- 四人麻将段位场自动打牌
- 邮箱密码登录
- Liqi 纯协议 lobby/game websocket
- 断线或账号已在对局时重连已有对局
- Mortal 原生 Candle 推理
- 立直二段决策：先向模型喂 `reach`，再按模型返回的 `dahai` 选择宣言牌
- operation stale guard 与 discard ACK 校验
- 段位自动切房：
  - 未到雀士：铜之间四人东
  - 到雀士：银之间四人南
  - 到雀杰及以上：金之间四人南

不支持三麻入口。

## macOS M 系列 Release 包

release zip 目录结构：

```text
majsoul-autopilot-rs-macos-arm64/
  majsoul-autopilot-rs
  settings.example.json
  README.md
  models/
    mortal-298k/
      model.safetensors
      model_config.json
```

解压后进入目录：

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

检查模型：

```bash
./majsoul-autopilot-rs --settings settings.json check-model
```

检查登录和当前段位目标：

```bash
./majsoul-autopilot-rs --settings settings.json check-login
```

开始自动打牌：

```bash
./majsoul-autopilot-rs --settings settings.json run
```

只跑一局 smoke test：

```bash
./majsoul-autopilot-rs --settings settings.json run --max-games 1
```

停止运行使用 `Ctrl-C`。

## 从源码构建

需要 Rust toolchain。macOS M 系列默认目标是 `aarch64-apple-darwin`。

```bash
cargo build --release -p majsoul-autopilot-rs
```

构建产物：

```text
target/release/majsoul-autopilot-rs
```

运行前需要准备：

- `settings.json`
- `models/mortal-298k/model.safetensors`
- `models/mortal-298k/model_config.json`

模型权重和本地账号配置默认不提交到 git。

## 配置文件

最小配置只需要三项：

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

- `model_path`：导出的 Mortal 模型目录，目录内需要 `model.safetensors` 和 `model_config.json`
- `autoplay_account.username`：雀魂邮箱账号
- `autoplay_account.password`：雀魂密码，运行时会按雀魂登录协议计算摘要

## 命令

```bash
./majsoul-autopilot-rs --settings settings.json check-model
./majsoul-autopilot-rs --settings settings.json check-login
./majsoul-autopilot-rs --settings settings.json run
./majsoul-autopilot-rs --settings settings.json run --max-games 1
./majsoul-autopilot-rs --settings settings.json replay-fixture path/to/fixture.json
```

## 验证

源码验证：

```bash
cargo test --workspace -- --nocapture
cargo clippy --workspace --all-targets -- -D warnings
```

release 包验证：

```bash
./majsoul-autopilot-rs --settings settings.json check-model
./majsoul-autopilot-rs --settings settings.json check-login
```
