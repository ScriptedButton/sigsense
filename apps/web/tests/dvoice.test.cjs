/* eslint-disable @typescript-eslint/no-require-imports -- Standalone Node test harness. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ts = require('../node_modules/typescript');

const LIB = path.join(__dirname, '../src/lib/sdr/dvoice');
const cache = {};
// Loads one TypeScript module in a fresh context; relative imports resolve to the other dvoice modules.
function load(name, shims = {}) {
  if (cache[name]) return cache[name];
  const source = fs.readFileSync(path.join(LIB, `${name}.ts`), 'utf8');
  const exportsObject = {};
  const req = (id) => {
    if (shims[id]) return shims[id];
    if (id.startsWith('./')) return load(id.slice(2), shims);
    throw new Error(`unexpected import ${id}`);
  };
  vm.runInNewContext(
    ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText,
    { exports: exportsObject, require: req, Math, Number, Object, Array, Map, Set, Date, JSON, String, Error, Int16Array, Float32Array, DataView, ArrayBuffer, Uint8Array, RegExp, encodeURIComponent, isFinite: Number.isFinite, parseInt, setTimeout, clearTimeout },
  );
  cache[name] = exportsObject;
  return exportsObject;
}
const plain = (x) => JSON.parse(JSON.stringify(x));

const apiShim = { '@/lib/api': { API_BASE: '/api', wsUrl: (p) => `ws://x${p}` } };
const types = load('types');
const model = load('model');
const player = load('pcm-player');
const transcribe = load('transcribe', apiShim);
const client = load('client', apiShim);

const call = (over = {}) => ({
  call_id: 'p25p1-ch0-x-1', protocol: 'p25p1', slot: null, kind: 'group', target_id: 4101, source_id: 1204567,
  frequency_hz: 851.0125e6, channel: 'ch0', started_at: 1000, updated_at: 1000, ended_at: null,
  encryption: { state: 'clear', algorithm_id: null, algorithm_name: null, key_id: null, mi_present: false, audio_decoded: true },
  emergency: false, priority: null, voice_frames: 0, voice_frames_bad: 0, ber: null, end_reason: null, meta: { call_num: 7 }, ...over,
});
const enc = { state: 'encrypted', algorithm_id: 0x84, algorithm_name: 'AES-256', key_id: 17, mi_present: true, audio_decoded: false };
const ev = (type, data, channel = 'ch0') => ({ v: 1, type, t: 5000, channel, data });

// ------------------------------------------------------------------ audio frames

function frameBytes({ slot = 0xff, flags = 0, callNum = 7, seq = 3, rate = 8000, samples = [1, -2, 300] } = {}) {
  const buf = new ArrayBuffer(16 + samples.length * 2);
  const v = new DataView(buf);
  v.setUint8(0, 0xa7); v.setUint8(1, 1); v.setUint8(2, slot); v.setUint8(3, flags);
  v.setUint32(4, callNum, true); v.setUint32(8, seq, true); v.setUint16(12, rate, true); v.setUint16(14, samples.length, true);
  samples.forEach((s, i) => v.setInt16(16 + i * 2, s, true));
  return buf;
}

test('audio frame parser reads the 16-byte header and PCM', () => {
  const f = types.parseDvAudioFrame(frameBytes({ slot: 1, flags: 3, callNum: 99, seq: 12 }));
  assert.equal(f.slot, 1); assert.equal(f.endOfCall, true); assert.equal(f.hadErrors, true);
  assert.equal(f.callNum, 99); assert.equal(f.seq, 12); assert.equal(f.sampleRate, 8000);
  assert.deepEqual(Array.from(f.pcm), [1, -2, 300]);
  assert.equal(types.parseDvAudioFrame(frameBytes()).slot, null);
});

test('audio frame parser rejects bad magic and wrong lengths', () => {
  const bad = frameBytes(); new DataView(bad).setUint8(0, 0x00);
  assert.throws(() => types.parseDvAudioFrame(bad));
  assert.throws(() => types.parseDvAudioFrame(frameBytes().slice(0, 17)));
  assert.throws(() => types.parseDvAudioFrame(new ArrayBuffer(4)));
});

// ------------------------------------------------------------------------ reducer

test('call events merge by id and keep newest first', () => {
  let s = model.INITIAL_STATE;
  s = model.reduceEvent(s, ev('call_start', call()));
  s = model.reduceEvent(s, ev('call_start', call({ call_id: 'dmr-ch0-0-2', protocol: 'dmr', slot: 0, started_at: 1005 })));
  s = model.reduceEvent(s, ev('call_update', call({ voice_frames: 50, updated_at: 1002 })));
  s = model.reduceEvent(s, ev('call_end', call({ voice_frames: 90, ended_at: 1006, end_reason: 'terminator' })));
  assert.equal(s.calls.length, 2);
  const p25 = s.calls.find((c) => c.call_id === 'p25p1-ch0-x-1');
  assert.equal(p25.voice_frames, 90); assert.equal(p25.ended_at, 1006);
  assert.equal(model.activeCalls(s).length, 1);
  assert.equal(s.lastEventAt, 5000);
});

test('link stats, system info, errors and talkgroups reduce per channel and are capped', () => {
  let s = model.INITIAL_STATE;
  s = model.reduceEvent(s, ev('link_stats', { channel: 'ch0', state: 'synced', sync_ratio: 0.9, ber: 0.01 }));
  s = model.reduceEvent(s, ev('system_info', { protocol: 'p25p1', nac: 0x293 }));
  s = model.reduceEvent(s, ev('talkgroup', { id: 4101, protocol: 'p25p1', label: null, last_heard: 1, call_count: 2, encrypted_calls: 0 }));
  for (let i = 0; i < 30; i++) s = model.reduceEvent(s, ev('error', { code: `e${i}`, severity: 'info', message: 'm', detail: {} }));
  assert.equal(s.links.ch0.state, 'synced'); assert.equal(s.systems['ch0:p25p1'].nac, 0x293);
  s = model.reduceEvent(s, ev('system_info', { protocol: 'dmr', color_code: 1 }));
  assert.equal(Object.keys(s.systems).length, 2); // two systems on one channel both kept
  assert.equal(s.talkgroups['p25p1:4101'].call_count, 2);
  assert.equal(s.errors.length, model.MAX_ERRORS); assert.equal(s.errors[0].code, 'e29');
});

test('the call log is capped', () => {
  let s = model.INITIAL_STATE;
  for (let i = 0; i < model.MAX_CALLS + 20; i++) s = model.reduceEvent(s, ev('call_start', call({ call_id: `c${i}`, started_at: i })));
  assert.equal(s.calls.length, model.MAX_CALLS);
  assert.equal(s.calls[0].call_id, `c${model.MAX_CALLS + 19}`);
});

test('mergeCallLog keeps a newer live update over the fetched copy and sorts newest first', () => {
  let s = model.reduceEvent(model.INITIAL_STATE, ev('call_update', call({ voice_frames: 80, updated_at: 1010 })));
  s = model.mergeCallLog(s, [call({ voice_frames: 10, updated_at: 1001 }), call({ call_id: 'older', started_at: 900, updated_at: 905, ended_at: 905 })]);
  assert.equal(s.calls.length, 2);
  assert.equal(s.calls[0].voice_frames, 80);
  assert.equal(s.calls[1].call_id, 'older');
  assert.equal(model.removeCall(s, 'older').calls.length, 1);
});

// ---------------------------------------------------------------- formatters, titles

test('formatters', () => {
  assert.equal(model.formatDuration(null), '—'); assert.equal(model.formatDuration(3.26), '3.3 s');
  assert.equal(model.formatDuration(75), '1:15'); assert.equal(model.formatDuration(42), '42 s');
  assert.equal(model.formatNac(0x293), '0x293'); assert.equal(model.formatNac(null), '—');
  assert.equal(model.formatPercent(0.0123, 1), '1.2%'); assert.equal(model.formatMhz(851.0125e6), '851.0125 MHz');
  assert.equal(model.callDurationS(call({ ended_at: 1007.5 })), 7.5);
});

test('call titles use aliases for talkgroups and handle private/all calls', () => {
  assert.equal(model.callTitle(call(), {}), 'TG 4101');
  assert.equal(model.callTitle(call(), { 'p25p1:4101': 'Dispatch' }), 'Dispatch (4101)');
  assert.equal(model.callTitle(call({ kind: 'private', target_id: 77 }), {}), 'ID 77');
  assert.equal(model.callTitle(call({ kind: 'all', target_id: null }), {}), 'All call');
  assert.equal(model.encryptionBadge(call({ encryption: enc }).encryption).label, 'Encrypted · AES-256 · key 17');
  assert.equal(model.encryptionBadge(call().encryption).tone, 'clear');
  assert.equal(model.isEncrypted(call({ encryption: enc })), true);
});

// --------------------------------------------------------------- filtering / sorting

const log = () => [
  call({ call_id: 'a', started_at: 100, ended_at: 103, target_id: 4101 }),
  call({ call_id: 'b', protocol: 'dmr', slot: 1, started_at: 200, ended_at: 220, target_id: 9, encryption: enc }),
  call({ call_id: 'c', started_at: 300, ended_at: 301, target_id: 4199, emergency: true }),
  call({ call_id: 'd', protocol: 'm17', started_at: 400, ended_at: 410, target_id: 884201, kind: 'private' }),
];

test('filters combine protocol, flag, duration and text (aliases included)', () => {
  const F = { ...model.DEFAULT_FILTER };
  const ids = (f, a = {}) => plain(model.filterCalls(log(), { ...F, ...f }, a, 500).map((c) => c.call_id));
  assert.deepEqual(ids({ protocol: 'dmr' }), ['b']);
  assert.deepEqual(ids({ flag: 'encrypted' }), ['b']);
  assert.deepEqual(ids({ flag: 'emergency' }), ['c']);
  assert.deepEqual(ids({ flag: 'clear' }), ['a', 'c', 'd']);
  assert.deepEqual(ids({ minDurationS: 5 }), ['b', 'd']);
  assert.deepEqual(ids({ query: 'dispatch' }, { 'p25p1:4101': 'Dispatch' }), ['a']);
  assert.deepEqual(ids({ query: '4199' }), ['c']);
  assert.deepEqual(ids({ query: 'aes' }), ['b']);
  assert.equal(model.activeFilterCount({ ...F, protocol: 'dmr', query: 'x' }), 2);
});

test('sorting is stable and respects direction', () => {
  const order = (key, dir) => plain(model.sortCalls(log(), key, dir, {}, 500).map((c) => c.call_id));
  assert.deepEqual(order('time', 'desc'), ['d', 'c', 'b', 'a']);
  assert.deepEqual(order('time', 'asc'), ['a', 'b', 'c', 'd']);
  assert.deepEqual(order('duration', 'desc'), ['b', 'd', 'a', 'c']);
  assert.deepEqual(order('talkgroup', 'asc'), ['b', 'a', 'c', 'd']);
  assert.deepEqual(order('protocol', 'asc')[0], 'b'); // "DMR" sorts before "M17" and "P25 Phase 1"
  const ties = [call({ call_id: 'x', started_at: 5 }), call({ call_id: 'y', started_at: 9 })];
  assert.deepEqual(plain(model.sortCalls(ties, 'talkgroup', 'asc', {}).map((c) => c.call_id)), ['y', 'x']); // equal keys: newest first
});

// ------------------------------------------------------------------------ exports

test('CSV export has a header, quotes commas and defuses spreadsheet formulas', () => {
  const calls = [call({ call_id: 'a,1', ended_at: 1003, meta: {} }), call({ call_id: '=cmd', ended_at: 1002 })];
  const csv = model.callsToCsv(calls, { 'p25p1:4101': 'Dis"patch' });
  const lines = csv.trim().split('\n');
  assert.equal(lines[0], model.CSV_COLUMNS.join(','));
  assert.equal(lines.length, 3);
  assert.ok(lines[1].startsWith('"a,1"'));
  assert.ok(lines[1].includes('"Dis""patch"'));
  assert.ok(lines[2].startsWith("'=cmd"));
  assert.deepEqual(plain(JSON.parse(model.callsToJson(calls))).map((c) => c.call_id), ['a,1', '=cmd']);
});

// ---------------------------------------------------------------- config payload

test('config payload maps talkgroup rules to follow lists and aliases to labels', () => {
  const payload = model.buildConfigPayload({
    enabled: true, source: 'hackrf',
    settings: { ...model.DEFAULT_SETTINGS, protocol: 'dmr', hangTimeS: 4, recording: true, maxRecordingGb: 20, transcribe: true, language: 'en' },
    prefs: {
      aliases: { 'p25p1:4101': ' Dispatch ', 'dmr:9': '', 'dmr:91': 'Local' },
      rules: { 'p25p1:4101': 'hold', 'dmr:9': 'skip', 'dmr:91': 'priority', 'dmr:5': 'priority' },
    },
    offsetHz: -12500, frequencyHz: 462.6e6,
  });
  const p = plain(payload);
  assert.equal(p.channels[0].protocol, 'dmr'); assert.equal(p.channels[0].offset_hz, -12500); assert.equal(p.channels[0].frequency_hz, 462.6e6);
  assert.deepEqual(p.follow.hold_talkgroups, [4101]); assert.deepEqual(p.follow.skip_talkgroups, [9]);
  assert.deepEqual(p.follow.priority_talkgroups.sort(), [5, 91]);
  assert.equal(p.follow.hang_time_s, 4);
  assert.equal('skip_encrypted' in p.follow, false); // encrypted calls are never followed; not a setting
  assert.deepEqual(p.talkgroup_labels, { 4101: 'Dispatch', 91: 'Local' });
  // These must match the API's ConfigBody (record is a mode string, not a boolean).
  assert.equal(p.enabled, true); assert.equal(p.source, 'hackrf');
  assert.equal(p.record, 'audio'); assert.equal(p.max_recording_gb, 20);
  assert.equal(p.transcribe, true); assert.equal(p.audio, true);
  const off = plain(model.buildConfigPayload({ enabled: false, source: 'upload', settings: model.DEFAULT_SETTINGS, prefs: model.EMPTY_PREFS, offsetHz: 0, frequencyHz: null }));
  assert.equal(off.enabled, false); assert.equal(off.record, 'off'); assert.equal(off.source, 'upload');
});

test('transcripts: partials replace, finals append, and stored text is the fallback', () => {
  let s = model.INITIAL_STATE;
  s = model.reduceTranscript(s, { kind: 'partial', call_id: 'a', text: 'unit tw' });
  assert.equal(model.transcriptText(s, call({ call_id: 'a' })), 'unit tw');
  s = model.reduceTranscript(s, { kind: 'partial', call_id: 'a', text: 'unit twelve' });
  s = model.reduceTranscript(s, { kind: 'final', call_id: 'a', text: 'Unit twelve en route.' });
  s = model.reduceTranscript(s, { kind: 'partial', call_id: 'a', text: 'Copy' });
  assert.equal(model.transcriptText(s, call({ call_id: 'a' })), 'Unit twelve en route. Copy');
  assert.equal(model.transcriptText(s, call({ call_id: 'other', transcript: ' stored text ' })), 'stored text');
  assert.equal(model.reduceTranscript(s, { kind: 'final', call_id: '', text: 'x' }), s);
});

// ------------------------------------------------------------ link health, voice, errors

test('link grade and labels', () => {
  const link = (o) => ({ state: 'voice', sync_ratio: 0.95, ber: 0.002, ...o });
  assert.equal(model.linkGrade(undefined), 'none');
  assert.equal(model.linkGrade(link({})), 'good');
  assert.equal(model.linkGrade(link({ sync_ratio: 0.7, ber: 0.03 })), 'fair');
  assert.equal(model.linkGrade(link({ sync_ratio: 0.4 })), 'weak');
  assert.equal(model.linkGrade(link({ state: 'searching' })), 'none');
  assert.equal(model.linkStateLabel(link({ state: 'lost' })), 'Signal lost');
});

test('voice availability prefers live engine data, then the local codec, then Codec2', () => {
  const V = model.voiceAvailable;
  assert.equal(V('p25p1', {}), false);
  assert.equal(V('p25p1', { engineVocoders: { imbe_7200x4400: { available: true } } }), true);
  assert.equal(V('p25p1', { engineVocoders: { imbe_7200x4400: { available: false } }, localProtocols: ['P25 Phase 1'] }), false);
  assert.equal(V('dmr', { localProtocols: ['DMR', 'NXDN'] }), true);
  assert.equal(V('nxdn96', { localProtocols: ['NXDN'] }), true);
  assert.equal(V('dstar', { localProtocols: ['DMR'] }), false);
  assert.equal(V('m17', { backends: [{ codec: 'codec2_3200', active: true }] }), true);
  assert.equal(V('m17', { backends: [{ codec: 'codec2_3200', active: false }] }), false);
  assert.equal(V('unknown', { localProtocols: ['DMR'] }), false);
});

test('engine errors map to one-line fixes', () => {
  const d = (code) => model.describeError({ code, severity: 'warning', message: 'raw', detail: {} });
  assert.match(d('vocoder_unavailable').fix, /codec/i);
  assert.match(d('capture_unavailable').fix, /HackRF/);
  assert.equal(d('something_new').title, 'raw');
});

// ------------------------------------------------------------------ player timing

test('jitter scheduling anchors, chains frames, re-anchors on underrun and drops when far ahead', () => {
  const S = player.scheduleFrame;
  const first = S(10, 0, 0.02);
  assert.equal(first.dropped, false); assert.ok(Math.abs(first.startAt - (10 + player.JITTER_S)) < 1e-9);
  const second = S(10.05, first.nextTime, 0.02);
  assert.equal(second.startAt, first.nextTime); // back to back, no gap
  const under = S(11, second.nextTime, 0.02); // clock passed the queue
  assert.ok(Math.abs(under.startAt - (11 + player.JITTER_S)) < 1e-9);
  const ahead = S(12, 12 + player.MAX_AHEAD_S + 0.2, 0.02);
  assert.equal(ahead.dropped, true);
  assert.deepEqual(Array.from(player.int16ToFloat(new Int16Array([0, 16384, -32768]))), [0, 0.5, -1]);
});

// ------------------------------------------------------------- WAV and resampling

function wavBytes(samples, rate = 8000, channels = 1) {
  const data = new Int16Array(samples);
  const buf = new ArrayBuffer(44 + data.length * 2);
  const v = new DataView(buf);
  const tag = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  tag(0, 'RIFF'); v.setUint32(4, 36 + data.length * 2, true); tag(8, 'WAVE'); tag(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, channels, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * channels * 2, true); v.setUint16(32, channels * 2, true); v.setUint16(34, 16, true);
  tag(36, 'data'); v.setUint32(40, data.length * 2, true);
  data.forEach((s, i) => v.setInt16(44 + i * 2, s, true));
  return buf;
}

test('WAV parsing reads mono, downmixes stereo and rejects other formats', () => {
  const mono = transcribe.parseWav(wavBytes([1, 2, 3, 4]));
  assert.equal(mono.sampleRate, 8000); assert.deepEqual(Array.from(mono.samples), [1, 2, 3, 4]);
  const stereo = transcribe.parseWav(wavBytes([10, 30, -10, -30], 16000, 2));
  assert.equal(stereo.sampleRate, 16000); assert.deepEqual(Array.from(stereo.samples), [20, -20]);
  assert.throws(() => transcribe.parseWav(new ArrayBuffer(10)));
  const notPcm = wavBytes([1, 2]); new DataView(notPcm).setUint16(20, 3, true);
  assert.throws(() => transcribe.parseWav(notPcm));
});

test('resampler doubles 8 kHz to 16 kHz and keeps a ramp monotonic', () => {
  const ramp = new Int16Array([0, 100, 200, 300, 400]);
  const out = transcribe.resampleLinear(ramp, 8000, 16000);
  assert.equal(out.length, 10);
  assert.equal(out[0], 0); assert.equal(out[1], 50); assert.equal(out[2], 100);
  for (let i = 1; i < out.length; i++) assert.ok(out[i] >= out[i - 1]);
  assert.equal(transcribe.resampleLinear(ramp, 8000, 8000), ramp);
});

// ---------------------------------------------------------------- client plumbing

test('message parsing ignores anything that is not a contract message', () => {
  const kind = (text) => client.parseMessage(text).kind;
  assert.equal(kind('not json'), 'ignored');
  assert.equal(kind('{"type":"nope","data":{}}'), 'ignored');
  assert.equal(kind('{"type":"call_start"}'), 'ignored');
  assert.equal(kind('[1,2]'), 'ignored');
  const ok = client.parseMessage(JSON.stringify({ ...ev('link_stats', { channel: 'ch0' }), id: 42 }));
  assert.equal(ok.kind, 'event'); assert.equal(ok.event.type, 'link_stats'); assert.equal(ok.id, 42);
  assert.equal(client.parseMessage(JSON.stringify(ev('link_stats', {}))).id, null);
  // transcripts arrive without the event envelope
  const tr = client.parseMessage('{"type":"transcript","kind":"final","call_id":"c1","text":"hello"}');
  assert.equal(tr.kind, 'transcript'); assert.equal(tr.transcript.kind, 'final'); assert.equal(tr.transcript.call_id, 'c1');
  assert.equal(client.parseMessage('{"type":"transcript","kind":"final"}').kind, 'ignored');
  assert.equal(client.parseMessage('{"type":"transcript","call_id":"c","text":"t"}').transcript.kind, 'partial');
});

test('call list responses: paginated items, legacy calls, or a bare list', () => {
  assert.equal(client.callsFromResponse({ items: [call()], total: 1 }).length, 1);
  assert.equal(client.callsFromResponse({ calls: [call(), call()] }).length, 2);
  assert.equal(client.callsFromResponse([call(), call(), call()]).length, 3);
  assert.equal(client.callsFromResponse({}).length, 0);
  assert.equal(client.dvApi.exportUrl('csv'), '/api/dvoice/calls/export?fmt=csv');
  assert.equal(client.dvApi.audioUrl('a/b'), '/api/dvoice/calls/a%2Fb/audio');
});
