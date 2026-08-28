const fs = require('fs');

// Update index.html
let index_html = fs.readFileSync('pet_shell/src/index.html', 'utf8');
index_html = index_html.replace(
  '<label>麦克风设备</label>',
  `<label>语音识别提示词 (Hotwords)</label>
    <input id="cfg-asr-prompt" type="text" spellcheck="false" placeholder="可选，用于指定专有名词等" />
    <label>麦克风设备</label>`
);
fs.writeFileSync('pet_shell/src/index.html', index_html);


// Update app.js
let app_js = fs.readFileSync('pet_shell/src/app.js', 'utf8');

app_js = app_js.replace(
  'function openSettings() {',
  `function openSettings() {
  const asrPrompt = $("cfg-asr-prompt");
  if (asrPrompt) asrPrompt.value = localStorage.getItem("pet_asr_prompt") || "";
`
);

app_js = app_js.replace(
  '  $("cfg-save").addEventListener("click", async () => {',
  `  $("cfg-save").addEventListener("click", async () => {
    const asrPrompt = $("cfg-asr-prompt");
    if (asrPrompt) localStorage.setItem("pet_asr_prompt", asrPrompt.value);
`
);

app_js = app_js.replace(
  'const raw = await invoke()("asr_transcribe", { url: asrUrl(), wavB64 });',
  `let url = asrUrl();
    const prompt = localStorage.getItem("pet_asr_prompt");
    if (prompt) {
      url += (url.includes("?") ? "&" : "?") + "initial_prompt=" + encodeURIComponent(prompt);
    }
    const raw = await invoke()("asr_transcribe", { url, wavB64 });`
);

fs.writeFileSync('pet_shell/src/app.js', app_js);

// Update asr_server.py
let asr_py = fs.readFileSync('pet_shell/tools/asr_server.py', 'utf8');
asr_py = asr_py.replace(
  '        if LANGUAGE:',
  `        initial_prompt = request.query_params.get("initial_prompt")
        if initial_prompt:
            options["initial_prompt"] = initial_prompt
        if LANGUAGE:`
);
fs.writeFileSync('pet_shell/tools/asr_server.py', asr_py);
