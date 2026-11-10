const { test, describe } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');

function extractFunction(name, prefix = '') {
  const match = source.match(new RegExp(`^function ${name}\\s*\\([^)]*\\)\\s*\\{[\\s\\S]*?^\\}`, 'm'));
  if (!match) throw new Error(`Function ${name} not found`);
  const ctx = vm.createContext({});
  vm.runInContext(prefix + '\n' + match[0], ctx);
  return vm.runInContext(name, ctx);
}

describe('tokenPercent', () => {
  const tokenPercent = extractFunction('tokenPercent');

  test('uses total input as the denominator', () => {
    assert.strictEqual(tokenPercent({ input: 30, cached: 20 }), 67);
    assert.strictEqual(tokenPercent({ input: 118452314, cached: 100947281 }), 85);
  });

  test('handles no input and a fully cached request', () => {
    assert.strictEqual(tokenPercent({ input: 0, cached: 0 }), 0);
    assert.strictEqual(tokenPercent({ input: 20, cached: 20 }), 100);
  });
});


describe('selectGraphData', () => {
  const selectGraphData = extractFunction('selectGraphData', 'const GRAPH_MAX_NODES = 400;');
  const data = {
    nodes: [
      { id: 1, scope: 'pet' },
      { id: 2, scope: 'private' },
      { id: 3, scope: 'group' },
      { id: 4, scope: 'pet' },
    ],
    edges: [
      { a: 1, b: 2 },
      { a: 2, b: 3 },
      { a: 3, b: 4 },
      { a: 1, b: 4 },
    ],
  };

  test('filters nodes and keeps only internal edges', () => {
    const result = selectGraphData(data, 'pet');
    assert.strictEqual(result.nodes.map((n) => n.id).join(','), '1,4');
    assert.strictEqual(result.edges.map((e) => `${e.a}-${e.b}`).join(','), '1-4');
    assert.strictEqual(result.truncated, false);
  });

  test('keeps all nodes by default and handles an empty scope', () => {
    assert.strictEqual(selectGraphData(data, 'all').nodes.length, 4);
    assert.strictEqual(selectGraphData(data, 'all').edges.length, 4);
    assert.strictEqual(selectGraphData(data, 'private').nodes.length, 1);
    assert.strictEqual(selectGraphData(data, 'private').edges.length, 0);
  });

  test('caps large graphs and removes edges to omitted nodes', () => {
    const nodes = Array.from({ length: 405 }, (_, i) => ({ id: i, importance: (i % 5) + 1 }));
    const result = selectGraphData({ nodes, edges: [{ a: 380, b: 385 }] }, 'all');
    assert.strictEqual(result.nodes.length, 400);
    assert.strictEqual(result.truncated, true);
    assert.strictEqual(result.edges.length, 0);
  });
});

describe('graphScopeCenters', () => {
  const graphScopeCenters = extractFunction('graphScopeCenters');

  test('spreads multiple scopes across the canvas', () => {
    const centers = graphScopeCenters([{ scope: 'pet' }, { scope: 'private' }, { scope: 'group' }], 900, 420);
    assert.strictEqual(JSON.stringify(centers.pet), '{"x":225,"y":210}');
    assert.strictEqual(JSON.stringify(centers.private), '{"x":450,"y":210}');
    assert.strictEqual(JSON.stringify(centers.group), '{"x":675,"y":210}');
  });

  test('centers a single scope', () => {
    assert.strictEqual(JSON.stringify(graphScopeCenters([{ scope: 'pet' }], 900, 420).pet), '{"x":450,"y":210}');
  });
});
