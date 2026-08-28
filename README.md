# astrbot_plugin_desktop_pet

**把 AstrBot 变成 Windows 桌面 Live2D 桌宠** —— 也可以**完全不需要 AstrBot**：独立模式直连任意 OpenAI 兼容大模型，5 分钟跑起来。

[中文](README.md) | [English](README_EN.md)

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D6.svg)
[![Release](https://img.shields.io/github/v/release/Koishi-Neko/astrbot_plugin_desktop_pet.svg)](https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/Koishi-Neko/astrbot_plugin_desktop_pet/release.yml?label=CI)](https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet/actions)

<!-- 演示截图：docs/assets/pet-demo.png（模型 + 气泡 + 输入框同框） -->
![演示](docs/assets/pet-demo.png)

## 目录

- [特性](#特性)
- [快速开始](#快速开始)
- [独立模式](#独立模式)
- [进阶玩法](#进阶玩法)
- [操作一览](#操作一览)
- [从源码构建](#从源码构建)
- [常见问题](#常见问题)
- [开发文档](#开发文档)
- [许可](#许可)

## 特性

一只住在你 Windows 桌面上的 Live2D 桌宠，两种运行方式：

| | AstrBot 模式（完整版） | 独立模式（轻量版） |
| --- | --- | --- |
| 大脑 | AstrBot（webchat 管道） | 任意 OpenAI 兼容大模型（云端 / 本地 Ollama） |
| 人格 / 历史 | 会话级人格 + 平台历史，可装记忆插件（LivingMemory） | 设置面板填人格文本，会话内记忆 |
| 日语配音 | SBV2 合成，逐句播放 + 口型同步 | 同左（TTS 地址可配） |
| 需要部署 | AstrBot（Docker / 本机） | 什么都不用，5 分钟上手 |

- **Live2D 桌面立绘**：透明无边框置顶小窗，情绪表情、戳一戳互动、视线跟随、随机待机小动作、长待机演出
- **多模型热切换**：内置桃濑日和 + 智乃/智乃Q版（本地），右键菜单即时切换并记忆；也可以把任意 Cubism 3~5 模型**拖拽上传**即用（支持文件夹 / .model3.json / .zip）
- **打字机气泡 + 输入框**：回复带【情绪】标签自动切表情，中文气泡 + 可选日语逐句配音
- **语音输入**：输入栏麦克风按钮，点击录音 → 本地 ASR（whisper @ Intel NPU）识别 → 自动发送；开关与识别服务地址在控制页配置
- **主动搭话**：深夜催睡、回来问候、久坐提醒；桌面感知会看着你的屏幕内容自然搭话（可配禁止抓取名单，微信/QQ/Office 等默认不抓）
- **WebUI 控制页**：AstrBot 模式下服务侧配置全部图形化，保存即生效

## 快速开始

### 路线 A：5 分钟体验（无 AstrBot）

1. 到 [Releases](https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet/releases) 下载 Windows 便携版（zip 解压即用，或 NSIS 安装包）。
   > 首次运行若出现 SmartScreen「Windows 已保护你的电脑」：exe 未购买代码签名所致，点「仍要运行」即可。
2. 右键立绘 → **设置 → 运行模式 → 独立模式**，填三项：
   - 模型地址（OpenAI 兼容，如 `https://api.deepseek.com/v1`；本地 [Ollama](https://ollama.com) 填 `http://localhost:11434/v1`）
   - 模型 API Key（本地 Ollama 可随便填）
   - 对话模型名（如 `deepseek-chat`）
3. 点「测试连接」看到模型回复即成功，双击立绘开聊。

日语配音（可选）：需要本机部署 [Style-Bert-VITS2](https://github.com/litagin02/Style-Bert-VITS2)，见[独立模式](#独立模式)末尾说明。

### 路线 B：AstrBot 完整玩法（人格 / 记忆 / QQ）

前提：已部署 AstrBot v4 并能打开 WebUI（默认 `http://localhost:6185`）。部署见 [AstrBot 官方文档](https://docs.astrbot.app/)。

1. **安装插件**：WebUI → 插件 → 安装插件 → 填本仓库地址 `https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet`。
   > 插件用到的 webchat 平台是 AstrBot 内置的，无需在「平台」配置里添加。
2. **创建 API Key**：WebUI → 设置 → API Key → 新建，勾选 **plugin、chat、file** 三个 scope，复制保存。
3. **获取桌宠壳**：[Releases](https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet/releases) 下载 Windows 版（NSIS 安装包，或便携 zip）。
4. **首次配置**：右键立绘 → 设置，填 AstrBot 地址（`http://localhost:6185` 即可，自动补全路径）和 API Key，点「测试连接」——plugin / chat / file 三项全绿即完成。双击立绘开始聊天。
5. **（可选）给桌宠选人格**：WebUI → 插件 → astrbot_plugin_desktop_pet → 控制页 → 桌宠人格，下拉选择保存（不设置则跟随 AstrBot 默认人格）。桌宠尚未发言时会话不存在，先发一条消息再来设置。

服务侧进阶配置（TTS、主动对话、桌面感知、主人身份、QQ 配音）都在 **WebUI → 插件 → astrbot_plugin_desktop_pet → 控制页**，保存即生效。

## 独立模式

不想部署 AstrBot 也能让桌宠工作：**设置面板 → 运行模式 → 独立模式**，桌宠改为直连任意 OpenAI 兼容的大模型 API（云端如 DeepSeek / Kimi，或本地 Ollama），对话、情绪表情、日语配音、主动对话/桌面感知全部可用，唯一区别是**没有长期记忆**（LivingMemory 等 AstrBot 插件能力，仅有本次运行的会话历史）。

| 能力 | AstrBot 模式 | 独立模式 |
| --- | --- | --- |
| 聊天 / 情绪标签 / 表情切换 | ✅ | ✅ |
| 日语配音（需本机部署 SBV2） | ✅ | ✅（TTS 地址可配） |
| 语音输入（需本地 ASR 服务） | ✅（开关/地址控制页配置） | ✅（默认 5055，config.local.json 可配） |
| 主动对话 / 桌面感知 | ✅ | ✅（截图内联直传，视觉模型=对话模型或单独指定） |
| 会话人格 | WebUI 控制页 | 设置面板「人格」文本（留空用内置默认） |
| 长期记忆（LivingMemory） | ✅（可选插件） | ❌（V1 无） |
| 配置入口 | WebUI 控制页 | 设置面板 / `config.local.json` |
| 状态监控控制页 | ✅ | ❌ |

配置（设置面板「独立模式」分区，或 `config.local.json` 的 `standalone` 节）：

```json
{
  "mode": "standalone",
  "standalone": {
    "llm_base_url": "https://api.deepseek.com/v1",
    "llm_api_key": "你的模型 API Key",
    "llm_model": "deepseek-chat",
    "persona": "可选，覆盖内置默认人格",
    "tts_url": "http://localhost:5000",
    "scene_model": "可选，桌面感知视觉模型，留空=用对话模型"
  },
  "asr": {
    "url": "http://127.0.0.1:5055"
  }
}
```

> 切换回 AstrBot 模式：设置面板把模式改回去即可，两种模式互不影响、随时切换。

**独立模式日语配音**：需要一台本地 Style-Bert-VITS2（SBV2）服务。本机（WSL 部署）SBV2 监听 `127.0.0.1:5000`，经 WSL2 localhost 转发，Windows 直接填 `http://localhost:5000` 即可合成（2026-08-03 全栈去容器化后已无回环桥）；TTS 地址留空则静默降级为纯文字气泡。

## 进阶玩法

<!-- 控制页截图：docs/assets/control-page.png（控制页 + 桌宠同框） -->
![WebUI 控制页与桌宠](docs/assets/control-page.png)

### TTS 日语配音（可选）

桌宠回复附带日语配音需要自行部署 [Style-Bert-VITS2](https://github.com/litagin02/Style-Bert-VITS2) 并准备一个声线模型：

1. 部署 SBV2，记下服务地址（AstrBot 在 Docker、SBV2 在 WSL 宿主时通常为 `http://172.18.0.1:5000`）。
2. 插件控制页「TTS 语音配置」填地址，下拉选择模型/说话人/风格，开启开关，保存。
3. 桌宠壳设置面板打开「语音（日语配音）」。

之后回复变为「中文气泡 + 日语逐句配音 + 口型同步」。开启控制页的「QQ 日语配音」后，bot 在 QQ 群聊/私聊的回复也会附带一条配音语音（SBV2 离线时自动降级为纯文字）。

> `2.7.0-JP-Extra` 系模型只支持日语；需要中文配音请另装标准三语模型。

### 语音输入（可选，本地 ASR）

打开输入栏，点左侧圆形麦克风按钮开始录音（红色呼吸动画），再点一次或说话后静音 1.2 秒自动停止，识别结果回填输入框，0.5 秒后自动发送（点输入框/键盘可取消）。录音中暂停视线跟随与待机动作。

- **识别引擎**：本地 whisper（OpenVINO，默认跑 Intel NPU），识别文本自动发送后走完整对话管线（人格/记忆/情绪/日语配音零改动）。
- **服务部署**：ASR 服务为 Windows 本地进程（`tools/asr_server.py`，FastAPI `http://127.0.0.1:5055`），需要 Python 3.12 + `openvino-genai` 与 whisper 模型（`whisper-large-v3-turbo-fp16-ov`，OpenVINO 官方转换版）。首次启动 NPU 模型编译约 4 分钟，期间麦克风按钮灰态，就绪后自动恢复。
- **开关与地址**：AstrBot 模式在插件控制页「语音输入」卡配置（启用开关 + 识别服务地址），壳端约 2 分钟内拉取生效；独立模式默认 `http://127.0.0.1:5055`，可用 `config.local.json` 的 `asr` 节覆盖。
- **默认停止**：ASR 服务不随桌宠自启，需要时运行 `pet_shell/tools/start_asr.ps1`（或双击 `start_asr.vbs`，幂等可重复跑；日志 `asr-npu\asr.log`）。`stop_all.ps1` 会一并停止。
- 识别语言锁定中文（英文语音也能正确转写）；隐私：音频仅在本机处理，不上传。

### 主动对话与桌面感知

在插件控制页配置（独立模式在 `config.local.json` 的 `proactive` 节），壳端约 2 分钟内拉取生效：

- **主动对话**：深夜催睡（23-02 点连续活动）、回来问候（离开 30min 后回归）、久坐提醒（连续活动 2h）。全局节流 45 分钟，全屏/输入中/离开状态不打扰。
- **桌面感知**：按观察间隔抓取**前台窗口**画面发给视觉模型，看到值得评论的内容（游戏进展、有趣页面）自然搭话，没什么可说的就安静。**截图会发送给你的 LLM 提供商**；视觉模型可在控制页的可用列表提示中选择，留空则跟随对话模型；「禁止抓取名单」内进程（默认含微信/QQ/钉钉/Office 等）在前台时直接跳过、不截图。独占全屏游戏抓不到（无边框窗口化即可）。

### 自定义 Live2D 模型

任意 Cubism 3/4 模型放入 `pet_shell/src/assets/live2d/chino/` 并把入口命名为 `chino.model3.json`（重新构建后生效），即可替换默认模型。**模型文件名与 model3.json 内部引用需为全 ASCII**。情绪→表情映射在 `pet_shell/src/app.js` 的 `EMOTION_EXPRESSIONS` 中按你的模型实际表情名修改。

多个模型可热切换：在 `app.js` 的 `MODELS` 注册表与 `MODEL_PROFILES` 能力档案中各加一条（资产放入 `assets/live2d/<key>/`），右键菜单「切换模型」即出现对应项，选择即时生效并记忆。

也可以**直接上传模型使用**（无需改代码）：把模型文件夹或 zip 拖到桌宠身上，或在设置面板「上传 Live2D 模型」填路径——支持文件夹 / `.model3.json` / `.zip`（Cubism 3~5 的 moc3），上传后自动切换并记忆，在「切换模型」子菜单中可随时切换或点 `×` 卸载。上传的模型存放在 `%LOCALAPPDATA%\com.astrbotpet.shell\models\`，经壳内 petmodel 协议运行时加载。

仓库内置官方免费示例模型**桃濑日和**（许可见模型目录 `ReadMe.txt` 与[官方许可页](https://www.live2d.com/zh-CHS/download/sample-data/)）。自定义模型涉及版权请勿入库分发（该目录已 gitignore）。

## 操作一览

| 操作 | 效果 |
| --- | --- |
| 单击立绘 | 戳一戳，随机动作/表情 |
| 双击立绘 | 开合输入框，回车发送 |
| 左下箭头圆钮 | 开合输入框（与气泡粉点同方位居下） |
| 输入栏左侧圆形麦克风 | 语音输入：点击开始/结束录音，识别后自动发送（灰态=服务未就绪或已关闭） |
| 拖动立绘 | 移动窗口 |
| 拖动右下角半透明手柄 | 调整窗口与模型大小（自动记忆） |
| 右键 | 聊天 / 切换模型 / 点击穿透 / 设置 / 退出 |
| `Ctrl+Shift+P` | 切换点击穿透（穿透后只能用快捷键或托盘切回） |
| 气泡头部粉点 / 点击气泡 | 收起气泡（回复结束 15s 自动收起） |
| 托盘图标 | 切换穿透 / 退出 |

## 从源码构建

前提：Node.js v20+、Rust stable（rustup）、VS 2022 Build Tools、Python 3。

```bash
git clone https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet
cd astrbot_plugin_desktop_pet/pet_shell
npm install
npm run dev     # 首次自动下载 Live2D 渲染库（也可单独跑 npm run setup）
npm run build   # 产出独立 exe：src-tauri/target/release/pet_shell.exe
```

> Live2D 渲染库（pixi / pixi-live2d-display / Live2D Cubism Core）因许可原因不入库，由 `tools/fetch_vendor.py` 按 SHA256 校验下载。下载失败请检查网络连接后重跑 `npm run setup`。
> 注意：`npm run dev` 产出的 debug exe 脱离 CLI 直接启动会白屏，独立运行请用 `npm run build` 的产物。

可在 `pet_shell/src/` 放 `config.local.json` 预置配置（已 gitignore；release 构建会内嵌此文件，构建机请勿放私钥，分发用户建议用设置面板配置）：

```json
{
  "mode": "astrbot",
  "base_url": "http://localhost:6185",
  "api_key": "你的 API Key",
  "standalone": {
    "llm_base_url": "https://api.deepseek.com/v1",
    "llm_api_key": "你的模型 API Key",
    "llm_model": "deepseek-chat",
    "tts_url": "http://localhost:5000"
  },
  "asr": {
    "url": "http://127.0.0.1:5055"
  }
}
```

## 常见问题

- **桌宠无回复（AstrBot 模式）**：设置面板点「测试连接」看分项结果；确认 AstrBot 日志有 `[desktop_pet] web api registered`；API Key 需 plugin+chat scope（桌面感知还要 file scope）。
- **桌宠无回复（独立模式）**：设置面板切到独立模式点「测试连接」；模型地址以 `/v1` 结尾最稳（根地址会自动补）；本地 Ollama 的 API Key 可随便填但不能为空。
- **独立模式没有长期记忆**：这是 V1 设计如此（会话内历史仍在）；需要记忆请用 AstrBot 模式 + LivingMemory。
- **Live2D 不显示（源码运行）**：确认 `src/vendor/` 下三个 js 已下载（`npm run setup`）；模型路径含非 ASCII 字符或模型非 Cubism 3/4 也会加载失败。
- **没有语音**：控制页 TTS 开关、SBV2 状态「可达」、模型/说话人已选，壳端设置「语音」开关——三处都要开（独立模式检查设置面板 TTS 地址）。
- **麦克风按钮灰态/点了没反应**：语音输入开关未开（控制页「语音输入」卡）或 ASR 服务未就绪（首次启动加载约 4 分钟，运行 `start_asr.vbs` 拉起；服务异常时悬停按钮看提示）。
- **语音识别结果不对**：默认锁定中文识别；识别是纯本地 whisper，口音/环境噪音影响大时可试着说慢一点，或在控制页确认识别服务地址指向的正是本地 ASR 服务。
- **回复不切表情**：模型没按格式输出情绪标签时用「平静」兜底，属正常；可在人格 prompt 里强化格式要求。
- **远端 AstrBot**：设置里把地址改成对应主机即可。API Key 即鉴权，请勿把 6185 端口暴露到公网。

## 开发文档

架构、接口一览、SSE 帧序、动作生成、调试技巧、发布流程见 [docs/dev.md](docs/dev.md)。

## 许可

代码 MIT。内置模型桃濑日和为 Live2D 官方免费示例数据，按其[许可条款](https://www.live2d.com/zh-CHS/download/sample-data/)随仓库分发。渲染库（pixi.js / pixi-live2d-display / Live2D Cubism Core）按各自许可由构建脚本下载，不入库。
