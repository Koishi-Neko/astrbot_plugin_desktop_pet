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

    // 语音输入运行状态：配置以插件侧为准，运行态来自壳端心跳上报
    const asrCfg = s.asr || {};
    const asrSt = s.asr_state;
    let asrLine;
    if (!asrSt) {
      asrLine = `<span class="bad">● 暂无上报</span>（桌宠未运行或版本过旧）`;
    } else if (asrSt.ready) {
      asrLine = `<span class="ok">● 就绪</span>  ${esc(asrSt.device || "")} ${esc(asrSt.model || "")}` +
        (asrSt.url ? `  @${esc(asrSt.url)}` : "");
    } else if (asrSt.loading) {
      asrLine = `<span class="warn-color">● 加载中</span>  （首次约 4 分钟）`;
    } else {
      asrLine = `<span class="bad">● 异常</span>  ${esc(asrSt.error || "未知")}`;
    }
    $("asr-state").innerHTML =
      `语音输入：${asrCfg.voice_input_enabled === false ? "已关闭" : "已启用"}\n` +
      `识别服务：${asrLine}`;

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

async function loadTokenStats() {
  const box = $("token-stats-box");
  box.innerHTML = "加载中…";
  try {
    const s = await bridge.apiGet("page/token_stats");
    if (!s.has_data) {
      box.innerHTML = "暂无数据";
      return;
    }
    const st = s.stats;
    const formatLine = (title, data) => {
      const { input, cached, output, ttft_avg } = data;
      return `【${title}】 输入 ${input} (其中命中缓存 ${cached}) / 输出 ${output} / 平均首字响应 ${ttft_avg}s`;
    };
    box.innerHTML = formatLine("今日", st.today) + "\n" + formatLine("总计", st.all_time);
  } catch (e) {
    box.innerHTML = "暂无数据";
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

// ---------- 桌宠人格 ----------

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

function formatProviderHint(p) {
  const flag = p.supports_image ? "支持图片" : "不支持图片";
  return `${p.id} · ${p.model} · ${flag}`;
}

function updateSceneProviderHint() {
  const input = $("scene-provider").value.trim();
  const hint = $("scene-provider-hint");
  if (!input) {
    hint.textContent = "留空：桌面感知将使用会话默认模型（推荐）。";
    hint.className = "warn";
    return;
  }
  const matched = sceneProviders.find((p) => p.id === input);
  if (matched) {
    hint.textContent = `已选择：${formatProviderHint(matched)}`;
    hint.className = "warn";
  } else {
    hint.textContent = `未匹配到已配置 provider「${input}」，保存时会校验失败。请从下拉建议中选择。`;
    hint.className = "warn";
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
    refreshStatus();
  } catch (e) {
    $("scene-save-msg").textContent = "保存失败：" + e.message;
  } finally {
    $("btn-save-scene").disabled = false;
    setTimeout(() => ($("scene-save-msg").textContent = ""), 5000);
  }
}

// ---------- 语音输入配置区 ----------

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
    refreshStatus();
  } catch (e) {
    $("asr-save-msg").textContent = "保存失败：" + e.message;
  } finally {
    $("btn-save-asr").disabled = false;
    setTimeout(() => ($("asr-save-msg").textContent = ""), 5000);
  }
}

// ---------- 内置记忆 ----------

const MEM_KIND_LABELS = { profile: "档案", fact: "事实", event: "事件", mood: "心情", promise: "约定", scene: "观察", diary: "日记" };
const MEM_SCOPE_LABELS = { pet: "桌宠", private: "私聊", group: "群聊" };
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
  $("memory-reflect-rounds").value = cfg.memory_reflect_rounds ?? 8;
  $("memory-recall-topk").value = cfg.memory_recall_top_k ?? 5;
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
    box.innerHTML = `<span class="bad">● 记忆存储未就绪${s && s.error ? "：" + esc(s.error) : ""}</span>`;
    return;
  }
  if (!s.enabled) {
    box.innerHTML = `<span class="warn-color">● 内置记忆已停用</span>`;
    return;
  }
  const kindStr = Object.entries(s.by_kind || {}).map(([k, n]) => `${MEM_KIND_LABELS[k] || k} ${n}`).join(" / ");
  const scopeStr = Object.entries(s.by_scope || {}).map(([k, n]) => `${MEM_SCOPE_LABELS[k] || k} ${n}`).join(" / ");
  let vecLine;
  if (s.vector) {
    vecLine = `<span class="ok">● 向量召回</span>  ${esc(s.embedding_provider || "")}${s.embedding_dim ? ` · ${s.embedding_dim} 维` : ""}` +
      `  · 覆盖率 ${s.with_embedding}/${s.total_active}` +
      (s.stale_embedding ? `  <span class="warn-color">（${s.stale_embedding} 条向量待重建）</span>` : "");
  } else {
    vecLine = `<span class="warn-color">● 降级召回</span>（无可用嵌入模型，按重要度+时效召回）`;
  }
  box.innerHTML =
    `记忆库：共 ${s.total_active} 条（${kindStr || "暂无"}）${scopeStr ? `\n范围分布：${scopeStr}` : ""}\n` +
    `召回：${vecLine}\n` +
    `待反思：${s.unreflected_pairs} 轮 · 最近反思：${esc(s.last_reflect_at || "（从未）")} · 最近日记：${esc(s.last_diary_date || "（无）")}`;
}

async function saveMemoryConfig() {
  $("btn-save-memory").disabled = true;
  $("memory-save-msg").textContent = "保存中…";
  try {
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
      memory_reflect_rounds: Number($("memory-reflect-rounds").value),
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

async function queryMemories(reset) {
  if (reset) memoryOffset = 0;
  const q = encodeURIComponent($("memory-search").value.trim());
  const scope = encodeURIComponent($("memory-filter-scope").value);
  try {
    const r = await bridge.apiGet(`page/memory_query?q=${q}&scope=${scope}&offset=${memoryOffset}&limit=${MEM_PAGE_LIMIT}`);
    renderMemoryState(r.summary);
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
$("btn-refresh").addEventListener("click", () => { refreshStatus(); loadTokenStats(); });
$("btn-save-master").addEventListener("click", saveMasterConfig);
$("btn-save-dub").addEventListener("click", saveJpDub);
$("btn-save-persona").addEventListener("click", savePersona);
$("btn-save-scene").addEventListener("click", saveSceneConfig);
$("btn-save-asr").addEventListener("click", saveAsrConfig);
$("btn-save").addEventListener("click", saveConfig);
$("btn-test").addEventListener("click", testTts);
$("btn-memory-refresh").addEventListener("click", () => { loadMemoryConfig(); queryMemories(false); });
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

await loadConfig();
await Promise.all([refreshStatus(), loadTokenStats(), loadModels(), loadMasterConfig(), loadSceneConfig(), loadAsrConfig(), loadPersonaConfig(), loadMemoryConfig(), queryMemories(true)]);
// 配置里的 style/speaker 选中值在模型列表加载后应用一次
onModelChange();
