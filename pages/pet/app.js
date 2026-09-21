const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);

let modelsInfo = null; // SBV2 /models/info 原文
let currentCfg = {};   // 已从服务端读取的 tts 配置
let lastStatus = null; // 最近一次 page/status 响应（总览与主动对话页共用）

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function fmtNum(n) {
  return Number(n || 0).toLocaleString();
}

// ---------- 标签路由（hash 路由 + 懒加载） ----------

const TABS = ["overview", "identity", "voice", "memory", "proactive"];
const loadedTabs = new Set(); // 已首次加载过的标签（保存类操作据此决定是否刷新总览）

function tabFromHash() {
  const h = (location.hash || "").replace(/^#\/?/, "");
  return TABS.includes(h) ? h : "overview";
}

function activateTab(name) {
  for (const t of TABS) {
    const btn = document.querySelector(`.tab[data-tab="${t}"]`);
    if (btn) btn.classList.toggle("active", t === name);
    $("panel-" + t).classList.toggle("hidden", t !== name);
  }
  // 记忆图谱的 rAF 循环只在记忆页可见时运行
  if (name === "memory") resumeGraphLoop();
  else stopGraphLoop();
  ensureTabLoaded(name);
}

function goToTab(name) {
  if (location.hash === "#" + name) activateTab(name);
  else location.hash = name; // hashchange 里统一 activateTab
}

const tabLoaders = {
  overview: loadOverviewTab,
  identity: loadIdentityTab,
  voice: loadVoiceTab,
  memory: loadMemoryTab,
  proactive: loadProactiveTab,
};

async function ensureTabLoaded(name) {
  if (loadedTabs.has(name)) return;
  loadedTabs.add(name);
  try {
    await tabLoaders[name]();
  } catch (e) {
    loadedTabs.delete(name); // 失败后下次切回允许重试
    console.error(`[pet] tab ${name} load failed:`, e);
  }
}

// 保存类操作成功后调用：只有总览/主动对话已加载过才重新拉状态，避免无谓请求
async function refreshStatusViews() {
  if (loadedTabs.has("overview") || loadedTabs.has("proactive")) await refreshStatus();
}

async function ensureStatus() {
  if (lastStatus) return lastStatus;
  await refreshStatus(); // 内部会渲染总览灯组与主动对话流水（DOM 隐藏时写入无害）
  return lastStatus;
}

// ---------- 总览：状态灯 ----------

function lampCard(status, title, descHtml) {
  return (
    `<div class="lamp-card"><div class="lamp-head">` +
    `<span class="lamp lamp-${status}"></span><span>${esc(title)}</span>` +
    `</div><div class="lamp-desc">${descHtml}</div></div>`
  );
}

// 待反思条数：新结构 unreflected={scope:条数}，兼容旧结构 unreflected_pairs=总数
function unreflectedInfo(s) {
  const u = s && s.unreflected;
  if (u && typeof u === "object") {
    const pet = u.pet || 0, priv = u.private || 0, grp = u.group || 0;
    return { total: pet + priv + grp, detail: `桌宠 ${pet} · 私聊 ${priv} · 群聊 ${grp}` };
  }
  return { total: (s && s.unreflected_pairs) || 0, detail: "" };
}

function memoryLamp(m) {
  if (!m || m.ready === false) {
    return lampCard("bad", "记忆", `存储未就绪${m && m.error ? "：" + esc(m.error) : ""}`);
  }
  if (!m.enabled) return lampCard("bad", "记忆", "内置记忆已停用");
  const un = unreflectedInfo(m);
  const base = `共 ${fmtNum(m.total_active)} 条 · 待反思 ${un.total} 条`;
  if (m.vector) {
    return lampCard("ok", "记忆",
      `向量召回 · ${esc(m.embedding_provider || "")}${m.embedding_dim ? ` ${m.embedding_dim} 维` : ""}\n` +
      `${base} · 覆盖率 ${m.with_embedding}/${m.total_active}` +
      (m.stale_embedding ? ` · ${m.stale_embedding} 条向量待重建` : ""));
  }
  return lampCard("warn", "记忆", `降级召回（无可用嵌入模型，按重要度+时效召回）\n${base}`);
}

function renderLamps(s) {
  const cards = [];

  // 桌宠壳：上报有无 / 新鲜度（>180s 红）/ 在线
  const r = s.shell_report;
  if (!r) {
    cards.push(lampCard("bad", "桌宠壳", "暂无上报（桌宠未运行或版本过旧；上报周期 60s）"));
  } else {
    const age = s.shell_report_age_s;
    if (age == null || age > 180) cards.push(lampCard("bad", "桌宠壳", `上报已过期（${age ?? "?"} 秒前）`));
    else cards.push(lampCard("ok", "桌宠壳", `在线 · ${age} 秒前上报`));
  }

  // 主动对话：开关 + 最近感知结果
  const scene = s.scene || {};
  if (!scene.proactive_enabled) {
    cards.push(lampCard("off", "主动对话", "已禁用"));
  } else {
    const ls = r && r.last_scene;
    if (!ls) {
      cards.push(lampCard("off", "主动对话", "已启用 · 暂无感知记录"));
    } else {
      const map = {
        spoke: ["ok", "已发言"],
        skip: ["warn", "略过（无可评论内容）"],
        blocked: ["bad", `拦截（${esc(ls.detail || "")}）`],
        error: ["bad", `失败（${esc(ls.detail || "")}）`],
      };
      const [st, text] = map[ls.outcome] || ["off", esc(ls.outcome || "未知")];
      cards.push(lampCard(st, "主动对话", `已启用 · 最近感知 ${esc(ls.t || "")}\n${text}`));
    }
  }

  // 语音合成 SBV2
  const sb = s.sbv2 || {};
  const ttsLine = `TTS ${s.tts_enabled ? "已启用" : "已禁用"}`;
  if (sb.reachable) {
    const gpu = (sb.gpu && sb.gpu[0]) || {};
    cards.push(lampCard("ok", "语音合成 SBV2",
      `延迟 ${sb.latency_ms}ms · 设备 ${esc((sb.devices || []).join(", "))}` +
      (gpu.gpu_memory ? `\n显存 ${Math.round(gpu.gpu_memory.used)}/${Math.round(gpu.gpu_memory.total)}MB` : "") +
      `\n${ttsLine}`));
  } else {
    cards.push(lampCard("bad", "语音合成 SBV2", `${esc(sb.error || "不可达")}\n${ttsLine}`));
  }

  // 语音识别 ASR
  const asrSt = s.asr_state;
  const asrCfg = s.asr || {};
  const asrCfgLine = `配置${asrCfg.voice_input_enabled === false ? "已关闭" : "已启用"}`;
  if (!asrSt) {
    cards.push(lampCard("bad", "语音识别 ASR", `暂无上报（桌宠未运行或版本过旧）\n${asrCfgLine}`));
  } else if (asrSt.ready) {
    cards.push(lampCard("ok", "语音识别 ASR",
      `${esc(asrSt.device || "")} ${esc(asrSt.model || "")}${asrSt.url ? ` @${esc(asrSt.url)}` : ""}\n${asrCfgLine}`));
  } else if (asrSt.loading) {
    cards.push(lampCard("warn", "语音识别 ASR", `模型加载中（首次约 4 分钟）\n${asrCfgLine}`));
  } else {
    cards.push(lampCard("bad", "语音识别 ASR", `${esc(asrSt.error || "未知异常")}\n${asrCfgLine}`));
  }

  // 记忆
  cards.push(memoryLamp(s.memory));

  $("status-lamps").innerHTML = cards.join("");
}

function renderPluginInfo(s) {
  $("plugin-info").innerHTML =
    `插件：astrbot_plugin_desktop_pet\n` +
    `桌宠会话 ID：${esc(s.pet_session_id)}\n` +
    `主人身份：${esc(s.master_name || "（未设置昵称）")}${s.master_qq ? `（QQ ${esc(s.master_qq)}）` : ""}\n` +
    `QQ 日语配音：${s.qq_jp_dub_enabled ? "已启用" : "已禁用"}\n` +
    `默认人格：${esc(s.default_persona || "（未设置）")}`;
}

async function refreshStatus() {
  try {
    const s = await bridge.apiGet("page/status");
    lastStatus = s;
    renderLamps(s);
    renderPluginInfo(s);
    renderProactiveFlow(s);
  } catch (e) {
    $("status-lamps").innerHTML =
      `<div class="lamp-card"><div class="lamp-head"><span class="lamp lamp-bad"></span><span>状态</span></div>` +
      `<div class="lamp-desc">状态获取失败：${esc(e.message)}</div></div>`;
  }
}

// ---------- 总览：Token 消耗统计 ----------

function tokenRow(title, d) {
  const wrap = document.createElement("div");
  wrap.className = "token-row";
  const input = d.input || 0;
  const cached = d.cached || 0;
  const pct = input > 0 ? Math.min(100, Math.round((cached / input) * 100)) : 0;
  wrap.innerHTML =
    `<div class="token-line"><span class="token-title">${esc(title)}</span>` +
    `输入 ${fmtNum(input)}（其中命中缓存 ${fmtNum(cached)}）· 输出 ${fmtNum(d.output)} · 平均首字响应 ${d.ttft_avg ?? 0}s</div>` +
    `<div class="token-bar-row"><div class="bar"><div class="bar-fill" style="width:${pct}%"></div></div>` +
    `<span class="token-pct">缓存命中率 ${pct}%</span></div>`;
  return wrap;
}

async function loadTokenStats() {
  const box = $("token-stats-box");
  box.innerHTML = "加载中…";
  try {
    const s = await bridge.apiGet("page/token_stats");
    if (!s.has_data) {
      box.textContent = "暂无数据";
      return;
    }
    box.innerHTML = "";
    box.appendChild(tokenRow("今日", s.stats.today));
    box.appendChild(tokenRow("累计", s.stats.all_time));
  } catch (e) {
    box.textContent = "暂无数据";
  }
}

async function loadOverviewTab() {
  await Promise.all([refreshStatus(), loadTokenStats()]);
}

// ---------- 身份与人格 ----------

async function loadMasterConfig() {
  const cfg = await bridge.apiGet("page/master_config");
  $("master-name").value = cfg.master_name || "";
  $("master-qq").value = cfg.master_qq || "";
  $("qq-jp-dub").checked = !!cfg.qq_jp_dub_enabled;
}

async function saveJpDub() {
  $("btn-save-dub").disabled = true;
  $("dub-save-msg").textContent = "保存中…";
  try {
    await bridge.apiPost("page/master_config", {
      qq_jp_dub_enabled: $("qq-jp-dub").checked,
    });
    $("dub-save-msg").textContent = "已保存，即时生效。";
    refreshStatusViews();
  } catch (e) {
    $("dub-save-msg").textContent = "保存失败：" + e.message;
  } finally {
    $("btn-save-dub").disabled = false;
    setTimeout(() => ($("dub-save-msg").textContent = ""), 4000);
  }
}

async function saveMasterConfig() {
  $("btn-save-master").disabled = true;
  $("master-save-msg").textContent = "保存中…";
  try {
    await bridge.apiPost("page/master_config", {
      master_name: $("master-name").value.trim(),
      master_qq: $("master-qq").value.trim(),
    });
    $("master-save-msg").textContent = "已保存，即时生效。";
    refreshStatusViews();
  } catch (e) {
    $("master-save-msg").textContent = "保存失败：" + e.message;
  } finally {
    $("btn-save-master").disabled = false;
    setTimeout(() => ($("master-save-msg").textContent = ""), 4000);
  }
}

async function loadPersonaConfig() {
  const sel = $("pet-persona");
  const hint = $("persona-hint");
  const btn = $("btn-save-persona");
  try {
    const cfg = await bridge.apiGet("page/persona_config");
    sel.innerHTML = "";
    for (const name of cfg.personas || []) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      sel.appendChild(opt);
    }
    if (!cfg.conversation_exists) {
      hint.textContent = "桌宠会话尚不存在：先让桌宠发一条消息，再来设置人格。";
      btn.disabled = true;
      sel.disabled = true;
      return;
    }
    sel.disabled = false;
    btn.disabled = false;
    const cur = cfg.current_persona_id;
    if (cur) {
      sel.value = cur;
      hint.textContent = `当前人格：${cur}`;
    } else {
      sel.value = cfg.default_persona || "default";
      hint.textContent = `当前未显式设置，跟随默认人格：${cfg.default_persona || "default"}`;
    }
  } catch (e) {
    hint.textContent = "人格配置读取失败：" + e.message;
  }
}

async function savePersona() {
  $("btn-save-persona").disabled = true;
  $("persona-save-msg").textContent = "保存中…";
  try {
    await bridge.apiPost("page/persona_config", { persona_id: $("pet-persona").value });
    $("persona-save-msg").textContent = "已保存，下条消息起生效。";
    loadPersonaConfig();
  } catch (e) {
    $("persona-save-msg").textContent = "保存失败：" + e.message;
  } finally {
    $("btn-save-persona").disabled = false;
    setTimeout(() => ($("persona-save-msg").textContent = ""), 4000);
  }
}

async function loadIdentityTab() {
  await Promise.all([loadMasterConfig(), loadPersonaConfig()]);
}

// ---------- 语音：TTS 配置 ----------

function fillSelect(sel, entries, keepValue) {
  sel.innerHTML = "";
  for (const { value, label } of entries) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    sel.appendChild(opt);
  }
  if (keepValue !== undefined && keepValue !== null) sel.value = String(keepValue);
}

function currentModelId() {
  return $("tts-model").value || currentCfg.tts_model_id || 0;
}

function onModelChange() {
  const m = (modelsInfo || {})[currentModelId()];
  if (!m) return;
  const spks = Object.entries(m.spk2id || {}).map(([name, id]) => ({ value: id, label: `${name} (${id})` }));
  const styles = Object.keys(m.style2id || {}).map((name) => ({ value: name, label: name }));
  fillSelect($("tts-speaker"), spks, currentCfg.tts_speaker_id);
  fillSelect($("tts-style"), styles, currentCfg.tts_style);
}

async function loadModels() {
  try {
    const r = await bridge.apiGet("page/sbv2_models");
    modelsInfo = r.models || {};
    const entries = Object.entries(modelsInfo).map(([id, m]) => ({
      value: id,
      label: `${id}: ${(m.config_path || "").replace(/^model_assets\//, "")}`,
    }));
    if (!entries.length) throw new Error("模型列表为空");
    fillSelect($("tts-model"), entries, currentCfg.tts_model_id);
    onModelChange();
    $("models-error").textContent = "";
  } catch (e) {
    $("models-error").textContent = "SBV2 模型列表拉取失败：" + e.message + "（将保留配置中的 ID，保存时请确认 SBV2 已启动）";
  }
}

async function loadConfig() {
  currentCfg = await bridge.apiGet("page/tts_config");
  $("tts-enabled").checked = !!currentCfg.tts_enabled;
  $("tts-base-url").value = currentCfg.tts_base_url || "";
  $("tts-length").value = currentCfg.tts_length ?? 1.0;
  $("tts-length-val").textContent = Number($("tts-length").value).toFixed(2);
}

function collectConfig() {
  return {
    tts_enabled: $("tts-enabled").checked,
    tts_base_url: $("tts-base-url").value.trim(),
    tts_model_id: Number($("tts-model").value || currentCfg.tts_model_id || 0),
    tts_speaker_id: Number($("tts-speaker").value || currentCfg.tts_speaker_id || 0),
    tts_style: $("tts-style").value || currentCfg.tts_style || "Neutral",
    tts_length: Number($("tts-length").value),
  };
}

async function saveConfig() {
  $("btn-save").disabled = true;
  $("save-msg").textContent = "保存中…";
  try {
    await bridge.apiPost("page/tts_config", collectConfig());
    $("save-msg").textContent = "已保存，即时生效。";
    refreshStatusViews();
  } catch (e) {
    $("save-msg").textContent = "保存失败：" + e.message;
  } finally {
    $("btn-save").disabled = false;
    setTimeout(() => ($("save-msg").textContent = ""), 4000);
  }
}

// ---------- 语音：试听 + 波形可视化 ----------

let wavePeaks = null;   // Float32Array，峰值抽样结果（0..1）
let waveRaf = 0;
let audioCtx = null;

function base64ToArrayBuffer(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

// canvas 按 devicePixelRatio 放大像素网格，保证高分屏清晰；返回 CSS 像素坐标系
function fitCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 300;
  const h = canvas.clientHeight || 72;
  const pw = Math.max(1, Math.round(w * dpr));
  const ph = Math.max(1, Math.round(h * dpr));
  if (canvas.width !== pw || canvas.height !== ph) {
    canvas.width = pw;
    canvas.height = ph;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

// 峰值抽样：每桶取绝对值峰值（桶内再步进抽样，避免逐样本扫描长音频）
function computePeaks(audioBuf, n) {
  const data = audioBuf.getChannelData(0);
  const peaks = new Float32Array(n);
  const step = Math.max(1, Math.floor(data.length / n));
  const inner = Math.max(1, Math.floor(step / 24));
  for (let i = 0; i < n; i++) {
    let max = 0;
    const start = i * step;
    const end = Math.min(start + step, data.length);
    for (let j = start; j < end; j += inner) {
      const v = Math.abs(data[j]);
      if (v > max) max = v;
    }
    peaks[i] = max;
  }
  return peaks;
}

function drawWave(progress) {
  if (!wavePeaks) return;
  const canvas = $("wave-canvas");
  const { ctx, w, h } = fitCanvas(canvas);
  ctx.clearRect(0, 0, w, h);
  const styles = getComputedStyle(document.documentElement);
  const playedColor = styles.getPropertyValue("--primary").trim() || "#35a08c";
  const restColor = styles.getPropertyValue("--wave-rest").trim() || "rgba(110,125,140,.35)";
  const n = wavePeaks.length;
  const barW = w / n;
  for (let i = 0; i < n; i++) {
    const bh = Math.max(1.5, wavePeaks[i] * (h - 8));
    ctx.fillStyle = i / n <= progress ? playedColor : restColor;
    ctx.fillRect(i * barW + barW * 0.2, (h - bh) / 2, Math.max(1, barW * 0.6), bh);
  }
  if (progress > 0) {
    ctx.fillStyle = playedColor;
    ctx.fillRect(Math.min(w - 2, progress * w), 0, 2, h);
  }
}

function waveTick() {
  const audio = $("test-audio");
  const progress = audio.duration ? audio.currentTime / audio.duration : 0;
  drawWave(progress);
  if (!audio.paused && !audio.ended) waveRaf = requestAnimationFrame(waveTick);
}

async function renderWaveform(b64) {
  const canvas = $("wave-canvas");
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) throw new Error("AudioContext 不可用");
    audioCtx = audioCtx || new AC();
    const buf = base64ToArrayBuffer(b64);
    const audioBuf = await new Promise((res, rej) => audioCtx.decodeAudioData(buf, res, rej));
    const n = Math.max(120, Math.min(360, Math.floor((canvas.clientWidth || 480) / 3)));
    wavePeaks = computePeaks(audioBuf, n);
    canvas.classList.remove("hidden");
    drawWave(0);
  } catch (e) {
    // 解码失败降级：只显示 audio 控件
    wavePeaks = null;
    canvas.classList.add("hidden");
  }
}

async function testTts() {
  $("btn-test").disabled = true;
  $("test-msg").textContent = "合成中…";
  try {
    const r = await bridge.apiPost("page/tts_test", {
      text: $("test-text").value,
      ...collectConfig(),
    });
    cancelAnimationFrame(waveRaf);
    const audio = $("test-audio");
    audio.src = "data:audio/wav;base64," + r.audio;
    audio.classList.remove("hidden");
    await renderWaveform(r.audio);
    await audio.play().catch(() => {});
    $("test-msg").textContent = "播放中";
  } catch (e) {
    $("test-msg").textContent = "合成失败：" + e.message;
  } finally {
    $("btn-test").disabled = false;
  }
}

// ---------- 语音：语音输入 ASR ----------

function renderAsrState(s) {
  const asrCfg = (s && s.asr) || {};
  const asrSt = s && s.asr_state;
  let asrLine;
  if (!asrSt) {
    asrLine = `<span class="txt-bad">● 暂无上报</span>（桌宠未运行或版本过旧）`;
  } else if (asrSt.ready) {
    asrLine = `<span class="txt-ok">● 就绪</span>  ${esc(asrSt.device || "")} ${esc(asrSt.model || "")}` +
      (asrSt.url ? `  @${esc(asrSt.url)}` : "");
  } else if (asrSt.loading) {
    asrLine = `<span class="txt-warn">● 加载中</span>  （首次约 4 分钟）`;
  } else {
    asrLine = `<span class="txt-bad">● 异常</span>  ${esc(asrSt.error || "未知")}`;
  }
  $("asr-state").innerHTML =
    `语音输入：${asrCfg.voice_input_enabled === false ? "已关闭" : "已启用"}\n` +
    `识别服务：${asrLine}`;
}

async function loadAsrConfig() {
  const cfg = await bridge.apiGet("page/asr_config");
  $("voice-input-enabled").checked = cfg.voice_input_enabled !== false;
  $("asr-url").value = cfg.asr_url || "";
}

async function saveAsrConfig() {
  $("btn-save-asr").disabled = true;
  $("asr-save-msg").textContent = "保存中…";
  try {
    await bridge.apiPost("page/asr_config", {
      voice_input_enabled: $("voice-input-enabled").checked,
      asr_url: $("asr-url").value.trim(),
    });
    $("asr-save-msg").textContent = "已保存，壳端约 2 分钟内拉取生效。";
    refreshStatusViews();
  } catch (e) {
    $("asr-save-msg").textContent = "保存失败：" + e.message;
  } finally {
    $("btn-save-asr").disabled = false;
    setTimeout(() => ($("asr-save-msg").textContent = ""), 5000);
  }
}

async function loadVoiceTab() {
  // 顺序依赖：loadConfig()（TTS）完成后 loadModels() 才能按配置回填，最后再 onModelChange() 应用一次
  await loadConfig();
  const st = await ensureStatus();
  if (st) renderAsrState(st);
  else $("asr-state").textContent = "运行状态获取失败";
  await Promise.all([loadModels(), loadAsrConfig()]);
  onModelChange();
}

// ---------- 记忆 ----------

const MEM_KIND_LABELS = { profile: "档案", fact: "事实", event: "事件", mood: "心情", promise: "约定", scene: "观察", diary: "日记" };
const MEM_SCOPE_LABELS = { pet: "桌宠", private: "私聊", group: "群聊" };
const MEM_KIND_ORDER = ["profile", "fact", "event", "mood", "promise", "scene", "diary"];
const MEM_SCOPE_ORDER = ["pet", "private", "group"];
let memoryOffset = 0;
const MEM_PAGE_LIMIT = 20;

async function loadMemoryConfig() {
  const cfg = await bridge.apiGet("page/memory_config");
  $("memory-enabled").checked = !!cfg.memory_enabled;
  $("memory-diary-enabled").checked = cfg.memory_diary_enabled !== false;
  $("memory-scope-private-enabled").checked = cfg.memory_scope_private_enabled !== false;
  $("memory-scope-group-enabled").checked = cfg.memory_scope_group_enabled !== false;
  $("memory-scope-pet-independent").checked = !!cfg.memory_scope_pet_independent;
  $("memory-scope-private-independent").checked = !!cfg.memory_scope_private_independent;
  $("memory-scope-group-independent").checked = !!cfg.memory_scope_group_independent;
  $("memory-group-context-count").value = cfg.memory_group_context_count ?? 10;
  $("memory-embed-provider").value = cfg.memory_embedding_provider_id || "";
  $("memory-llm-provider").value = cfg.memory_provider_id || "";
  // 字段改名：memory_reflect_rounds → memory_reflect_batch_messages（语义=每范围攒满 N 条消息触发一次反思）
  $("memory-reflect-batch").value = cfg.memory_reflect_batch_messages ?? cfg.memory_reflect_rounds ?? 8;
  $("memory-recall-topk").value = cfg.memory_recall_top_k ?? 3;
  $("memory-recall-minscore").value = cfg.memory_recall_min_score ?? 0.35;
  $("memory-recall-maxchars").value = cfg.memory_recall_max_chars ?? 800;
  const el = $("memory-embed-list");
  el.innerHTML = "";
  for (const p of cfg.embedding_providers || []) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.label = p.dim ? `${p.dim} 维` : "";
    el.appendChild(opt);
  }
  const ll = $("memory-llm-list");
  ll.innerHTML = "";
  for (const id of cfg.llm_providers || []) {
    const opt = document.createElement("option");
    opt.value = id;
    ll.appendChild(opt);
  }
}

function renderMemoryState(s) {
  const box = $("memory-state");
  if (!s || s.ready === false) {
    box.innerHTML = `<span class="txt-bad">● 记忆存储未就绪${s && s.error ? "：" + esc(s.error) : ""}</span>`;
    return;
  }
  if (!s.enabled) {
    box.innerHTML = `<span class="txt-warn">● 内置记忆已停用</span>`;
    return;
  }
  const kindStr = Object.entries(s.by_kind || {}).map(([k, n]) => `${MEM_KIND_LABELS[k] || k} ${n}`).join(" / ");
  const scopeStr = Object.entries(s.by_scope || {}).map(([k, n]) => `${MEM_SCOPE_LABELS[k] || k} ${n}`).join(" / ");
  let vecLine;
  if (s.vector) {
    vecLine = `<span class="txt-ok">● 向量召回</span>  ${esc(s.embedding_provider || "")}${s.embedding_dim ? ` · ${s.embedding_dim} 维` : ""}` +
      `  · 覆盖率 ${s.with_embedding}/${s.total_active}` +
      (s.stale_embedding ? `  <span class="txt-warn">（${s.stale_embedding} 条向量待重建）</span>` : "");
  } else {
    vecLine = `<span class="txt-warn">● 降级召回</span>（无可用嵌入模型，按重要度+时效召回）`;
  }
  const un = unreflectedInfo(s);
  box.innerHTML =
    `记忆库：共 ${s.total_active} 条（${kindStr || "暂无"}）${scopeStr ? `\n范围分布：${scopeStr}` : ""}\n` +
    `召回：${vecLine}\n` +
    `待反思：${un.total} 条${un.detail ? `（${un.detail}）` : ""} · 最近反思：${esc(s.last_reflect_at || "（从未）")} · 最近日记：${esc(s.last_diary_date || "（无）")}`;
}

async function saveMemoryConfig() {
  $("btn-save-memory").disabled = true;
  $("memory-save-msg").textContent = "保存中…";
  try {
    const batch = Number($("memory-reflect-batch").value);
    await bridge.apiPost("page/memory_config", {
      memory_enabled: $("memory-enabled").checked,
      memory_diary_enabled: $("memory-diary-enabled").checked,
      memory_scope_private_enabled: $("memory-scope-private-enabled").checked,
      memory_scope_group_enabled: $("memory-scope-group-enabled").checked,
      memory_scope_pet_independent: $("memory-scope-pet-independent").checked,
      memory_scope_private_independent: $("memory-scope-private-independent").checked,
      memory_scope_group_independent: $("memory-scope-group-independent").checked,
      memory_group_context_count: Number($("memory-group-context-count").value),
      memory_embedding_provider_id: $("memory-embed-provider").value.trim(),
      memory_provider_id: $("memory-llm-provider").value.trim(),
      // 字段改名过渡期：新端点认 memory_reflect_batch_messages，旧端点认 memory_reflect_rounds；
      // 后端只持久化已知 key、忽略未知 key，两个都发即可同时兼容新旧后端
      memory_reflect_batch_messages: batch,
      memory_reflect_rounds: batch,
      memory_recall_top_k: Number($("memory-recall-topk").value),
      memory_recall_min_score: Number($("memory-recall-minscore").value),
      memory_recall_max_chars: Number($("memory-recall-maxchars").value),
    });
    $("memory-save-msg").textContent = "已保存，即时生效。";
    queryMemories(false);
  } catch (e) {
    $("memory-save-msg").textContent = "保存失败：" + e.message;
  } finally {
    $("btn-save-memory").disabled = false;
    setTimeout(() => ($("memory-save-msg").textContent = ""), 4000);
  }
}

// 分布横条：数据取自 memory_query 返回的 summary.by_kind / by_scope
function renderDistGroup(el, data, labels, order) {
  const max = Math.max(1, ...order.map((k) => data[k] || 0));
  el.innerHTML = "";
  for (const k of order) {
    const n = data[k] || 0;
    const row = document.createElement("div");
    row.className = "dist-row";
    row.innerHTML =
      `<span class="dist-label">${esc(labels[k] || k)}</span>` +
      `<div class="bar"><div class="bar-fill" style="width:${Math.round((n / max) * 100)}%"></div></div>` +
      `<span class="dist-n">${n}</span>`;
    el.appendChild(row);
  }
}

function renderMemoryDist(summary) {
  const s = summary || {};
  renderDistGroup($("mem-dist-kind"), s.by_kind || {}, MEM_KIND_LABELS, MEM_KIND_ORDER);
  renderDistGroup($("mem-dist-scope"), s.by_scope || {}, MEM_SCOPE_LABELS, MEM_SCOPE_ORDER);
}

async function queryMemories(reset) {
  if (reset) memoryOffset = 0;
  const q = encodeURIComponent($("memory-search").value.trim());
  const scope = encodeURIComponent($("memory-filter-scope").value);
  try {
    // 既有 quirk 保留：bridge.apiGet 在宿主 SDK 与本仓库中均只有单参 endpoint 用法，
    // 无依据表明支持第二参 params 对象，故继续把 query string 拼进 endpoint。
    const r = await bridge.apiGet(`page/memory_query?q=${q}&scope=${scope}&offset=${memoryOffset}&limit=${MEM_PAGE_LIMIT}`);
    renderMemoryState(r.summary);
    renderMemoryDist(r.summary);
    const list = $("memory-list");
    const items = r.items || [];
    if (!items.length) {
      list.textContent = "（空）";
    } else {
      list.innerHTML = "";
      for (const m of items) {
        const row = document.createElement("div");
        row.className = "mem-row";
        const content = document.createElement("span");
        content.className = "mem-content";
        content.textContent = `[${String(m.created_at || "").slice(0, 10)}·${MEM_KIND_LABELS[m.kind] || m.kind}·${MEM_SCOPE_LABELS[m.scope] || m.scope || "桌宠"}] ${m.content}`;
        row.appendChild(content);
        const sel = document.createElement("select");
        for (let i = 1; i <= 5; i++) {
          const opt = document.createElement("option");
          opt.value = String(i);
          opt.textContent = `重要度 ${i}`;
          sel.appendChild(opt);
        }
        sel.value = String(m.importance);
        sel.addEventListener("change", async () => {
          await bridge.apiPost("page/memory_op", { action: "set_importance", id: m.id, importance: Number(sel.value) });
        });
        row.appendChild(sel);
        const del = document.createElement("button");
        del.className = "btn small";
        del.textContent = "删除";
        del.addEventListener("click", async () => {
          await bridge.apiPost("page/memory_op", { action: "delete", id: m.id });
          queryMemories(false);
        });
        row.appendChild(del);
        list.appendChild(row);
      }
    }
    const total = r.total || 0;
    const page = Math.floor(memoryOffset / MEM_PAGE_LIMIT) + 1;
    const pages = Math.max(1, Math.ceil(total / MEM_PAGE_LIMIT));
    $("memory-page-info").textContent = `第 ${page}/${pages} 页 · 共 ${total} 条`;
    $("btn-memory-prev").disabled = memoryOffset <= 0;
    $("btn-memory-next").disabled = memoryOffset + MEM_PAGE_LIMIT >= total;
  } catch (e) {
    $("memory-list").textContent = "查询失败：" + e.message;
  }
}

async function memoryOp(action, extra, btn) {
  if (btn) btn.disabled = true;
  const msg = $("memory-op-msg");
  msg.textContent = "执行中…";
  try {
    const r = await bridge.apiPost("page/memory_op", { action, ...(extra || {}) });
    if (action === "import_livingmemory") {
      const bs = Object.entries(r.by_scope || {}).map(([k, n]) => `${MEM_SCOPE_LABELS[k] || k} ${n}`).join("/");
      msg.textContent = `迁移完成：库中 ${r.found} 条，新增 ${r.imported} 条${bs ? `（${bs}）` : ""}，去重跳过 ${r.skipped} 条。`;
    } else if (action === "reflect_now" || action === "reembed") {
      msg.textContent = r.started ? "任务已开始，稍后点「刷新」看结果。" : `未开始：${r.reason || ""}`;
    } else {
      msg.textContent = "完成。";
    }
    queryMemories(false);
  } catch (e) {
    msg.textContent = "失败：" + e.message;
  } finally {
    if (btn) btn.disabled = false;
    setTimeout(() => (msg.textContent = ""), 8000);
  }
}

async function testMemoryRecall() {
  const box = $("memory-test-result");
  $("btn-memory-test").disabled = true;
  box.classList.remove("hidden");
  box.textContent = "召回中…";
  try {
    const r = await bridge.apiPost("page/memory_recall_test", {
      text: $("memory-test-text").value,
      scope: $("memory-test-scope").value,
    });
    const lines = (r.hits || []).map(
      (h) => `- [${MEM_KIND_LABELS[h.kind] || h.kind}·${MEM_SCOPE_LABELS[h.scope] || h.scope || "?"}]${h.cos != null ? ` cos=${h.cos}` : ""} score=${h.score}  ${h.content}`
    );
    box.textContent =
      `召回范围：${MEM_SCOPE_LABELS[r.scope] || r.scope} · 召回方式：${r.vector ? "向量" : "降级（重要度+时效）"}\n` +
      (lines.length ? lines.join("\n") : "（无命中）") +
      (r.block ? `\n\n—— 实际注入块 ——\n${r.block}` : "");
  } catch (e) {
    box.textContent = "召回失败：" + e.message;
  } finally {
    $("btn-memory-test").disabled = false;
  }
}

async function addMemory() {
  const msg = $("memory-add-msg");
  $("btn-memory-add").disabled = true;
  try {
    const r = await bridge.apiPost("page/memory_op", {
      action: "add",
      content: $("memory-add-content").value.trim(),
      kind: $("memory-add-kind").value,
      importance: Number($("memory-add-importance").value),
      scope: $("memory-add-scope").value,
    });
    if (r.added) {
      msg.textContent = `已添加（#${r.id}）。`;
      $("memory-add-content").value = "";
      queryMemories(true);
    } else {
      msg.textContent = `与既有记忆 #${r.duplicate_of} 重复，未添加。`;
    }
  } catch (e) {
    msg.textContent = "添加失败：" + e.message;
  } finally {
    $("btn-memory-add").disabled = false;
    setTimeout(() => ($("memory-add-msg").textContent = ""), 5000);
  }
}

// ---------- 记忆图谱（canvas 力导向图：节点斥力 + 边弹簧 + 速度阻尼） ----------

const GRAPH_MAX_NODES = 400;
const graph = { nodes: [], edges: [], loaded: false, raf: 0, drag: null, hover: null, settle: 0 };

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

async function loadMemoryGraph() {
  const hint = $("graph-hint");
  hint.classList.add("hidden");
  stopGraphLoop();
  graph.loaded = false;
  try {
    const g = await bridge.apiGet("page/memory_graph");
    const nodes = g.nodes || [];
    if (!nodes.length) {
      // nodes 为空：按 reason 显示提示
      graph.nodes = [];
      graph.edges = [];
      const { ctx, w, h } = fitCanvas($("memory-graph"));
      ctx.clearRect(0, 0, w, h);
      hint.textContent = g.reason || "暂无可展示的记忆节点";
      hint.classList.remove("hidden");
      return;
    }
    const truncated = initGraph(g);
    // vector:false 但带节点（索引不可用）：仅画节点、无边，并附原因说明
    const notes = [];
    if (truncated) notes.push(`节点较多，仅展示重要度最高的 ${GRAPH_MAX_NODES} 个`);
    if (!g.vector) notes.push(g.reason || "向量索引不可用，仅显示节点");
    if (notes.length) {
      hint.textContent = notes.join("；");
      hint.classList.remove("hidden");
    }
  } catch (e) {
    hint.textContent = "图谱加载失败：" + e.message;
    hint.classList.remove("hidden");
  }
}

function initGraph(g) {
  const canvas = $("memory-graph");
  const { w, h } = fitCanvas(canvas);
  let nodes = g.nodes || [];
  let truncated = false;
  if (nodes.length > GRAPH_MAX_NODES) {
    // 节点过多时 O(n²) 斥力会卡，按重要度截取并明示
    nodes = [...nodes].sort((a, b) => (b.importance || 3) - (a.importance || 3)).slice(0, GRAPH_MAX_NODES);
    truncated = true;
  }
  const idxById = new Map();
  const cx = w / 2, cy = h / 2;
  graph.nodes = nodes.map((n, i) => {
    idxById.set(n.id, i);
    // 黄金角螺旋初始布点，开局分布均匀、收敛快
    const ang = i * 2.399963;
    const rad = 12 * Math.sqrt(i + 1);
    return {
      id: n.id,
      kind: n.kind,
      scope: n.scope || "pet",
      importance: n.importance || 3,
      content: n.content || "",
      created_at: n.created_at || "",
      x: cx + rad * Math.cos(ang),
      y: cy + rad * Math.sin(ang),
      vx: 0,
      vy: 0,
      r: 3 + (n.importance || 3) * 1.5, // 半径随重要度 1-5 递增
    };
  });
  graph.edges = (g.edges || [])
    .map((e) => ({ a: idxById.get(e.a), b: idxById.get(e.b), w: e.w || 1 }))
    .filter((e) => e.a !== undefined && e.b !== undefined && e.a !== e.b);
  graph.drag = null;
  graph.hover = null;
  graph.settle = 0;
  graph.loaded = true;
  for (let i = 0; i < 80; i++) tickGraph(); // 预跑若干步，避免开局炸开
  drawGraph();
  startGraphLoop();
  return truncated;
}

function tickGraph() {
  const nodes = graph.nodes;
  const canvas = $("memory-graph");
  const w = canvas.clientWidth || 600;
  const h = canvas.clientHeight || 320;

  // 节点斥力（全对，O(n²)）
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      let dx = a.x - b.x;
      let dy = a.y - b.y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 1) {
        dx = Math.random() - 0.5;
        dy = Math.random() - 0.5;
        d2 = 1;
      }
      const d = Math.sqrt(d2);
      const f = Math.min(2.5, 2600 / d2);
      const fx = (dx / d) * f;
      const fy = (dy / d) * f;
      a.vx += fx; a.vy += fy;
      b.vx -= fx; b.vy -= fy;
    }
  }

  // 边弹簧：边权越大，目标距离越短、拉力越强
  for (const e of graph.edges) {
    const a = nodes[e.a];
    const b = nodes[e.b];
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const d = Math.max(1, Math.hypot(dx, dy));
    const rest = 42 + 30 / Math.max(0.2, e.w);
    const f = (d - rest) * 0.015 * Math.min(2, e.w);
    const fx = (dx / d) * f;
    const fy = (dy / d) * f;
    a.vx += fx; a.vy += fy;
    b.vx -= fx; b.vy -= fy;
  }

  // 弱向心力 + 速度阻尼 + 边界约束
  const cx = w / 2, cy = h / 2;
  for (const n of nodes) {
    if (graph.drag === n) {
      n.vx = 0;
      n.vy = 0;
      continue;
    }
    n.vx += (cx - n.x) * 0.004;
    n.vy += (cy - n.y) * 0.004;
    n.vx *= 0.86;
    n.vy *= 0.86;
    n.x += n.vx;
    n.y += n.vy;
    const m = n.r + 4;
    if (n.x < m) n.x = m;
    if (n.x > w - m) n.x = w - m;
    if (n.y < m) n.y = m;
    if (n.y > h - m) n.y = h - m;
  }
}

function drawGraph() {
  if (!graph.loaded) return;
  const canvas = $("memory-graph");
  const { ctx, w, h } = fitCanvas(canvas);
  ctx.clearRect(0, 0, w, h);

  ctx.strokeStyle = cssVar("--graph-edge", "rgba(120,130,140,.3)");
  for (const e of graph.edges) {
    const a = graph.nodes[e.a];
    const b = graph.nodes[e.b];
    ctx.globalAlpha = Math.min(0.9, 0.15 + e.w * 0.12);
    ctx.lineWidth = Math.min(2.5, 0.5 + e.w * 0.3);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  const colors = {
    pet: cssVar("--graph-pet", "#35a08c"),
    private: cssVar("--graph-private", "#6f97d6"),
    group: cssVar("--graph-group", "#d9a441"),
  };
  for (const n of graph.nodes) {
    ctx.beginPath();
    ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
    ctx.fillStyle = colors[n.scope] || colors.pet;
    ctx.globalAlpha = graph.hover === n ? 1 : 0.88;
    ctx.fill();
    if (graph.hover === n) {
      ctx.globalAlpha = 1;
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = cssVar("--text", "#333");
      ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;
}

function graphLoop() {
  if (!graph.loaded) {
    graph.raf = 0;
    return;
  }
  tickGraph();
  drawGraph();
  // 稳定检测：全网速度足够小且不在拖拽时停表，交互会重新唤醒
  let maxV = 0;
  for (const n of graph.nodes) maxV = Math.max(maxV, Math.abs(n.vx) + Math.abs(n.vy));
  if (maxV < 0.02 && !graph.drag) {
    if (++graph.settle > 30) {
      graph.raf = 0;
      return;
    }
  } else {
    graph.settle = 0;
  }
  graph.raf = requestAnimationFrame(graphLoop);
}

function startGraphLoop() {
  if (graph.raf || !graph.loaded) return;
  if ($("panel-memory").classList.contains("hidden")) return;
  graph.settle = 0;
  graph.raf = requestAnimationFrame(graphLoop);
}

function stopGraphLoop() {
  cancelAnimationFrame(graph.raf);
  graph.raf = 0;
}

function resumeGraphLoop() {
  if (graph.loaded) startGraphLoop();
}

function graphPos(evt) {
  const rect = $("memory-graph").getBoundingClientRect();
  return { x: evt.clientX - rect.left, y: evt.clientY - rect.top };
}

function nodeAt(pos) {
  let best = null;
  let bestD = Infinity;
  for (const n of graph.nodes) {
    const d = Math.hypot(n.x - pos.x, n.y - pos.y);
    if (d < n.r + 4 && d < bestD) {
      best = n;
      bestD = d;
    }
  }
  return best;
}

function showGraphTooltip(n, p) {
  const tip = $("graph-tooltip");
  tip.innerHTML =
    `<div class="tip-meta">${esc(MEM_KIND_LABELS[n.kind] || n.kind)} · ${esc(MEM_SCOPE_LABELS[n.scope] || n.scope)} · 重要度 ${n.importance}${n.created_at ? ` · ${esc(String(n.created_at).slice(0, 10))}` : ""}</div>` +
    `<div>${esc(n.content)}</div>`;
  tip.classList.remove("hidden");
  moveGraphTooltip(p);
}

function moveGraphTooltip(p) {
  const tip = $("graph-tooltip");
  const wrap = $("memory-graph").parentElement.getBoundingClientRect();
  const x = Math.min(p.x + 12, Math.max(4, wrap.width - tip.offsetWidth - 8));
  const y = Math.max(4, p.y - 12 - tip.offsetHeight);
  tip.style.left = x + "px";
  tip.style.top = y + "px";
}

function hideGraphTooltip() {
  $("graph-tooltip").classList.add("hidden");
}

function bindGraphEvents() {
  const canvas = $("memory-graph");
  canvas.addEventListener("pointerdown", (e) => {
    if (!graph.loaded) return;
    const n = nodeAt(graphPos(e));
    if (n) {
      graph.drag = n;
      graph.hover = null;
      hideGraphTooltip();
      startGraphLoop();
      canvas.setPointerCapture(e.pointerId);
      e.preventDefault();
    }
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!graph.loaded) return;
    const p = graphPos(e);
    if (graph.drag) {
      const rect = canvas.getBoundingClientRect();
      graph.drag.x = Math.min(Math.max(p.x, graph.drag.r), rect.width - graph.drag.r);
      graph.drag.y = Math.min(Math.max(p.y, graph.drag.r), rect.height - graph.drag.r);
      graph.drag.vx = 0;
      graph.drag.vy = 0;
      startGraphLoop();
      return;
    }
    const n = nodeAt(p);
    if (n !== graph.hover) {
      graph.hover = n;
      if (n) showGraphTooltip(n, p);
      else hideGraphTooltip();
      drawGraph();
    } else if (n) {
      moveGraphTooltip(p);
    }
    canvas.style.cursor = n ? "pointer" : "default";
  });
  const endDrag = () => {
    if (graph.drag) {
      graph.drag = null;
      startGraphLoop();
    }
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("pointerleave", () => {
    if (!graph.drag) {
      graph.hover = null;
      hideGraphTooltip();
      drawGraph();
    }
  });
}

async function loadMemoryTab() {
  await Promise.all([loadMemoryConfig(), queryMemories(true), loadMemoryGraph()]);
}

// ---------- 主动对话 / 桌面感知 ----------

let sceneProviders = []; // GET 时附带的已配置 provider 列表（下拉建议+校验）

function formatProviderHint(p) {
  const flag = p.supports_image ? "支持图片" : "不支持图片";
  return `${p.id} · ${p.model} · ${flag}`;
}

function updateSceneProviderHint() {
  const input = $("scene-provider").value.trim();
  const hint = $("scene-provider-hint");
  if (!input) {
    hint.textContent = "留空：桌面感知将使用会话默认模型（推荐）。";
    return;
  }
  const matched = sceneProviders.find((p) => p.id === input);
  if (matched) {
    hint.textContent = `已选择：${formatProviderHint(matched)}`;
  } else {
    hint.textContent = `未匹配到已配置 provider「${input}」，保存时会校验失败。请从下拉建议中选择。`;
  }
}

async function loadSceneConfig() {
  const cfg = await bridge.apiGet("page/scene_config");
  $("proactive-enabled").checked = !!cfg.proactive_enabled;
  $("scene-enabled").checked = !!cfg.scene_enabled;
  $("scene-interval").value = String(cfg.scene_interval_min || 30);
  $("scene-provider").value = cfg.scene_provider || "";
  $("scene-blocklist").value = cfg.scene_blocklist || "";
  $("intent-perceive-enabled").checked = cfg.intent_perceive_enabled !== false;
  $("intent-perceive-keywords").value = cfg.intent_perceive_keywords || "";
  sceneProviders = cfg.providers || [];
  const dl = $("provider-list");
  dl.innerHTML = "";
  for (const p of sceneProviders) {
    const opt = document.createElement("option");
    opt.value = p.id;
    // label 用于浏览器下拉时显示更友好；不同浏览器表现略有差异，value 始终保持 provider id
    opt.label = `${p.model} · ${p.supports_image ? "支持图片" : "不支持图片"}`;
    dl.appendChild(opt);
  }
  updateSceneProviderHint();
}

async function saveSceneConfig() {
  $("btn-save-scene").disabled = true;
  $("scene-save-msg").textContent = "保存中…";
  try {
    await bridge.apiPost("page/scene_config", {
      proactive_enabled: $("proactive-enabled").checked,
      scene_enabled: $("scene-enabled").checked,
      scene_interval_min: Number($("scene-interval").value),
      scene_provider: $("scene-provider").value.trim(),
      scene_blocklist: $("scene-blocklist").value.trim(),
      intent_perceive_enabled: $("intent-perceive-enabled").checked,
      intent_perceive_keywords: $("intent-perceive-keywords").value,
    });
    $("scene-save-msg").textContent = "已保存，壳端约 2 分钟内拉取生效。";
    refreshStatusViews();
  } catch (e) {
    $("scene-save-msg").textContent = "保存失败：" + e.message;
  } finally {
    $("btn-save-scene").disabled = false;
    setTimeout(() => ($("scene-save-msg").textContent = ""), 5000);
  }
}

// 动态流水：上报状态 + 生效配置 + 最近感知 + shell_report.events 事件列表
function renderProactiveFlow(s) {
  const r = s.shell_report;
  const scene = s.scene || {};
  const reportLine = !r
    ? `<span class="txt-bad">● 暂无桌宠上报</span>（桌宠未运行或版本过旧；上报周期 60s）`
    : (() => {
        const age = s.shell_report_age_s;
        const stale = age == null || age > 180;
        return stale
          ? `<span class="txt-bad">● 桌宠上报已过期（${age} 秒前）</span>`
          : `<span class="txt-ok">● 桌宠在线（${age} 秒前上报）</span>`;
      })();
  let lastSceneLine = "";
  if (r && r.last_scene) {
    const ls = r.last_scene;
    const outcomeMap = {
      spoke: "已发言",
      skip: "略过（无可评论内容）",
      blocked: `拦截（${esc(ls.detail || "")}）`,
      error: `失败（${esc(ls.detail || "")}）`,
    };
    lastSceneLine = `\n最近一次感知：${esc(ls.t || "")} · ${outcomeMap[ls.outcome] || esc(ls.outcome || "")}`;
  }
  $("pet-report").innerHTML =
    `上报：${reportLine}\n` +
    `主动对话：${scene.proactive_enabled ? "已启用" : "已禁用"}\n` +
    `桌面感知：${scene.scene_enabled ? `已启用 · 每 ${scene.scene_interval_min ?? "?"} 分钟` : "已禁用"}\n` +
    `视觉模型：${esc(scene.provider || "（留空）跟随会话默认模型")}\n` +
    `禁止抓取：${esc(((scene.blocklist || []).join(", ")) || "（空）")}` +
    lastSceneLine;

  const box = $("proactive-events");
  const events = (r && Array.isArray(r.events) ? r.events : []).slice().reverse(); // 最新在前
  if (!events.length) {
    box.innerHTML = `<div class="msg">暂无动态</div>`;
    return;
  }
  box.innerHTML = "";
  for (const ev of events) {
    const item = document.createElement("div");
    item.className = "event-item";
    const isFire = ev.type === "fire";
    const text = isFire
      ? `触发「${esc(ev.rule || "?")}」${ev.prompt ? `：${esc(ev.prompt)}` : ""}`
      : `略过${ev.gate ? `（${esc(ev.gate)}）` : ""}${ev.reason ? `：${esc(ev.reason)}` : ""}`;
    item.innerHTML =
      `<span class="lamp lamp-${isFire ? "ok" : "off"} event-dot"></span>` +
      `<span class="event-t">${esc(ev.t || "")}</span>` +
      `<span class="event-text">${text}</span>`;
    box.appendChild(item);
  }
}

async function loadProactiveTab() {
  const st = await ensureStatus();
  if (!st) $("pet-report").textContent = "动态获取失败";
  await loadSceneConfig();
}

// ---------- 初始化 ----------

await bridge.ready();

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => goToTab(btn.dataset.tab));
});
window.addEventListener("hashchange", () => activateTab(tabFromHash()));

$("btn-refresh").addEventListener("click", () => { refreshStatus(); loadTokenStats(); });
$("btn-save-master").addEventListener("click", saveMasterConfig);
$("btn-save-dub").addEventListener("click", saveJpDub);
$("btn-save-persona").addEventListener("click", savePersona);
$("btn-save-scene").addEventListener("click", saveSceneConfig);
$("btn-save-asr").addEventListener("click", saveAsrConfig);
$("btn-save").addEventListener("click", saveConfig);
$("btn-test").addEventListener("click", testTts);
$("btn-memory-refresh").addEventListener("click", () => { loadMemoryConfig(); queryMemories(false); loadMemoryGraph(); });
$("btn-save-memory").addEventListener("click", saveMemoryConfig);
$("btn-memory-import").addEventListener("click", (e) => memoryOp("import_livingmemory", {}, e.target));
$("btn-memory-reflect").addEventListener("click", (e) => memoryOp("reflect_now", {}, e.target));
$("btn-memory-reembed").addEventListener("click", (e) => memoryOp("reembed", {}, e.target));
$("btn-memory-test").addEventListener("click", testMemoryRecall);
$("btn-memory-search").addEventListener("click", () => queryMemories(true));
$("memory-filter-scope").addEventListener("change", () => queryMemories(true));
$("btn-memory-prev").addEventListener("click", () => { memoryOffset = Math.max(0, memoryOffset - MEM_PAGE_LIMIT); queryMemories(false); });
$("btn-memory-next").addEventListener("click", () => { memoryOffset += MEM_PAGE_LIMIT; queryMemories(false); });
$("btn-memory-add").addEventListener("click", addMemory);
$("btn-graph-reload").addEventListener("click", loadMemoryGraph);
$("tts-model").addEventListener("change", () => {
  // 切换模型时说话人/风格跟随新模型，默认值用其第一个
  const m = (modelsInfo || {})[$("tts-model").value];
  currentCfg.tts_speaker_id = 0;
  currentCfg.tts_style = m && m.style2id ? Object.keys(m.style2id)[0] : "Neutral";
  onModelChange();
});
$("tts-length").addEventListener("input", () => {
  $("tts-length-val").textContent = Number($("tts-length").value).toFixed(2);
});
$("scene-provider").addEventListener("input", updateSceneProviderHint);

// 试听进度线：播放时 rAF 逐帧重绘，timeupdate 兜底（后台节流时仍大致正确）
const testAudio = $("test-audio");
testAudio.addEventListener("play", () => {
  cancelAnimationFrame(waveRaf);
  waveTick();
});
testAudio.addEventListener("timeupdate", () => {
  if (wavePeaks && testAudio.duration) drawWave(testAudio.currentTime / testAudio.duration);
});
testAudio.addEventListener("pause", () => cancelAnimationFrame(waveRaf));
testAudio.addEventListener("ended", () => {
  cancelAnimationFrame(waveRaf);
  drawWave(1);
});

bindGraphEvents();

window.addEventListener("resize", () => {
  if (wavePeaks && testAudio.duration) drawWave(testAudio.currentTime / testAudio.duration);
  else if (wavePeaks) drawWave(0);
  if (graph.loaded && !$("panel-memory").classList.contains("hidden")) {
    drawGraph();
    startGraphLoop(); // 尺寸变化后唤醒几帧让边界约束生效
  }
});

// 初始路由：默认总览，刷新后停留在原标签
activateTab(tabFromHash());
