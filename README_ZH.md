# 雀魂自动打牌

基于 [Mortal](https://github.com/Equim-chan/Mortal) AI 的雀魂全自动打牌工具。自动登录、匹配、打牌、循环，完全无人值守。

## 功能

- **全自动**：登录 → 匹配 → 打牌 → 循环
- **Mortal AI**：使用预训练的 Mortal 模型，支持四麻和三麻
- **自动切换模型**：根据对局类型自动切换四麻/三麻模型
- **WebUI 面板**：实时对局状态查看，访问 `http://localhost:3002`
- **定期重启**：每 N 局自动软重启浏览器，提高稳定性
- **错误恢复**：自动从卡死状态和连接问题中恢复

## 环境要求

- Python >= 3.12
- Node.js（构建 WebUI 用，如果使用预编译资源则不需要）
- Mortal 模型权重文件（`mortal.pth`）

## 安装部署

### 1. 克隆仓库并安装依赖

```bash
git clone <repo-url>
cd majsoul-autopilot

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# 安装 Python 依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium
```

### 2. 配置设置

```bash
cp settings/settings.json.example settings/settings.json
```

编辑 `settings/settings.json`：

```json
{
  "autoplay_account": {
    "username": "你的邮箱",
    "password": "你的密码"
  },
  "autoplay_mode": {
    "type": "4p_south",
    "room": "gold"
  }
}
```

主要配置项：

| 字段 | 说明 | 可选值 |
|------|------|--------|
| `autoplay_mode.type` | 对局类型 | `4p_south`（四麻南）、`4p_east`（四麻东）、`3p_south`（三麻南）、`3p_east`（三麻东） |
| `autoplay_mode.room` | 房间等级 | `bronze`（铜）、`silver`（银）、`gold`（金）、`jade`（玉）、`throne`（王座） |
| `autoplay_headless` | 隐藏浏览器窗口 | `true` / `false` |
| `autoplay_time` | 操作延迟（秒） | 模拟人类操作的时间间隔 |
| `webui_port` | WebUI 端口 | 默认 `3002` |
| `model_path` | 模型文件路径 | 相对于 `mjai_bot/mortal/` 目录 |

### 3. 放置模型文件

下载 Mortal 模型文件，放到以下位置：

```
mjai_bot/mortal/mortal.pth      # 四麻模型
mjai_bot/mortal3p/mortal.pth    # 三麻模型（可选）
```

### 4. 构建 WebUI（可选）

项目已包含预编译的前端资源。如果修改了前端代码，需要重新构建：

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
1. 启动 MITM 代理（拦截游戏协议）
2. 启动 WebUI，地址 `http://localhost:3002`
3. 打开 Chromium 浏览器并进入雀魂
4. 使用配置的账号登录
5. 自动开始匹配和打牌

### WebUI 面板

在浏览器中打开 `http://localhost:3002` 可以看到：
- 实时牌桌：所有玩家的牌河和副露
- 手牌和自摸牌
- AI 推荐（前 3 个操作及概率）
- 对手剩余手牌数量（暗牌显示）
- 立直宣言横置显示

### 停止运行

按 `Ctrl+C` 优雅停止。程序会完成当前操作后清理退出。

## 架构

```
浏览器 (Playwright/Chromium)
    ↕ WebSocket（经 MITM 代理）
MITM 代理 (mitmproxy)
    ↕ Liqi 协议 → MJAI 格式
游戏循环线程
    ↕ MJAI 消息
Mortal AI 模型
    ↕ 操作推荐
浏览器自动化（JS 注入）
    → 执行游戏操作
```

## 项目结构

```
majsoul-autopilot/
├── run_autoplay.py          # 主入口
├── settings/                # 配置文件
├── mjai_bot/                # AI 模型与推理
│   ├── mortal/              # 四麻模型 + libriichi
│   └── mortal3p/            # 三麻模型 + libriichi
├── mitm/                    # MITM 代理与协议桥接
├── autoplay/                # 浏览器自动化与操作执行
├── akagi/                   # 游戏状态处理与 WebUI 服务
└── webui/                   # React 前端源码
```

## 免责声明

本项目仅供学习和研究用途。
