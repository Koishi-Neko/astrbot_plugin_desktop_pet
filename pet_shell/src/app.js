/* AstrBotPet 桌宠壳前端逻辑 */

const DEFAULT_BASE_URL = "http://localhost:6185/api/v1/plugins/extensions";

const $ = (id) => document.getElementById(id);
const avatar = $("avatar");
const bubble = $("bubble");
const bubbleText = $("bubble-text");
const inputBar = $("input-bar");
const chatInput = $("chat-input");
const menu = $("menu");
const settings = $("settings");
const statusTip = $("status-tip");

// ---------- 配置 ----------

let fileConfig = null; // config.local.json（可选，预置配置，优先级低于 localStorage）

async function loadFileConfig() {
  try {
    const resp = await fetch("config.local.json");
    if (resp.ok) fileConfig = await resp.json();
  } catch {
    /* 文件不存在时忽略 */
  }
}

function loadConfig() {
  return {
    baseUrl: (
      localStorage.getItem("pet_base_url") ||
      (fileConfig && fileConfig.base_url) ||
      DEFAULT_BASE_URL
    ).replace(/\/+$/, ""),
    apiKey:
      localStorage.getItem("pet_api_key") || (fileConfig && fileConfig.api_key) || "",
  };
}

function saveConfig(baseUrl, apiKey) {
  localStorage.setItem("pet_base_url", baseUrl.trim().replace(/\/+$/, ""));
  localStorage.setItem("pet_api_key", apiKey.trim());
}

// ---------- 立绘 / 情绪 ----------

// 情绪中文名 -> 立绘文件名（英文，避免资产协议对非 ASCII 文件名的兼容问题）
const EMOTION_FILES = {
  "平静": "calm",
  "高兴": "happy",
  "生气": "angry",
  "害羞": "shy",
  "惊讶": "surprised",
  "难过": "sad",
  "疑惑": "confused",
  "调皮": "playful",
};

let currentEmotion = "平静";

// ---------- Live2D ----------

const LIVE2D_MODEL_URL = "assets/live2d/chino/chino.model3.json";
let live2dModel = null;

// 情绪 -> 模型表情（智乃模型 expressions/ 下的表情，null = 恢复默认表情）
const EMOTION_EXPRESSIONS = {
  "平静": null,
  "高兴": "star_eyes",
  "生气": "dark_face",
  "害羞": "blush",
  "惊讶": "oo_mouth",
  "难过": "closed_happy",
  "疑惑": "confused",
  "调皮": "closed_smile",
};

async function initLive2D() {
  try {
    if (!window.PIXI || !PIXI.live2d || !PIXI.live2d.Live2DModel) return;
    // 高倍缩小模型时开 mipmap，减少锯齿/模糊
    PIXI.settings.MIPMAP_TEXTURES = PIXI.MIPMAP_MODES.ON;
    const canvas = document.getElementById("live2d-canvas");
    const pet = document.getElementById("pet");
    const app = new PIXI.Application({
      view: canvas,
      transparent: true,
      autoStart: true,
      resizeTo: pet,
      resolution: window.devicePixelRatio || 1, // 高分屏按物理像素渲染
      autoDensity: true,
    });
    const model = await PIXI.live2d.Live2DModel.from(LIVE2D_MODEL_URL);
    app.stage.addChild(model);
    // 记录未缩放的本地尺寸（pivot 必须用本地坐标）
    const localW = model.width;
    const localH = model.height;

    const fit = () => {
      const w = pet.clientWidth;
      const h = pet.clientHeight;
      const scale = Math.min(w / localW, h / localH) * 0.98;
      model.scale.set(scale);
      model.pivot.set(localW / 2, localH);
      model.x = w / 2;
      model.y = h;
    };
    fit();
    app.renderer.on("resize", fit);

    live2dModel = model;
    avatar.classList.add("hidden"); // Live2D 就绪后隐藏静态立绘
    model.motion("idle_sway"); // 待机增强版（原 idle + 低频摆动）
    // 任何动作播完都回到待机循环；长待机演出自然结束时复位演出状态
    // 注意两点：
    // 1. motionFinish 只在 internalModel.motionManager 上派发（Live2DModel 不转发）；
    // 2. 必须用 FORCE：被播完的动作若是 FORCE 优先级，此时当前优先级尚未重置，
    //    NORMAL 会被优先级检查拒绝，导致 idle_sway 接不上
    model.internalModel.motionManager.on("motionFinish", () => {
      onLongIdleFinished();
      model.motion("idle_sway", 0, PIXI.live2d.MotionPriority.FORCE).catch(() => {});
    });
  } catch (e) {
    console.warn("Live2D 初始化失败，回退为静态立绘：", e);
  }
}

// 重置表情（注意：expression() 不传参会随机应用一个表情，必须用 resetExpression）
function resetExpression() {
  if (!live2dModel) return;
  try {
    live2dModel.internalModel.motionManager.expressionManager.resetExpression();
  } catch (e) {
    console.warn("重置表情失败:", e);
  }
}

function playEmotionMotion(label) {
  if (!live2dModel) return;
  const expr = EMOTION_EXPRESSIONS[label];
  try {
    if (expr) {
      live2dModel.expression(expr);
    } else {
      resetExpression();
    }
  } catch (e) {
    console.warn("切换表情失败：", label, e);
  }
}

let emotionResetTimer = null;

function setEmotion(label) {
  currentEmotion = EMOTION_FILES[label] ? label : "平静";
  playEmotionMotion(currentEmotion);
  avatar.src = `assets/${EMOTION_FILES[currentEmotion]}.png`;
  // 非默认情绪 8s 后自动回正为默认表情
  if (emotionResetTimer) clearTimeout(emotionResetTimer);
  if (EMOTION_EXPRESSIONS[currentEmotion]) {
    emotionResetTimer = setTimeout(() => {
      emotionResetTimer = null;
      currentEmotion = "平静";
      resetExpression();
      avatar.src = `assets/${EMOTION_FILES["平静"]}.png`;
    }, 8000);
  }
}

avatar.addEventListener("error", () => {
  if (currentEmotion !== "平静") {
    currentEmotion = "平静";
    avatar.src = "assets/calm.png";
  }
});

// ---------- 语音播放与口型 ----------

let voiceEnabled = localStorage.getItem("pet_voice") !== "0";
let audioCtx = null;
let analyser = null;
const audioQueue = [];
let audioPlaying = false;
let lipSyncRaf = null;

function ensureAudioCtx() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 512;
    analyser.connect(audioCtx.destination);
  }
  if (audioCtx.state === "suspended") audioCtx.resume().catch(() => {});
  return audioCtx;
}

function b64ToArrayBuffer(b64) {
  const bin = atob(b64);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf.buffer;
}

function enqueueAudio(b64) {
  try {
    const ctx = ensureAudioCtx();
    ctx.decodeAudioData(
      b64ToArrayBuffer(b64),
      (buf) => {
        audioQueue.push(buf);
        if (!audioPlaying) playNextAudio();
      },
      (e) => console.warn("音频解码失败:", e)
    );
  } catch (e) {
    console.warn("音频入队失败:", e);
  }
}

function playNextAudio() {
  const buf = audioQueue.shift();
  if (!buf) {
    audioPlaying = false;
    stopLipSync();
    return;
  }
  audioPlaying = true;
  const ctx = ensureAudioCtx();
  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.connect(analyser);
  startLipSync();
  src.onended = () => playNextAudio();
  src.start();
}

function startLipSync() {
  if (lipSyncRaf || !analyser) return;
  const data = new Uint8Array(analyser.frequencyBinCount);
  const tick = () => {
    analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i++) {
      const v = (data[i] - 128) / 128;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / data.length);
    const mouth = Math.min(1, rms * 3.5); // 音量 → 张嘴幅度
    if (live2dModel) {
      try {
        live2dModel.internalModel.coreModel.setParameterValueById("ParamMouthOpenY", mouth);
      } catch (e) { /* 忽略 */ }
    }
    lipSyncRaf = requestAnimationFrame(tick);
  };
  lipSyncRaf = requestAnimationFrame(tick);
}

function stopLipSync() {
  if (lipSyncRaf) {
    cancelAnimationFrame(lipSyncRaf);
    lipSyncRaf = null;
  }
  if (live2dModel) {
    try {
      live2dModel.internalModel.coreModel.setParameterValueById("ParamMouthOpenY", 0);
    } catch (e) { /* 忽略 */ }
  }
}

// ---------- 打字机 ----------

const typeQueue = [];
let typeTimer = null;

function queueType(text) {
  for (const ch of text) typeQueue.push(ch);
  if (!typeTimer) {
    typeTimer = setInterval(() => {
      if (typeQueue.length === 0) {
        clearInterval(typeTimer);
        typeTimer = null;
        return;
      }
      bubbleText.textContent += typeQueue.shift();
      bubble.scrollTop = bubble.scrollHeight;
    }, 30);
  }
}

function showBubble() {
  bubbleText.textContent = "";
  bubble.classList.remove("hidden");
  if (bubbleHideTimer) clearTimeout(bubbleHideTimer);
}

// ---------- 气泡自动收起 ----------
// 回复结束 15 秒后自动收起，点击气泡可立即收起（模型始终不动）。

let bubbleHideTimer = null;

function hideBubble() {
  bubble.classList.add("hidden");
}

bubble.addEventListener("click", hideBubble);

// 左上角小圆点：切换气泡显示/隐藏（重新显示时保留上次内容）
$("bubble-toggle").addEventListener("click", (e) => {
  e.stopPropagation();
  if (bubble.classList.contains("hidden")) {
    if (!bubbleText.textContent) {
      bubbleText.textContent = "戳我下方的小智乃，和我聊聊天吧~";
    }
    bubble.classList.remove("hidden");
    if (bubbleHideTimer) clearTimeout(bubbleHideTimer);
  } else {
    hideBubble();
  }
});

function scheduleBubbleHide() {
  if (bubbleHideTimer) clearTimeout(bubbleHideTimer);
  bubbleHideTimer = setTimeout(hideBubble, 15000);
}

// ---------- 对话（管道模式：经 AstrBot open API /chat，享受人格/记忆/日志） ----------

const PET_SESSION_ID = "desktop_pet"; // 需与插件配置 pet_session_id 一致
let sending = false;

const invoke = () => window.__TAURI__.core.invoke;
const listenEvent = (name, cb) => window.__TAURI__.event.listen(name, cb);

// open API 根地址：base_url 是 .../api/v1/plugins/extensions，去掉后两段得到 .../api/v1
function openApiRoot(baseUrl) {
  return baseUrl.replace(/\/plugins\/extensions$/, "");
}

const PET_EMOTION_TAG = /^\s*【([^】]{1,8})】\s*/;
const PET_JP_TAG = /【\s*JP\s*】/i;
const SENTENCE_RE = /[^。！？!?；;\n]+[。！？!?；;\n]*/g;

function splitSentences(text) {
  return (text.match(SENTENCE_RE) || []).map((s) => s.trim()).filter(Boolean);
}

// 解析「【情绪】中文正文【JP】日语配音稿」
function parsePetReply(text) {
  let emotion = "平静";
  let body = text || "";
  const m = body.match(PET_EMOTION_TAG);
  if (m) {
    const label = m[1].trim();
    emotion = EMOTION_FILES[label] ? label : "平静";
    body = body.slice(m[0].length);
  }
  const parts = body.split(PET_JP_TAG);
  return {
    emotion,
    zh: (parts[0] || "").trim(),
    jp: (parts[1] || "").trim(),
  };
}

// 逐句调用插件 TTS 并顺序播放（后台执行，不阻塞气泡）
async function speakJp(jpText, cfg) {
  const ttsUrl = cfg.baseUrl + "/desktop_pet/pet/tts";
  for (const seg of splitSentences(jpText)) {
    try {
      const resp = await invoke()("pet_tts", { url: ttsUrl, apiKey: cfg.apiKey, text: seg });
      const d = JSON.parse(resp);
      if (d.audio) enqueueAudio(d.audio);
      else console.warn("tts 无音频:", d);
    } catch (e) {
      console.warn("tts 合成失败:", e);
    }
  }
}

async function sendChat(text) {
  if (sending || !text.trim()) return;
  const cfg = loadConfig();
  if (!cfg.apiKey) {
    showBubble();
    queueType("先在右键菜单「设置」里填入 AstrBot 的 API Key 哦。");
    return;
  }
  sending = true;
  lastChatAt = Date.now();
  chatInput.disabled = true;
  showBubble();
  queueType("…");

  let full = "";
  let unlisten = null;
  let resolveFinished;
  const finished = new Promise((resolve) => (resolveFinished = resolve));
  unlisten = await listenEvent("pet-chat", (ev) => {
    const data = ev.payload || {};
    if (data.type === "complete") {
      full = typeof data.data === "string" ? data.data : full;
    } else if (data.type === "connect_error") {
      resolveFinished({ error: data.message });
    } else if (data.type === "end") {
      resolveFinished({ error: null });
    }
    // session_id / run_started / plain / agent_stats / message_saved 等帧无需处理
  });

  try {
    await invoke()("pet_open_chat", {
      url: openApiRoot(cfg.baseUrl) + "/chat",
      apiKey: cfg.apiKey,
      message: text,
      sessionId: PET_SESSION_ID,
      username: PET_SESSION_ID,
    });
    const { error } = await finished;

    // 清掉 "…" 占位符
    typeQueue.length = 0;
    bubbleText.textContent = "";

    if (error) throw new Error(error);
    if (!full) throw new Error("AstrBot 返回了空内容");

    const { emotion, zh, jp } = parsePetReply(full);
    setEmotion(emotion);
    for (const seg of splitSentences(zh || full)) queueType(seg);
    scheduleBubbleHide();
    if (voiceEnabled && jp) speakJp(jp, cfg);
  } catch (err) {
    console.error(err);
    typeQueue.length = 0;
    bubbleText.textContent = "";
    setEmotion("难过");
    queueType("连接不上 AstrBot 了……检查一下面板和 API Key 吧。");
    scheduleBubbleHide();
  } finally {
    if (unlisten) unlisten();
    sending = false;
    lastChatAt = Date.now();
    chatInput.disabled = false;
    chatInput.focus();
  }
}

// ---------- 交互 ----------

// 右下角手柄：拖动调整窗口（模型）大小
const resizeHandle = $("resize-handle");
let resizing = null;

resizeHandle.addEventListener("mousedown", (e) => {
  e.preventDefault();
  e.stopPropagation(); // 不触发立绘拖拽
  resizing = { x: e.screenX, y: e.screenY, w: window.innerWidth, h: window.innerHeight };
});

document.addEventListener("mousemove", (e) => {
  if (!resizing) return;
  invoke()("resize_window", {
    width: resizing.w + (e.screenX - resizing.x),
    height: resizing.h + (e.screenY - resizing.y),
  }).catch(() => {});
});

document.addEventListener("mouseup", () => {
  if (!resizing) return;
  resizing = null;
  localStorage.setItem("pet_win_w", String(window.innerWidth));
  localStorage.setItem("pet_win_h", String(window.innerHeight));
});

// 启动时恢复上次的窗口尺寸
(function restoreWindowSize() {
  const w = parseInt(localStorage.getItem("pet_win_w") || "0", 10);
  const h = parseInt(localStorage.getItem("pet_win_h") || "0", 10);
  if (w >= 200 && h >= 300) {
    invoke()("resize_window", { width: w, height: h }).catch(() => {});
  }
})();

// 单击立绘：戳一戳（随机动作/表情反馈）；双击：开合输入框
let dragMoved = false;
avatar.parentElement.addEventListener("mousedown", () => (dragMoved = false));
avatar.parentElement.addEventListener("mousemove", () => (dragMoved = true));
avatar.parentElement.addEventListener("mouseup", () => {
  if (!dragMoved) poke();
});
avatar.parentElement.addEventListener("dblclick", () => {
  inputBar.classList.toggle("hidden");
  if (!inputBar.classList.contains("hidden")) chatInput.focus();
});

// 戳一戳：随机动作或短暂表情
const POKE_MOTIONS = ["nod", "tilt", "sway", "shake"];
const POKE_EXPRS = ["closed_smile", "pout", "blush", "o_surprised"];

function poke() {
  if (!live2dModel) {
    playEmotionMotion("高兴");
    return;
  }
  // 长待机演出中：只允许表情互动，动作不被打断
  if (longIdleActive) {
    const x = POKE_EXPRS[Math.floor(Math.random() * POKE_EXPRS.length)];
    console.log("[poke-idle] expr:", x);
    flashExpression(x, 2500);
    return;
  }
  if (Math.random() < 0.6) {
    const m = POKE_MOTIONS[Math.floor(Math.random() * POKE_MOTIONS.length)];
    console.log("[poke]", m);
    live2dModel.motion(m).catch(() => {});
  } else {
    const x = POKE_EXPRS[Math.floor(Math.random() * POKE_EXPRS.length)];
    console.log("[poke] expr:", x);
    flashExpression(x, 2500);
  }
}

chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && chatInput.value.trim()) {
    const text = chatInput.value.trim();
    chatInput.value = "";
    sendChat(text);
  }
});

// 右键菜单
document.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  settings.classList.add("hidden");
  menu.classList.remove("hidden");
  const w = 190, h = 170;
  menu.style.left = Math.min(e.clientX, window.innerWidth - w) + "px";
  menu.style.top = Math.min(e.clientY, window.innerHeight - h) + "px";
  refreshTtsServiceLabel();
});

document.addEventListener("click", (e) => {
  if (!menu.contains(e.target)) menu.classList.add("hidden");
});

// 点击窗口外的桌面区域会让窗口失焦，此时也收起菜单
window.addEventListener("blur", () => menu.classList.add("hidden"));

// Esc 关闭菜单
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") menu.classList.add("hidden");
});

$("menu-toggle-input").addEventListener("click", () => {
  inputBar.classList.toggle("hidden");
  if (!inputBar.classList.contains("hidden")) chatInput.focus();
});

// 点击穿透：JS 侧记录状态，与 Rust 回调（快捷键/托盘）保持同步
let clickThroughOn = false;

$("menu-passthrough").addEventListener("click", () => {
  invoke()("set_click_through", { enabled: !clickThroughOn }).catch((e) =>
    showStatusTip("切换失败：" + e, 2000)
  );
});

// SBV2 语音服务开关：停止可释放 ~4.3GB 显存（高负荷任务时用）
let ttsServiceOn = true;

async function refreshTtsServiceLabel() {
  try {
    const st = (await invoke()("sbv2_status", {})).trim();
    ttsServiceOn = st === "active";
    if (!ttsServiceOn) voiceEnabled = false;
  } catch {}
  $("menu-tts-service").textContent = ttsServiceOn
    ? "语音服务：开（点击释放显存）"
    : "语音服务：关（点击恢复）";
}

$("menu-tts-service").addEventListener("click", async () => {
  menu.classList.add("hidden");
  showStatusTip(ttsServiceOn ? "正在停止语音服务…" : "正在启动语音服务（约 30~60 秒）…", 120000);
  try {
    const st = (await invoke()("sbv2_service", { action: ttsServiceOn ? "stop" : "start" })).trim();
    ttsServiceOn = st === "active";
    voiceEnabled = ttsServiceOn ? localStorage.getItem("pet_voice") !== "0" : false;
    refreshTtsServiceLabel();
    showStatusTip(ttsServiceOn ? "语音服务已恢复（首次合成会慢一些）" : "语音服务已停止，显存已释放", 3000);
  } catch (e) {
    showStatusTip("语音服务操作失败：" + e, 4000);
  }
});

$("menu-settings").addEventListener("click", () => {
  const cfg = loadConfig();
  $("cfg-base-url").value = cfg.baseUrl;
  $("cfg-api-key").value = cfg.apiKey;
  $("cfg-voice").checked = voiceEnabled;
  $("cfg-message").textContent = "";
  settings.classList.remove("hidden");
});

$("cfg-voice").addEventListener("change", () => {
  voiceEnabled = $("cfg-voice").checked;
  localStorage.setItem("pet_voice", voiceEnabled ? "1" : "0");
});

$("menu-quit").addEventListener("click", () => {
  window.__TAURI__.core.invoke("quit_app");
});

// 设置面板
$("cfg-save").addEventListener("click", () => {
  saveConfig($("cfg-base-url").value, $("cfg-api-key").value);
  $("cfg-message").textContent = "已保存。";
});

$("cfg-close").addEventListener("click", () => settings.classList.add("hidden"));

$("cfg-test").addEventListener("click", async () => {
  const baseUrl = $("cfg-base-url").value.trim().replace(/\/+$/, "");
  const apiKey = $("cfg-api-key").value.trim();
  $("cfg-message").textContent = "测试中…";
  try {
    const text = await invoke()("pet_health", { baseUrl, apiKey });
    const data = JSON.parse(text);
    $("cfg-message").textContent = `连接成功（默认模型可用：${data.default_provider_available}）`;
  } catch (err) {
    $("cfg-message").textContent = `连接失败：${err}`;
  }
});

// ---------- 穿透状态提示（由 Rust 侧回调） ----------

function showStatusTip(text, ms) {
  statusTip.textContent = text;
  statusTip.classList.remove("hidden");
  if (ms) setTimeout(() => statusTip.classList.add("hidden"), ms);
}

window.onClickThrough = (enabled) => {
  clickThroughOn = enabled;
  showStatusTip(enabled ? "穿透模式：Ctrl+Shift+P 恢复" : "已恢复交互", enabled ? 0 : 2000);
};

// ---------- 灵动待机系统 ----------

let lastMouseMove = 0;

// 视线跟随鼠标（canvas 是 pointer-events:none，需手动转发坐标）
// 注意：透明窗口的 mousemove 只在事件经过窗口不透明区域时派发，
// 无法依赖"出界事件"回正，改用看门狗：无新事件 1.5s 自动回正。
document.addEventListener("mousemove", (e) => {
  lastMouseMove = Date.now();
  if (recenterAnim) { // 鼠标一动，立即打断回正动画
    clearInterval(recenterAnim);
    recenterAnim = null;
  }
  if (!live2dModel) return;
  const r = document.getElementById("live2d-canvas").getBoundingClientRect();
  const x = e.clientX - r.left;
  const y = e.clientY - r.top;
  if (x >= 0 && y >= 0 && x <= r.width && y <= r.height) {
    live2dModel.focus(x, y);
  }
});

// 回正：1.2s 缓动把视线目标降到 0（比库默认归位更柔和）
let recenterAnim = null;

function gazeRecenter() {
  if (!live2dModel || recenterAnim) return;
  try {
    const fc = live2dModel.internalModel.focusController;
    const sx = fc.x, sy = fc.y;
    if (Math.abs(sx) < 0.02 && Math.abs(sy) < 0.02) return; // 已经居中
    const t0 = performance.now();
    const dur = 1200;
    recenterAnim = setInterval(() => {
      const k = Math.min(1, (performance.now() - t0) / dur);
      const e = k * k * (3 - 2 * k); // smoothstep
      try {
        fc.focus(sx * (1 - e), sy * (1 - e));
      } catch (err) { /* 忽略 */ }
      if (k >= 1) {
        clearInterval(recenterAnim);
        recenterAnim = null;
      }
    }, 50);
  } catch (e) {
    /* 忽略 */
  }
}

// 鼠标离开文档立即回正
document.addEventListener("mouseleave", gazeRecenter);

// 看门狗：鼠标静止或离开 3s 后回正
setInterval(() => {
  if (Date.now() - lastMouseMove > 3000) gazeRecenter();
}, 500);

function flashExpression(name, ms = 3500) {
  live2dModel.expression(name);
  setTimeout(() => {
    // 恢复到当前情绪对应的表情
    const expr = EMOTION_EXPRESSIONS[currentEmotion];
    if (expr) {
      live2dModel.expression(expr);
    } else {
      resetExpression();
    }
  }, ms);
}

function gazeWander() {
  if (Date.now() - lastMouseMove < 8000) return; // 用户在动鼠标时不抢视线
  const r = document.getElementById("live2d-canvas").getBoundingClientRect();
  live2dModel.focus(Math.random() * r.width, Math.random() * r.height);
}

let coinIdleTimer = null;
let longIdleActive = false; // 长待机演出中：暂停随机调度、戳一戳只闪表情
let lastChatAt = Date.now(); // 最近一次对话时间，25s 无对话保底触发演出
const LONG_IDLE_TRIGGER_MS = 25000;

// 长待机演出为 60s 单次动作（末尾 4s 曲线内淡出），播完经 motionFinish 平滑回待机；
// 对话/戳一戳均不打断
function enterLongIdle() {
  longIdleActive = true;
  console.log("[idle] 进入长待机演出");
  live2dModel.motion("coin_sway", 0, PIXI.live2d.MotionPriority.FORCE).catch(() => {});
  clearTimeout(coinIdleTimer);
  // 兜底：正常情况下由 motionFinish 复位，此处防止意外卡死
  coinIdleTimer = setTimeout(exitLongIdle, 62000);
}

// 保底触发：25s 无对话自动进入演出（对话中与演出中不触发）
setInterval(() => {
  if (live2dModel && !sending && !longIdleActive && Date.now() - lastChatAt > LONG_IDLE_TRIGGER_MS) {
    enterLongIdle();
  }
}, 1000);

function exitLongIdle() {
  if (!longIdleActive) return;
  longIdleActive = false;
  console.log("[idle] 长待机演出结束");
  clearTimeout(coinIdleTimer);
  if (live2dModel) {
    live2dModel.motion("idle_sway", 0, PIXI.live2d.MotionPriority.FORCE).catch(() => {});
  }
}

// 演出动作自然播完：复位状态（idle_sway 由 motionFinish 处理器接回）
function onLongIdleFinished() {
  if (!longIdleActive) return;
  longIdleActive = false;
  console.log("[idle] 长待机演出自然结束");
  clearTimeout(coinIdleTimer);
}

const IDLE_ACTIONS = [
  ["nod", () => live2dModel.motion("nod").catch(() => {})],
  ["tilt", () => live2dModel.motion("tilt").catch(() => {})],
  ["sway", () => live2dModel.motion("sway").catch(() => {})],
  ["shake", () => live2dModel.motion("shake").catch(() => {})],
  ["expr:star_eyes", () => flashExpression("star_eyes")],
  ["expr:closed_smile", () => flashExpression("closed_smile")],
  ["expr:pout", () => flashExpression("pout")],
  ["expr:sleepy", () => flashExpression("sleepy")],
  ["expr:staff", () => flashExpression("staff", 5000)],
  ["gaze", gazeWander],
];

function scheduleIdleAction() {
  const delay = 25000 + Math.random() * 35000; // 25~60s
  setTimeout(() => {
    try {
      if (live2dModel && !sending && !longIdleActive) {
        const [name, act] = IDLE_ACTIONS[Math.floor(Math.random() * IDLE_ACTIONS.length)];
        act();
        console.log("[idle] 随机待机动作:", name);
      }
    } catch (e) {
      console.warn("[idle] 待机动作失败:", e);
    }
    scheduleIdleAction();
  }, delay);
}

scheduleIdleAction();

// 启动提示
(async () => {
  await loadFileConfig();
  initLive2D(); // 异步加载 Live2D，失败自动回退静态立绘
  if (!loadConfig().apiKey) {
    showBubble();
    queueType("你好呀！先在右键菜单「设置」里填好 AstrBot 地址和 API Key，我就能陪你聊天啦。");
  }
})();
