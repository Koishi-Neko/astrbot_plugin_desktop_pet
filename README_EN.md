# astrbot_plugin_desktop_pet

[中文](README.md) | **English**

Turn [AstrBot](https://github.com/AstrBotDevs/AstrBot) into the brain of a Windows desktop Live2D pet. The pet is a webchat session inside AstrBot — persona, memory and history all live on the AstrBot side.

<!-- Demo screenshot: docs/assets/pet-demo.png (model + chat bubble + input bar) -->
![Demo](docs/assets/pet-demo.png)

## Features

- **Live2D desktop companion**: transparent, borderless, always-on-top window with emotion expressions, poke reactions, eye tracking, random idle motions and a long-idle performance
- **Full chat capability**: rides the AstrBot webchat pipeline — per-session persona, LivingMemory recall, platform history and logs are inherited automatically
- **Typewriter bubble + input bar**: double-click the model to chat; emotion tags in replies switch expressions
- **Japanese voice dubbing (optional)**: Style-Bert-VITS2 synthesis, sentence-by-sentence playback with mouth sync; QQ replies can carry dubbing too
- **Proactive chatter**: late-night reminders, welcome-back greetings, sedentary alerts; optional scene awareness comments on what you're doing (with a capture blocklist)
- **WebUI control page**: all server-side settings in one graphical page, applied instantly without restart

## Quick Start

Prerequisite: a deployed AstrBot v4 with access to its WebUI (default `http://localhost:6185`). See the [AstrBot docs](https://docs.astrbot.app/).

### 1. Install the plugin

WebUI → Plugins → Install → paste this repo URL: `https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet`.

> The webchat platform used by the plugin is built into AstrBot; nothing to add under "Platforms".

### 2. Create an API Key

WebUI → Settings → API Key → New, with **plugin, chat and file** scopes. Copy and save it.

### 3. Get the pet shell

Download the Windows build from [Releases](https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet/releases) (NSIS installer, or the portable zip — unzip and run).

> SmartScreen may warn "Windows protected your PC" because the exe is not code-signed; click "Run anyway". To build it yourself, see [Build from source](#build-from-source).

### 4. First-run configuration

The pet prompts you on first launch: **right-click the model → Settings**, enter the AstrBot address (`http://localhost:6185` is enough; the path is auto-completed) and your API Key, then hit "Test connection" — plugin / chat / file all green means done.

Double-click the model to open the input bar and start chatting.

### 5. (Optional) Pick a persona for the pet

Visit `http://localhost:6185/chat` directly in the address bar (this page is not in the sidebar): select the `desktop_pet` conversation → choose a persona in its settings. The conversation appears only after the pet has sent at least one message.

Advanced server-side settings (TTS, proactive chat, scene awareness, master identity, QQ dubbing) live in **WebUI → Plugins → astrbot_plugin_desktop_pet → Control Page**, applied on save.

## Advanced

<!-- Control page screenshot: docs/assets/control-page.png (control page + pet) -->
![WebUI control page and the pet](docs/assets/control-page.png)

### TTS Japanese dubbing (optional)

Voice replies require your own [Style-Bert-VITS2](https://github.com/litagin02/Style-Bert-VITS2) deployment and a voice model:

1. Deploy SBV2 and note its address (typically `http://172.18.0.1:5000` when AstrBot runs in Docker and SBV2 on the WSL host).
2. In the plugin control page "TTS" card, fill in the address, pick model/speaker/style from the dropdowns, enable and save.
3. Enable "Voice (Japanese dubbing)" in the pet shell settings panel.

Replies then become "Chinese bubble + Japanese sentence-by-sentence voice + mouth sync". Enabling "QQ Japanese dubbing" in the control page also attaches a voice message to the bot's QQ replies (falls back to plain text when SBV2 is offline).

> `2.7.0-JP-Extra` models only support Japanese; install a standard trilingual model if you need Chinese voice.

### Proactive chat & scene awareness

Configured in the plugin control page; the shell pulls changes within ~2 minutes:

- **Proactive chat**: late-night reminder (active past 23:00–02:00), welcome-back (after 30+ min away), sedentary alert (2h continuous activity). Global 45-minute throttle; never disturbs while fullscreen, typing or away.
- **Scene awareness**: periodically captures the **foreground window** and asks a vision model to comment on interesting content (game progress, funny pages); stays silent when there's nothing to say. **Screenshots are sent to your LLM provider.** Processes on the blocklist (WeChat/QQ/DingTalk/Office… by default) are never captured. Exclusive-fullscreen games can't be captured (borderless windowed works).

### Custom Live2D models

Drop any Cubism 3/4 model into `pet_shell/src/assets/live2d/chino/` with the entry file named `chino.model3.json` (rebuild required) to replace the default model. **Model file names and references inside model3.json must be ASCII-only.** Map emotions to your model's expression names in `EMOTION_EXPRESSIONS` in `pet_shell/src/app.js`.

The bundled default model **Momose Hiyori** is Live2D's official free sample (license: see `ReadMe.txt` in the model folder and the [official license page](https://www.live2d.com/zh-CHS/download/sample-data/)). Do not commit custom models to the repo (the folder is gitignored).

## Controls

| Action | Effect |
| --- | --- |
| Single-click model | Poke — random motion/expression |
| Double-click model | Toggle input bar; Enter to send |
| Drag model | Move window |
| Drag bottom-right handle | Resize window & model (remembered) |
| Right-click | Chat / click-through / settings / quit |
| `Ctrl+Shift+P` | Toggle click-through (only hotkey or tray can revert) |
| Pink dot on bubble / click bubble | Collapse bubble (auto-collapses 15s after replies) |
| Tray icon | Toggle click-through / quit |

## Build from source

Prerequisites: Node.js v20+, Rust stable (rustup), VS 2022 Build Tools, Python 3.

```bash
git clone https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet
cd astrbot_plugin_desktop_pet/pet_shell
npm install
npm run dev     # first run auto-downloads Live2D renderer libs (or: npm run setup)
npm run build   # standalone exe: src-tauri/target/release/pet_shell.exe
```

> The Live2D renderer libraries (pixi / pixi-live2d-display / Live2D Cubism Core) are not committed for licensing reasons; `tools/fetch_vendor.py` downloads them with SHA256 verification. If downloads fail, check your network connection and re-run `npm run setup`.
> Note: the debug exe produced via `npm run dev` shows a white screen when launched outside the CLI — use the `npm run build` artifact for standalone runs.

You can preset configuration via `pet_shell/src/config.local.json` (gitignored):

```json
{
  "base_url": "http://localhost:6185",
  "api_key": "your API Key"
}
```

## FAQ

- **Pet doesn't reply**: run "Test connection" in settings for per-scope results; check AstrBot logs for `[desktop_pet] web api registered`; the API Key needs plugin+chat scopes (plus file for scene awareness).
- **Live2D not showing (source build)**: make sure the three js files exist under `src/vendor/` (`npm run setup`); non-ASCII model paths or non-Cubism 3/4 models also fail to load.
- **No voice**: all three must be on — control page TTS switch with SBV2 reachable and model/speaker selected, plus the shell's "Voice" toggle.
- **Replies don't change expressions**: when the model omits emotion tags the pet falls back to "calm"; reinforce the format in the persona prompt.
- **Remote AstrBot**: just change the address in settings. The API Key is the credential — do not expose port 6185 to the public internet.

## Development docs

Architecture, API reference, SSE frame sequence, motion generation, debugging tips and the release process: [docs/dev.md](docs/dev.md) (Chinese).

## License

Code: MIT. The bundled Momose Hiyori model is Live2D's official free sample data, redistributed under its [license terms](https://www.live2d.com/zh-CHS/download/sample-data/). Renderer libraries (pixi.js / pixi-live2d-display / Live2D Cubism Core) are downloaded by the build script under their own licenses and are not committed.
