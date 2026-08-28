const fs = require('fs');
let app_js = fs.readFileSync('pet_shell/src/app.js', 'utf8');

// Fix stopMicRecording missing classList updates
app_js = app_js.replace(
  '    micBtn.classList.remove("recording");\n  if (micWaveform) micWaveform.classList.add("hidden");\n  if (micAnimFrame) cancelAnimationFrame(micAnimFrame);\n  chatInput.classList.remove("mic-recording");\n\n    showBubble();',
  '    micBtn.classList.remove("recording");\n    showBubble();'
);
app_js = app_js.replace(
  'function stopMicRecording() {\n  if (!micRecording) return;\n  micRecording = false;\n  clearTimeout(micCapTimer);\n  micBtn.classList.remove("recording");\n  if (micWorklet) { try { micWorklet.disconnect(); micWorklet.port.close(); } catch (e) {} }',
  'function stopMicRecording() {\n  if (!micRecording) return;\n  micRecording = false;\n  clearTimeout(micCapTimer);\n  micBtn.classList.remove("recording");\n  if (micWaveform) micWaveform.classList.add("hidden");\n  if (micAnimFrame) cancelAnimationFrame(micAnimFrame);\n  chatInput.classList.remove("mic-recording");\n  if (micWorklet) { try { micWorklet.disconnect(); micWorklet.port.close(); } catch (e) {} }'
);

// Fix populateMicDevices not being called
// find function setSettings() since it seems openSettings is not in app.js globally but might be inside an event listener? Let's check where the menu is handled
app_js = app_js.replace(
  '$("menu-settings").addEventListener("click", () => {\n  menu.classList.add("hidden");\n  $("cfg-mode").value = petMode();',
  '$("menu-settings").addEventListener("click", () => {\n  menu.classList.add("hidden");\n  populateMicDevices();\n  const asrPrompt = $("cfg-asr-prompt");\n  if (asrPrompt) asrPrompt.value = localStorage.getItem("pet_asr_prompt") || "";\n  $("cfg-mode").value = petMode();'
);

// Fix saving ASR prompt
app_js = app_js.replace(
  '$("cfg-save").addEventListener("click", () => {\n  const mode = $("cfg-mode").value;',
  '$("cfg-save").addEventListener("click", () => {\n  const mode = $("cfg-mode").value;\n  const asrPrompt = $("cfg-asr-prompt");\n  if (asrPrompt) localStorage.setItem("pet_asr_prompt", asrPrompt.value);'
);

fs.writeFileSync('pet_shell/src/app.js', app_js);
