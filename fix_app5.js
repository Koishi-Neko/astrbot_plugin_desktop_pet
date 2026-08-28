const fs = require('fs');
let app_js = fs.readFileSync('pet_shell/src/app.js', 'utf8');

app_js = app_js.replace(
  '    if (!micSpeechSeen && Date.now() - micStartTime > ASR_PRESPEECH_MAX_MS) {\n      stopMicRecording();\n    }\n  }',
  '    if (!micSpeechSeen && Date.now() - micStartTime > ASR_PRESPEECH_MAX_MS) {\n      stopMicRecording();\n    }\n    micRmsHistory.push(rms);\n    micRmsHistory.shift();\n  }'
);

fs.writeFileSync('pet_shell/src/app.js', app_js);
