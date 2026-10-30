/* eslint-disable @typescript-eslint/no-require-imports -- Standalone Node test harness. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('../node_modules/typescript');

const source = fs.readFileSync(path.join(__dirname, '../src/lib/sdr/transcription-engine.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;

for (const cacheAvailable of [false, true]) {
 for (const task of ['transcribe', 'translate']) {
  for (const language of ['es', 'auto']) {
  test(`on-device ${task} (${language}) works with browser cache ${cacheAvailable ? 'available' : 'unavailable on HTTP LAN'}`, async () => {
    const env = {};
    const attempts = [];
    const exportsObject = {};
    vm.runInNewContext(compiled, {
      exports: exportsObject,
      ...(cacheAvailable ? { caches: {} } : {}),
      require(id) {
        if (id === './audio-utils') return { resampleLinear: (audio) => audio, rmsLevel: () => 0.1 };
        assert.equal(id, '@huggingface/transformers');
        return {
          env,
          async pipeline(pipelineTask, model, options) {
            // Reproduce the Transformers.js failure before backend initialization.
            if (env.useBrowserCache && !cacheAvailable) throw new Error('Browser cache is not available in this environment.');
            attempts.push(options.device);
            assert.equal(pipelineTask, 'automatic-speech-recognition');
            assert.equal(model, 'onnx-community/whisper-tiny');
            if (options.device === 'webgpu') throw new Error('WebGPU unavailable');
            return async (_audio, decodeOptions) => {
              assert.equal(decodeOptions.task, task);
              assert.equal(decodeOptions.language, language === 'auto' ? undefined : language);
              assert.equal(Object.hasOwn(decodeOptions, 'language'), language !== 'auto');
              return { text: 'Radio check' };
            };
          },
        };
      },
    });
    const engine = new exportsObject.TranscriptionEngine();
    engine.setOptions({ task, language });
    await engine.setEnabled(true);
    assert.equal(engine.getState().status, 'listening');
    assert.equal(engine.getState().error, null);
    assert.equal(env.useBrowserCache, cacheAvailable);
    assert.deepEqual(attempts, ['webgpu', 'wasm']);

    // Verify audio still reaches inference after loading without GPU support.
    const transcript = new Promise((resolve, reject) => {
      const unsubscribe = engine.subscribe((state) => {
        if (state.error) { unsubscribe(); reject(new Error(state.error)); }
        if (state.segments.length) {
          unsubscribe();
          resolve(state.segments[0].text);
        }
      });
    });
    engine.pushAudio(new Float32Array(4 * 16000).fill(0.1), 16000);
    assert.equal(await transcript, 'Radio check');
    engine.dispose();
  });
}

 }
}
