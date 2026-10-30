/* eslint-disable @typescript-eslint/no-require-imports -- Standalone Node test harness. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('../node_modules/typescript');

test('translation restores settings, uses the device despite server preference, and returns to server for transcription', async () => {
  class Engine {
    state = { enabled: false, status: 'idle', segments: [] };
    options = { language: 'en', prompt: '' };
    listeners = [];
    subscribe(listener) { this.listeners.push(listener); }
    getState() { return this.state; }
    getOptions() { return this.options; }
    setOptions(options) { this.options = { ...this.options, ...options }; }
    async setEnabled(enabled) {
      this.state = { ...this.state, enabled };
      for (const listener of this.listeners) listener(this.state);
    }
    setTap() {}
  }
  const stored = new Map([
    ['sigsense.stt-engine', JSON.stringify('server')],
    ['sigsense.stt-options', JSON.stringify({ language: 'es', prompt: '', task: 'translate' })],
  ]);
  const exportsObject = {};
  const source = fs.readFileSync(path.join(__dirname, '../src/lib/sdr/transcription-hub.ts'), 'utf8');
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText, {
    exports: exportsObject,
    localStorage: { getItem: (key) => stored.get(key), setItem: (key, value) => stored.set(key, value) },
    require(id) {
      if (id === './transcription-engine') return { TranscriptionEngine: Engine };
      assert.equal(id, './remote-transcription-engine');
      return { RemoteTranscriptionEngine: Engine, fetchSttStatus: async () => ({ available: true }) };
    },
  });
  const hub = new exportsObject.TranscriptionHub(() => ({}));
  hub.restore();
  assert.equal(hub.device.options.task, 'translate');
  assert.equal(hub.device.options.language, 'es');
  await hub.setEnabled(true);
  assert.equal(hub.getState().engine, 'device');
  assert.equal(hub.remote.state.enabled, false);
  hub.setTap(() => {});
  await new Promise(setImmediate);
  assert.equal(hub.getState().engine, 'device');

  hub.setOptions({ task: 'transcribe' });
  await new Promise(setImmediate);
  assert.equal(hub.getState().engine, 'server');
  assert.equal(hub.device.state.enabled, false);
  assert.equal(hub.remote.state.enabled, true);
  assert.equal(JSON.parse(stored.get('sigsense.stt-options')).task, 'transcribe');
});
