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
      `主人身份：${esc(s.master_name || "（未设置昵称）")}${s.master_qq ? ` (QQ ${esc(s.master_qq)})` : ""}\n` +
      `QQ 日语配音：${s.qq_jp_dub_enabled ? "已启用" : "已禁用"}\n` +
      `默认人格：${esc(s.default_persona || "（未设置）")}`;

    // 主动对话 / 桌面感知动态：配置以插件侧为准，运行态来自壳端心跳上报
    const r = s.shell_report;
    const scene = s.scene || {};
    const reportLine = !r
      ? `<span class="bad">● 暂无桌宠上报</span>（桌宠未运行或版本过旧；上报周期 60s）`
      : (() => {
          const age = s.shell_report_age_s;
          const stale = age == null || age > 180;
          return stale
            ? `<span class="bad">● 桌宠上报已过期（${age} 秒前）</span>`
            : `<span class="ok">● 桌宠在线（${age} 秒前上报）</span>`;
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
  } catch (e) {
    $("status-box").textContent = "状态获取失败：" + e.message;
  }
}

// ---------- 主人身份配置区 ----------

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
    refreshStatus();
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
    refreshStatus();
  } catch (e) {
    $("master-save-msg").textContent = "保存失败：" + e.message;
  } finally {
    $("btn-save-master").disabled = false;
    setTimeout(() => ($("master-save-msg").textContent = ""), 4000);
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

// ---------- 主动对话 / 桌面感知配置区 ----------

let sceneProviders = []; // GET 时附带的已配置 provider 列表（下拉建议+校验）

async function loadSceneConfig() {
  const cfg = await bridge.apiGet("page/scene_config");
  $("proactive-enabled").checked = !!cfg.proactive_enabled;
  $("scene-enabled").checked = !!cfg.scene_enabled;
  $("scene-interval").value = String(cfg.scene_interval_min || 30);
  $("scene-provider").value = cfg.scene_provider || "";
  $("scene-blocklist").value = cfg.scene_blocklist || "";
  sceneProviders = cfg.providers || [];
  const dl = $("provider-list");
  dl.innerHTML = "";
  for (const p of sceneProviders) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.label = `${p.model}${p.supports_image ? "（支持图片）" : "（不支持图片）"}`;
    dl.appendChild(opt);
  }
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
    });
    $("scene-save-msg").textContent = "已保存，壳端约 2 分钟内拉取生效。";
    refreshStatus();
  } catch (e) {
    $("scene-save-msg").textContent = "保存失败：" + e.message;
  } finally {
    $("btn-save-scene").disabled = false;
    setTimeout(() => ($("scene-save-msg").textContent = ""), 5000);
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
$("btn-save-master").addEventListener("click", saveMasterConfig);
$("btn-save-dub").addEventListener("click", saveJpDub);
$("btn-save-scene").addEventListener("click", saveSceneConfig);
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
await Promise.all([refreshStatus(), loadModels(), loadMasterConfig(), loadSceneConfig()]);
// 配置里的 style/speaker 选中值在模型列表加载后应用一次
onModelChange();
