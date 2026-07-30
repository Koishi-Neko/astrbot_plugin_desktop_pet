# astrbot_plugin_desktop_pet

**中文** | [English](README_EN.md)

把 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 变成 Windows 桌面 Live2D 桌宠的大脑：桌宠是 AstrBot 里的一个 webchat 会话，人格、记忆、历史全都在 AstrBot 侧，换个皮就是同一个它。

<!-- 演示截图：docs/assets/pet-demo.png（模型 + 气泡 + 输入框同框） -->
![演示](docs/assets/pet-demo.png)

## 特性

- **Live2D 桌面立绘**：透明无边框置顶小窗，情绪表情、戳一戳互动、视线跟随、随机待机小动作、长待机演出
- **完整聊天能力**：走 AstrBot webchat 管道，会话级人格、平台历史与日志自动继承；安装记忆类插件（如 LivingMemory）时还会自动获得记忆召回/反思，不装也不影响任何功能
- **打字机气泡 + 输入框**：双击立绘开聊，回复带情绪标签自动切表情
- **日语配音（可选）**：Style-Bert-VITS2 合成，逐句播放 + 口型同步；QQ 侧 bot 回复也能附带配音
- **主动搭话**：深夜催睡、回来问候、久坐提醒；桌面感知可看着你的屏幕内容自然搭话（可配禁止抓取名单）
- **WebUI 控制页**：服务侧配置全部图形化，保存即生效，无需重启

## 快速开始

前提：已部署 AstrBot v4 并能打开 WebUI（默认 `http://localhost:6185`）。部署见 [AstrBot 官方文档](https://docs.astrbot.app/)。

### 1. 安装插件

WebUI → 插件 → 安装插件 → 填本仓库地址 `https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet`。

> 插件用到的 webchat 平台是 AstrBot 内置的，无需在「平台」配置里添加。

### 2. 创建 API Key

WebUI → 设置 → API Key → 新建，勾选 **plugin、chat、file** 三个 scope，复制保存。

### 3. 获取桌宠壳

到 [Releases](https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet/releases) 下载 Windows 版（NSIS 安装包，或便携 zip 解压即用）。

> 首次运行若出现 SmartScreen「Windows 已保护你的电脑」：exe 未购买代码签名所致，点「仍要运行」即可。想自己构建见下文[从源码构建](#从源码构建)。

### 4. 首次配置

首次启动桌宠会提示你配置：**右键立绘 → 设置**，填 AstrBot 地址（`http://localhost:6185` 即可，自动补全路径）和上一步的 API Key，点「测试连接」——plugin / chat / file 三项全绿即完成。

双击立绘打开输入框，开始聊天吧。

### 5.（可选）给桌宠选人格

**WebUI → 插件 → astrbot_plugin_desktop_pet → 控制页 → 桌宠人格**，下拉选择保存即可（不设置则跟随 AstrBot 默认人格）。桌宠尚未发言时会话不存在，先发一条消息再来设置。

服务侧进阶配置（TTS、主动对话、桌面感知、主人身份、QQ 配音）都在 **WebUI → 插件 → astrbot_plugin_desktop_pet → 控制页**，保存即生效。

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

### 主动对话与桌面感知

在插件控制页配置，壳端约 2 分钟内拉取生效：

- **主动对话**：深夜催睡（23-02 点连续活动）、回来问候（离开 30min 后回归）、久坐提醒（连续活动 2h）。全局节流 45 分钟，全屏/输入中/离开状态不打扰。
- **桌面感知**：按观察间隔抓取**前台窗口**画面发给视觉模型，看到值得评论的内容（游戏进展、有趣页面）自然搭话，没什么可说的就安静。**截图会发送给你的 LLM 提供商**；「禁止抓取名单」内进程（默认含微信/QQ/钉钉/Office 等）在前台时直接跳过、不截图。独占全屏游戏抓不到（无边框窗口化即可）。

### 自定义 Live2D 模型

任意 Cubism 3/4 模型放入 `pet_shell/src/assets/live2d/chino/` 并把入口命名为 `chino.model3.json`（重新构建后生效），即可替换默认模型。**模型文件名与 model3.json 内部引用需为全 ASCII**。情绪→表情映射在 `pet_shell/src/app.js` 的 `EMOTION_EXPRESSIONS` 中按你的模型实际表情名修改。

仓库内置官方免费示例模型**桃濑日和**（许可见模型目录 `ReadMe.txt` 与[官方许可页](https://www.live2d.com/zh-CHS/download/sample-data/)）。自定义模型涉及版权请勿入库分发（该目录已 gitignore）。

## 操作一览

| 操作 | 效果 |
| --- | --- |
| 单击立绘 | 戳一戳，随机动作/表情 |
| 双击立绘 | 开合输入框，回车发送 |
| 拖动立绘 | 移动窗口 |
| 拖动右下角半透明手柄 | 调整窗口与模型大小（自动记忆） |
| 右键 | 聊天 / 点击穿透 / 设置 / 退出 |
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

可在 `pet_shell/src/` 放 `config.local.json` 预置配置（已 gitignore）：

```json
{
  "base_url": "http://localhost:6185",
  "api_key": "你的 API Key"
}
```

## 常见问题

- **桌宠无回复**：设置面板点「测试连接」看分项结果；确认 AstrBot 日志有 `[desktop_pet] web api registered`；API Key 需 plugin+chat scope（桌面感知还要 file scope）。
- **Live2D 不显示（源码运行）**：确认 `src/vendor/` 下三个 js 已下载（`npm run setup`）；模型路径含非 ASCII 字符或模型非 Cubism 3/4 也会加载失败。
- **没有语音**：控制页 TTS 开关、SBV2 状态「可达」、模型/说话人已选，壳端设置「语音」开关——三处都要开。
- **回复不切表情**：模型没按格式输出情绪标签时用「平静」兜底，属正常；可在人格 prompt 里强化格式要求。
- **远端 AstrBot**：设置里把地址改成对应主机即可。API Key 即鉴权，请勿把 6185 端口暴露到公网。

## 开发文档

架构、接口一览、SSE 帧序、动作生成、调试技巧、发布流程见 [docs/dev.md](docs/dev.md)。

## 许可

代码 MIT。内置模型桃濑日和为 Live2D 官方免费示例数据，按其[许可条款](https://www.live2d.com/zh-CHS/download/sample-data/)随仓库分发。渲染库（pixi.js / pixi-live2d-display / Live2D Cubism Core）按各自许可由构建脚本下载，不入库。
