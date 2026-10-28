/* eslint-disable @typescript-eslint/no-require-imports -- Standalone Node test harness. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ts = require('../node_modules/typescript');
const source = fs.readFileSync(path.join(__dirname, '../src/lib/sdr/transcript-export.ts'), 'utf8');
const exportsObject = {};
vm.runInNewContext(ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText,
  {exports: exportsObject, Date, Math, String, JSON});
const {transcriptToSrt, transcriptToText, transcriptToJson} = exportsObject;
const at = Date.UTC(2026, 9, 23, 18, 0, 0);
const segs = [
  {id: 'u1', text: 'roger that', at, startS: 4.0, endS: 5.5, frequencyHz: 462562500, mode: 'NBFM', confidence: 0.91},
  {id: 'u2', text: 'copy ten four', at: at + 9000, startS: 13.2, endS: 15.0, confidence: 0.4},
];
test('SRT uses the server clock relative to the first line', () => {
  const srt = transcriptToSrt(segs);
  assert.match(srt, /^1\n00:00:00,000 --> 00:00:01,500\n\[462\.5625 MHz NBFM\] roger that\n/);
  assert.match(srt, /\n2\n00:00:09,200 --> 00:00:11,000\ncopy ten four\n/);
});
test('SRT falls back to arrival times when there is no stream clock', () => {
  const srt = transcriptToSrt([{id: 'a', text: 'one', at}, {id: 'b', text: 'two words here', at: at + 5000}]);
  assert.match(srt, /00:00:05,000 --> 00:00:06,500/);
});
test('text export is one line per transmission with its tag', () => {
  const lines = transcriptToText(segs).split('\n');
  assert.equal(lines.length, 2);
  assert.match(lines[0], /\[462\.5625 MHz NBFM\] roger that$/);
});
test('JSON export keeps confidence and nulls missing fields', () => {
  const data = JSON.parse(transcriptToJson(segs));
  assert.equal(data[0].confidence, 0.91);
  assert.equal(data[1].frequency_hz, null);
});
