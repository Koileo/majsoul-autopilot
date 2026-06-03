[English](README.md)

# 雀魂自动打牌

> **免责声明**：本项目仅供学习和研究用途。使用本工具造成的账号封禁等后果由使用者自行承担，作者不承担任何责任。

基于 [Mortal](https://github.com/Equim-chan/Mortal) AI 的雀魂全自动打牌工具。它可以自动登录、按段位选择房间、匹配、打牌、断线恢复并循环运行。

本项目基于 [shinkuan](https://github.com/shinkuan) 的 [Akagi](https://github.com/shinkuan/Akagi) 开发。

## 当前架构

现在的雀魂网页端是 Unity WebGL。旧版 Laya 全局对象（`uiscript`、`app.NetAgent`、`GameMgr`）已经不可用，所以默认自动打牌路径不再依赖浏览器 JS、视觉识别或坐标点击。

当前默认路径是纯协议：

```
run_autoplay.py
    -> Liqi 协议客户端（登录、线路发现、匹配、对局重连）
    -> Liqi 对局 socket（FastTest auth/sync/inputOperation）
    -> Majsoul bridge（Liqi 广播 -> MJAI 事件）
    -> Mortal 模型（MJAI 决策）
    -> Liqi 操作 RPC

Jsonl 日志 -> WebUI watcher -> React WebUI 面板
MITM 代理 -> 协议/状态桥与兼容事件流
```

旧的 Playwright 浏览器自动化代码仍保留在仓库中作为参考，但默认入口使用 `autoplay/protocol_automation.py`。

## 功能

- **纯协议自动打牌**：通过 Liqi WebSocket RPC 完成密码登录、线路发现、匹配、对局认证/同步和操作执行。
- **Mortal AI 决策**：将对局广播转换为 MJAI 事件后交给 Mortal 模型。
- **正确处理立直宣言牌**：当 Mortal 选择立直时，先把 `reach` 事件喂回模型，再由模型给出宣言时要打的牌。
- **按段位自动切房**：未到雀士打铜之间四人东；雀士打银之间四人南；雀杰及以上打金之间四人南。
- **恢复逻辑**：可重连已有对局，账号忙碌时避免重复匹配；遇到 stale queue/缺铜币的 `1304` 会尝试领取复活币后重新匹配；操作窗口失败时会立即恢复。
- **WebUI 面板**：访问 `http://localhost:3002` 查看实时对局状态和模型推理。

## 环境要求

- Python >= 3.12
- Node.js，仅在重新构建 WebUI 时需要
- Mortal 模型权重文件，例如 [`VoidShine/mortal-298k`](https://huggingface.co/VoidShine/mortal-298k)

## 安装部署

### 1. 克隆仓库并安装依赖

```bash
git clone <repo-url>
cd majsoul-autopilot

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

默认纯协议 runner 不需要执行 `playwright install chromium`。只有你明确要调试旧浏览器自动化路径时才需要安装 Playwright 浏览器。

### 2. 配置设置

```bash
cp settings/settings.json.example settings/settings.json
```

编辑 `settings/settings.json`：

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

主要配置项：

| 字段 | 说明 | 备注 |
|------|------|------|
| `autoplay_account.username` | 雀魂邮箱/账号登录名 | 只保存在本地 `settings/settings.json`，该文件已被 gitignore |
| `autoplay_account.password` | 雀魂密码 | 登录 RPC 前会按客户端逻辑哈希 |
| `model_path` | Mortal 模型文件路径 | 绝对路径或相对于项目根目录 |
| `autoplay_mode.type` / `room` | 初始模式提示 | runner 登录后会刷新四麻段位，并可能自动覆盖该配置 |
| `webui_port` | WebUI 端口 | 默认 `3002` |
| `mitm.host` / `mitm.port` | 本地 MITM 兼容桥 | 默认 `127.0.0.1:7880` |

自动段位目标：

| 四麻段位 | 目标房间 |
|----------|----------|
| 未到雀士 | `4p_east` / `bronze` |
| 雀士 | `4p_south` / `silver` |
| 雀杰及以上 | `4p_south` / `gold` |

### 3. 放置模型文件

下载 Mortal 模型文件，默认放到 `mjai_bot/mortal/mortal.pth`，或在 `model_path` 中改成你的路径。

模型下载地址：[VoidShine/mortal-298k](https://huggingface.co/VoidShine/mortal-298k)

### 4. 构建 WebUI

项目已包含预编译前端资源。修改前端后再重新构建：

```bash
cd webui
npm install
npm run build
```

## 使用方法

```bash
python run_autoplay.py
```

程序启动后会：

1. 启动 WebUI，地址 `http://localhost:3002`。
2. 启动本地 MITM 兼容桥。
3. 通过 Liqi 协议登录雀魂。
4. 刷新账号四麻段位并选择目标房间。
5. 通过 `startUnifiedMatch` 进入匹配队列。
6. 连接对局 socket，同步牌局，将 MJAI 事件送入 Mortal，并通过 Liqi RPC 执行动作。
7. 遇到已有对局、操作失败、stale queue 或临时冷却时自动重连/恢复。

打开 `http://localhost:3002` 查看实时对局状态。按一次 `Ctrl+C` 请求退出；再按一次强制退出。

如果只想查看 WebUI：

```bash
python run_webui.py
```

## 运行注意事项

- runner 打牌时不要在浏览器里登录同一个雀魂账号。账号被别处顶掉后，runner 会尝试重连，但可能短时间停止操作。
- `startUnifiedMatch` 返回 `1304` 不一定只是残留匹配队列，也可能是需要领取复活币。runner 会检查 `fetchReviveCoinInfo`，可领时调用 `gainReviveCoin`，然后重新匹配。
- 线路会优先从雀魂 route/config 接口获取，失败时回退到内置 route 域名。
- WebUI 会监听 JSONL 流；日志被重写或截断时会自动重置缓存状态。

## 项目结构

```
majsoul-autopilot/
├── run_autoplay.py                 # 主协议自动打牌循环
├── settings/                       # 本地配置 schema/example
├── autoplay/
│   ├── protocol_automation.py      # Liqi 登录、匹配、重连和操作 RPC
│   └── majsoul_automation.py       # 旧 Playwright 路径，保留作参考
├── mjai_bot/                       # Mortal 模型集成
├── mitm/                           # Liqi 解析、桥接和 MITM 兼容事件流
├── akagi/                          # 对局状态处理与 WebUI 服务
├── webui/                          # React 前端源码
└── tests/                          # 协议恢复和 WebUI watcher 测试
```

## 测试

运行关键回归测试：

```bash
python -m unittest tests.test_protocol_recovery tests.test_webui_watcher
```

## 致谢

本项目基于 [shinkuan](https://github.com/shinkuan) 的 [Akagi](https://github.com/shinkuan/Akagi) 开发，Akagi 提供了原始协议桥和 Mortal Bot 集成。Majsoul Autopilot 当前使用协议优先的 runner，并配套 React WebUI 面板。

## 许可证

本项目使用与原 Akagi 项目相同的 **GNU Affero General Public License v3 附加 Commons Clause** 许可证。详见 [LICENSE.txt](LICENSE.txt)。

本项目仅供学习和研究用途。
