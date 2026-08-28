const fs = require('fs');
let app_js = fs.readFileSync('pet_shell/src/app.js', 'utf8');
app_js = app_js.replace(
  '$("menu-settings").addEventListener("click", () => {\n  const cfg = loadConfig();',
  '$("menu-settings").addEventListener("click", () => {\n  populateMicDevices();\n  const asrPrompt = $("cfg-asr-prompt");\n  if (asrPrompt) asrPrompt.value = localStorage.getItem("pet_asr_prompt") || "";\n  const cfg = loadConfig();'
);
fs.writeFileSync('pet_shell/src/app.js', app_js);
