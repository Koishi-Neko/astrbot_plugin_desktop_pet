# astrbot_plugin_desktop_pet

**Turn AstrBot into your personal Windows Live2D desktop pet** — or **just ditch AstrBot entirely**: hook it up directly to any OpenAI-compatible LLM via Standalone Mode and get a living pet on your screen in 5 minutes.

[中文](README.md) | [English](README_EN.md)

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D6.svg)
[![Release](https://img.shields.io/github/v/release/Koishi-Neko/astrbot_plugin_desktop_pet.svg)](https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/Koishi-Neko/astrbot_plugin_desktop_pet/release.yml?label=CI)](https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet/actions)

<!-- Demo screenshot: docs/assets/pet-demo.png (model + bubble + input box) -->
![Demo](docs/assets/pet-demo.png)

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Standalone Mode](#standalone-mode)
- [Advanced](#advanced)
- [Controls](#controls)
- [Build from source](#build-from-source)
- [FAQ](#faq)
- [Development Docs](#development-docs)
- [License](#license)

## Features

A Live2D companion living right on your Windows desktop. We give you two ways to run it:

| | AstrBot Mode (The Full Experience) | Standalone Mode (Lightweight & Lazy) |
| --- | --- | --- |
| The Brain | AstrBot (via webchat pipeline) | Any OpenAI-compatible LLM (Cloud API / local Ollama) |
| Persona & History | Session-level persona + platform history, plus built-in long-term memory (vector recall + reflection) | Toss a persona prompt in the settings panel. Remembers the current session. |
| Japanese Voice | SBV2 synthesis, sentence-by-sentence playback + lip sync | Same deal (just give it a TTS URL) |
| Setup Required | Get AstrBot running (Docker / native) | Literally nothing, up and running in 5 minutes |

- **Live2D Desktop Mascot**: A borderless, transparent, always-on-top little window. It changes expressions, reacts when you poke it, follows your cursor, does random idle animations, and even has special long-idle performances.
- **Hot-swap Multiple Models**: Ships out-of-the-box with Momose Hiyori + Chino/Chino Chibi (local). Swap instantly from the right-click menu and it'll remember your choice. Got your own Cubism 3~5 models? Just **drag and drop** to upload them (supports folders, `.model3.json`, or `.zip`).
- **Typewriter Bubble + Input Box**: If the reply has an [emotion] tag, the expression automatically changes. Chinese text bubbles, plus optional Japanese sentence-by-sentence dubbing if you're into that.
- **Voice Input**: Hit the mic icon on the input bar, speak → local ASR (whisper @ Intel NPU) transcribes it → auto-sends. Save your keystrokes. Switches and server addresses are configurable in the control page.
- **Proactive Chat**: Nags you to sleep late at night, welcomes you back if you step away, and reminds you to stretch if you've been sitting too long. It also subtly peeks at your desktop and comments on interesting stuff (don't worry, there's a blocklist—WeChat, QQ, and Office are ignored by default).
- **Built-in Long-term Memory** (AstrBot mode): The pet remembers things on its own—no extra memory plugin needed. An LLM periodically reflects chats into memories, recalls them by vector similarity when relevant, and writes a little pet diary every night. You can browse, edit, and test recall on the control page, and if you were using LivingMemory before, there's a one-click import for your old memories.
- **WebUI Control Page**: If you're running AstrBot mode, all the server-side configs have a slick GUI. Save and it takes effect instantly.

## Quick Start
<a id="quick-start"></a>

### Route A: The 5-Minute Speedrun (No AstrBot)

1. Head to [Releases](https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet/releases) and grab the Windows portable version (unzip and run, or use the NSIS installer).
   > If Windows SmartScreen pops up complaining "Windows protected your PC" on first run: don't panic, it's just because I didn't buy a code signing cert for the exe. Click "Run anyway".
2. Right-click the pet → **Settings → Operating Mode → Standalone Mode**, and fill in these three:
   - Model API URL (Anything OpenAI-compatible, like `https://api.deepseek.com/v1`; if running local [Ollama](https://ollama.com), use `http://localhost:11434/v1`)
   - Model API Key (If it's local Ollama, just smash your keyboard to enter a dummy key)
   - Chat Model Name (e.g., `deepseek-chat`)
3. Hit "Test connection". If the model talks back, you're golden. Double-click the pet to start chatting.

Japanese Voice (Optional): Want it to speak? You'll need to run [Style-Bert-VITS2](https://github.com/litagin02/Style-Bert-VITS2) locally. See the notes under [Standalone Mode](#standalone-mode).

### Route B: The AstrBot Ultimate Form (Persona / Memory / QQ Integration)

Prerequisite: You already have AstrBot v4 running and can access its WebUI (default is `http://localhost:6185`). Need help? Check the [official AstrBot docs](https://docs.astrbot.app/).

1. **Install the plugin**: WebUI → Plugins → Install Plugin → Paste this repo's URL: `https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet`.
   > Note: The plugin uses AstrBot's built-in webchat platform. You do NOT need to configure anything in the "Platforms" tab.
2. **Generate an API Key**: WebUI → Settings → API Key → New. Check all three scopes: **plugin, chat, file**, then copy and save it.
3. **Get the Pet Shell**: Download the Windows version from [Releases](https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet/releases) (NSIS installer or portable zip).
4. **First-time Setup**: Right-click the pet → Settings. Plug in your AstrBot address (just `http://localhost:6185` is fine, it auto-completes the path) and paste your API Key. Hit "Test connection" — once plugin / chat / file all light up green, you're set. Double-click to chat.
5. **(Optional) Pick a Persona**: WebUI → Plugins → astrbot_plugin_desktop_pet → Control Page → Pet Persona. Pick one from the dropdown and save (leaves it blank to inherit AstrBot's default). Heads up: if the pet hasn't spoken yet, the session doesn't exist, so send it one message first before setting this.

All the fancy server-side tweaks (TTS, proactive chat, scene awareness, master identity, QQ voice) live in **WebUI → Plugins → astrbot_plugin_desktop_pet → Control Page**. Save it and it works right away.

## Standalone Mode
<a id="standalone-mode"></a>

Too lazy to deploy AstrBot? The pet can fly solo: **Settings Panel → Operating Mode → Standalone Mode**. It bypasses AstrBot entirely and hooks directly into any OpenAI-compatible API (like DeepSeek / Kimi in the cloud, or a local Ollama). Chatting, expression changes, Japanese dubbing, proactive chat, and scene awareness all work perfectly. The only trade-off? **It has gold-fish memory** (long-term memory is a built-in plugin ability in AstrBot mode; standalone only remembers the current session).

| Capability | AstrBot Mode | Standalone Mode |
| --- | --- | --- |
| Chat / Emotion Tags / Expressions | ✅ | ✅ |
| Japanese Dubbing (Requires local SBV2) | ✅ | ✅ (Just drop in the TTS URL) |
| Voice Input (Requires local ASR service) | ✅ (Configure switch/URL in Control Page) | ✅ (Defaults to 15055, tweakable in config.local.json) |
| Proactive Chat / Scene Awareness | ✅ | ✅ (Screenshots sent inline; vision model = chat model or manually specified) |
| Session Persona | WebUI Control Page | Direct text in Settings Panel "Persona" (leave blank for built-in default) |
| Long-term Memory | ✅ (Built into the plugin: vector recall + reflection + diary) | ❌ (Not in V1, sorry) |
| Where's the config? | WebUI Control Page | Settings Panel / `config.local.json` |
| Status Dashboard | ✅ | ❌ |

To configure it, head to the "Standalone Mode" section in the settings panel, or manually edit the `standalone` block in `config.local.json`:

```json
{
  "mode": "standalone",
  "standalone": {
    "llm_base_url": "https://api.deepseek.com/v1",
    "llm_api_key": "Your Model API Key",
    "llm_model": "deepseek-chat",
    "persona": "Optional, overrides the built-in default persona",
    "tts_url": "http://localhost:5000",
    "scene_model": "Optional, vision model for scene awareness; leave blank to use the chat model"
  },
  "asr": {
    "url": "http://127.0.0.1:15055"
  }
}
```

> Wanna go back to AstrBot mode? Just flip the dropdown back in the settings panel. The two modes operate independently; you can switch whenever.

**About Standalone Japanese Dubbing**: You need to spin up a local Style-Bert-VITS2 (SBV2) instance. If your local (WSL-deployed) SBV2 is listening on `127.0.0.1:5000`, WSL2 forwards localhost natively—just type `http://localhost:5000` in Windows and it'll synthesize flawlessly (since we ripped out the containerized backend on 2026-08-03, network bridging is no longer a headache). Leave the TTS URL blank, and it silently degrades to a quiet text bubble.

## Advanced Playbook
<a id="advanced"></a>

<!-- 控制页截图：docs/assets/control-page.png（控制页 + 桌宠同框） -->
![WebUI Control Page and Pet](docs/assets/control-page.png)

### TTS Japanese Voice (Optional, but highly recommended)

To get Japanese audio alongside the text replies, you gotta deploy [Style-Bert-VITS2](https://github.com/litagin02/Style-Bert-VITS2) yourself and grab a voice model:

1. Spin up SBV2 and note the URL (usually `http://172.18.0.1:5000` if AstrBot is in Docker and SBV2 is on your WSL host).
2. In the plugin control page under the "TTS" card, paste the URL, pick your model/speaker/style from the dropdowns, flip the switch, and save.
3. Open the pet shell settings panel and toggle on "Voice (Japanese dubbing)".

Boom, replies are now "Chinese bubble + sentence-by-sentence Japanese voice + lip sync". If you turn on "QQ Japanese dubbing" in the control page, the bot will also shoot a voice message into QQ groups/DMs when it replies (if SBV2 crashes, it gracefully degrades to plain text).

> Heads up: `2.7.0-JP-Extra` models only speak Japanese. If you want Chinese voice, find yourself a standard trilingual model.

### Voice Input (Optional, Local ASR)

Pop open the input bar, smack the round mic button on the left to start recording (it breathes red), then click again or just stay quiet for 1.2 seconds to stop. The transcribed text jumps into the input box and auto-sends half a second later (click the box or press a key if you need to bail). Gaze-following and idle animations take a break while you're recording.

- **Under the Hood**: Local whisper (OpenVINO, running on your Intel NPU by default). The recognized text goes through the exact same chat pipeline as typing—persona, memory, emotions, Japanese dubbing, all intact.
- **The Service**: It's a Windows-side process (`tools/asr_server.py`, a FastAPI server at `http://127.0.0.1:15055`). You'll need Python 3.12 + `openvino-genai` and a whisper model (`whisper-large-v3-turbo-fp16-ov`, the official OpenVINO export). The first time it boots, the NPU compile takes about 4 minutes; the mic button will stay greyed out and automatically come alive when it's ready.
- **Toggle & Config**: In AstrBot mode, set the switch and URL in the control page "Voice input" card (the shell pulls the update in ~2 mins). In standalone mode, it defaults to `http://127.0.0.1:15055`, which you can override in the `asr` block of `config.local.json`.
- **Hotwords / Accents**: You can chuck an `initial_prompt` into the settings panel to help it catch weird proper nouns. If the NPU static-shape pipeline chokes on it, the server smartly retries without the prompt, so your voice input never straight-up breaks.
- **Manual Start**: To save your boot times, the ASR service does NOT auto-start. When you want to use it, run `pet_shell/tools/start_asr.ps1` (or just double-click `start_asr.vbs`, it's idempotent; check `asr-npu\asr.log` for logs). Running `stop_all.ps1` kills it.
- Recognition is strictly locked to Chinese for now (though if you speak English, it surprisingly transcribes it fine anyway). Privacy check: audio is processed entirely on your machine, nothing gets uploaded.

### Proactive Chat & Desktop Snooping (Scene Awareness)

Configure these in the plugin control page (or the `proactive` block of `config.local.json` if in standalone mode). Changes sync to the shell in about 2 minutes:

- **Proactive Chat**: Late-night scolding (active between 23:00–02:00), welcome-back greetings (away for 30+ mins), and sedentary warnings (2h of continuous activity). It has a global 45-minute throttle, and if you're in a fullscreen game, typing, or AFK, it knows better than to bother you.
- **Scene Awareness**: Every so often, it grabs a screenshot of your **topmost foreground window** and hands it to a vision model. If it sees something cool (like your game progress or a funny meme), it strikes up a conversation. If there's nothing interesting, it keeps quiet. **Note: These screenshots are sent to your LLM provider.** You can pick the vision model from a dropdown in the control page, or leave it blank to just use the chat model. There's a blocklist (WeChat, QQ, DingTalk, Office, etc., by default)—if a blocked app is in focus, it skips the screenshot. Also, exclusive fullscreen games can't be captured; play in borderless windowed mode if you want it to watch.
- **Intent Perception (On-demand Look)**: Type or tell the pet "看看我的屏幕" (Look at my screen) or "what am I doing", and it instantly snaps the foreground window and attaches it to your message. Since the pet itself has focus when you ask, it's smart enough to grab the topmost window *beneath* itself. This is completely separate from the automatic snooping—you can use this even if auto-awareness is off. It respects the same blocklist; if you try to make it look at a blocked app, it'll refuse and verbally tell you it's not allowed. You can tweak the trigger keywords in the control page (one per line; negations like "don't look" won't trigger it). If it fails to capture (exclusive fullscreen, DRM, minimized), it'll tell you honestly. Voice commands require the ASR service; typed commands work out of the box.

### Swapping in Your Custom Live2D Waifu/Husbando

Got a Cubism 3 or 4 model? Toss it into `pet_shell/src/assets/live2d/chino/` and rename the entry file to `chino.model3.json` (you'll need to rebuild for this to take effect). **Crucial: The model filenames and internal references in model3.json MUST be ASCII-only.** To get the emotion tags mapping correctly, tweak `EMOTION_EXPRESSIONS` in `pet_shell/src/app.js` to match your model's actual expression names.

Want to keep a harem and hot-swap them? Add an entry to the `MODELS` registry and the `MODEL_PROFILES` capability map in `app.js` (put the assets under `assets/live2d/<key>/`). It'll instantly show up in the right-click "Switch model" menu, and it remembers your pick.

Too lazy to edit code? You can **upload models directly**: Just drag a model folder or a zip file right onto the pet, or punch the path into "Upload Live2D model" in the settings. Folders, `.model3.json`, and `.zip` (Cubism 3~5 moc3) are all supported. It switches immediately, remembers the new model across reboots, and you can uninstall it via the `×` button in the "Switch model" submenu. Uploaded models live in `%LOCALAPPDATA%\com.astrbotpet.shell\models\` and load via the shell's custom `petmodel` protocol.

I've bundled the official free sample model **Momose Hiyori** in the repo (license details are in the model's `ReadMe.txt` and the [official license page](https://www.live2d.com/zh-CHS/download/sample-data/)). If you use custom models, watch out for copyrights and don't commit them (the folder is gitignored).

> ⚠️ Let me be clear: to avoid copyright drama, this release contains ZERO model files other than the official, freely distributable Momose Hiyori.

## Controls
<a id="controls"></a>

| What you do | What it does |
| --- | --- |
| Single-click the pet | Poke it — triggers a random motion or expression |
| Double-click the pet | Toggles the input bar; hit Enter to send your message |
| Click the bottom-left arrow button | Toggles the input bar (it's right under the pink dot on the bubble) |
| Click the round mic button on the input bar | Voice input: click to start/stop recording, auto-sends once transcribed (if it's grey, the service isn't ready or it's dead) |
| Drag the pet | Moves the window around |
| Drag the translucent handle on the bottom-right | Resizes the window & model (it remembers the size) |
| Right-click the pet | Chat / Switch model / Click-through / Settings / Quit |
| Press `Ctrl+Shift+P` | Toggles click-through (if it's transparent to clicks, you can only revert via hotkey or the tray icon) |
| Click the pink dot on the bubble / Click the bubble itself | Collapses the text bubble (it also auto-hides 15s after a reply) |
| Right-click the tray icon | Recover the pet: Toggle click-through / Quit |

## Build from Source (For the Hardcore)
<a id="build-from-source"></a>

Prerequisites: You need Node.js v20+, a stable Rust toolchain (rustup), VS 2022 Build Tools, and Python 3.

```bash
git clone https://github.com/Koishi-Neko/astrbot_plugin_desktop_pet
cd astrbot_plugin_desktop_pet/pet_shell
npm install
npm run dev     # The first run auto-downloads the Live2D renderer libs (or run npm run setup manually)
npm run build   # Spits out the standalone exe: src-tauri/target/release/pet_shell.exe
```

> Why aren't the Live2D renderer libraries (pixi / pixi-live2d-display / Live2D Cubism Core) in the repo? Licensing. `tools/fetch_vendor.py` downloads them and checks the SHA256 hashes. If the download fails, check your internet (or VPN) and re-run `npm run setup`.
> Note: If you run the debug exe produced by `npm run dev` outside of the CLI, you'll just get a white screen. For a standalone executable, always use the artifact from `npm run build`.

If you want to bake in some config for users, drop a `config.local.json` in `pet_shell/src/` (it's gitignored; release builds **embed** this file, so **do NOT commit your private keys on a build machine**. It's better to let end-users configure it via the settings panel):

```json
{
  "mode": "astrbot",
  "base_url": "http://localhost:6185",
  "api_key": "Your API Key",
  "standalone": {
    "llm_base_url": "https://api.deepseek.com/v1",
    "llm_api_key": "Your Model API Key",
    "llm_model": "deepseek-chat",
    "tts_url": "http://localhost:5000"
  },
  "asr": {
    "url": "http://127.0.0.1:15055"
  }
}
```

## FAQ (When Things Break)
<a id="faq"></a>

- **The pet is playing dead (AstrBot mode)**: Open the settings panel and click "Test connection" to see which check fails. Look at your AstrBot logs for `[desktop_pet] web api registered`. Did you give your API Key both plugin and chat scopes? (Add the file scope too if you want scene awareness).
- **The pet is playing dead (Standalone mode)**: Switch to standalone in settings and click "Test connection". Make sure your base URL ends in `/v1` (it tries to auto-complete bare roots, but play it safe). If you're on local Ollama, the API Key field can't be empty—just type anything.
- **Standalone mode has goldfish memory**: Yep, that's by design in V1 (it remembers the current session, though). If you want long-term memory, go back to AstrBot mode—the plugin has its own built-in long-term memory there, no extra memory plugin needed.
- **Live2D isn't rendering (Source build)**: Go check if the three js files exist under `src/vendor/` (run `npm run setup`). Also, if your model path has non-ASCII characters, or it isn't a Cubism 3/4 model, it's gonna fail to load.
- **It's a mute (No Voice)**: Check three things: Is the TTS switch on in the control page? Is SBV2 "reachable"? Did you pick a model/speaker? Oh, and make sure the "Voice" toggle in the pet shell settings is actually on. (For standalone, check the TTS URL in settings).
- **Mic button is grey / does nothing**: Did you turn on the voice-input switch in the control page? Is the ASR service actually running? (First boot takes ~4 mins; run `start_asr.vbs` to wake it up. Hover over the button to see what it's complaining about).
- **The transcription is complete garbage**: It's locked to Chinese by default. Since it's a local whisper model, heavy accents or a noisy room will wreck its accuracy—try speaking slower. Or double-check the control page to ensure the URL points to your actual local ASR service.
- **It replies, but its face never changes**: If the LLM doesn't output the emotion tags properly, the pet falls back to "calm", which is normal. Yell at your LLM in the persona prompt to strictly follow the formatting rules.
- **My AstrBot is on a remote server**: Just change the address in the settings to your server's IP. The API Key is your password—**do NOT expose port 6185 to the public internet**.

## Development Docs (For the Nerds)
<a id="development-docs"></a>

Wanna see the architecture, API references, SSE frame sequences, how motion generation works, debugging tips, or the release flow? Read [docs/dev.md](docs/dev.md) (It's in Chinese).

## License
<a id="license"></a>

My code is MIT. The bundled "Momose Hiyori" model is Live2D's official free sample data, redistributed loosely under their [license terms](https://www.live2d.com/zh-CHS/download/sample-data/). The renderer libraries (pixi.js / pixi-live2d-display / Live2D Cubism Core) are fetched by the build scripts under their respective licenses and aren't committed to this repo.
