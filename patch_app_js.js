const fs = require('fs');

let app_js = fs.readFileSync('pet_shell/src/app.js', 'utf8');

// Add global variable
app_js = app_js.replace('const micBtn = $("mic-btn");', `const micBtn = $("mic-btn");
const micWaveform = $("mic-waveform");
let micWaveformCtx = null;
let micRmsHistory = new Array(30).fill(0); // For 60px width, 2px per bar
let micAnimFrame = null;

function drawWaveform() {
  if (!micWaveformCtx && micWaveform) {
    micWaveformCtx = micWaveform.getContext("2d");
  }
  if (!micWaveformCtx) return;
  const ctx = micWaveformCtx;
  const w = micWaveform.width;
  const h = micWaveform.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#e53935";
  const barW = w / micRmsHistory.length;
  for (let i = 0; i < micRmsHistory.length; i++) {
    const rms = micRmsHistory[i];
    // max expected rms for normal speech is ~0.1 to 0.3
    const barH = Math.min(h, Math.max(2, (rms / 0.15) * h));
    const x = i * barW;
    const y = (h - barH) / 2;
    ctx.fillRect(x, y, barW - 1, barH);
  }
  if (micRecording) {
    micAnimFrame = requestAnimationFrame(drawWaveform);
  }
}
`);

// Add to startMicRecording
app_js = app_js.replace('  micBtn.classList.add("recording");', `  micBtn.classList.add("recording");
  if (micWaveform) micWaveform.classList.remove("hidden");
  micRmsHistory.fill(0);
  if (micAnimFrame) cancelAnimationFrame(micAnimFrame);
  drawWaveform();
  chatInput.classList.add("mic-recording");
`);

// Add to stopMicRecording
app_js = app_js.replace('  micBtn.classList.remove("recording");', `  micBtn.classList.remove("recording");
  if (micWaveform) micWaveform.classList.add("hidden");
  if (micAnimFrame) cancelAnimationFrame(micAnimFrame);
  chatInput.classList.remove("mic-recording");
`);


app_js = app_js.replace('      micNoiseFloor = micNoiseFloor * 0.98 + Math.min(rms, 0.02) * 0.02; // 静音期缓慢跟踪底噪\n    }', `      micNoiseFloor = micNoiseFloor * 0.98 + Math.min(rms, 0.02) * 0.02; // 静音期缓慢跟踪底噪
    }
    micRmsHistory.push(rms);
    micRmsHistory.shift();`);

fs.writeFileSync('pet_shell/src/app.js', app_js);

let style_css = fs.readFileSync('pet_shell/src/style.css', 'utf8');
style_css = style_css.replace('padding: 8px 12px 8px 36px;', 'padding: 8px 12px 8px 36px; transition: padding 0.2s;');
style_css = style_css.replace('#chat-input.mic-pending {\n  padding-left: 104px; /* Give room for mic btn + waveform */\n}', '#chat-input.mic-recording {\n  padding-left: 104px;\n}');
fs.writeFileSync('pet_shell/src/style.css', style_css);
