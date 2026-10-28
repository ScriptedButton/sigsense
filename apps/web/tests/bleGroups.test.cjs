/* eslint-disable @typescript-eslint/no-require-imports -- Standalone Node test harness. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ts = require('../node_modules/typescript');
const source = fs.readFileSync(path.join(__dirname,'../src/lib/bleGroups.ts'),'utf8');
const exportsObject = {};
vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2017}}).outputText,{exports:exportsObject});
const {groupPacketsByDevice,packetKey,mergeRecentPackets} = exportsObject;
const packet = (changes={}) => ({access_address:'0x8E89BED6',channel:37,pdu_type:0,pdu_type_name:'ADV_IND',header_hex:'0009',payload_hex:'010000000002020106',crc_hex:'000000',crc_ok:true,length:9,adv_address:'02:00:00:00:00:01',offset_bits:0,packet_kind:'advertising',...changes});

test('one device across channels, repeated broadcasts remain activity',()=>{
  const packets=[packet({capture_id:'a'}),packet({capture_id:'b',channel:39})];
  const groups=groupPacketsByDevice(packets);
  assert.equal(groups.length,1);assert.equal(groups[0].packetCount,2);
  assert.notEqual(packetKey(packets[0]),packetKey(packets[1]));
});
test('same numeric address with different public/random types stays separate',()=>{
  assert.equal(groupPacketsByDevice([packet(),packet({header_hex:'4009'})]).length,2);
});
test('data messages without advertiser address group by connection',()=>{
  const a=packet({packet_kind:'data',adv_address:null,access_address:'0x12345678',pdu_type_name:'LL_DATA'});
  assert.equal(groupPacketsByDevice([a,{...a,channel:4}]).length,1);
  assert.equal(groupPacketsByDevice([a])[0].label,'Bluetooth connection');
});
test('broadcast names are primary device labels',()=>{
  assert.equal(groupPacketsByDevice([packet({ad_structures:[{type:9,detail:'Desk lamp'}]})])[0].label,'Desk lamp');
});
test('all messages are visible by default including Apple manufacturer broadcasts',()=>{
  const a=packet({ad_structures:[{type:255,detail:'Apple manufacturer data'}]});
  const scan=packet({pdu_type_name:'SCAN_REQ',adv_address:'02:00:00:00:00:02'});
  assert.equal(groupPacketsByDevice([a,scan]).length,2);
});


test('recent window retains packets from intermediate frames and ignores invalid CRCs', () => {
  const first = packet({capture_id:'first'});
  const middle = packet({capture_id:'middle',channel:38});
  const last = packet({capture_id:'last',channel:39});
  const pending = mergeRecentPackets([], [first, middle]);
  const result = mergeRecentPackets(pending, [first, last, packet({crc_ok:false})]);
  assert.deepEqual(Array.from(result, p => p.capture_id), ['first', 'middle', 'last']);
});

test('duplicate-only and empty frames preserve the array to avoid regrouping devices', () => {
  const previous = [packet({capture_id:'a'})];
  assert.equal(mergeRecentPackets(previous, []), previous);
  assert.equal(mergeRecentPackets(previous, [{...previous[0]}]), previous);
  assert.equal(mergeRecentPackets(previous, [packet({crc_ok:false})]), previous);
});

test('recent window evicts oldest packets and preserves repeat broadcasts in different captures', () => {
  const packets = Array.from({length:150}, (_, i) => packet({capture_id:String(i)}));
  const result = mergeRecentPackets([], packets);
  assert.equal(result.length, 100);
  assert.equal(result[0].capture_id, '50');
  assert.equal(result[99].capture_id, '149');
  assert.equal(mergeRecentPackets(result, [packet({capture_id:'150'})])[0].capture_id, '51');
});

const {broadcastKey,groupBroadcasts,deviceKey,attachDeviceIdentity} = exportsObject;
test('broadcast dedup collapses identical content across captures and channels while preserving activity', () => {
  const a = packet({capture_id:'a'});
  const b = {...a, capture_id:'b', channel:39};
  const groups = groupBroadcasts([a,b]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].count, 2);
  assert.deepEqual(Array.from(groups[0].channels), [37,39]);
  assert.equal(broadcastKey(a),broadcastKey({...b,header_hex:a.header_hex.toUpperCase()}));
});
test('broadcast dedup never combines different payloads, headers, PHYs, or access addresses', () => {
  const a=packet();
  const variants=[a,{...a,payload_hex:'010000000002020105'},{...a,header_hex:'4009'},{...a,phy:'LE2M'},{...a,access_address:'0x12345678'}];
  assert.equal(groupBroadcasts(variants).length,5);
  assert.equal(variants.length,5);
});
test('connection evidence associates data with its device using address type, not LL header bits', () => {
  const announcement=packet({header_hex:'4009'});
  const data=packet({packet_kind:'data',access_address:'0x12345678',adv_address:null,header_hex:'0109'});
  const associated=attachDeviceIdentity([data],[{access_address:'0x12345678',advertiser_address:announcement.adv_address,advertiser_address_type:'random'}]);
  assert.equal(deviceKey(associated[0]),deviceKey(announcement));
  assert.equal(groupPacketsByDevice([announcement,...associated]).length,1);
  assert.equal(data.adv_address,null);
  assert.equal(attachDeviceIdentity([data],[])[0],data);
});

const {BleSessionCapture} = exportsObject;
test('whole-device snapshots retain session messages after they leave the live display', () => {
  const capture = new BleSessionCapture();
  const packets = Array.from({length:250}, (_, i) => packet({capture_id:String(i)}));
  capture.append(packets);
  const recent = mergeRecentPackets([], packets);
  assert.equal(recent.length,100);
  const all = capture.forDevice(deviceKey(packets[0]));
  assert.equal(all.length,250);
  assert.equal(all[0].capture_id,'0');
  assert.equal(all[249].capture_id,'249');
  assert.equal(capture.forDevice('other-device').length,0);
});
test('session capture bounds are explicit, ignore invalid/duplicate receptions, and reset cleanly', () => {
  const capture = new BleSessionCapture(2);
  const a=packet({capture_id:'a'}), b=packet({capture_id:'b'});
  assert.equal(capture.append([a,a,{...a,crc_ok:false},b,packet({capture_id:'c'})]).length,2);
  assert.equal(capture.full,true);
  capture.clear();
  assert.equal(capture.full,false);
  assert.equal(capture.forDevice(deviceKey(a)).length,0);
});
