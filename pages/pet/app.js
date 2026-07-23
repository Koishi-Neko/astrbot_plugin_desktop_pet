const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);

let modelsInfo = null; // SBV2 /models/info 原文
let currentCfg = {};   // 已从服务端读取的 tts 配置

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// ---------- 状态区 ----------

async function refreshStatus() {
  $("status-box").innerHTML = "加载中…";
  try {
    const s = await bridge.apiGet("page/status");
    const sb = s.sbv2 || {};
    let sbv2Line;
    if (sb.reachable) {
      const gpu = (sb.gpu && sb.gpu[0]) || {};
      sbv2Line = `<span class="ok">● 可达</span>  延迟 ${sb.latency_ms}ms  设备 ${esc((sb.devices || []).join(", "))}` +
        (gpu.gpu_memory ? `  显存 ${Math.round(gpu.gpu_memory.used)}/${Math.round(gpu.gpu_memory.total)}MB` : "");
    } else {
      sbv2Line = `<span class="bad">● 不可达</span>  ${esc(sb.error || "")}`;
    }
    $("status-box").innerHTML =
      `插件：astrbot_plugin_desktop_pet\n` +
      `TTS：${s.tts_enabled ? "已启用" : "已禁用"}\n` +
      `SBV2：${sbv2Line}\n` +
      `桌宠会话 ID：${esc(s.pet_session_id)}\n` +
      `默认人格：${esc(s.default_persona || "（未设置）")}`;
  } catch (e) {
    $("status-box").textContent = "状态获取失败：" + e.message;
  }
}

// ---------- TTS 配置区 ----------

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
    refreshStatus();
  } catch (e) {
    $("save-msg").textContent = "保存失败：" + e.message;
  } finally {
    $("btn-save").disabled = false;
    setTimeout(() => ($("save-msg").textContent = ""), 4000);
  }
}

// ---------- 试听 ----------

async function testTts() {
  $("btn-test").disabled = true;
  $("test-msg").textContent = "合成中…";
  try {
    const r = await bridge.apiPost("page/tts_test", {
      text: $("test-text").value,
      ...collectConfig(),
    });
    const audio = $("test-audio");
    audio.src = "data:audio/wav;base64," + r.audio;
    audio.classList.remove("hidden");
    await audio.play().catch(() => {});
    $("test-msg").textContent = "播放中";
  } catch (e) {
    $("test-msg").textContent = "合成失败：" + e.message;
  } finally {
    $("btn-test").disabled = false;
  }
}

// ---------- 初始化 ----------

await bridge.ready();
$("btn-refresh").addEventListener("click", refreshStatus);
$("btn-save").addEventListener("click", saveConfig);
$("btn-test").addEventListener("click", testTts);
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

await loadConfig();
await Promise.all([refreshStatus(), loadModels()]);
// 配置里的 style/speaker 选中值在模型列表加载后应用一次
onModelChange();
