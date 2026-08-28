const fs = require('fs');

// Update index.html
let index_html = fs.readFileSync('pet_shell/src/index.html', 'utf8');
index_html = index_html.replace(
  '<label class="settings-checkbox"><input id="cfg-voice" type="checkbox" /> 语音（日语配音）</label>\n    <div class="settings-divider"></div>',
  `<label class="settings-checkbox"><input id="cfg-voice" type="checkbox" /> 语音（日语配音）</label>
    <div class="settings-divider"></div>
    <label>麦克风设备</label>
    <select id="cfg-mic-device"><option value="">默认设备</option></select>
    <div class="settings-divider"></div>`
);
fs.writeFileSync('pet_shell/src/index.html', index_html);


// Update app.js
let app_js = fs.readFileSync('pet_shell/src/app.js', 'utf8');

// Add settings code
const populateCode = `
async function populateMicDevices() {
  const sel = $("cfg-mic-device");
  if (!sel) return;
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const audioInputs = devices.filter(d => d.kind === 'audioinput');

    // Clear existing except default
    while (sel.options.length > 1) {
      sel.remove(1);
    }

    audioInputs.forEach((device, index) => {
      // If label is empty (permission not granted yet), use generic name
      const label = device.label || \`麦克风 \${index + 1}\`;
      if (device.deviceId) {
        const option = document.createElement("option");
        option.value = device.deviceId;
        option.text = label;
        sel.appendChild(option);
      }
    });

    const saved = localStorage.getItem("pet_mic_device");
    if (saved) {
      sel.value = saved;
    }
  } catch (e) {
    console.warn("enumerateDevices failed:", e);
  }
}

$("cfg-mic-device")?.addEventListener("change", (e) => {
  localStorage.setItem("pet_mic_device", e.target.value);
});

// openSettings intercept
const origOpenSettings = openSettings;
function openSettings() {
  origOpenSettings();
  populateMicDevices();
}
`;

// Wait we can just append it or insert it where `function setSettings()` is.
app_js = app_js.replace('function openSettings() {\n  menu.classList.add("hidden");', `function openSettings() {
  menu.classList.add("hidden");
  populateMicDevices();`);

app_js += `
async function populateMicDevices() {
  const sel = $("cfg-mic-device");
  if (!sel) return;
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const audioInputs = devices.filter(d => d.kind === 'audioinput');

    while (sel.options.length > 1) {
      sel.remove(1);
    }

    audioInputs.forEach((device, index) => {
      const label = device.label || \`麦克风 \${index + 1}\`;
      if (device.deviceId || device.label) {
         // Some browsers return empty deviceId but has label if permissions are weird.
         // Actually enumerateDevices often returns deviceId even if label is empty.
         // Wait, deviceId is usually present. If it's a default generic one, we use index.
      }
      const option = document.createElement("option");
      option.value = device.deviceId;
      option.text = label;
      sel.appendChild(option);
    });

    const saved = localStorage.getItem("pet_mic_device");
    if (saved) {
      sel.value = saved;
    }
  } catch (e) {
    console.warn("enumerateDevices failed:", e);
  }
}

$("cfg-mic-device")?.addEventListener("change", (e) => {
  localStorage.setItem("pet_mic_device", e.target.value);
});
`;

// Modify startMicRecording constraint
app_js = app_js.replace(
  'audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },',
  `audio: (() => {
        const savedDevice = localStorage.getItem("pet_mic_device");
        const constraints = { echoCancellation: true, noiseSuppression: true, channelCount: 1 };
        if (savedDevice) constraints.deviceId = { exact: savedDevice };
        return constraints;
      })(),`
);

fs.writeFileSync('pet_shell/src/app.js', app_js);
