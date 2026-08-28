const { execSync } = require('child_process');

try {
  execSync('node --check pet_shell/src/app.js');
  console.log("Syntax is OK");
} catch (e) {
  console.log("Syntax error");
}
