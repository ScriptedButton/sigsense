/* eslint-disable @typescript-eslint/no-require-imports -- Standalone Node test harness. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ts = require('../node_modules/typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/lib/sdr/signal-id/backend-classes.ts'), 'utf8');
const exportsObject = {};
vm.runInNewContext(ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText,
  {exports: exportsObject, Set, String});
const {BACKEND_CLASSES, isBuiltinClass, customLabelColor} = exportsObject;

test('the browser copy of the backend class list matches classes.py', () => {
  const py = fs.readFileSync(path.join(__dirname, '../../../packages/ml/sigsense_ml/classes.py'), 'utf8');
  const block = py.slice(py.indexOf('SIGNAL_CLASSES: list[str] = ['), py.indexOf('\n]', py.indexOf('SIGNAL_CLASSES')));
  const names = [...block.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual([...BACKEND_CLASSES], names);
});

test('built-in versus user-invented labels', () => {
  assert.ok(isBuiltinClass('WFM'));
  assert.ok(isBuiltinClass('5G-NR'));
  assert.ok(isBuiltinClass('unknown'));
  assert.ok(!isBuiltinClass('Airport ground control'));
});

test('a custom label always gets the same colour, and different labels differ', () => {
  assert.equal(customLabelColor('Local DMR'), customLabelColor('Local DMR'));
  assert.notEqual(customLabelColor('Local DMR'), customLabelColor('Pager'));
  assert.match(customLabelColor('x'), /^oklch\(/);
});
