# astrbot_plugin_desktop_pet

把 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 变成桌面桌宠的大脑：本仓库包含两部分——

- **AstrBot 插件**（仓库根目录）：在 AstrBot 内注册 HTTP+SSE 对话接口，负责人设、情绪标签解析、LLM 调用。
- **桌宠壳**（`pet_shell/`，Tauri 2 + 纯 HTML/JS）：Windows 桌面上的透明、无边框、置顶小窗，显示立绘（随情绪切换）、打字机气泡和聊天输入，通过 SSE 与插件实时对话。

```
桌宠壳 (Windows)                      AstrBot
┌────────────────────┐  HTTP+SSE   ┌──────────────────────────┐
│ 透明置顶窗口        │ ─────────► │ desktop_pet 插件          │
│  立绘/气泡/输入框   │  POST chat │  persona + llm_generate() │
│                    │ ◄───────── │  emotion/delta/done 帧    │
└────────────────────┘            └──────────────────────────┘
```

## 一、安装 AstrBot 插件

方式 A（推荐）：AstrBot WebUI → 插件 → 安装插件 → 填本仓库地址。

方式 B（手动）：把仓库根目录的 `main.py`、`metadata.yaml`、`_conf_schema.json` 拷到 `AstrBot/data/plugins/astrbot_plugin_desktop_pet/`，重启 AstrBot。

插件配置项（WebUI 插件卡片 → 配置）：

| 配置 | 说明 |
| --- | --- |
| `provider_id` | 指定 LLM 提供商 ID，留空用 AstrBot 默认对话模型 |
| `persona` | 桌宠人设（system prompt），情绪输出格式要求会自动追加 |
| `default_emotion` | 模型没按格式输出时的兜底情绪，默认「平静」 |
| `history_turns` | 每次请求携带的最大历史轮数，默认 10 |

## 二、创建 API Key

插件接口挂在 dashboard 插件扩展路径下，需要带 `plugin` scope 的 API Key 鉴权：

- WebUI → 设置 → API Key → 新建（勾选 plugin scope）。

请求时通过 `X-API-Key: <key>` 或 `Authorization: ApiKey <key>` 或 `?api_key=` 传递（注意：`Bearer` 前缀会被当作 dashboard JWT，不会按 API Key 处理）。

## 三、接口说明

### `POST /api/v1/plugins/extensions/desktop_pet/pet/chat`

```json
{"message": "你好", "history": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}
```

响应 `text/event-stream`：

```
data: {"type":"emotion","label":"高兴"}
data: {"type":"delta","text":"嗨嗨！"}
data: {"type":"delta","text":"今天也要加油哦！"}
data: {"type":"done"}
```

异常时会先补一帧 `{"type":"error","message":"..."}`，并以兜底情绪回复，保证桌宠侧不中断。

### `GET /api/v1/plugins/extensions/desktop_pet/pet/health`

探活，返回插件与默认模型可用状态。

## 四、运行桌宠壳（pet_shell）

前提：Rust 工具链（rustup）+ MSVC Build Tools + Node.js。

```bash
cd pet_shell
npm install
npm run dev        # 开发调试
npm run build      # 产出独立 exe（src-tauri/target/release）
```

首次运行：右键桌宠 →「设置」，填入 AstrBot 地址（默认 `http://localhost:6185/api/v1/plugins/extensions`）和上一步的 API Key。也可以在 `pet_shell/src/` 下放一个 `config.local.json` 预置配置（不会被 git 提交）：

```json
{
  "base_url": "http://localhost:6185/api/v1/plugins/extensions",
  "api_key": "你的 API Key"
}
```

操作：

- **单击立绘**：弹出/收起输入框，回车发送。
- **对话气泡**：回复结束 15 秒后自动收起；左上角粉色小圆点可随时切换显示/隐藏（重新显示保留上次内容）；点击气泡也可收起。
- **拖动立绘**：移动位置。
- **拖动右下角半透明手柄**：调整窗口和模型大小（自动记忆，重启恢复）。
- **右键**：聊天 / 点击穿透 / 设置 / 退出。
- **Ctrl+Shift+P**：切换点击穿透（穿透开启后窗口不接收任何鼠标事件，只能用快捷键或托盘菜单切回）。
- **托盘图标**：切换穿透 / 退出。

## 五、立绘与 Live2D

### 静态立绘（兜底）

`pet_shell/src/assets/` 下按情绪命名（英文文件名）：`calm.png`（平静）、`happy.png`（高兴）、`angry.png`（生气）、`shy.png`（害羞）、`surprised.png`（惊讶）、`sad.png`（难过）、`confused.png`（疑惑）、`playful.png`（调皮）。同名覆盖即可（建议透明背景 PNG，256×256 以上）。仓库内置的是脚本生成的占位图（`pet_shell/tools/gen_assets.py`）。

### Live2D（推荐）

桌宠优先尝试加载 `pet_shell/src/assets/live2d/chino/chino.model3.json`（Cubism 3/4 模型，pixi-live2d-display 渲染），加载失败自动回退静态立绘。

- **模型自备**：Live2D 模型与渲染库（`src/assets/live2d/`、`src/vendor/`）涉及版权与 Live2D SDK 许可，**不包含在仓库中**（已 gitignore），请自行准备：
  - 模型：任意 Cubism 3/4 模型目录（含 `.moc3`、`model3.json`、贴图、`motions/`、`expressions/`），放到 `src/assets/live2d/chino/` 并把入口文件命名为 `chino.model3.json`；注意 **文件名与内部引用需为 ASCII**（Tauri 资产协议对非 ASCII 路径支持不佳）。
  - 渲染库（下载到 `src/vendor/`）：`pixi.js@6.5.x` 的 `pixi.min.js`、`pixi-live2d-display@0.4.0` 的 `cubism4.min.js`、Live2D 官方的 `live2dcubismcore.min.js`。
- **情绪映射**：`src/app.js` 的 `EMOTION_EXPRESSIONS` 把 8 种情绪映射到模型表情（expression 名称），`null` 表示恢复默认表情；按你的模型实际表情名修改即可。
- 模型只有动作没有表情时，可把 `playEmotionMotion` 改为调用 `model.motion(组名)`。

## 六、常见问题

- **桌宠无回复**：先在设置面板点「测试连接」；再确认 AstrBot 日志里插件已加载（`web api registered`）。
- **回复没有切换表情**：模型未按格式输出情绪标签时会用 `default_emotion` 兜底，属正常现象；可在人设里强化格式要求。
- **Live2D 不显示**：打开 devtools（debug 构建自动弹出）看控制台；常见原因是模型路径含非 ASCII 字符、vendor 库缺失，或模型不是 Cubism 3/4 格式。
- **远端 AstrBot**：把地址改成对应主机即可（注意 6185 端口的访问控制，API Key 即鉴权，请勿暴露到公网）。

## 许可

MIT
