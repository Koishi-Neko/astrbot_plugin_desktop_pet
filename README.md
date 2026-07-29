# astrbot_plugin_desktop_pet

把 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 变成桌面桌宠的大脑。本仓库包含两部分：

- **AstrBot 插件**（仓库根目录）：在 AstrBot 内挂载 HTTP 路由与 WebUI 控制页，经 `on_llm_request` / `on_decorating_result` 钩子为桌宠会话注入情绪/日语配音格式要求与主人身份标注，并代理 Style-Bert-VITS2 合成语音。
- **桌宠壳**（`pet_shell/`，Tauri 2 + 纯 HTML/JS）：Windows 桌面上的透明、无边框、置顶小窗，Live2D 立绘（随情绪切表情 + 程序化动作）、打字机气泡、聊天输入、灵动待机、主动对话，通过 AstrBot open API 与插件实时对话。

```
桌宠壳 (Windows)                          AstrBot
┌───────────────────────┐  open API SSE  ┌──────────────────────────────┐
│ Tauri 透明置顶窗口     │ ─────────────► │ /api/v1/chat (webchat 管道)   │
│ Live2D / 气泡 / 输入框 │  POST /chat    │  人格 + LivingMemory + 历史   │
│ Rust 原生 HTTP 层      │ ◄───────────── │  on_llm_request 注入格式要求  │
│ 主动对话 / 待机演出    │  SSE 流式回包  │ desktop_pet 插件 /pet/* 路由  │
└───────────────────────┘                └──────────────────────────────┘
```

桌宠作为 webchat 会话（`webchat!desktop_pet!desktop_pet`）走 AstrBot open API `/api/v1/chat`，自动获得**会话级人格**、**LivingMemory 记忆召回/反思**、平台历史与日志——人格/记忆/历史均在 AstrBot 侧管理，壳端不存历史。插件仅负责格式注入、TTS 代理与控制页。

## 一、安装 AstrBot 插件

方式 A（推荐）：AstrBot WebUI → 插件 → 安装插件 → 填本仓库地址。

方式 B（手动）：把仓库根目录的 `main.py`、`metadata.yaml`、`_conf_schema.json`、`pages/` 拷到 `AstrBot/data/plugins/astrbot_plugin_desktop_pet/`，重启 AstrBot。

> 本插件用到的 webchat 平台是 AstrBot 内置无条件启动的，无需在「平台」配置里添加。

## 二、创建 API Key

桌宠壳经 AstrBot open API 通信，需要带 `plugin` + `chat` scope 的 API Key 鉴权：

- WebUI → 设置 → API Key → 新建（勾选 plugin、chat scope）。

请求时通过 `X-API-Key: <key>` 或 `Authorization: ApiKey <key>` 或 `?api_key=` 传递。**注意：`Bearer` 前缀会被当作 dashboard JWT，不会按 API Key 处理。**

## 三、配置入口：WebUI 控制页（推荐）

插件自带 WebUI 控制页，是配置的**主入口**。进入方式：WebUI → 插件 → 找到「astrbot_plugin_desktop_pet」→ 点详情/控制页。控制页包含：

- **状态区**：SBV2 连通性/延迟/显存、桌宠会话 ID、当前主人身份、QQ 配音开关、默认人格。
- **主人身份**卡片：主人昵称、主人 QQ 号（桌宠会话与 QQ 中该账号消息会被识别为同一位主人）。
- **QQ 日语配音**卡片：开关（开启后 bot 在全部 QQ 群聊/私聊回复附带一条日语配音语音，文字仍为中文；SBV2 离线自动降级为纯文字）。
- **TTS 语音配置**卡片：启用开关、SBV2 服务地址、模型/说话人/风格（下拉实时拉 SBV2 `/models/info`）、语速滑块。
- **试听**区：用当前参数即时合成播放，不保存配置。

控制页保存即时生效，无需重启。同一组配置项**也**暴露在插件配置 schema（`_conf_schema.json`）里作为备用入口，但**请只在一个地方编辑**，避免双入口状态不同步——下拉/试听体验都在控制页，推荐用控制页。

### 为桌宠会话选人格

桌宠会话的人格在 **WebUI 聊天页**（地址栏访问 `http://<astrbot-host>:6185/chat`，该页不在左侧导航菜单，需直接输 URL）里设置：左侧会话列表选 `desktop_pet` → 在该会话设置里选人格。注意：只有桌宠发过消息、AstrBot 产生 conversation 记录后，`desktop_pet` 才会出现在会话列表里。

## 四、接口说明

### AstrBot open API（桌宠对话主通道）

`POST /api/v1/chat` —— 桌宠经此走 webchat 管道，享有 AstrBot 全部对话能力。SSE 帧序列：`session_id` → `user_message_saved` → `run_started` → `plain`×N → `agent_stats` → **`complete`（全文）** → `message_saved` → `end`。壳端用 `complete` 帧本地解析情绪标签与日语配音稿。

### 插件自有路由（挂在 dashboard 插件扩展路径下，需带 plugin scope 的 API Key）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET  | `/api/v1/plugins/extensions/desktop_pet/pet/health` | 探活，返回插件、默认模型可用性、情绪列表、TTS/QQ 配音开关、会话 ID |
| POST | `/api/v1/plugins/extensions/desktop_pet/pet/tts` | 日语 TTS 合成：`{"text":"..."}` → `{"audio":"<base64 wav>","format":"wav"}`，壳端按句调用 |
| GET  | `/api/v1/plugins/extensions/desktop_pet/pet/personas` | 列出 AstrBot 人格（供桌宠选用参考） |
| *    | `/api/v1/plugins/extensions/astrbot_plugin_desktop_pet/page/*` | WebUI 控制页后端（status / sbv2_models / tts_config / master_config / tts_test） |

> 控制页 API 路由前缀必须是**插件全名**（`astrbot_plugin_desktop_pet/page/...`），bridge 按插件名转发；用 `desktop_pet/page/...` 会报「未找到该路由」。

## 五、运行桌宠壳（pet_shell）

前提：Rust 工具链（rustup stable）+ MSVC Build Tools（VS 2022）+ Node.js（v20+）。

```bash
cd pet_shell
npm install
npm run dev        # 开发调试（tauri dev，热重载前端）
npm run build      # 产出独立 exe（src-tauri/target/release/）
```

首次运行：右键桌宠 →「设置」，填入 AstrBot 地址（默认 `http://localhost:6185/api/v1/plugins/extensions`）和上一步的 API Key，点「测试连接」。也可以在 `pet_shell/src/` 下放一个 `config.local.json` 预置配置（已 gitignore，不会提交）：

```json
{
  "base_url": "http://localhost:6185/api/v1/plugins/extensions",
  "api_key": "你的 API Key",
  "proactive": {
    "enabled": true,
    "globalCooldownMin": 45,
    "rules": {
      "night_owl":     { "enabled": true, "startHour": 23, "endHour": 2,  "activeHours": 1,   "cooldownHours": 2 },
      "welcome_back":  { "enabled": true, "awayMinutes": 30,                "cooldownHours": 1 },
      "sedentary":     { "enabled": true, "activeHours": 2,                 "cooldownHours": 2 }
    },
    "scene": { "enabled": false, "intervalMin": 30, "maxIdleMin": 10, "provider": "scnet/Kimi-K2.6",
               "blocklist": ["weixin.exe", "wechat.exe", "qq.exe", "wemeetapp.exe", "winword.exe", "excel.exe", "powerpnt.exe"] }
  }
}
```

`proactive` 节可省略（用内置默认值，即上例）。运行时设置面板的开关与 localStorage 优先级高于此文件。

设置面板内还有：
- **语音（日语配音）**：开关桌宠回复的日语语音播放（仅控制壳端播放，服务端仍按配置合成）。
- **主动对话（适时插话）**：开关主动对话。开启后壳端每 30s 检查态势（深夜连续活动催睡、离开后回来问候、久坐提醒），满足条件时以情境提示触发一次主动发言；全屏应用前台 / 输入框打开 / 对话中自动免打扰。
- **桌面感知（看屏幕主动搭话）**：默认关。开启后按「观察间隔」抓取**前台窗口**画面（Windows Graphics Capture 进程级抓取，只含目标窗口内容，遮挡窗口也能抓），经 open API `/api/v1/file` 上传后随情境消息发给「视觉模型」识图：看到值得评论的内容（游戏进展、文档、有趣页面）就自然搭话，没什么值得说的模型回【略过】则静默。**截图会发送给云端 LLM 提供商**；观察间隔与视觉模型均可在设置面板调整（localStorage 即时生效）。**禁止抓取名单**：`proactive.scene.blocklist`（或设置面板文本框，逗号分隔进程名）内的进程位于前台时直接跳过、不截图——默认含微信（`weixin`/`wechat` 等）、QQ/TIM、企业微信、钉钉、腾讯会议、Word/Excel/PowerPoint。要求：API Key 需带 `file` scope；视觉模型需 modalities 含 image（实测 `scnet/Kimi-K2.6` 可用，`deepseek/deepseek-v4-pro` 接口拒收 `image_url` 勿用）。已知限制：独占全屏游戏绕过 DWM 抓不到（无边框窗口化即可）、窗口最小化抓不到、DRM 内容黑帧（自动跳过）。

## 六、操作

- **单击立绘**：戳一戳，随机动作/表情反馈。
- **双击立绘**：开合输入框，回车发送。
- **对话气泡**：回复结束 15 秒后自动收起；头部左侧粉色小圆点可随时切换显示/隐藏；点击气泡也可收起。
- **拖动立绘**：移动窗口位置。
- **拖动右下角半透明手柄**：调整窗口和模型大小（自动记忆，重启恢复）。
- **右键**：聊天 / 点击穿透 / 设置 / 退出。
- **Ctrl+Shift+P**：切换点击穿透（穿透开启后窗口不接收任何鼠标事件，只能用快捷键或托盘菜单切回）。
- **托盘图标**：切换穿透 / 退出。

## 七、立绘与 Live2D

### 静态立绘（兜底）

`pet_shell/src/assets/` 下按情绪命名（英文文件名，避免资产协议对非 ASCII 文件名的兼容问题）：`calm.png`（平静）、`happy.png`（高兴）、`angry.png`（生气）、`shy.png`（害羞）、`surprised.png`（惊讶）、`sad.png`（难过）、`confused.png`（疑惑）、`playful.png`（调皮）。同名覆盖即可（建议透明背景 PNG，256×256 以上）。仓库内置的是脚本生成的占位图（`pet_shell/tools/gen_assets.py`）。

### Live2D（推荐）

桌宠优先尝试加载 `pet_shell/src/assets/live2d/chino/chino.model3.json`（Cubism 3/4 模型，pixi-live2d-display 渲染），加载失败自动回退静态立绘。

- **模型自备**：Live2D 模型与渲染库涉及版权与 Live2D SDK 许可，**不包含在仓库中**（已 gitignore），请自行准备：
  - 模型：任意 Cubism 3/4 模型目录（含 `.moc3`、`model3.json`、贴图、`motions/`、`expressions/`），放到 `src/assets/live2d/chino/` 并把入口文件命名为 `chino.model3.json`；**模型文件名与 model3.json 内部引用需为全 ASCII**（Tauri 资产协议对非 ASCII 路径支持不佳）。
  - 渲染库（下载到 `src/vendor/`）：`pixi.js@6.5.x` 的 `pixi.min.js`、`pixi-live2d-display@0.4.0` 的 `cubism4.min.js`、Live2D 官方的 `live2dcubismcore.min.js`。
- **情绪映射**：`src/app.js` 的 `EMOTION_EXPRESSIONS` 把 8 种情绪映射到模型表情（expression 名称），`null` 表示恢复默认表情；按你的模型实际表情名修改即可。
- **程序化动作**：`pet_shell/tools/gen_motions.py` 程序化生成 motion3.json（点头/摇头/歪头/摇摆/待机增强 `idle_sway`/长待机演出 `coin_sway` 等）并自动注册进 model3.json；改 `AMPLITUDES` 常量即可调整幅度，重跑脚本即重新生成。
- **灵动待机系统**（`src/app.js`）：
  - 视线跟随鼠标（3 秒看门狗缓动回正；点击穿透模式下自动失效）；
  - 随机待机调度：每 25~60 秒随机触发小动作、短暂表情或视线游移；对话与演出期间自动暂停；
  - 长待机演出 `coin_sway`：45 秒的手部形态保持 + 头身慢摇，FORCE 优先级进出，演出中发消息立即退出。
- **调试**：`pet_shell/src/lab/index.html` 是动作实验室（`python -m http.server 8765` 后开 `http://localhost:8765/lab/`），按钮即时播放任意动作/表情调参；`probe.html` 是运行时参数记录探针。
- 注意：`model.expression()` 不传参会**随机**应用表情，恢复默认必须用 `expressionManager.resetExpression()`。

## 八、TTS 语音（可选）

桌宠回复附带日语配音需要 **Style-Bert-VITS2**（自备部署，本项目不提供模型与服务）：

1. 自行部署 SBV2（litagin02 仓库），准备一个日语声线模型。
2. 在插件控制页「TTS 语音配置」填 SBV2 地址、选择模型/说话人/风格，开启开关。
3. 桌宠回复格式变为「【情绪】中文正文【JP】日语配音稿」：中文进气泡，日语分句逐句合成顺序播放（壳端 AudioContext 队列 + 口型同步）。

AstrBot 与 SBV2 的网络：若 AstrBot 跑在 Docker、SBV2 跑在 WSL 宿主，插件侧填 `http://172.18.0.1:5000`（docker 网桥网关 IP）；docker 网络重建需改该地址。QQ 日语配音同走该 SBV2。

> ATRI 等 `2.7.0-JP-Extra` 模型**只支持 language=JP**，中文合成会 500；要中文语音需另装非 JP-Extra 的标准三语模型。

## 九、常见问题

- **桌宠无回复**：先在设置面板点「测试连接」；再确认 AstrBot 日志里插件已加载（`[desktop_pet] web api registered`）；确认 API Key 带 plugin+chat scope 且用 `X-API-Key` / `Authorization: ApiKey` 传递（不要用 `Bearer`）。
- **回复没有切换表情**：模型未按格式输出情绪标签时会用「平静」兜底，属正常现象；可在人格 prompt 里强化格式要求。插件已在用户消息末尾补格式提醒对抗长人格稀释。
- **没有语音**：确认控制页 TTS 开关已开、SBV2 状态区显示「可达」、模型/说话人/风格已选；壳端设置面板「语音」开关也要开。
- **Live2D 不显示**：打开 devtools（debug 构建自动弹出）看控制台；常见原因是模型路径含非 ASCII 字符、vendor 库缺失，或模型不是 Cubism 3/4 格式。内嵌资产模式下 CSP 要求 `'unsafe-eval'`（已配置），否则 PIXI 初始化失败回退静态立绘。
- **打包的 exe 白屏「127.0.0.1 拒绝连接」**：经 `tauri dev` 产出的 debug exe 会烘焙 dev server 地址（`127.0.0.1:1430`），脱离 CLI 直接启动时白屏。**独立运行的 exe 必须用 `cargo build --release` 或 `npm run build` 产出**（走内嵌资产 `tauri.localhost`）。排查技巧：设 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222` 启动，访问 `http://localhost:9222/json` 看 WebView 实际加载的 URL。
- **远端 AstrBot**：把桌宠设置里的地址改成对应主机即可（注意 6185 端口的访问控制，API Key 即鉴权，请勿暴露到公网）。

## 十、工具脚本

`pet_shell/tools/`：

- `gen_motions.py`：生成/更新程序化动作 motion3.json 并注册进 model3.json。
- `gen_assets.py`：生成 8 张占位情绪 PNG 与图标。
- `start_all.ps1` / `stop_all.ps1`：一键启停整套服务（WSL 服务 + AstrBot 容器 + 桌宠 exe）。**PowerShell 5.1 需以 UTF-8 BOM 保存，否则中文解析报错。**
- `start_all.vbs` / `stop_all.vbs`：wscript 隐藏调起对应 ps1，全程无控制台窗口闪烁，日常双击用这两个。

## 许可

MIT
