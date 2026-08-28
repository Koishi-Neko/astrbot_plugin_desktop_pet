const fs = require('fs');

let index_html = fs.readFileSync('pet_shell/src/index.html', 'utf8');
index_html = index_html.replace('<input id="chat-input"', '<canvas id="mic-waveform" width="60" height="24" class="hidden"></canvas>\n    <input id="chat-input"');
fs.writeFileSync('pet_shell/src/index.html', index_html);

let style_css = fs.readFileSync('pet_shell/src/style.css', 'utf8');
if (!style_css.includes('#mic-waveform')) {
    style_css += `

#mic-waveform {
  position: absolute;
  left: 36px;
  top: 50%;
  transform: translateY(-50%);
  border-radius: 4px;
  pointer-events: none;
}
#chat-input.mic-pending {
  padding-left: 104px; /* Give room for mic btn + waveform */
}
`;
    // also need to change #chat-input padding? Original #chat-input has padding-left: 36px
    style_css = style_css.replace('#chat-input {\n  width: 100%;\n  border: 1px solid #ccc;\n  border-radius: 16px;\n  padding: 6px 12px 6px 36px;', '#chat-input {\n  width: 100%;\n  border: 1px solid #ccc;\n  border-radius: 16px;\n  padding: 6px 12px 6px 104px;');
    fs.writeFileSync('pet_shell/src/style.css', style_css);
}
