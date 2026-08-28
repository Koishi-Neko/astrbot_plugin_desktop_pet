const fs = require('fs');
let app_js = fs.readFileSync('pet_shell/src/app.js', 'utf8');

// Remove empty block
app_js = app_js.replace(
  '      if (device.deviceId || device.label) {\n         // Some browsers return empty deviceId but has label if permissions are weird.\n         // Actually enumerateDevices often returns deviceId even if label is empty.\n         // Wait, deviceId is usually present. If it\'s a default generic one, we use index.\n      }',
  ''
);

// Fallback logic for deviceId
// Find: if (savedDevice) constraints.deviceId = { exact: savedDevice };
app_js = app_js.replace(
  '  try {\n    micStream = await navigator.mediaDevices.getUserMedia({\n      audio: (() => {\n        const savedDevice = localStorage.getItem("pet_mic_device");\n        const constraints = { echoCancellation: true, noiseSuppression: true, channelCount: 1 };\n        if (savedDevice) constraints.deviceId = { exact: savedDevice };\n        return constraints;\n      })(),\n    });',
  `  try {
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
    }`
);

fs.writeFileSync('pet_shell/src/app.js', app_js);
