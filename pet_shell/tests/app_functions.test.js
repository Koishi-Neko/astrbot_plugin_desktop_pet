const { test, describe } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sourcePath = path.join(__dirname, '..', 'src', 'app.js');
const source = fs.readFileSync(sourcePath, 'utf8');

/**
 * Extracts a function definition from the source text and evaluates it in an isolated VM context.
 * This is used to test pure functions from app.js without needing to modify it into an ES module.
 */
function extractFunction(sourceCode, fnName) {
  // Regex to extract the function body, handling any parameters
  const regex = new RegExp(`^function ${fnName}\\s*\\([^)]*\\)\\s*\\{([\\s\\S]*?)^\\}`, 'm');
  const match = sourceCode.match(regex);
  if (!match) {
    throw new Error(`Function ${fnName} not found in source.`);
  }
  const code = match[0];
  const ctx = vm.createContext({});
  vm.runInContext(code, ctx);
  return vm.runInContext(fnName, ctx);
}

const sceneWindowKey = extractFunction(source, 'sceneWindowKey');
const fmtDur = extractFunction(source, 'fmtDur');
const normalizeBaseUrl = extractFunction(source, 'normalizeBaseUrl');

describe('sceneWindowKey', () => {
  test('returns empty string if proc is falsy', () => {
    assert.strictEqual(sceneWindowKey(null, 'Title'), '');
    assert.strictEqual(sceneWindowKey('', 'Title'), '');
    assert.strictEqual(sceneWindowKey(undefined, 'Title'), '');
  });

  test('handles empty or null title', () => {
    assert.strictEqual(sceneWindowKey('proc.exe', null), 'proc.exe|');
    assert.strictEqual(sceneWindowKey('proc.exe', ''), 'proc.exe|');
    assert.strictEqual(sceneWindowKey('proc.exe', undefined), 'proc.exe|');
  });

  test('strips unread-count prefixes with halfwidth parens', () => {
    assert.strictEqual(sceneWindowKey('wechat.exe', '(3) WeChat'), 'wechat.exe|WeChat');
    assert.strictEqual(sceneWindowKey('wechat.exe', ' (10)   WeChat'), 'wechat.exe|WeChat');
  });

  test('strips unread-count prefixes with fullwidth parens', () => {
    assert.strictEqual(sceneWindowKey('wechat.exe', '（3）WeChat'), 'wechat.exe|WeChat');
    assert.strictEqual(sceneWindowKey('wechat.exe', '  （10） WeChat'), 'wechat.exe|WeChat');
  });

  test('collapses multi-whitespace and trims', () => {
    assert.strictEqual(sceneWindowKey('proc.exe', '  Hello   World  '), 'proc.exe|Hello World');
    assert.strictEqual(sceneWindowKey('proc.exe', 'A \t B \n C'), 'proc.exe|A B C');
  });

  test('combines prefix stripping and whitespace collapsing', () => {
    assert.strictEqual(sceneWindowKey('proc.exe', ' (1)   Hello   World  '), 'proc.exe|Hello World');
  });
});

describe('fmtDur', () => {
  test('formats sub-hour durations', () => {
    assert.strictEqual(fmtDur(0), '0分钟');
    assert.strictEqual(fmtDur(60000), '1分钟');
    assert.strictEqual(fmtDur(59 * 60000), '59分钟');
  });

  test('formats exact-hour durations', () => {
    assert.strictEqual(fmtDur(60 * 60000), '1小时');
    assert.strictEqual(fmtDur(120 * 60000), '2小时');
  });

  test('formats hour+minute durations', () => {
    assert.strictEqual(fmtDur(61 * 60000), '1小时1分钟');
    assert.strictEqual(fmtDur(150 * 60000), '2小时30分钟');
  });

  test('rounds to nearest minute', () => {
    assert.strictEqual(fmtDur(29999), '0分钟'); // 0.499 mins -> 0 mins
    assert.strictEqual(fmtDur(30000), '1分钟'); // 0.5 mins -> 1 min
    assert.strictEqual(fmtDur(89999), '1分钟'); // 1.499 mins -> 1 min
    assert.strictEqual(fmtDur(90000), '2分钟'); // 1.5 mins -> 2 mins
    // 59.5 mins -> 60 mins -> 1 hour
    assert.strictEqual(fmtDur(59.5 * 60000), '1小时');
  });
});

describe('normalizeBaseUrl', () => {
  test('handles empty or blank input', () => {
    assert.strictEqual(normalizeBaseUrl(''), '');
    assert.strictEqual(normalizeBaseUrl('   '), '');
    assert.strictEqual(normalizeBaseUrl(null), '');
    assert.strictEqual(normalizeBaseUrl(undefined), '');
  });

  test('appends API path if missing', () => {
    assert.strictEqual(normalizeBaseUrl('http://example.com'), 'http://example.com/api/v1/plugins/extensions');
    assert.strictEqual(normalizeBaseUrl('http://example.com:8080'), 'http://example.com:8080/api/v1/plugins/extensions');
  });

  test('strips trailing slashes before appending', () => {
    assert.strictEqual(normalizeBaseUrl('http://example.com/'), 'http://example.com/api/v1/plugins/extensions');
    assert.strictEqual(normalizeBaseUrl('http://example.com///'), 'http://example.com/api/v1/plugins/extensions');
  });

  test('does not append API path if already present', () => {
    assert.strictEqual(normalizeBaseUrl('http://example.com/api/v1/plugins/extensions'), 'http://example.com/api/v1/plugins/extensions');
    assert.strictEqual(normalizeBaseUrl('http://example.com/api/v1/plugins/extensions/'), 'http://example.com/api/v1/plugins/extensions');
    assert.strictEqual(normalizeBaseUrl('http://example.com/api/v1'), 'http://example.com/api/v1');
    assert.strictEqual(normalizeBaseUrl('http://example.com/api/v1/'), 'http://example.com/api/v1');
  });
});
