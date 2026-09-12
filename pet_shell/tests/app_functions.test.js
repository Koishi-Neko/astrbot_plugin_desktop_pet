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

describe('matchPerceiveIntent（指令感知匹配器）', () => {
  // matchPerceiveIntent 依赖模块级 const INTENT_NEGATIONS，一并注入同一 VM 上下文
  function loadIntentMatcher() {
    const ctx = vm.createContext({});
    const negations = source.match(/^const INTENT_NEGATIONS = \[[\s\S]*?\];/m)[0];
    const defaults = source.match(/^const INTENT_PERCEIVE_DEFAULT_KEYWORDS = \[[\s\S]*?\];/m)[0];
    const fn = source.match(/^function matchPerceiveIntent\s*\([^)]*\)\s*\{[\s\S]*?^\}/m)[0];
    vm.runInContext(
      `${negations}\n${defaults}\n${fn}\n` +
        'this.matchPerceiveIntent = matchPerceiveIntent;\n' +
        'this.DEFAULT_KEYWORDS = INTENT_PERCEIVE_DEFAULT_KEYWORDS;',
      ctx
    );
    return { match: ctx.matchPerceiveIntent, DEFAULT_KEYWORDS: ctx.DEFAULT_KEYWORDS };
  }
  const { match, DEFAULT_KEYWORDS } = loadIntentMatcher();

  test('命中常见中文指令', () => {
    assert.strictEqual(match('看看我的屏幕', DEFAULT_KEYWORDS), '看我的屏幕');
    assert.strictEqual(match('快看看屏幕！', DEFAULT_KEYWORDS), '看看屏幕');
    assert.strictEqual(match('我在干嘛呢', DEFAULT_KEYWORDS), '我在干嘛');
    assert.strictEqual(match('你知道我在做什么吗', DEFAULT_KEYWORDS), '我在做什么');
    assert.strictEqual(match('看看这个页面', DEFAULT_KEYWORDS), '看看这个');
    assert.strictEqual(match('屏幕上有什么好玩的', DEFAULT_KEYWORDS), '屏幕上');
  });

  test('命中英文指令（大小写/空白不敏感）', () => {
    assert.strictEqual(match('Look At My Screen!', DEFAULT_KEYWORDS), 'look at my screen');
    assert.strictEqual(match('WHAT AM I DOING now', DEFAULT_KEYWORDS), 'what am i doing');
  });

  test('空白容忍（ASR 转写可能带多余空格）', () => {
    assert.strictEqual(match('看 看 屏 幕', DEFAULT_KEYWORDS), '看看屏幕');
    assert.strictEqual(match('look  at   my screen', DEFAULT_KEYWORDS), 'look at my screen');
  });

  test('否定护栏：含否定词一律不触发', () => {
    assert.strictEqual(match('别看我的屏幕', DEFAULT_KEYWORDS), null);
    assert.strictEqual(match('不要看屏幕', DEFAULT_KEYWORDS), null);
    assert.strictEqual(match('不许看我在干嘛', DEFAULT_KEYWORDS), null);
  });

  test('无关文本/空输入不触发', () => {
    assert.strictEqual(match('今天天气不错', DEFAULT_KEYWORDS), null);
    assert.strictEqual(match('屏幕好亮', DEFAULT_KEYWORDS), null);
    assert.strictEqual(match('', DEFAULT_KEYWORDS), null);
    assert.strictEqual(match(null, DEFAULT_KEYWORDS), null);
    assert.strictEqual(match(undefined, DEFAULT_KEYWORDS), null);
  });

  test('自定义关键词列表生效', () => {
    assert.strictEqual(match('帮我截个图', ['截个图']), '截个图');
    assert.strictEqual(match('帮我截个图', ['别的词']), null);
    assert.strictEqual(match('看看屏幕', []), null); // 空列表不触发
  });

  test('默认关键词每条都能自洽命中', () => {
    for (const kw of DEFAULT_KEYWORDS) {
      assert.strictEqual(match(kw, DEFAULT_KEYWORDS) !== null, true, `默认关键词「${kw}」应能命中`);
    }
  });
});

describe('intentFailNote（截图失败解释附注）', () => {
  const intentFailNote = extractFunction(source, 'intentFailNote');

  test('名单拦截：点名进程 + 不可抓取', () => {
    const note = intentFailNote(new Error('blocked:weixin.exe'));
    assert.ok(note.includes('weixin.exe'));
    assert.ok(note.includes('不可抓取'));
  });

  test('已知错误语义映射', () => {
    assert.ok(intentFailNote(new Error('self_window')).includes('没有其他可抓取'));
    assert.ok(intentFailNote(new Error('minimized')).includes('最小化'));
    assert.ok(intentFailNote(new Error('black_frame')).includes('受保护'));
    assert.ok(intentFailNote(new Error('no_foreground')).includes('没有前台窗口'));
  });

  test('未知错误走通用文案且截断', () => {
    const note = intentFailNote(new Error('wgc_interop: something went wrong'));
    assert.ok(note.includes('截图失败'));
    assert.ok(note.includes('请向主人说明'));
  });

  test('字符串入参也能处理', () => {
    assert.ok(intentFailNote('blocked:qq.exe').includes('qq.exe'));
  });
});

describe('proactiveLog functions', () => {
  function createCtx() {
    const store = {};
    const ctx = vm.createContext({
      localStorage: {
        getItem(key) { return store[key] || null; },
        setItem(key, val) { store[key] = String(val); },
        clear() { for(let k in store) delete store[k]; }
      },
      reportStatusSoon: () => {}
    });
    const logFireStr = source.match(/^function proactiveLogFire\s*\([^)]*\)\s*\{[\s\S]*?^\}/m)[0];
    const logSkipStr = source.match(/^function proactiveLogSkip\s*\([^)]*\)\s*\{[\s\S]*?^\}/m)[0];
    vm.runInContext(logFireStr, ctx);
    vm.runInContext(logSkipStr, ctx);
    return { ctx, store };
  }

  test('dedup: same gate+reason+rule bumps existing entry to the end and refreshes timestamp', () => {
    const { ctx, store } = createCtx();
    const proactiveLogSkip = (gateName, reason, ruleId) => vm.runInContext(`proactiveLogSkip("${gateName}", "${reason}"${ruleId !== undefined ? `, "${ruleId}"` : ''})`, ctx);

    proactiveLogSkip("tick", "disabled");
    const t1 = JSON.parse(store["pet_proactive_log"])[0].t;
    proactiveLogSkip("scene", "debouncing");
    proactiveLogSkip("tick", "disabled"); // Dedup test without ruleId

    let logs = JSON.parse(store["pet_proactive_log"]);
    assert.strictEqual(logs.length, 2);
    assert.strictEqual(logs[0].gate, "scene");
    assert.strictEqual(logs[1].gate, "tick");

    proactiveLogSkip("rule", "cooldown", "night_owl");
    proactiveLogSkip("scene", "idle");
    proactiveLogSkip("rule", "cooldown", "night_owl"); // Dedup test with ruleId
    logs = JSON.parse(store["pet_proactive_log"]);
    assert.strictEqual(logs.length, 4);
    assert.strictEqual(logs[2].gate, "scene");
    assert.strictEqual(logs[3].gate, "rule");
    assert.strictEqual(logs[3].rule, "night_owl");
  });

  test('fire entry and skip entry coexist and keep their type', () => {
    const { ctx, store } = createCtx();
    const proactiveLogSkip = (gateName, reason) => vm.runInContext(`proactiveLogSkip("${gateName}", "${reason}")`, ctx);
    const proactiveLogFire = (ruleId, prompt) => vm.runInContext(`proactiveLogFire("${ruleId}", "${prompt}")`, ctx);

    proactiveLogSkip("tick", "global_cd");
    proactiveLogFire("night_owl", "hello");
    proactiveLogSkip("tick", "global_cd");

    const logs = JSON.parse(store["pet_proactive_log"]);
    assert.strictEqual(logs.length, 2);
    assert.strictEqual(logs[0].type, "fire");
    assert.strictEqual(logs[0].rule, "night_owl");
    assert.strictEqual(logs[1].type, "skip");
    assert.strictEqual(logs[1].gate, "tick");
  });

  test('buffer is capped at 20 entries (oldest dropped)', () => {
    const { ctx, store } = createCtx();
    const proactiveLogSkip = (gateName, reason) => vm.runInContext(`proactiveLogSkip("${gateName}", "${reason}")`, ctx);

    for (let i = 0; i < 25; i++) {
      proactiveLogSkip("scene", `reason_${i}`);
    }
    const logs = JSON.parse(store["pet_proactive_log"]);
    assert.strictEqual(logs.length, 20);
    assert.strictEqual(logs[0].reason, "reason_5");
    assert.strictEqual(logs[19].reason, "reason_24");
  });

  test('corrupt localStorage JSON does not throw', () => {
    const { ctx, store } = createCtx();
    store["pet_proactive_log"] = "{ invalid json";

    // Test skip
    const proactiveLogSkip = (gateName, reason) => vm.runInContext(`proactiveLogSkip("${gateName}", "${reason}")`, ctx);
    assert.doesNotThrow(() => {
      proactiveLogSkip("tick", "disabled");
    });

    store["pet_proactive_log"] = "{ invalid json";

    // Test fire
    const proactiveLogFire = (ruleId, prompt) => vm.runInContext(`proactiveLogFire("${ruleId}", "${prompt}")`, ctx);
    assert.doesNotThrow(() => {
      proactiveLogFire("welcome_back", "welcome!");
    });
  });
});
