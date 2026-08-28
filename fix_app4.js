const fs = require('fs');
let app_js = fs.readFileSync('pet_shell/src/app.js', 'utf8');

if (!app_js.includes("micRmsHistory.push(rms)")) {
    app_js = app_js.replace(
    '      micNoiseFloor = micNoiseFloor * 0.98 + Math.min(rms, 0.02) * 0.02; // 静音期缓慢跟踪底噪\n      if (micSpeechSeen && micSilenceMs >= ASR_SILENCE_MS) {',
    '      micNoiseFloor = micNoiseFloor * 0.98 + Math.min(rms, 0.02) * 0.02; // 静音期缓慢跟踪底噪\n    }\n    micRmsHistory.push(rms);\n    micRmsHistory.shift();\n    if (!micSpeechSeen) {\n      if (micSpeechSeen && micSilenceMs >= ASR_SILENCE_MS) {' // just to maintain syntax, but wait, the original logic is:
    );
}
