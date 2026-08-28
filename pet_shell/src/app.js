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

// 地址归一化：允许只填面板根地址（如 http://localhost:6185），自动补插件扩展路径
function normalizeBaseUrl(u) {
  let s = (u || "").trim().replace(/\/+$/, "");
  if (s && !/\/api\/v1(\/|$)/.test(s)) s += "/api/v1/plugins/extensions";
  return s;
}

function loadConfig() {
  return {
    baseUrl: normalizeBaseUrl(
      localStorage.getItem("pet_base_url") ||
        (fileConfig && fileConfig.base_url) ||
        DEFAULT_BASE_URL
    ),
    apiKey:
      localStorage.getItem("pet_api_key") || (fileConfig && fileConfig.api_key) || "",
  };
}

function saveConfig(baseUrl, apiKey) {
  localStorage.setItem("pet_base_url", normalizeBaseUrl(baseUrl));
  localStorage.setItem("pet_api_key", apiKey.trim());
}

// ---------- 独立模式（无 AstrBot）配置 ----------

// 运行模式：localStorage.pet_mode > fileConfig.mode > "astrbot"
function petMode() {
  const ls = localStorage.getItem("pet_mode");
  if (ls === "astrbot" || ls === "standalone") return ls;
  return fileConfig && fileConfig.mode === "standalone" ? "standalone" : "astrbot";
}

const STANDALONE_DEFAULTS = {
  llm_base_url: "https://api.deepseek.com/v1",
  llm_api_key: "",
  llm_model: "deepseek-chat",
  persona: "",
  tts_url: "http://localhost:5000",
  tts_model_id: 0,
  tts_speaker_id: 0,
  tts_style: "Neutral",
  tts_length: 1.0,
  scene_model: "", // 桌面感知视觉模型，留空 = 用对话模型
};

// 优先级：localStorage.pet_* > config.local.json standalone 节 > 内置默认
function loadStandaloneConfig() {
  const f = (fileConfig && fileConfig.standalone) || {};
  const pick = (k, d) => {
    const ls = localStorage.getItem(k);
    return ls !== null ? ls : f[k] !== undefined ? f[k] : d;
  };
  const num = (k, d) => {
    const v = Number(pick(k, d));
    return Number.isFinite(v) ? v : d;
  };
  return {
    llmBaseUrl: String(pick("pet_llm_base_url", STANDALONE_DEFAULTS.llm_base_url)).trim() || STANDALONE_DEFAULTS.llm_base_url,
    llmApiKey: String(pick("pet_llm_api_key", "")).trim(),
    llmModel: String(pick("pet_llm_model", STANDALONE_DEFAULTS.llm_model)).trim() || STANDALONE_DEFAULTS.llm_model,
    persona: String(pick("pet_persona", "")),
    ttsUrl: String(pick("pet_tts_url", STANDALONE_DEFAULTS.tts_url)).trim(),
    ttsModelId: num("pet_tts_model_id", STANDALONE_DEFAULTS.tts_model_id),
    ttsSpeakerId: num("pet_tts_speaker_id", STANDALONE_DEFAULTS.tts_speaker_id),
    ttsStyle: String(pick("pet_tts_style", STANDALONE_DEFAULTS.tts_style)).trim() || "Neutral",
    ttsLength: num("pet_tts_length", STANDALONE_DEFAULTS.tts_length),
    sceneModel: String(pick("pet_scene_model", "")).trim(),
  };
}

function saveStandaloneConfig() {
  localStorage.setItem("pet_llm_base_url", $("cfg-llm-base-url").value.trim());
  localStorage.setItem("pet_llm_api_key", $("cfg-llm-api-key").value.trim());
  localStorage.setItem("pet_llm_model", $("cfg-llm-model").value.trim());
  localStorage.setItem("pet_scene_model", $("cfg-scene-model").value.trim());
  localStorage.setItem("pet_persona", $("cfg-persona").value);
  localStorage.setItem("pet_tts_url", $("cfg-tts-url").value.trim());
}

// 内置默认人格（独立模式兜底；设置面板可覆盖）
const STANDALONE_DEFAULT_PERSONA =
  "你是桌宠「智乃」，一个住在主人 Windows 桌面上的活泼可爱的女孩子。你说话口语化、简短（1~3 句），带着活泼俏皮，偶尔有一点日式口癖。你会关心主人的作息和状态。";

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

// 可切换模型注册表：key -> 显示名/资产路径
const MODELS = {
  chino: { name: "智乃", url: "assets/live2d/chino/chino.model3.json" },
  chino_q: { name: "智乃Q版", url: "assets/live2d/chino_q/chino_q.model3.json" },
  ariu: { name: "阿露", url: "assets/live2d/ariu/ariu.model3.json" },
  hiyori: { name: "桃濑日和", url: "assets/live2d/hiyori/hiyori.model3.json" },
};

// 模型能力档案：不同模型的表情/动作差异在此收口。
// hiyori（桃濑日和，官方免费示例模型）无 exp3 表情文件，但有动作组。
// chino_q（Q版智乃）有 exp3 表情与复用的程序化动作（参数同名），无 coin_sway。
const MODEL_PROFILES = {
  chino: {
    expressions: EMOTION_EXPRESSIONS,
    idleMotion: "idle_sway",
    coinSway: true, // 长待机演出（程序化动作，智乃专属）
    pokeMotions: ["nod", "tilt", "sway", "shake"],
    pokeExprs: ["closed_smile", "pout", "blush", "o_surprised"],
    idleMotions: null, // null = 用 IDLE_ACTIONS 原列表
  },
  chino_q: {
    expressions: {
      "平静": null,
      "高兴": "heart_eyes_blush",
      "生气": null,
      "害羞": "heart_eyes_blush",
      "惊讶": "o_mouth",
      "难过": "squeezed_eyes",
      "疑惑": null,
      "调皮": "squeezed_eyes",
    },
    idleMotion: "Idle",
    coinSway: false,
    pokeMotions: ["nod", "tilt", "sway", "shake"],
    pokeExprs: ["heart_eyes_blush", "squeezed_eyes", "o_mouth", "magic_staff", "hold"],
    idleMotions: ["nod", "tilt", "sway", "shake"],
  },
  ariu: {
    // 阿露（VTube 皮套，moc3=Cubism 4.2）：无自带动作，全套程序化动作复用智乃曲线；
    // 原生情绪脸 dizzy_eyes/dark_face + 合成表情 happy/surprised/sad/playful/shy（眉毛被刘海挡住，全靠眼嘴）；
    // heart_eyes(aixin) 参数存在但贴图层无可见效果（疑似上游半成品），保留注册不映射；
    // hat/jk_bag/gamepad/coat_off/skirt/twintail_*_off 为道具服装开关
    expressions: {
      "平静": null,
      "高兴": "happy",
      "生气": "dark_face",
      "害羞": "shy",
      "惊讶": "surprised",
      "难过": "sad",
      "疑惑": "dizzy_eyes",
      "调皮": "playful",
    },
    idleMotion: "idle_sway",
    coinSway: true, // 长待机演出持裙摆+手柄（gen_motions.py 第二参 "quinzi,shoubing" 生成）
    pokeMotions: ["ear_perk", "curious", "twist", "tilt", "shake"],
    pokeExprs: ["happy", "dizzy_eyes", "playful", "hat", "gamepad"],
    idleMotions: ["ear_perk", "ear_wiggle", "twist", "curious", "ear_fold", "nod", "tilt", "sway"],
    idleExprs: ["playful", "happy"], // 待机随机闪 wink/笑（currentIdleActions 支持 idleExprs）
  },
  hiyori: {
    expressions: null, // 无表情文件，情绪仅走气泡/语音
    idleMotion: "Idle",
    coinSway: false,
    pokeMotions: ["Tap", "Flick", "Tap@Body", "Flick@Body", "FlickDown"],
    pokeExprs: [],
    idleMotions: ["Flick", "FlickDown", "Tap", "Tap@Body", "Flick@Body"],
  },
};
let activeProfile = MODEL_PROFILES.chino; // 加载成功后按实际模型设置
let currentModelKey = null; // 当前模型 key（MODELS），custom url 时为 chino

// 加载候选：显式自定义（config.local.json live2d.model_url）> 上次选择 > 本地智乃 > 内置桃濑日和
function modelCandidates() {
  const list = [];
  const custom = fileConfig && fileConfig.live2d && fileConfig.live2d.model_url;
  if (custom) list.push({ key: "chino", url: custom });
  const saved = localStorage.getItem("pet_model");
  if (saved && MODELS[saved]) list.push({ key: saved, url: MODELS[saved].url });
  list.push({ key: "chino", url: MODELS.chino.url });
  list.push({ key: "hiyori", url: MODELS.hiyori.url });
  const seen = new Set();
  return list.filter((c) => {
    const k = `${c.key}|${c.url}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

let pixiApp = null;
let fitModel = null; // 当前模型的自适应布局函数（resize 时调用）

// 把加载好的模型挂上舞台：布局、待机动作、motionFinish 回接
function attachModel(model, usedKey) {
  activeProfile = MODEL_PROFILES[usedKey] || MODEL_PROFILES.chino;
  currentModelKey = usedKey;
  const pet = document.getElementById("pet");
  pixiApp.stage.addChild(model);
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
  fitModel = fit;
  live2dModel = model;
  avatar.classList.add("hidden"); // Live2D 就绪后隐藏静态立绘
  if (activeProfile.idleMotion) model.motion(activeProfile.idleMotion).catch(() => {}); // 待机动作
  // 任何动作播完都回到待机循环；长待机演出自然结束时复位演出状态
  // 注意两点：
  // 1. motionFinish 只在 internalModel.motionManager 上派发（Live2DModel 不转发）；
  // 2. 必须用 FORCE：被播完的动作若是 FORCE 优先级，此时当前优先级尚未重置，
  //    NORMAL 会被优先级检查拒绝，导致待机动作接不上
  model.internalModel.motionManager.on("motionFinish", () => {
    onLongIdleFinished();
    if (!activeProfile.idleMotion) return;
    model
      .motion(activeProfile.idleMotion, 0, PIXI.live2d.MotionPriority.FORCE)
      .catch(() => {});
  });
}

// 热切换模型：先加载新模型成功后再拆旧模型，失败则原模型不受影响
let modelSwitching = false;
async function switchModel(key) {
  if (!pixiApp || !MODELS[key] || key === currentModelKey || modelSwitching) return;
  modelSwitching = true;
  try {
    await ensureProfile(key);
    const model = await PIXI.live2d.Live2DModel.from(MODELS[key].url);
    exitLongIdle(); // 安全：未在演出中直接返回
    if (emotionResetTimer) {
      clearTimeout(emotionResetTimer);
      emotionResetTimer = null;
    }
    currentEmotion = "平静";
    const old = live2dModel;
    live2dModel = null;
    fitModel = null;
    if (old) {
      pixiApp.stage.removeChild(old);
      old.destroy();
    }
    attachModel(model, key);
    localStorage.setItem("pet_model", key);
    showStatusTip(`已切换：${MODELS[key].name}`, 2000);
  } catch (e) {
    console.warn(`切换模型失败（${key}）：`, e);
    showStatusTip(`模型加载失败：${MODELS[key].name}`, 2500);
  } finally {
    modelSwitching = false;
  }
}

// ---------- 用户上传模型（磁盘加载，petmodel 自定义协议） ----------

// petmodel 协议（Rust 侧服务 appdata models 目录）：路径带 "/" 层级，model3.json 相对引用可正确解析
const uploadedModelUrl = (key) => `http://petmodel.localhost/${key}/model.model3.json`;

// 上传模型的通用能力档案：动作组全用，无表情映射（情绪走气泡/语音，同 hiyori 策略）
function genericProfile(settings) {
  const groups = Object.keys((settings && settings.FileReferences && settings.FileReferences.Motions) || {});
  const idle = groups.find((g) => /^idle$/i.test(g)) || groups[0] || null;
  return {
    expressions: null,
    idleMotion: idle,
    coinSway: false,
    pokeMotions: groups,
    pokeExprs: [],
    idleMotions: groups, // 空数组也是 truthy：无动作时待机池只剩视线游移
  };
}

// 上传模型首次使用前从 model3.json 构建通用档案
async function ensureProfile(key) {
  if (MODEL_PROFILES[key] || !MODELS[key] || !MODELS[key].uploaded) return;
  try {
    const settings = await (await fetch(MODELS[key].url)).json();
    MODEL_PROFILES[key] = genericProfile(settings);
  } catch (e) {
    console.warn(`构建模型档案失败（${key}），用空档案兜底：`, e);
    MODEL_PROFILES[key] = {
      expressions: null,
      idleMotion: null,
      coinSway: false,
      pokeMotions: [],
      pokeExprs: [],
      idleMotions: [],
    };
  }
}

// 启动时把 appdata models 目录里已上传的模型并入注册表（须在 modelCandidates 之前）
async function registerUploadedModels() {
  try {
    const list = await invoke()("pet_model_list");
    for (const m of list) {
      MODELS[m.key] = { name: m.name, url: uploadedModelUrl(m.key), uploaded: true };
    }
    if (list.length) console.log(`[live2d] 已注册 ${list.length} 个上传模型`);
  } catch (e) {
    console.warn("读取已上传模型失败:", e);
  }
}

// 上传（文件夹/.model3.json/.zip）→ 注册 → 自动切换
async function handleModelUpload(srcPath) {
  const m = await invoke()("pet_model_upload", { srcPath });
  MODELS[m.key] = { name: m.name, url: uploadedModelUrl(m.key), uploaded: true };
  await ensureProfile(m.key);
  renderModelSubmenu();
  await switchModel(m.key); // 内部会状态提示
  return m;
}

async function initLive2D() {
  try {
    if (!window.PIXI || !PIXI.live2d || !PIXI.live2d.Live2DModel) return;
    await registerUploadedModels();
    // 高倍缩小模型时开 mipmap，减少锯齿/模糊
    PIXI.settings.MIPMAP_TEXTURES = PIXI.MIPMAP_MODES.ON;
    const canvas = document.getElementById("live2d-canvas");
    const pet = document.getElementById("pet");
    pixiApp = new PIXI.Application({
      view: canvas,
      transparent: true,
      autoStart: true,
      resizeTo: pet,
      resolution: window.devicePixelRatio || 1, // 高分屏按物理像素渲染
      autoDensity: true,
    });
    pixiApp.renderer.on("resize", () => {
      if (fitModel) fitModel();
    });
    let model = null;
    let usedKey = null;
    for (const c of modelCandidates()) {
      try {
        model = await PIXI.live2d.Live2DModel.from(c.url);
        usedKey = c.key;
        break;
      } catch (e) {
        console.warn(`Live2D 模型加载失败（${c.url}），尝试下一个：`, e.message || e);
      }
    }
    if (!model) throw new Error("所有候选模型均加载失败");
    console.log(`[live2d] 使用模型档案: ${usedKey}`);
    await ensureProfile(usedKey);
    attachModel(model, usedKey);
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
  const exprs = activeProfile.expressions;
  if (!exprs) return; // 当前模型无表情文件（如桃濑日和），情绪仅走气泡/语音
  const expr = exprs[label];
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
  // 非默认情绪 8s 后自动回正为默认表情（仅当前模型有对应表情文件时）
  const hasExpr = activeProfile.expressions && activeProfile.expressions[currentEmotion];
  if (emotionResetTimer) clearTimeout(emotionResetTimer);
  if (hasExpr) {
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

// open API 根地址：base_url 是 .../api/v1/plugins/extensions，取 /api/v1 前缀
function openApiRoot(baseUrl) {
  const m = baseUrl.match(/^(.*\/api\/v1)(\/|$)/);
  if (!m) throw new Error("AstrBot 地址格式不正确（应包含 /api/v1），请在设置里修正");
  return m[1];
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

// ---------- 独立模式对话（直连 OpenAI 兼容 API，无 AstrBot） ----------

// 与插件 main.py 的 EMOTION_INSTRUCTION(_TTS) 保持一致（独立模式在本地拼 system prompt）
const EMOTION_INSTRUCTION =
  "\n\n【输出格式要求】每次回复必须以情绪标签开头，格式为「【情绪】正文」，" +
  "情绪只能从以下列表中选择一个：平静、高兴、生气、害羞、惊讶、难过、疑惑、调皮。" +
  "标签之后紧接回复正文。正文要口语化、简短（1~3 句），" +
  "就像桌宠气泡里说的话。不要使用 markdown、列表或代码块，" +
  "除开头的情绪标签外不要输出任何其他方括号标记。";

const EMOTION_INSTRUCTION_TTS =
  "\n\n【输出格式要求·必须严格遵守】每次回复必须同时包含以下三部分，缺一不可：" +
  "①情绪标签：回复以「【情绪】」开头，情绪只能从以下列表中选择一个：平静、高兴、生气、害羞、惊讶、难过、疑惑、调皮；" +
  "②中文正文：口语化、简短（1~3 句），就像桌宠气泡里说的话；" +
  "③日语配音稿：以「【JP】」开头，紧接与中文正文意思对应的日语，必须是纯日语口语短句，" +
  "用于语音合成朗读，不含中文、不含任何方括号标记。" +
  "完整格式示例：「【高兴】今天也好想你呀，主人！【JP】今日も会いたかったよ、ご主人様！」" +
  "禁止省略【JP】部分。不要使用 markdown、列表或代码块，" +
  "除开头的情绪标签和【JP】外不要输出任何其他方括号标记。";

// 会话历史：内存环形缓冲（~16 轮），重启即忘
let standaloneHistory = []; // [{role, content}]
const STANDALONE_HISTORY_MAX = 32;

function pushStandaloneHistory(role, content) {
  standaloneHistory.push({ role, content });
  if (standaloneHistory.length > STANDALONE_HISTORY_MAX) {
    standaloneHistory.splice(0, standaloneHistory.length - STANDALONE_HISTORY_MAX);
  }
}

function standaloneSystemPrompt(voice) {
  const s = loadStandaloneConfig();
  const persona = (s.persona || STANDALONE_DEFAULT_PERSONA).trim();
  const identity =
    "\n\n【身份说明】当前通过电脑桌面桌宠与你对话的用户就是你的主人本人，" +
    "请像对待主人一样对待他，不要把他当成陌生用户或其他人。";
  const fmt = voice ? EMOTION_INSTRUCTION_TTS : EMOTION_INSTRUCTION;
  return persona + identity + fmt;
}

async function sendChatStandalone(text, opts = {}) {
  const proactive = !!opts.proactive; // 主动发言：不占用/不聚焦输入框
  const imageB64 = opts.imageB64 || ""; // 桌面感知截图（jpeg base64，data URL 内联）
  const skipToken = !!opts.skipToken; // 允许模型回【略过】静默丢弃
  if (sending || !text.trim()) return "error";
  const scfg = loadStandaloneConfig();
  if (!scfg.llmApiKey) {
    showBubble();
    queueType("先在右键菜单「设置」的独立模式里填入模型 API Key 哦。");
    return "error";
  }
  sending = true;
  const prevLastChatAt = lastChatAt;
  lastChatAt = Date.now();
  if (!proactive) chatInput.disabled = true;
  showBubble();
  queueType("…");

  const voice = voiceEnabled;
  const msgs = [{ role: "system", content: standaloneSystemPrompt(voice) }];
  for (const h of standaloneHistory) msgs.push(h);
  const userText = voice
    ? text +
      "\n（格式提醒：本次回复必须包含【情绪】中文正文和【JP】日语配音稿三部分，【JP】为纯日语，缺一不可。）"
    : text;
  msgs.push({ role: "user", content: userText });

  let silent = false;
  try {
    const full = await invoke()("pet_chat_direct", {
      baseUrl: scfg.llmBaseUrl,
      apiKey: scfg.llmApiKey,
      model: imageB64 && scfg.sceneModel ? scfg.sceneModel : scfg.llmModel,
      messages: msgs,
      imageB64,
    });

    // 清掉 "…" 占位符
    typeQueue.length = 0;
    bubbleText.textContent = "";

    if (!full || !full.trim()) throw new Error("模型返回了空内容");
    const { emotion, zh, jp } = parsePetReply(full);
    if (skipToken && /^【\s*略过\s*】/.test((zh || full).trim())) {
      silent = true;
      hideBubble();
      return "silent";
    }
    pushStandaloneHistory("user", text);
    pushStandaloneHistory("assistant", full); // 存原文（含格式标签），后续轮次格式自洽
    setEmotion(emotion);
    for (const seg of splitSentences(zh || full)) queueType(seg);
    scheduleBubbleHide();
    if (voice && jp) speakJpStandalone(jp, scfg);
    return "spoken";
  } catch (err) {
    console.error(err);
    typeQueue.length = 0;
    bubbleText.textContent = "";
    setEmotion("难过");
    const msg = String((err && err.message) || err || "");
    if (/HTTP 40[13]/.test(msg)) {
      queueType("模型 API Key 无效或没有权限……去设置里检查一下 Key 吧。");
    } else if (/HTTP 429/.test(msg)) {
      queueType("模型服务限流了……稍等一会儿再试试吧。");
    } else if (/HTTP \d+/.test(msg)) {
      queueType("模型服务返回了错误……" + msg.slice(0, 60));
    } else {
      queueType("连接不上模型服务了……检查一下独立模式的地址和网络吧。");
    }
    scheduleBubbleHide();
    return "error";
  } finally {
    sending = false;
    lastChatAt = silent ? prevLastChatAt : Date.now(); // 略过不算发言，不占全局节流
    if (!silent) lastRealChatAt = Date.now(); // 主动对话节流只认真实发言
    if (!proactive) {
      chatInput.disabled = false;
      chatInput.focus();
    }
  }
}

// 独立模式 TTS：直连 Style-Bert-VITS2（Query 参数），失败静默降级纯文字
async function speakJpStandalone(jpText, scfg) {
  const base = scfg.ttsUrl.replace(/\/+$/, "");
  if (!base) return;
  for (const seg of splitSentences(jpText)) {
    try {
      const resp = await invoke()("pet_tts_sbv2", {
        url: base,
        text: seg,
        modelId: scfg.ttsModelId,
        speakerId: scfg.ttsSpeakerId,
        style: scfg.ttsStyle,
        length: scfg.ttsLength,
      });
      const d = JSON.parse(resp);
      if (d.audio) enqueueAudio(d.audio);
      else console.warn("tts 无音频:", d);
    } catch (e) {
      console.warn("tts 合成失败:", e);
    }
  }
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

async function sendChat(text, opts = {}) {
  if (petMode() === "standalone") return sendChatStandalone(text, opts);
  const proactive = !!opts.proactive; // 主动发言：不占用/不聚焦输入框
  const image = opts.image || null; // {attachmentId}：随消息附截图（桌面感知）
  const provider = opts.provider || ""; // 强制 selected_provider（识图需视觉模型）
  const skipToken = !!opts.skipToken; // 允许模型回【略过】静默丢弃
  if (sending || !text.trim()) return "error";
  const cfg = loadConfig();
  if (!cfg.apiKey) {
    showBubble();
    queueType("先在右键菜单「设置」里填入 AstrBot 的 API Key 哦。");
    return "error";
  }
  sending = true;
  const prevLastChatAt = lastChatAt;
  lastChatAt = Date.now();
  if (!proactive) chatInput.disabled = true;
  showBubble();
  queueType("…");

  let full = "";
  let silent = false; // 命中【略过】：静默收尾，还原气泡与 lastChatAt
  let unlisten = null;
  let resolveFinished;
  const finished = new Promise((resolve) => (resolveFinished = resolve));
  // 兜底 1：整体超时（帧流中断且无 stream_end 时也能解锁，防气泡永久卡"…"）
  const chatTimeout = setTimeout(
    () => resolveFinished({ error: "等待回复超时（180s）……网络或服务端可能卡住了" }),
    180_000,
  );
  unlisten = await listenEvent("pet-chat", (ev) => {
    const data = ev.payload || {};
    if (data.type === "complete") {
      full = typeof data.data === "string" ? data.data : full;
    } else if (data.type === "connect_error") {
      resolveFinished({ error: data.message });
    } else if (data.type === "end") {
      resolveFinished({ error: null });
    } else if (data.type === "stream_end") {
      // 兜底 2：连接关闭但未收到 end 帧（如服务端返回了非 SSE 的错误体）。
      // 已拿到 complete 全文则视为成功收尾，否则报错解锁，绝不永久挂起。
      resolveFinished({ error: full ? null : "连接已中断：未收到结束帧" });
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
      attachmentId: image ? image.attachmentId : "",
      provider,
    });
    const { error } = await finished;

    // 清掉 "…" 占位符
    typeQueue.length = 0;
    bubbleText.textContent = "";

    if (error) throw new Error(error);
    if (!full) throw new Error("AstrBot 返回了空内容");

    const { emotion, zh, jp } = parsePetReply(full);
    if (skipToken && /^【\s*略过\s*】/.test((zh || full).trim())) {
      silent = true;
      hideBubble();
      return "silent";
    }
    setEmotion(emotion);
    for (const seg of splitSentences(zh || full)) queueType(seg);
    scheduleBubbleHide();
    if (voiceEnabled && jp) speakJp(jp, cfg);
    return "spoken";
  } catch (err) {
    console.error(err);
    typeQueue.length = 0;
    bubbleText.textContent = "";
    setEmotion("难过");
    const msg = String((err && err.message) || err || "");
    if (/HTTP 40[13]/.test(msg)) {
      queueType("API Key 无效或权限（scope）不足……去 AstrBot 面板检查一下 Key 吧。");
    } else if (/HTTP \d+/.test(msg)) {
      queueType("AstrBot 返回了错误……" + msg.slice(0, 60));
    } else {
      queueType("连接不上 AstrBot 了……检查一下面板和 API Key 吧。");
    }
    scheduleBubbleHide();
    return "error";
  } finally {
    clearTimeout(chatTimeout);
    if (unlisten) unlisten();
    sending = false;
    lastChatAt = silent ? prevLastChatAt : Date.now(); // 略过不算发言，不占全局节流
    if (!silent) lastRealChatAt = Date.now(); // 主动对话节流只认真实发言
    if (!proactive) {
      chatInput.disabled = false;
      chatInput.focus();
    }
  }
}

// ---------- 语音输入（本地 ASR @ 127.0.0.1:5055，whisper @ NPU） ----------

const ASR_DEFAULT_URL = "http://127.0.0.1:5055";
const ASR_MAX_MS = 30000; // 硬上限
const ASR_SILENCE_MS = 1200; // 说话后静音自动截止
const ASR_PRESPEECH_MAX_MS = 8000; // 一直没检测到说话时提前放弃
const ASR_AUTO_SEND_MS = 500; // 识别结果入框后自动发送延迟（点击输入框/键盘可取消）

// 语音输入远程配置（插件控制页编辑下发；独立模式无插件时用本地偏好）
// 优先级：远程 > localStorage（壳端设置面板）> config.local.json > 默认
let asrRemoteCfg = null; // {voice_input_enabled, asr_url, fetchedAt}
const ASR_REMOTE_TTL_MS = 120_000;

async function fetchAsrConfig(force = false) {
  if (petMode() === "standalone") {
    asrRemoteCfg = null;
    return asrRemoteCfg;
  }
  if (!force && asrRemoteCfg && Date.now() - asrRemoteCfg.fetchedAt < ASR_REMOTE_TTL_MS) {
    return asrRemoteCfg;
  }
  try {
    const cfg = loadConfig();
    if (!cfg.apiKey) return asrRemoteCfg;
    const text = await invoke()("pet_get", {
      url: cfg.baseUrl + "/desktop_pet/pet/asr_config",
      apiKey: cfg.apiKey,
    });
    const d = JSON.parse(text);
    if (d && typeof d.asr_url === "string") {
      asrRemoteCfg = {
        voice_input_enabled: d.voice_input_enabled !== false,
        asr_url: d.asr_url.trim() || ASR_DEFAULT_URL,
        fetchedAt: Date.now(),
      };
    }
  } catch (e) {
    console.warn("[asr] 拉取远程语音配置失败（沿用旧值/默认）:", e);
  }
  return asrRemoteCfg;
}

function voiceInputEnabled() {
  if (asrRemoteCfg) return asrRemoteCfg.voice_input_enabled;
  return localStorage.getItem("pet_voice_input") !== "0";
}

function asrUrl() {
  if (asrRemoteCfg) return asrRemoteCfg.asr_url;
  const ls = localStorage.getItem("pet_asr_url");
  if (ls !== null) return ls.trim() || ASR_DEFAULT_URL;
  const u = fileConfig && fileConfig.asr && fileConfig.asr.url;
  return String(u || "").trim() || ASR_DEFAULT_URL;
}

let micRecording = false;
let micAutoSendTimer = null;
let micCancelAutoSend = false;

const micBtn = $("mic-btn");
const micWaveform = $("mic-waveform");
let micWaveformCtx = null;
let micRmsHistory = new Array(30).fill(0); // For 60px width, 2px per bar
let micAnimFrame = null;

function drawWaveform() {
  if (!micWaveformCtx && micWaveform) {
    micWaveformCtx = micWaveform.getContext("2d");
  }
  if (!micWaveformCtx) return;
  const ctx = micWaveformCtx;
  const w = micWaveform.width;
  const h = micWaveform.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#e53935";
  const barW = w / micRmsHistory.length;
  for (let i = 0; i < micRmsHistory.length; i++) {
    const rms = micRmsHistory[i];
    // max expected rms for normal speech is ~0.1 to 0.3
    const barH = Math.min(h, Math.max(2, (rms / 0.15) * h));
    const x = i * barW;
    const y = (h - barH) / 2;
    ctx.fillRect(x, y, barW - 1, barH);
  }
  if (micRecording) {
    micAnimFrame = requestAnimationFrame(drawWaveform);
  }
}


// AudioWorklet 采集处理器：mic-worklet.js（真实文件走 'self'，CSP 无需放开 blob:）
const MIC_WORKLET_URL = "mic-worklet.js";

let micStream = null;
let micContext = null;
let micSource = null;
let micWorklet = null;
let micRaw16k = [];
let micSpeechSeen = false;
let micSilenceMs = 0;
let micStartTime = 0;
let micCapTimer = null;
let micNoiseFloor = 0.01; // 自适应底噪初始值（约 -40dB）

// 触发即授予权限（启动时在 app 初始化里调用一次）
function grantMicPermission() {
  invoke()("grant_mic_permission").catch((e) => console.warn("[mic] 预授权失败（弹窗兜底）:", e));
}

// 语音服务健康探测：未就绪时按钮灰态（点击给提示），每 30s 自动重试至就绪
let asrReady = false;
let asrHealthTimer = null;
let asrState = null; // 上报用：{ready, loading, device, model, url, error}

async function probeAsrHealth() {
  try {
    const raw = await invoke()("asr_health", { url: asrUrl() });
    const h = JSON.parse(raw);
    if (h && h.status === "ok") {
      asrReady = true;
      asrState = { ready: true, loading: false, device: h.device, model: h.model, url: asrUrl(), error: null };
      micBtn.classList.remove("asr-off");
      micBtn.title = "语音输入（点击开始/结束录音）";
    } else {
      asrReady = false;
      asrState = { ready: false, loading: false, device: null, model: null, url: asrUrl(), error: String((h && h.load_error) || "未知") };
      micBtn.classList.add("asr-off");
      micBtn.title = "语音服务异常：" + String((h && h.load_error) || "未知");
    }
  } catch (e) {
    asrReady = false;
    asrState = { ready: false, loading: true, device: null, model: null, url: asrUrl(), error: null };
    micBtn.classList.add("asr-off");
    micBtn.title = "语音服务未就绪（首次加载约 4 分钟，自动重试中）";
  }
  clearTimeout(asrHealthTimer);
  asrHealthTimer = setTimeout(() => {
    if (!micRecording) probeAsrHealth();
  }, 30000);
}

async function startMicRecording() {
  if (micRecording) return;
  if (!voiceInputEnabled()) {
    showBubble();
    queueType("语音输入已关闭……去插件控制页开启一下试试。");
    scheduleBubbleHide();
    return;
  }
  if (!asrReady) {
    showBubble();
    queueType("语音服务还没准备好……首次启动要加载约 4 分钟，稍后再试。");
    scheduleBubbleHide();
    return;
  }
  micRecording = true;
  micRaw16k = [];
  micSpeechSeen = false;
  micSilenceMs = 0;
  micStartTime = Date.now();
  micNoiseFloor = 0.01;
  micBtn.classList.add("recording");
  if (micWaveform) micWaveform.classList.remove("hidden");
  micRmsHistory.fill(0);
  if (micAnimFrame) cancelAnimationFrame(micAnimFrame);
  drawWaveform();
  chatInput.classList.add("mic-recording");

  try {
    let constraints = { echoCancellation: true, noiseSuppression: true, channelCount: 1 };
    const savedDevice = localStorage.getItem("pet_mic_device");
    if (savedDevice) constraints.deviceId = { exact: savedDevice };

    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: constraints });
    } catch (e) {
      if (e.name === "OverconstrainedError" || e.name === "NotFoundError") {
        console.warn("[mic] 无法使用指定的麦克风设备，回退到默认设备:", e);
        constraints = { echoCancellation: true, noiseSuppression: true, channelCount: 1 };
        micStream = await navigator.mediaDevices.getUserMedia({ audio: constraints });
      } else {
        throw e;
      }
    }
    micContext = new AudioContext();
    if (!micContext.audioWorklet) throw new Error("AudioWorklet 不支持");
    micSource = micContext.createMediaStreamSource(micStream);
    await micContext.audioWorklet.addModule(MIC_WORKLET_URL);
    micWorklet = new AudioWorkletNode(micContext, "pet-mic-collector");
    micWorklet.port.onmessage = onMicAudioChunk;
    micSource.connect(micWorklet);
    micCapTimer = setTimeout(stopMicRecording, ASR_MAX_MS);
  } catch (e) {
    console.warn("[mic] 采集启动失败:", e);
    micRecording = false;
    micBtn.classList.remove("recording");
    showBubble();
    queueType("麦克风不可用……检查权限或录音设备。");
    scheduleBubbleHide();
  }
}

function stopMicRecording() {
  if (!micRecording) return;
  micRecording = false;
  clearTimeout(micCapTimer);
  micBtn.classList.remove("recording");
  if (micWaveform) micWaveform.classList.add("hidden");
  if (micAnimFrame) cancelAnimationFrame(micAnimFrame);
  chatInput.classList.remove("mic-recording");
  if (micWorklet) { try { micWorklet.disconnect(); micWorklet.port.close(); } catch (e) {} }
  if (micSource) { try { micSource.disconnect(); } catch (e) {} }
  if (micStream) { micStream.getTracks().forEach((t) => t.stop()); }
  if (micContext) { try { micContext.close(); } catch (e) {} }
  micStream = micContext = micSource = micWorklet = null;

  const hadSpeech = micSpeechSeen;
  const chunks = micRaw16k;
  micRaw16k = [];
  if (!hadSpeech || chunks.length < 1600) return; // 没说话 / <0.1s：静默丢弃
  transcribeAndFill(encodeWavPcm16(chunks, 16000));
}

// 采集回调：降采样到 16kHz（滑动均值抗混叠）并入列，每 10ms 做一次 VAD
function onMicAudioChunk(ev) {
  const chunk = ev.data;
  const sr = micContext ? micContext.sampleRate : 48000;
  const step = Math.max(1, Math.round(sr / 16000));
  for (let i = 0; i + step <= chunk.length; i += step) {
    let s = 0;
    for (let j = 0; j < step; j++) s += chunk[i + j];
    micRaw16k.push(s / step);
  }
  if (micRaw16k.length >= 160) {
    const win = micRaw16k.slice(-160); // 最近 10ms
    let sum = 0;
    for (const v of win) sum += v * v;
    const rms = Math.sqrt(sum / win.length);
    const thr = Math.max(micNoiseFloor * 5, 0.01);
    if (rms > thr) {
      micSpeechSeen = true;
      micSilenceMs = 0;
    } else {
      micSilenceMs += 10;
      micNoiseFloor = micNoiseFloor * 0.98 + Math.min(rms, 0.02) * 0.02; // 静音期缓慢跟踪底噪
      if (micSpeechSeen && micSilenceMs >= ASR_SILENCE_MS) {
        stopMicRecording();
        return;
      }
    }
    if (!micSpeechSeen && Date.now() - micStartTime > ASR_PRESPEECH_MAX_MS) {
      stopMicRecording();
    }
    micRmsHistory.push(rms);
    micRmsHistory.shift();
  }
}

// PCM16 单声道 WAV → base64（44 字节头 + 数据）
function encodeWavPcm16(samples, sampleRate) {
  const n = samples.length;
  const buf = new ArrayBuffer(44 + n * 2);
  const dv = new DataView(buf);
  const wstr = (o, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(o + i, s.charCodeAt(i)); };
  wstr(0, "RIFF"); dv.setUint32(4, 36 + n * 2, true); wstr(8, "WAVE");
  wstr(12, "fmt "); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true);
  dv.setUint16(22, 1, true); dv.setUint32(24, sampleRate, true);
  dv.setUint32(28, sampleRate * 2, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
  wstr(36, "data"); dv.setUint32(40, n * 2, true);
  let o = 44;
  for (let i = 0; i < n; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    dv.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    o += 2;
  }
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

// 转写并回填输入框，0.5s 后自动发送（点击输入框/键盘/再点麦克风可取消）
async function transcribeAndFill(wavB64) {
  showBubble();
  queueType("…");
  try {
    const url = asrUrl();
    const prompt = localStorage.getItem("pet_asr_prompt") || null;
    const raw = await invoke()("asr_transcribe", { url, wavB64, initial_prompt: prompt });
    const data = JSON.parse(raw);
    const text = String((data && data.text) || "").trim();
    typeQueue.length = 0;
    bubbleText.textContent = "";
    if (!text) {
      queueType("没听清，再说一次？");
      scheduleBubbleHide();
      return;
    }
    chatInput.value = text;
    micCancelAutoSend = false;
    chatInput.classList.add("mic-pending");
    chatInput.focus();
    micAutoSendTimer = setTimeout(() => {
      micAutoSendTimer = null; // 已触发：下次点击麦克风不再走"取消"分支
      chatInput.classList.remove("mic-pending");
      if (!micCancelAutoSend && chatInput.value === text) {
        if (sending) return; // 回复进行中：只回填不自动发，用户回车再发
        sendChat(text).catch(() => {});
      }
    }, ASR_AUTO_SEND_MS);
  } catch (e) {
    typeQueue.length = 0;
    bubbleText.textContent = "";
    const msg = String((e && e.message) || e || "");
    queueType(/连接 ASR 服务失败/.test(msg)
      ? "语音服务没起来……重启桌宠或 start_all 后再试。"
      : "语音识别出错：" + msg.slice(0, 40));
    scheduleBubbleHide();
  }
}

micBtn.addEventListener("click", () => {
  if (micRecording) {
    stopMicRecording();
    return;
  }
  if (micAutoSendTimer) { // 待发送阶段：点击 = 取消自动发送
    clearTimeout(micAutoSendTimer);
    micAutoSendTimer = null;
    micCancelAutoSend = true;
    chatInput.classList.remove("mic-pending");
    return;
  }
  startMicRecording();
});

// 用户在输入框动手 = 取消自动发送
chatInput.addEventListener("keydown", () => {
  if (micAutoSendTimer) {
    clearTimeout(micAutoSendTimer);
    micAutoSendTimer = null;
    micCancelAutoSend = true;
    chatInput.classList.remove("mic-pending");
  }
});
chatInput.addEventListener("mousedown", () => {
  if (micAutoSendTimer) {
    clearTimeout(micAutoSendTimer);
    micAutoSendTimer = null;
    micCancelAutoSend = true;
    chatInput.classList.remove("mic-pending");
  }
});

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

// 输入框开合收口：切 hidden + 同步左下箭头方向 + 打开时聚焦
function setInputBar(open) {
  const willOpen = typeof open === "boolean" ? open : inputBar.classList.contains("hidden");
  inputBar.classList.toggle("hidden", !willOpen);
  $("input-toggle").textContent = willOpen ? "▼" : "▲";
  if (willOpen) chatInput.focus();
}

$("input-toggle").addEventListener("click", () => setInputBar());

// 单击立绘：戳一戳（随机动作/表情反馈）；双击：开合输入框
let dragMoved = false;
avatar.parentElement.addEventListener("mousedown", () => (dragMoved = false));
avatar.parentElement.addEventListener("mousemove", () => (dragMoved = true));
avatar.parentElement.addEventListener("mouseup", () => {
  if (!dragMoved) poke();
});
avatar.parentElement.addEventListener("dblclick", () => setInputBar());

// 戳一戳：随机动作或短暂表情（动作/表情池按当前模型档案）
function poke() {
  if (!live2dModel) {
    playEmotionMotion("高兴");
    return;
  }
  // 长待机演出中：只允许表情互动，动作不被打断
  if (longIdleActive) {
    const pool0 = activeProfile.pokeExprs;
    if (!pool0.length) return;
    const x = pool0[Math.floor(Math.random() * pool0.length)];
    console.log("[poke-idle] expr:", x);
    flashExpression(x, 2500);
    return;
  }
  const canExpr = activeProfile.pokeExprs.length > 0;
  const canMotion = activeProfile.pokeMotions.length > 0;
  if (!canExpr && !canMotion) return;
  if ((canMotion && Math.random() < 0.6) || !canExpr) {
    const pool = activeProfile.pokeMotions;
    const m = pool[Math.floor(Math.random() * pool.length)];
    console.log("[poke]", m);
    live2dModel.motion(m).catch(() => {});
  } else {
    const pool = activeProfile.pokeExprs;
    const x = pool[Math.floor(Math.random() * pool.length)];
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

// 窗口内确认框（原生 confirm() 超出小窗边界会被裁剪，禁用）
function askConfirm(text) {
  return new Promise((resolve) => {
    const dlg = $("confirm-dialog");
    $("confirm-text").textContent = text;
    dlg.classList.remove("hidden");
    const done = (v) => {
      dlg.classList.add("hidden");
      $("confirm-ok").onclick = $("confirm-cancel").onclick = null;
      resolve(v);
    };
    $("confirm-ok").onclick = () => done(true);
    $("confirm-cancel").onclick = () => done(false);
  });
}

// 切换模型子菜单：从 MODELS 动态生成，当前模型打勾；上传的模型带卸载按钮
function renderModelSubmenu() {
  const sub = $("model-submenu");
  if (!sub) return;
  sub.innerHTML = "";
  for (const [key, m] of Object.entries(MODELS)) {
    const div = document.createElement("div");
    div.className = "menu-item" + (key === currentModelKey ? " active" : "");
    div.textContent = (key === currentModelKey ? "✓ " : "") + m.name;
    div.addEventListener("click", (e) => {
      e.stopPropagation();
      menu.classList.add("hidden");
      switchModel(key).then(renderModelSubmenu);
    });
    if (m.uploaded) {
      const del = document.createElement("span");
      del.className = "model-del";
      del.textContent = "×";
      del.title = "卸载该模型（删除本地文件）";
      del.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!(await askConfirm(`卸载模型「${m.name}」？将删除其本地文件。`))) return;
        try {
          if (key === currentModelKey) await switchModel("chino");
          await invoke()("pet_model_delete", { key });
          delete MODELS[key];
          delete MODEL_PROFILES[key];
          if (localStorage.getItem("pet_model") === key) {
            localStorage.removeItem("pet_model");
          }
          showStatusTip(`已卸载：${m.name}`, 2000);
        } catch (err) {
          showStatusTip(`卸载失败：${err}`, 3000);
        }
        menu.classList.add("hidden");
        renderModelSubmenu();
      });
      div.appendChild(del);
    }
    sub.appendChild(div);
  }
}

// 右键菜单
document.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  settings.classList.add("hidden");
  renderModelSubmenu();
  menu.classList.remove("hidden");
  const w = 190, h = 175;
  menu.style.left = Math.min(e.clientX, window.innerWidth - w) + "px";
  menu.style.top = Math.min(e.clientY, window.innerHeight - h) + "px";
});

document.addEventListener("click", (e) => {
  if (!menu.contains(e.target)) menu.classList.add("hidden");
});

// 点击窗口外的桌面区域会让窗口失焦，此时也收起菜单
window.addEventListener("blur", () => menu.classList.add("hidden"));

// Esc 关闭菜单/确认框
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  menu.classList.add("hidden");
  const dlg = $("confirm-dialog");
  if (dlg && !dlg.classList.contains("hidden")) {
    dlg.classList.add("hidden");
    if ($("confirm-cancel").onclick) $("confirm-cancel").onclick();
  }
});

$("menu-toggle-input").addEventListener("click", () => setInputBar());

// 点击穿透：JS 侧记录状态，与 Rust 回调（快捷键/托盘）保持同步
let clickThroughOn = false;

$("menu-passthrough").addEventListener("click", () => {
  invoke()("set_click_through", { enabled: !clickThroughOn }).catch((e) =>
    showStatusTip("切换失败：" + e, 2000)
  );
});

// 设置面板：模式分区显隐联动
function syncModeSections() {
  const standalone = $("cfg-mode").value === "standalone";
  $("cfg-astrbot-section").classList.toggle("hidden", standalone);
  $("cfg-standalone-section").classList.toggle("hidden", !standalone);
  $("cfg-section-note").textContent = standalone
    ? "主动对话与桌面感知（开关/间隔/名单）在 config.local.json 的 proactive 节配置"
    : "主动对话与桌面感知（开关/间隔/模型/名单）在 AstrBot 插件控制页配置";
}

$("cfg-mode").addEventListener("change", syncModeSections);

$("menu-settings").addEventListener("click", () => {
  populateMicDevices();
  const asrPrompt = $("cfg-asr-prompt");
  if (asrPrompt) asrPrompt.value = localStorage.getItem("pet_asr_prompt") || "";
  const cfg = loadConfig();
  $("cfg-base-url").value = cfg.baseUrl;
  $("cfg-api-key").value = cfg.apiKey;
  const scfg = loadStandaloneConfig();
  $("cfg-mode").value = petMode();
  $("cfg-llm-base-url").value = scfg.llmBaseUrl;
  $("cfg-llm-api-key").value = scfg.llmApiKey;
  $("cfg-llm-model").value = scfg.llmModel;
  $("cfg-scene-model").value = scfg.sceneModel;
  $("cfg-persona").value = scfg.persona;
  $("cfg-tts-url").value = scfg.ttsUrl;
  $("cfg-voice").checked = voiceEnabled;
  $("cfg-message").textContent = "";
  syncModeSections();
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
  const mode = $("cfg-mode").value;
  const asrPrompt = $("cfg-asr-prompt");
  if (asrPrompt) localStorage.setItem("pet_asr_prompt", asrPrompt.value);
  localStorage.setItem("pet_mode", mode);
  if (mode === "standalone") {
    saveStandaloneConfig();
  } else {
    saveConfig($("cfg-base-url").value, $("cfg-api-key").value);
  }
  $("cfg-message").textContent = "已保存。";
});

$("cfg-close").addEventListener("click", () => settings.classList.add("hidden"));

$("cfg-test").addEventListener("click", async () => {
  if ($("cfg-mode").value === "standalone") {
    // 独立模式探活：发一条空消息看模型是否可达
    const baseUrl = $("cfg-llm-base-url").value.trim();
    const apiKey = $("cfg-llm-api-key").value.trim();
    const model = $("cfg-llm-model").value.trim();
    if (!baseUrl || !apiKey || !model) {
      $("cfg-message").textContent = "请先填独立模式的模型地址、Key 和模型名。";
      return;
    }
    $("cfg-message").textContent = "测试中…";
    try {
      const text = await invoke()("pet_chat_direct", {
        baseUrl,
        apiKey,
        model,
        messages: [{ role: "user", content: "只回复两个字：在线" }],
        imageB64: "",
      });
      $("cfg-message").textContent = `✓ 连接成功，模型回复：${String(text).slice(0, 40)}`;
    } catch (err) {
      const msg = String((err && err.message) || err || "");
      if (/HTTP 40[13]/.test(msg)) $("cfg-message").textContent = "✗ API Key 无效或没有权限";
      else if (/HTTP 429/.test(msg)) $("cfg-message").textContent = "✗ 模型服务限流";
      else if (/HTTP \d+/.test(msg)) $("cfg-message").textContent = `✗ 模型服务错误：${msg.slice(0, 80)}`;
      else $("cfg-message").textContent = `✗ 连接失败：${msg.slice(0, 80)}`;
    }
    return;
  }
  const baseUrl = normalizeBaseUrl($("cfg-base-url").value);
  const apiKey = $("cfg-api-key").value.trim();
  $("cfg-message").textContent = "测试中…";
  try {
    // pet_capabilities 返回 Rust 结构体（Tauri 序列化为对象），直接使用
    const c = await invoke()("pet_capabilities", { baseUrl, apiKey });
    const rows = ["✓ 连接成功"];
    rows.push(c.plugin ? "✓ plugin scope（插件路由）" : "✗ plugin scope：去面板 API Keys 补上");
    if (c.plugin) rows.push(c.provider ? "✓ 默认对话模型可用" : "✗ 默认对话模型不可用");
    rows.push(c.chat ? "✓ chat scope（对话）" : "✗ chat scope：去面板 API Keys 补上");
    rows.push(c.file ? "✓ file scope（桌面感知上传）" : "✗ file scope：去面板 API Keys 补上");
    $("cfg-message").textContent = rows.join("\n");
    $("cfg-message").scrollIntoView({ block: "nearest" });
  } catch (err) {
    const msg = String((err && err.message) || err || "");
    if (/HTTP 40[13]/.test(msg)) $("cfg-message").textContent = "✗ API Key 无效或权限不足（需 plugin scope）";
    else if (/连接失败/.test(msg)) $("cfg-message").textContent = "✗ 连不上 AstrBot：" + msg.slice(0, 80);
    else $("cfg-message").textContent = "✗ " + msg.slice(0, 80);
    $("cfg-message").scrollIntoView({ block: "nearest" });
  }
});

// 上传模型（路径输入）
$("cfg-model-upload").addEventListener("click", async () => {
  const p = $("cfg-model-path").value.trim();
  if (!p) {
    $("cfg-message").textContent = "请先填模型路径。";
    return;
  }
  $("cfg-message").textContent = "导入中…";
  try {
    const m = await handleModelUpload(p);
    $("cfg-model-path").value = "";
    $("cfg-message").textContent = `已导入并切换：${m.name}`;
  } catch (e) {
    $("cfg-message").textContent = `导入失败：${e}`;
  }
});

// 拖拽上传：Tauri 2 原生拖放事件（HTML5 DnD 被其取代），drop 即导入
if (window.__TAURI__ && window.__TAURI__.webview) {
  window.__TAURI__.webview
    .getCurrentWebview()
    .onDragDropEvent((ev) => {
      const p = ev.payload;
      if (p.type === "drop" && p.paths && p.paths.length) {
        showStatusTip("正在导入模型…");
        handleModelUpload(p.paths[0])
          .then((m) => showStatusTip(`已导入：${m.name}`, 2500))
          .catch((e) => showStatusTip(`导入失败：${e}`, 4000));
      }
    })
    .catch((e) => console.warn("拖放监听注册失败:", e));
}

// ---------- 穿透状态提示（由 Rust 侧回调） ----------

function showStatusTip(text, ms) {
  statusTip.textContent = text;
  statusTip.classList.remove("hidden");
  if (ms) setTimeout(() => statusTip.classList.add("hidden"), ms);
}

window.onClickThrough = (enabled, autoDriven) => {
  clickThroughOn = enabled;
  showStatusTip(
    enabled
      ? autoDriven
        ? "检测到全屏应用：自动穿透中（Ctrl+Shift+P 临时恢复）"
        : "穿透模式：Ctrl+Shift+P 恢复"
      : "已恢复交互",
    enabled ? 0 : 2000
  );
};

// ---------- 灵动待机系统 ----------

let lastMouseMove = 0;

// 视线跟随鼠标（canvas 是 pointer-events:none，需手动转发坐标）
// 注意：透明窗口的 mousemove 只在事件经过窗口不透明区域时派发，
// 无法依赖"出界事件"回正，改用看门狗：无新事件 1.5s 自动回正。
document.addEventListener("mousemove", (e) => {
  lastMouseMove = Date.now();
  if (micRecording) return; // 录音中暂停视线跟随（集中在输入框交互上）
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
  if (!activeProfile.expressions) return; // 无表情文件的模型（桃濑日和）跳过
  live2dModel.expression(name);
  setTimeout(() => {
    // 恢复到当前情绪对应的表情
    const expr = activeProfile.expressions[currentEmotion];
    if (expr) {
      live2dModel.expression(expr);
    } else {
      resetExpression();
    }
  }, ms);
}

function gazeWander() {
  if (micRecording) return; // 录音中不随机游移视线
  if (Date.now() - lastMouseMove < 8000) return; // 用户在动鼠标时不抢视线
  const r = document.getElementById("live2d-canvas").getBoundingClientRect();
  live2dModel.focus(Math.random() * r.width, Math.random() * r.height);
}

let coinIdleTimer = null;
let longIdleActive = false; // 长待机演出中：暂停随机调度、戳一戳只闪表情
let lastChatAt = Date.now(); // 最近一次对话时间，25s 无对话保底触发演出
let lastRealChatAt = 0; // 真实发言时间（启动不占位）：驱动主动对话 45min 节流，与 coin_sway 解耦
const LONG_IDLE_TRIGGER_MS = 25000;

// 长待机演出为 60s 单次动作（末尾 4s 曲线内淡出），播完经 motionFinish 平滑回待机；
// 对话/戳一戳均不打断。仅智乃档案启用（coin_sway 是其专属程序化动作）
function enterLongIdle() {
  if (!activeProfile.coinSway) return;
  longIdleActive = true;
  console.log("[idle] 进入长待机演出");
  live2dModel.motion("coin_sway", 0, PIXI.live2d.MotionPriority.FORCE).catch(() => {});
  clearTimeout(coinIdleTimer);
  // 兜底：正常情况下由 motionFinish 复位，此处防止意外卡死
  coinIdleTimer = setTimeout(exitLongIdle, 62000);
}

// 保底触发：25s 无对话自动进入演出（对话中与演出中不触发；无 coin_sway 能力的模型跳过）
setInterval(() => {
  if (
    activeProfile.coinSway &&
    live2dModel &&
    !sending &&
    !micRecording &&
    !longIdleActive &&
    Date.now() - lastChatAt > LONG_IDLE_TRIGGER_MS
  ) {
    enterLongIdle();
  }
}, 1000);

function exitLongIdle() {
  if (!longIdleActive) return;
  longIdleActive = false;
  console.log("[idle] 长待机演出结束");
  clearTimeout(coinIdleTimer);
  if (live2dModel) {
    live2dModel
      .motion(activeProfile.idleMotion, 0, PIXI.live2d.MotionPriority.FORCE)
      .catch(() => {});
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

// 按当前模型档案取待机动作池（hiyori 用其动作组，无表情项）
function currentIdleActions() {
  if (activeProfile.idleMotions) {
    return [
      ...activeProfile.idleMotions.map((m) => [
        m,
        () => live2dModel.motion(m).catch(() => {}),
      ]),
      // 可选：待机随机闪表情（档案配 idleExprs 时启用，如 ariu 的 wink/笑）
      ...(activeProfile.idleExprs || []).map((e) => [
        `expr:${e}`,
        () => flashExpression(e),
      ]),
      ["gaze", gazeWander],
    ];
  }
  return IDLE_ACTIONS;
}

function scheduleIdleAction() {
  const delay = 25000 + Math.random() * 35000; // 25~60s
  setTimeout(() => {
    try {
      if (live2dModel && !sending && !micRecording && !longIdleActive) {
        const actions = currentIdleActions();
        const [name, act] = actions[Math.floor(Math.random() * actions.length)];
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

// ---------- 主动对话（态势感知 V2） ----------
// 30s 一轮态势检查：规则命中即以【情境】消息走 webchat 管线主动发言
//（人格/记忆/语音与正常对话完全一致）；免打扰优先，宁可少说。
// 长待机演出（coin_sway）不构成免打扰：发言时演出继续，
// 情绪走表情通道叠加，与动作通道互不干扰。
// V2：设置面板开关、规则阈值 config.local.json 配置化、触发历史。
const PROACTIVE_TICK_MS = 30_000;
const IDLE_ACTIVE_THRESHOLD_MS = 60_000; // 空闲超 60s 视为离开

// 默认参数；config.local.json 的 proactive 节可覆盖（localStorage 开关优先级最高）
const PROACTIVE_DEFAULTS = {
  enabled: true,
  globalCooldownMin: 45, // 全局节流：距上次发言（含正常对话）至少 N 分钟
  rules: {
    night_owl: { enabled: true, startHour: 23, endHour: 2, activeHours: 1, cooldownHours: 2 },
    welcome_back: { enabled: true, awayMinutes: 30, cooldownHours: 1 },
    sedentary: { enabled: true, activeHours: 2, cooldownHours: 2 },
  },
  // 桌面感知（scene_watch）：抓前台窗口截图→视觉模型识图→选说（【略过】静默）
  scene: {
    enabled: false, // 默认关，UI/配置开启（截屏上传云端 LLM，由用户掌控）
    intervalMin: 30, // 观察间隔
    maxIdleMin: 10, // 用户空闲超此值不看
    provider: "", // 视觉模型；空 = 跟随会话默认模型（插件控制页下发为准）
    // 禁止抓取的进程名单（小写进程名）：IM/会议/Office 文档，抓取前就拦截
    blocklist: [
      "weixin.exe", // 微信 4.x
      "wechat.exe", // 微信 3.x
      "wechatappex.exe", // 微信小程序/视频号宿主
      "wechatplayer.exe",
      "qq.exe",
      "tim.exe",
      "wxwork.exe", // 企业微信
      "dingtalk.exe", // 钉钉
      "wemeetapp.exe", // 腾讯会议
      "winword.exe", // Word
      "excel.exe", // Excel
      "powerpnt.exe", // PowerPoint
    ],
  },
};
let proactiveParams = PROACTIVE_DEFAULTS;

// loadFileConfig 之后调用：合并 config.local.json 的 proactive 覆盖
function applyProactiveConfig() {
  const c = fileConfig && fileConfig.proactive;
  if (!c) return;
  proactiveParams = {
    globalCooldownMin: c.globalCooldownMin ?? PROACTIVE_DEFAULTS.globalCooldownMin,
    rules: Object.fromEntries(
      Object.entries(PROACTIVE_DEFAULTS.rules).map(([k, v]) => [
        k,
        { ...v, ...((c.rules && c.rules[k]) || {}) },
      ])
    ),
    scene: { ...PROACTIVE_DEFAULTS.scene, ...(c.scene || {}) },
  };
}

// 主动对话/桌面感知生效参数：全部由插件侧统一下发（控制页配置），
// 壳端 120s 缓存远程拉取；插件不可达时回退 config.local.json/内置默认。
// 壳端设置面板只保留连接与本地偏好项。
let sceneRemoteCfg = null; // {provider, blocklist[], proactive_enabled, scene_enabled, scene_interval_min, fetchedAt}
const SCENE_REMOTE_TTL_MS = 120_000;

async function fetchSceneConfig(force = false) {
  if (petMode() === "standalone") {
    sceneRemoteCfg = null; // 独立模式无远程配置，直接用 config.local.json/内置默认
    return sceneRemoteCfg;
  }
  if (!force && sceneRemoteCfg && Date.now() - sceneRemoteCfg.fetchedAt < SCENE_REMOTE_TTL_MS) {
    return sceneRemoteCfg;
  }
  try {
    const cfg = loadConfig();
    if (!cfg.apiKey) return sceneRemoteCfg;
    const text = await invoke()("pet_get", {
      url: cfg.baseUrl + "/desktop_pet/pet/scene_config",
      apiKey: cfg.apiKey,
    });
    const d = JSON.parse(text);
    if (d && typeof d.provider === "string" && Array.isArray(d.blocklist)) {
      sceneRemoteCfg = {
        provider: d.provider.trim(), // 空串 = 跟随会话默认模型（不带 selected_provider）
        blocklist: d.blocklist.map((s) => String(s).toLowerCase()),
        proactive_enabled: d.proactive_enabled !== false,
        scene_enabled: d.scene_enabled === true,
        scene_interval_min: Number(d.scene_interval_min) > 0 ? Number(d.scene_interval_min) : null,
        fetchedAt: Date.now(),
      };
    }
  } catch (e) {
    console.warn("[scene] 拉取远程感知配置失败（沿用旧值/默认）:", e);
  }
  return sceneRemoteCfg;
}

// 总开关：远程 > config.local.json > 默认开
function proactiveEnabled() {
  if (sceneRemoteCfg) return sceneRemoteCfg.proactive_enabled;
  const c = fileConfig && fileConfig.proactive;
  return !(c && c.enabled === false);
}
function sceneEnabled() {
  if (sceneRemoteCfg) return sceneRemoteCfg.scene_enabled;
  return !!proactiveParams.scene.enabled;
}
function sceneParams() {
  const base = proactiveParams.scene;
  return {
    enabled: sceneEnabled(),
    intervalMin: (sceneRemoteCfg && sceneRemoteCfg.scene_interval_min) || base.intervalMin,
    maxIdleMin: base.maxIdleMin,
    // 远程下发后即使空串也以其为准（空 = 跟随会话默认模型）
    provider: sceneRemoteCfg ? sceneRemoteCfg.provider : base.provider,
    blocklist: (sceneRemoteCfg && sceneRemoteCfg.blocklist) || base.blocklist,
  };
}
// 这些进程是前台时不值得看（自己/桌面壳；用户名单另行拦截）
const SCENE_SKIP_PROCESSES = ["pet_shell.exe", "explorer.exe"];
// 桌面感知动态触发参数（事件驱动，见 sceneWatchTick）
const SCENE_DEBOUNCE_MS = 75_000; // 窗口变化后稳定这么久才抓（等页面加载/主人看进去）
const SCENE_MIN_INTERVAL_MS = 10 * 60_000; // 任意两次抓取的全局最小间隔（防快速切换烧 token）
const SCENE_HEARTBEAT_MS = 90 * 60_000; // 无变化保底心跳：长静态活动不完全沉默

let proactiveLastFiredAt = 0;
const proactiveRuleCd = {}; // ruleId -> 上次触发时间
let proactiveWasIdle = false;
let proactiveIdlePeakMs = 0;
let proactiveActiveSince = Date.now();

function fmtDur(ms) {
  const m = Math.round(ms / 60000);
  if (m < 60) return `${m}分钟`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h}小时${m % 60}分钟` : `${h}小时`;
}

// 触发历史：最近 20 条存 localStorage，window.__proactiveLog() 查看
function proactiveLogFire(ruleId, promptText) {
  try {
    const logs = JSON.parse(localStorage.getItem("pet_proactive_log") || "[]");
    logs.push({
      t: new Date().toLocaleString("zh-CN", { hour12: false }),
      rule: ruleId,
      prompt: promptText.slice(0, 50),
    });
    localStorage.setItem("pet_proactive_log", JSON.stringify(logs.slice(-20)));
  } catch {}
  reportStatusSoon(); // 有新动态就尽快同步到控制页
}

const PROACTIVE_RULES = [
  {
    id: "night_owl", // 深夜催睡
    when: (s, p) => {
      if (s.isFullscreen) return false; // 全屏应用（游戏/视频）免打扰
      const h = new Date().getHours();
      const inWindow =
        p.startHour <= p.endHour
          ? h >= p.startHour && h < p.endHour
          : h >= p.startHour || h < p.endHour; // 跨零点时段
      return inWindow && s.activeMs > p.activeHours * 3600_000;
    },
    prompt: (s) =>
      `【情境】现在已经是晚上${new Date().getHours()}点多了，主人连续使用电脑${fmtDur(s.activeMs)}没有休息。请主动对主人说一句话，提醒主人早点休息。`,
  },
  {
    id: "welcome_back", // 回来问候
    when: (s, p) => !s.isFullscreen && s.justReturnedFromMs > p.awayMinutes * 60_000,
    prompt: (s) =>
      `【情境】主人离开了${fmtDur(s.justReturnedFromMs)}，刚刚回到电脑前。请主动对主人说一句话，欢迎主人回来。`,
  },
  {
    id: "sedentary", // 久坐提醒
    when: (s, p) => !s.isFullscreen && s.activeMs > p.activeHours * 3600_000,
    prompt: (s) =>
      `【情境】主人已经连续使用电脑${fmtDur(s.activeMs)}没有休息了。请主动对主人说一句话，关心一下主人的身体。`,
  },
];

// 由空闲时长推导：连续活动时长 / 是否刚回来（及离开时长）
function proactiveState(ctx) {
  const now = Date.now();
  const idleMs = ctx.idle_seconds * 1000;
  const isIdle = idleMs >= IDLE_ACTIVE_THRESHOLD_MS;
  let justReturnedFromMs = 0;
  if (isIdle) {
    proactiveWasIdle = true;
    proactiveIdlePeakMs = Math.max(proactiveIdlePeakMs, idleMs);
    proactiveActiveSince = now;
  } else {
    if (proactiveWasIdle && proactiveIdlePeakMs > 10 * 60_000) justReturnedFromMs = proactiveIdlePeakMs;
    proactiveWasIdle = false;
    proactiveIdlePeakMs = 0;
  }
  return { activeMs: now - proactiveActiveSince, justReturnedFromMs };
}

async function proactiveTick() {
  if (!proactiveEnabled() || sending) return;
  if (!inputBar.classList.contains("hidden")) return; // 输入框打开中，勿打扰
  const now = Date.now();
  const globalCdMs = proactiveParams.globalCooldownMin * 60_000;
  if (now - proactiveLastFiredAt < globalCdMs) return;
  if (lastRealChatAt && now - lastRealChatAt < globalCdMs) return; // 本次运行还没真实发言则不节流
  const ctx = await invoke()("get_system_context", {}).catch(() => null);
  if (!ctx) return;
  const s = proactiveState(ctx);
  s.isFullscreen = !!ctx.is_fullscreen; // 全屏门槛已下沉到各规则 when()
  let fired = false;
  for (const rule of PROACTIVE_RULES) {
    const p = proactiveParams.rules[rule.id];
    if (!p || !p.enabled) continue;
    if (now - (proactiveRuleCd[rule.id] || 0) < p.cooldownHours * 3600_000) continue;
    if (!rule.when(s, p)) continue;
    proactiveRuleCd[rule.id] = now;
    proactiveLastFiredAt = now;
    fired = true;
    const text = rule.prompt(s);
    console.log(`[proactive] 触发规则 ${rule.id}`, s);
    proactiveLogFire(rule.id, text);
    sendChat(text, { proactive: true });
    break;
  }
  if (!fired) await sceneWatchTick(ctx, now);
}

// ---------- 桌面感知（scene_watch） ----------
// 事件驱动：30s tick 免费拿到的前台进程+标题做指纹比对，变化→防抖 75s→抓取送视觉模型；
// 无变化不抓（省 token），90min 保底心跳防长静态沉默。原 intervalMin 降级为同窗口最小间隔，
// 另有 10min 全局最小间隔防快速切窗。全屏游戏正是目标场景，不做全屏免打扰；用户离开时不看。
// lastSceneResult 随状态上报，控制页可见最近一次感知结果。
let lastSceneResult = null; // {t, outcome: spoke/skip/blocked/error, detail}
let sceneLastKey = ""; // 上个 tick 的窗口指纹（proc|归一化标题）
let scenePending = null; // 变化待触发 {key, since}（防抖中）
let lastSceneCaptureAt = 0; // 上次实际抓取时刻（成败都计）
let lastSceneCaptureKey = ""; // 上次抓取的窗口指纹
function setLastScene(outcome, detail = "") {
  lastSceneResult = {
    t: new Date().toLocaleString("zh-CN", { hour12: false }),
    outcome,
    detail: String(detail).slice(0, 80),
  };
  reportStatusSoon();
}

// 窗口指纹：进程 + 归一化标题（去 "(3) " 式动态计数前缀、折叠空白，减少伪变化）
function sceneWindowKey(proc, title) {
  const t = String(title || "")
    .replace(/^\s*[（(]\d+[)）]\s*/, "")
    .replace(/\s+/g, " ")
    .trim();
  return proc ? proc + "|" + t : "";
}

async function sceneWatchTick(ctx, now) {
  const p = sceneParams();
  if (!p.enabled) { sceneLastKey = ""; scenePending = null; return; }
  const proc = (ctx.foreground_process || "").toLowerCase();
  const key = sceneWindowKey(proc, ctx.foreground_title);

  // 人不在：只记指纹不触发（回来由 welcome_back 接管），待触发一并清掉
  if (ctx.idle_seconds > p.maxIdleMin * 60) { sceneLastKey = key; scenePending = null; return; }

  const blocked = !!proc && p.blocklist.includes(proc);
  const capturable = !!proc && !SCENE_SKIP_PROCESSES.includes(proc) && !blocked;

  // ① 窗口变化检测（便宜：仅字符串比对，不截图）
  if (key !== sceneLastKey) {
    sceneLastKey = key;
    if (blocked) {
      // 名单进程（IM/会议/Office 等）：记一条历史（10min 节流）便于确认拦截生效
      if (now - (proactiveRuleCd.scene_watch_blocked || 0) > 10 * 60_000) {
        proactiveRuleCd.scene_watch_blocked = now;
        proactiveLogFire("scene_watch(blocked)", `前台「${proc}」在禁止抓取名单中，已跳过`);
        setLastScene("blocked", proc);
      }
      scenePending = null;
    } else {
      scenePending = capturable ? { key, since: now } : null;
    }
    return;
  }

  // ② 触发判定：变化防抖到期 或 无变化保底心跳
  let reason = null;
  if (scenePending) {
    if (now - scenePending.since < SCENE_DEBOUNCE_MS) return; // 防抖中
    reason = "change";
  } else if (capturable && now - lastSceneCaptureAt >= SCENE_HEARTBEAT_MS) {
    reason = "heartbeat";
  }
  if (!reason) return;

  // ③ 频率闸门：同窗口最小间隔（原 intervalMin 语义降级）+ 全局最小间隔
  if (key === lastSceneCaptureKey && now - lastSceneCaptureAt < p.intervalMin * 60_000) {
    scenePending = null;
    return;
  }
  if (now - lastSceneCaptureAt < SCENE_MIN_INTERVAL_MS) { scenePending = null; return; }

  scenePending = null;
  lastSceneCaptureAt = now; // 看过即计，失败也等下个间隔
  lastSceneCaptureKey = key;
  await fetchSceneConfig(); // 过期才发请求；配置来自插件控制页
  const p2 = sceneParams(); // 拉取后可能更新了 provider/blocklist
  try {
    const shot = await invoke()("capture_window", {});
    const where = shot.window_title || shot.process;
    if (petMode() === "standalone") {
      // 独立模式：截图 base64 内联直传（免 /file 上传）
      const scfg = loadStandaloneConfig();
      if (!scfg.llmApiKey) return;
      const prompt = `【情境】这是主人当前前台窗口「${where}」的截图。如果你看到值得评论的内容（比如游戏进展、正在写的文档、有趣的页面），就自然地对主人说一两句；如果没什么值得说的，只回复【略过】。`;
      console.log("[scene] 触发桌面感知(" + reason + "):", where);
      proactiveLogFire("scene_watch(" + reason + ")", prompt);
      sendChat(prompt, {
        proactive: true,
        skipToken: true,
        imageB64: shot.jpeg_b64,
      }).then((r) => {
        setLastScene(r === "spoken" ? "spoke" : r === "silent" ? "skip" : "error", r === "error" ? "对话管线异常" : where);
      });
      return;
    }
    const cfg = loadConfig();
    if (!cfg.apiKey) return;
    const up = await invoke()("pet_upload_file", {
      url: openApiRoot(cfg.baseUrl) + "/file",
      apiKey: cfg.apiKey,
      filename: "scene.jpg",
      contentType: "image/jpeg",
      dataB64: shot.jpeg_b64,
    });
    const upJson = JSON.parse(up);
    const attachmentId = upJson.attachment_id || (upJson.data && upJson.data.attachment_id);
    if (!attachmentId) throw new Error("上传响应无 attachment_id: " + up);
    const prompt = `【情境】这是主人当前前台窗口「${where}」的截图。如果你看到值得评论的内容（比如游戏进展、正在写的文档、有趣的页面），就自然地对主人说一两句；如果没什么值得说的，只回复【略过】。`;
    console.log("[scene] 触发桌面感知(" + reason + "):", where);
    proactiveLogFire("scene_watch(" + reason + ")", prompt);
    sendChat(prompt, {
      proactive: true,
      skipToken: true,
      image: { attachmentId },
      provider: p2.provider,
    }).then((r) => {
      setLastScene(r === "spoken" ? "spoke" : r === "silent" ? "skip" : "error", r === "error" ? "对话管线异常" : where);
    });
  } catch (e) {
    console.warn("[scene] 感知失败:", e);
    setLastScene("error", e && e.message ? e.message : e);
  }
}

setInterval(proactiveTick, PROACTIVE_TICK_MS);

// 调试句柄：强制触发某条规则（跳过条件与冷却），CDP 测试用
window.__proactiveFire = (ruleId) => {
  const rule = PROACTIVE_RULES.find((r) => r.id === ruleId);
  if (!rule) return "未知规则: " + ruleId;
  const s = { activeMs: 3 * 3600_000, justReturnedFromMs: 40 * 60_000 };
  proactiveLastFiredAt = Date.now();
  const text = rule.prompt(s);
  proactiveLogFire(ruleId + "(debug)", text);
  sendChat(text, { proactive: true });
  return "fired: " + ruleId;
};
window.__proactiveLog = () => JSON.parse(localStorage.getItem("pet_proactive_log") || "[]");
window.__proactiveParams = () => proactiveParams;
window.__proactiveTick = proactiveTick;
window.__sceneShot = () => invoke()("capture_window", {}); // CDP 调试用：抓一帧看效果
window.__sceneState = () => ({ sceneLastKey, scenePending, lastSceneCaptureAt, lastSceneCaptureKey, lastSceneResult }); // CDP 调试用：看动态触发状态机
window.__sceneWatch = async () => { // CDP 调试用：强制一次桌面感知（跳防抖跳频率闸门）
  const ctx = await invoke()("get_system_context", {}).catch(() => null);
  if (!ctx) return "no_ctx";
  const proc = (ctx.foreground_process || "").toLowerCase();
  sceneLastKey = sceneWindowKey(proc, ctx.foreground_title);
  scenePending = { key: sceneLastKey, since: 0 }; // since=0：防抖立即到期
  lastSceneCaptureAt = 0;
  lastSceneCaptureKey = "";
  await sceneWatchTick(ctx, Date.now());
  return "done";
};

// ---------- 状态上报（控制页「主动对话/桌面感知动态」监控） ----------
// 60s 心跳 + 每次触发后（防抖 5s）上报开关/间隔/最近事件；插件内存暂存，重启即清
let reportTimer = null;
async function reportStatus() {
  if (petMode() === "standalone") return; // 独立模式无控制页，不上报
  try {
    const cfg = loadConfig();
    if (!cfg.apiKey) return;
    await fetchSceneConfig(); // TTL 守卫，顺带让开关/间隔跟随控制页变更
    await fetchAsrConfig(); // TTL 守卫，顺带让语音开关/地址跟随控制页变更
    const sp = sceneParams();
    await invoke()("pet_post_json", {
      url: cfg.baseUrl + "/desktop_pet/pet/status_report",
      apiKey: cfg.apiKey,
      body: {
        proactive_enabled: proactiveEnabled(),
        scene_enabled: sp.enabled,
        scene_interval_min: sp.intervalMin,
        events: window.__proactiveLog(),
        last_scene: lastSceneResult,
        asr: asrState,
      },
    });
  } catch (e) {
    console.warn("[report] 状态上报失败:", e);
  }
}
function reportStatusSoon() {
  if (reportTimer) clearTimeout(reportTimer);
  reportTimer = setTimeout(reportStatus, 5000);
}
setInterval(reportStatus, 60_000);
setTimeout(reportStatus, 8000); // 启动后稍候上报首包

// 启动提示
(async () => {
  await loadFileConfig();
  applyProactiveConfig(); // 主动对话参数覆盖（须在 loadFileConfig 之后）
  grantMicPermission(); // 语音输入：预授予 WebView2 麦克风权限（失败弹窗兜底）
  await fetchAsrConfig(true); // 语音输入：先拉远程配置（开关/地址），再按生效地址探测
  probeAsrHealth(); // 语音输入：服务健康探测（未就绪按钮灰态 + 30s 自动重试）
  // 主动对话/桌面感知配置已收口插件控制页，清理全部旧本地键
  for (const k of [
    "pet_proactive",
    "pet_scene",
    "pet_scene_interval",
    "pet_scene_provider",
    "pet_scene_blocklist",
  ]) {
    localStorage.removeItem(k);
  }
  fetchSceneConfig(true); // 预拉一次远程感知配置，首个周期即可用（独立模式跳过）
  initLive2D(); // 异步加载 Live2D，失败自动回退静态立绘
  if (petMode() === "standalone") {
    if (!loadStandaloneConfig().llmApiKey) {
      showBubble();
      queueType("你好呀！现在是无 AstrBot 的独立模式，先在右键菜单「设置」里填好模型 API Key 吧。");
    }
  } else if (!loadConfig().apiKey) {
    showBubble();
    queueType("你好呀！先在右键菜单「设置」里填好 AstrBot 地址和 API Key，我就能陪你聊天啦。");
  }
})();

async function populateMicDevices() {
  const sel = $("cfg-mic-device");
  if (!sel) return;
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const audioInputs = devices.filter(d => d.kind === 'audioinput');

    while (sel.options.length > 1) {
      sel.remove(1);
    }

    audioInputs.forEach((device, index) => {
      const label = device.label || `麦克风 ${index + 1}`;

      const option = document.createElement("option");
      option.value = device.deviceId;
      option.text = label;
      sel.appendChild(option);
    });

    const saved = localStorage.getItem("pet_mic_device");
    if (saved) {
      sel.value = saved;
    }
  } catch (e) {
    console.warn("enumerateDevices failed:", e);
  }
}

$("cfg-mic-device")?.addEventListener("change", (e) => {
  localStorage.setItem("pet_mic_device", e.target.value);
});
