/* AstrBotPet 桌宠壳前端逻辑 */

const DEFAULT_BASE_URL = "http://localhost:6185/api/v1/plugins/extensions";
const HISTORY_LIMIT = 10; // 与插件端 history_turns 对齐

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
  "疑惑": "o_mouth",
  "调皮": "heart_eyes",
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
    model.motion("idle");
    // 任何动作播完都回到 idle，形成循环
    model.on("motionFinish", () => model.motion("idle"));
  } catch (e) {
    console.warn("Live2D 初始化失败，回退为静态立绘：", e);
  }
}

function playEmotionMotion(label) {
  if (!live2dModel) return;
  const expr = EMOTION_EXPRESSIONS[label];
  try {
    // 不带参数调用 expression() 可恢复默认表情
    live2dModel.expression(expr || undefined);
  } catch (e) {
    console.warn("切换表情失败：", label, e);
  }
}

function setEmotion(label) {
  currentEmotion = EMOTION_FILES[label] ? label : "平静";
  playEmotionMotion(currentEmotion);
  avatar.src = `assets/${EMOTION_FILES[currentEmotion]}.png`;
}

avatar.addEventListener("error", () => {
  if (currentEmotion !== "平静") {
    currentEmotion = "平静";
    avatar.src = "assets/calm.png";
  }
});

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

// ---------- 对话 ----------

const history = [];
let sending = false;

const invoke = () => window.__TAURI__.core.invoke;
const listenEvent = (name, cb) => window.__TAURI__.event.listen(name, cb);

async function sendChat(text) {
  if (sending || !text.trim()) return;
  const cfg = loadConfig();
  if (!cfg.apiKey) {
    showBubble();
    queueType("先在右键菜单「设置」里填入 AstrBot 的 API Key 哦。");
    return;
  }
  sending = true;
  chatInput.disabled = true;
  showBubble();
  queueType("…");

  let full = "";
  let unlisten = null;
  // 等待 SSE 流结束的 Promise：stream_end / connect_error 时收尾
  let resolveFinished;
  const finished = new Promise((resolve) => (resolveFinished = resolve));
  unlisten = await listenEvent("pet-chat", (ev) => {
    const data = ev.payload || {};
    if (data.type === "emotion") {
      setEmotion(data.label);
    } else if (data.type === "delta") {
      if (!full) {
        // 首个正文帧到达，清掉 "…" 占位符
        typeQueue.length = 0;
        bubbleText.textContent = "";
      }
      full += data.text;
      queueType(data.text);
    } else if (data.type === "error") {
      console.warn("pet backend error:", data.message);
    } else if (data.type === "connect_error") {
      resolveFinished({ error: data.message });
    } else if (data.type === "stream_end") {
      scheduleBubbleHide();
      resolveFinished({ error: null });
    }
  });

  try {
    await invoke()("pet_chat", {
      baseUrl: cfg.baseUrl,
      apiKey: cfg.apiKey,
      message: text,
      history: history.slice(-HISTORY_LIMIT * 2),
    });
    const { error } = await finished;

    // 清掉占位符（如果还没有内容）
    if (!full) {
      typeQueue.length = 0;
      bubbleText.textContent = "";
    }

    if (error) {
      throw new Error(error);
    }

    history.push({ role: "user", content: text });
    if (full) history.push({ role: "assistant", content: full });
    while (history.length > HISTORY_LIMIT * 2) history.shift();
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

// 单击立绘：切换输入框
let dragMoved = false;
avatar.parentElement.addEventListener("mousedown", () => (dragMoved = false));
avatar.parentElement.addEventListener("mousemove", () => (dragMoved = true));
avatar.parentElement.addEventListener("mouseup", () => {
  if (!dragMoved) {
    inputBar.classList.toggle("hidden");
    if (!inputBar.classList.contains("hidden")) chatInput.focus();
    playEmotionMotion("高兴"); // 戳一戳桌宠
  }
});

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
  const w = 190, h = 140;
  menu.style.left = Math.min(e.clientX, window.innerWidth - w) + "px";
  menu.style.top = Math.min(e.clientY, window.innerHeight - h) + "px";
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

$("menu-passthrough").addEventListener("click", () => {
  // 实际切换由 Rust 侧快捷键/托盘完成，这里提示一下
  showStatusTip("按 Ctrl+Shift+P 切换穿透", 2000);
});

$("menu-settings").addEventListener("click", () => {
  const cfg = loadConfig();
  $("cfg-base-url").value = cfg.baseUrl;
  $("cfg-api-key").value = cfg.apiKey;
  $("cfg-message").textContent = "";
  settings.classList.remove("hidden");
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
  showStatusTip(enabled ? "穿透模式：Ctrl+Shift+P 恢复" : "已恢复交互", enabled ? 0 : 2000);
};

// 启动提示
(async () => {
  await loadFileConfig();
  initLive2D(); // 异步加载 Live2D，失败自动回退静态立绘
  if (!loadConfig().apiKey) {
    showBubble();
    queueType("你好呀！先在右键菜单「设置」里填好 AstrBot 地址和 API Key，我就能陪你聊天啦。");
  }
})();
