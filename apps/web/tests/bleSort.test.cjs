/* eslint-disable @typescript-eslint/no-require-imports -- Standalone Node test harness. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ts = require('../node_modules/typescript');
const load = (file, req) => {
  const source = fs.readFileSync(path.join(__dirname, '../src/lib/', file), 'utf8');
  const exportsObject = {};
  vm.runInNewContext(ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText,
    {exports: exportsObject, require: req, Map, Set, Math, Number, Object, Array, parseInt});
  return exportsObject;
};
const groups = load('bleGroups.ts', () => ({}));
const sort = load('bleSort.ts', (id) => (id === '@/lib/bleGroups' ? groups : {}));
const {groupPacketsByDevice} = groups;
const {sortAndFilterGroups, DEFAULT_FILTERS, activeFilterCount, arrivalOrder, typeCounts, groupMetrics} = sort;

const packet = (c = {}) => ({access_address: '0x8E89BED6', channel: 37, pdu_type: 0, pdu_type_name: 'ADV_IND', header_hex: '0009',
  payload_hex: '010000000002020106', crc_hex: '000000', crc_ok: true, length: 9, adv_address: '02:00:00:00:00:01', offset_bits: 0, packet_kind: 'advertising', ...c});
const dev = (n, extra = {}) => packet({adv_address: `02:00:00:00:00:0${n}`, ...extra});

// A strong quiet device, a weak chatty one, a middling named one, one with no RSSI.
const packets = [
  dev(1, {rssi_dbfs: -40}), dev(2, {rssi_dbfs: -80}), dev(2, {rssi_dbfs: -78, channel: 38}), dev(2, {rssi_dbfs: -79, channel: 39}),
  dev(3, {rssi_dbfs: -60, ad_structures: [{type: 9, label: 'Name', data_hex: '41', detail: 'Zed lamp'}], pdu_type_name: 'ADV_NONCONN_IND'}),
  dev(4, {}),
];
const all = groupPacketsByDevice(packets);
// Results come from another VM context; copy to plain arrays so deepEqual compares values.
const plain = (x) => JSON.parse(JSON.stringify(x));
const run = (s, f = {}) => plain(sortAndFilterGroups(all, s, {...DEFAULT_FILTERS, ...f}, {order: arrivalOrder(packets)}).map((r) => r.group.address.slice(-1)));

test('sort by strength puts the strongest first and devices with no reading last', () => {
  assert.deepEqual(run({key: 'strength', dir: 'desc'}), ['1', '3', '2', '4']);
  assert.deepEqual(run({key: 'strength', dir: 'asc'}), ['2', '3', '1', '4']);
});
test('sort by message count', () => {
  assert.equal(run({key: 'messages', dir: 'desc'})[0], '2');
});
test('sort by name is case-insensitive and stable', () => {
  const order = run({key: 'name', dir: 'asc'});
  assert.equal(order.length, 4);
  assert.equal(order[3], '3'); // "Zed lamp" sorts after the generic "Unnamed Bluetooth device" rows
  assert.equal(order[0], '1'); // equal labels fall back to the device key
});
test('last seen follows arrival order', () => {
  assert.equal(run({key: 'last-seen', dir: 'desc'})[0], '4');
  assert.equal(run({key: 'last-seen', dir: 'asc'})[0], '1');
});
test('min strength filter drops weak and unmeasured devices', () => {
  assert.deepEqual(run({key: 'strength', dir: 'desc'}, {minStrengthDbfs: -65}), ['1', '3']);
});
test('min messages and channel filters', () => {
  assert.deepEqual(run({key: 'messages', dir: 'desc'}, {minMessages: 2}), ['2']);
  assert.deepEqual(run({key: 'messages', dir: 'desc'}, {channels: [39]}), ['2']);
});
test('type filter matches any selected type', () => {
  assert.deepEqual(run({key: 'strength', dir: 'desc'}, {types: ['named']}), ['3']);
  assert.deepEqual(run({key: 'strength', dir: 'desc'}, {types: ['non-connectable']}), ['3']);
  assert.deepEqual(run({key: 'strength', dir: 'desc'}, {types: ['named', 'connectable']}).sort(), ['1', '2', '3', '4']);
});
test('beacon and apple detection', () => {
  const b = groupPacketsByDevice([dev(5, {ad_structures: [{type: 255, label: 'M', data_hex: '4c000215aabb', detail: 'x'}]})]);
  assert.ok(groupMetrics(b[0]).types.has('beacon'));
  const a = groupPacketsByDevice([dev(6, {summary: 'Apple Continuity: Nearby'})]);
  assert.ok(groupMetrics(a[0]).types.has('apple'));
});
test('strength averages the newest readings, peak keeps the maximum', () => {
  const m = groupMetrics(all.find((g) => g.address.endsWith('2')));
  assert.ok(Math.abs(m.strengthDbfs - (-79)) < 0.5);
  assert.equal(m.peakDbfs, -78);
});
test('falls back to RF track power when packets carry no RSSI', () => {
  const g = all.find((x) => x.address.endsWith('4'));
  assert.equal(groupMetrics(g, {fallbackStrengthDbfs: () => -55}).strengthDbfs, -55);
});
test('equal rows keep a stable order and filter count', () => {
  const same = groupPacketsByDevice([dev(7, {rssi_dbfs: -50}), dev(8, {rssi_dbfs: -50})]);
  const a = plain(sortAndFilterGroups(same, {key: 'strength', dir: 'desc'}, DEFAULT_FILTERS).map((r) => r.group.key));
  const b = plain(sortAndFilterGroups([...same].reverse(), {key: 'strength', dir: 'desc'}, DEFAULT_FILTERS).map((r) => r.group.key));
  assert.deepEqual(a, b);
  assert.equal(activeFilterCount(DEFAULT_FILTERS), 0);
  assert.equal(activeFilterCount({...DEFAULT_FILTERS, types: ['apple'], minMessages: 3}), 2);
  assert.equal(typeCounts(all).named, 1);
});
