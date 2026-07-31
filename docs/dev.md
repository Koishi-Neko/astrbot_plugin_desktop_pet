# 开发文档

面向开发者/二开者的实现细节。用户使用说明见 [README.md](../README.md)。

## 架构

```
桌宠壳 (Windows)                          AstrBot
┌───────────────────────┐  open API SSE  ┌──────────────────────────────┐
│ Tauri 透明置顶窗口     │ ─────────────► │ /api/v1/chat (webchat 管道)   │
│ Live2D / 气泡 / 输入框 │  POST /chat    │  人格 + 历史 (+可选记忆插件)  │
│ Rust 原生 HTTP 层      │ ◄───────────── │  on_llm_request 注入格式要求  │
│ 主动对话 / 待机演出    │  SSE 流式回包  │ desktop_pet 插件 /pet/* 路由  │
└───────────────────────┘                └──────────────────────────────┘
```

桌宠作为 webchat 会话（`webchat!desktop_pet!desktop_pet`）走 AstrBot open API `/api/v1/chat`，自动获得**会话级人格**、平台历史与日志——人格/历史均在 AstrBot 侧管理，壳端不存历史。**记忆召回/反思不是本插件的功能**：安装记忆类插件（如 LivingMemory）时由其在管道中自动提供，不装不影响桌宠任何功能。插件仅负责格式注入、TTS 代理与控制页。

> WebView2 有 CORS 限制，前端不直接 fetch 插件接口：所有 HTTP 走 Rust reqwest 原生层（`pet_*` Tauri 命令），SSE 经 Tauri event 推回前端。

## 仓库结构

| 路径 | 说明 |
| --- | --- |
| `main.py` / `metadata.yaml` / `_conf_schema.json` | AstrBot 插件本体 |
| `pages/pet/` | WebUI 控制页三件套（index.html / app.js / style.css） |
| `pet_shell/src/` | 桌宠壳前端（app.js 单文件主体 + lab 调试页） |
| `pet_shell/src-tauri/` | Tauri Rust 工程（原生 HTTP、窗口抓取、系统态势） |
| `pet_shell/src/assets/live2d/hiyori/` | 内置默认模型桃濑日和（官方免费示例） |
| `pet_shell/src/vendor/` | 渲染库（gitignore，`npm run setup` 下载） |
| `pet_shell/tools/` | 工具脚本（见下文） |

## 接口

### AstrBot open API（桌宠对话主通道）

`POST /api/v1/chat` —— 桌宠经此走 webchat 管道，享有 AstrBot 全部对话能力。SSE 帧序列：`session_id` → `user_message_saved` → `run_started` → `plain`×N → `agent_stats` → **`complete`（全文）** → `message_saved` → `end`。壳端用 `complete` 帧本地解析情绪标签与日语配音稿。

带图消息：`message` 接受段列表，图片段只认 `attachment_id`，需先 `POST /api/v1/file`（multipart 字段名 `file`）上传，API Key 需 `file` scope。`selected_provider` 是请求级参数（不粘会话）。

### 插件自有路由（需带 plugin scope 的 API Key）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET  | `/api/v1/plugins/extensions/desktop_pet/pet/health` | 探活，返回插件、默认模型可用性、情绪列表、TTS/QQ 配音开关、会话 ID |
| POST | `/api/v1/plugins/extensions/desktop_pet/pet/tts` | 日语 TTS 合成：`{"text":"..."}` → `{"audio":"<base64 wav>","format":"wav"}`，壳端按句调用 |
| GET  | `/api/v1/plugins/extensions/desktop_pet/pet/personas` | 列出 AstrBot 人格（供桌宠选用参考） |
| GET  | `/api/v1/plugins/extensions/desktop_pet/pet/scene_config` | 桌面感知配置下发：`{"provider":"...","blocklist":[...]}`，壳端 120s 缓存拉取 |
| POST | `/api/v1/plugins/extensions/desktop_pet/pet/status_report` | 壳端状态上报（60s 心跳 + 触发后防抖），插件内存暂存供控制页监控 |
| *    | `/api/v1/plugins/extensions/astrbot_plugin_desktop_pet/page/*` | WebUI 控制页后端（status / sbv2_models / tts_config / master_config / persona_config / scene_config / tts_test） |

> 控制页 API 路由前缀必须是**插件全名**（`astrbot_plugin_desktop_pet/page/...`），bridge 按插件名转发；用 `desktop_pet/page/...` 会报「未找到该路由」。控制页加载在 Dashboard 登录态下（此时 API Key 401 属正常）。

## 插件（main.py）要点

- 钩子：`on_llm_request`（**priority=-10**，须后于记忆类插件等注入型插件执行，如 LivingMemory 的记忆召回注入）对桌宠会话注入【情绪】中文【JP】日语格式要求 + 主人身份改写；`on_decorating_result` 把 QQ 回复拆成 `Plain(中文)+Record(日语配音)`。
- 身份改写：`_rewrite_pet_identity()` 改写当前请求 `extra_user_content_parts` + 历史 `contexts` + `req.prompt`/`system_prompt`（`provider_settings.identifier` 会把 `User ID: desktop_pet` 追加进每条用户消息，不改写模型会把用户叫成 desktop_pet）。
- 长人格 prompt 会稀释 system 侧格式要求 → 在**用户消息末尾**补格式提醒（桌宠和 QQ 两侧都需要）。
- 配置持久化：写回 `data/config/astrbot_plugin_desktop_pet_config.json` 即时生效；schema 中与控制页重叠的键全部 `invisible: true`，仅 `pet_session_id` 可见（内部常量，不应让用户改）。

## 桌宠壳（pet_shell）要点

- 配置链：`localStorage` > `config.local.json` > 内置默认。`config.local.json` 在 release 构建中会被内嵌（构建机勿放私钥）。
- Live2D：`MODELS` 注册表（key → 显示名/url）+ `modelCandidates()` 候选链（`config.local.json live2d.model_url` → `localStorage pet_model` 上次选择 → chino → hiyori → 静态透明兜底）；`MODEL_PROFILES` 能力档案分流（hiyori 无 exp3 表情、用自带动作组、无 coin_sway）。**热切换**：右键菜单「切换模型」→ `switchModel(key)`（先加载新模型成功再销毁旧模型，`attachModel` 收口布局/待机/motionFinish 回接），选择持久化到 `localStorage pet_model`。本机第三个模型 `chino_q`（Q版智乃，本地不入库）：exp3 表情（o_mouth/squeezed_eyes/heart_eyes_blush/hat/hold/hold_toggle/magic_staff）+ 复用智乃程序化动作 nod/shake/tilt/sway（标准参数同名），无 coin_sway。pixi-live2d-display Cubism4，DPI 2x + mipmap。
- **用户模型上传/卸载**：设置面板路径上传或拖拽（Tauri 2 原生 `onDragDropEvent`）→ Rust `pet_model_upload`（文件夹/`.model3.json`/`.zip`；复制到 `%LOCALAPPDATA%\com.astrbotpet.shell\models\<key>\`，全部文件扁平化 ASCII 重命名并重写 model3.json 引用，校验 moc3 版本字节 1~5）→ 注册进 `MODELS` 自动切换。运行时经自定义协议 `petmodel://localhost/<key>/<file>`（Windows 下 `http://petmodel.localhost`，路径带真实 "/" 层级使相对引用可解析；asset protocol 全量百分号编码路径不可用，勿回退）。已上传模型启动时经 `pet_model_list` 扫描注册，`genericProfile`（动作组全用、无表情映射、无 coin_sway）；子菜单 `×` 卸载（`pet_model_delete` 删目录）。zip 解压需 zip crate `deflate` feature。
- 待机系统：idle_sway 循环 + 随机调度器（25~60s 小动作/表情/视线游移）+ 视线跟随（3s 看门狗缓动回正）+ 长待机演出 `coin_sway`（智乃档案，25s 无对话保底，发言不退出、情绪走表情通道叠加）。
- 主动对话：app.js 尾部模块，30s tick，规则 night_owl / welcome_back / sedentary 均带独立冷却；全局节流 45min（`lastChatAt`）；免打扰：全屏 / 输入框打开 / 对话中 / 空闲超时。
- 桌面感知：Rust `capture_window`（WGC 进程级抓取，只含目标窗口；遮挡可抓；独占全屏/最小化/DRM 抓不到）→ `/api/v1/file` 上传 → 视觉模型识图；`scene_blocklist` 抓取前拦截。
- 调试句柄：`__proactiveFire/__proactiveTick/__proactiveParams/__proactiveLog/__sceneShot/__sceneWatch`；lab 页暴露 `window.__model`/`__app`。

### Live2D 踩坑备忘

- `model.expression()` 不传参 = **随机**表情；重置用 `expressionManager.resetExpression()`。
- 循环动作不结束，同级 NORMAL 优先级会被永久排队；打断必须 `MotionPriority.FORCE`，回接 idle 也要 FORCE。
- `motionFinish` 只在 `internalModel.motionManager` 上派发（`model.on()` 不触发）。
- 闭眼必须走表情通道（exp3.json），不能写进动作曲线（eyeBlink 每帧覆盖）。
- 模型/贴图/动作文件名必须全 ASCII（Tauri 资产协议不支持非 ASCII），model3.json 内部引用同。
- `PIXI.live2d.CubismConfig.setOpacityFromMotion` 默认 false，加载模型前显式置 true（否则 PartOpacity 曲线被跳过）。

### 构建踩坑备忘

- **独立运行的 exe 必须 `cargo build --release` / `npm run build` 产出**：经 `tauri dev` 编译的 debug exe 会把 dev server 地址烘进二进制，脱离 CLI 启动白屏。排查：设 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222` 启动，访问 `http://localhost:9222/json` 看 WebView 实际 URL。
- 内嵌资产模式 CSP 生效，PIXI 需要 `script-src 'unsafe-eval'`（已在 tauri.conf.json 配置）。
- release exe 内嵌资产是构建时快照：改 `src/` 后需重新构建；tauri-build 可能对整目录挪回不触发重烘，删 `target/release/.fingerprint/pet_shell-*` 强制重编。

## 工具脚本（pet_shell/tools/）

| 脚本 | 说明 |
| --- | --- |
| `fetch_vendor.py` | 下载 vendor 渲染库三件套（SHA256 校验；`npm run setup`，或随 `npm run dev/build` 自动执行） |
| `gen_motions.py` | 程序化生成 motion3.json（nod/shake/tilt/sway/idle_sway/coin_sway）并注册进 model3.json（智乃档案） |
| `gen_assets.py` | 生成 8 张占位情绪 PNG 与图标（静态兜底用，可选） |
| `start_all.ps1` / `stop_all.ps1` | 一键启停整套服务（WSL + AstrBot 容器 + 桌宠 exe）。**PowerShell 5.1 需 UTF-8 BOM 保存** |
| `start_all.vbs` / `stop_all.vbs` | wscript 隐藏调起 ps1，无控制台窗口，日常双击用 |
| `asr_server.py` | 语音输入 ASR 服务（whisper @ NPU，**封存待激活**，未接入启动项） |

动作实验室：`cd pet_shell/src && python -m http.server 8765` 后开 `http://localhost:8765/lab/`（`probe.html` 为运行时参数探针）。

## 发布流程（维护者）

1. 版本号三处同步：`pet_shell/package.json`、`pet_shell/src-tauri/Cargo.toml`（+ Cargo.lock）、`pet_shell/src-tauri/tauri.conf.json`。
2. 打 tag 推送：`git tag v0.x.y && git push origin v0.x.y` → GitHub Actions 自动构建并在 Release 建草稿（NSIS 安装包 + 便携 zip）。
3. 网页端检查草稿无误后 Publish。也可 Actions 页手动 dispatch（输入 tag 名）。
