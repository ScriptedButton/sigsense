/* eslint-disable @typescript-eslint/no-require-imports -- Standalone Node test harness. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ts = require('../node_modules/typescript');
const exportsObject = {};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/lib/bleSavedGroups.ts'),'utf8'), {
  compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2017},
}).outputText, {exports:exportsObject});
const {groupSavedMessages,savedBroadcastLabels} = exportsObject;
const item = (id, changes={}) => ({id,name:id,kind:'broadcast',created_at:1,demo:false,
  device_keys:['AA:BB:CC:DD:EE:FF:random'],message_count:1,broadcast_count:1,
  replayable_count:1,broadcast_key:id,...changes});

test('separately saved broadcasts group with their device in either save order', () => {
  const device=item('device',{kind:'device',name:'Desk lamp'});
  const on=item('on',{name:'Lamp ON'}), off=item('off',{name:'Lamp OFF'});
  for (const items of [[device,on,off],[off,on,device]]) {
    const groups=groupSavedMessages(items);
    assert.equal(groups.length,1);
    assert.equal(groups[0].name,'Desk lamp');
    assert.equal(groups[0].items[0].id,'device');
    assert.deepEqual(new Set(Array.from(groups[0].items,x=>x.name)),new Set(['Desk lamp','Lamp ON','Lamp OFF']));
  }
});
test('same address separates public/random identities and demo/live sources', () => {
  const groups=groupSavedMessages([item('a'),item('b',{demo:true}),item('c',{device_keys:['AA:BB:CC:DD:EE:FF:public']})]);
  assert.equal(groups.length,3);
});
test('anonymous and multi-device captures never merge by names or shared advertising access address', () => {
  const groups=groupSavedMessages([item('a',{device_keys:[],name:'Same'}),item('b',{device_keys:[],name:'Same'}),
    item('c',{device_keys:['one','two']}),item('d',{device_keys:['one','two']})]);
  assert.equal(groups.length,4);
});
test('multiple device snapshots stay accessible under the latest device name without merging messages', () => {
  const old=item('old',{kind:'device',created_at:1,name:'Old name'});
  const newer=item('new',{kind:'device',created_at:2,name:'Renamed lamp'});
  const input=[old,newer,item('on')];
  const group=groupSavedMessages(input)[0];
  assert.equal(group.name,'Renamed lamp');
  assert.equal(group.items.length,3);
  assert.equal(input[0],old);
  assert.equal(savedBroadcastLabels(input).on,'on');
});

const {knownBleDevices} = exportsObject;
test('feed identifies saved devices and retains broadcast names for the previously identified roster', () => {
  const known=knownBleDevices([item('device',{kind:'device',name:'Display Shelf'}),
    item('on',{name:'Display Shelf - ON'}),item('off',{name:'Display Shelf - OFF'})],false);
  const device=known.get('AA:BB:CC:DD:EE:FF:random');
  assert.equal(known.size,1);
  assert.equal(device.name,'Display Shelf');
  assert.equal(device.saved,true);
  assert.deepEqual(Array.from(device.broadcastNames).sort(),['Display Shelf - OFF','Display Shelf - ON']);
});
test('feed identity never crosses live/demo or public/random boundaries', () => {
  const items=[item('live',{kind:'device',name:'Live lamp'}),
    item('demo',{kind:'device',name:'Demo lamp',demo:true})];
  assert.equal(knownBleDevices(items,false).get('AA:BB:CC:DD:EE:FF:random').name,'Live lamp');
  assert.equal(knownBleDevices(items,true).get('AA:BB:CC:DD:EE:FF:random').name,'Demo lamp');
  assert.equal(knownBleDevices(items,false).has('AA:BB:CC:DD:EE:FF:public'),false);
});
test('manually identified BLE names override capture and advertised names without including RF occupancy tracks', () => {
  const known=knownBleDevices([item('saved',{kind:'device',name:'Old lamp name'})],false,{
    'ble:AA:BB:CC:DD:EE:FF:random':'My lamp',
    'ble:11:22:33:44:55:66:public':'Previously named sensor',
    'rf:temporary':'Unknown RF occupancy',
  });
  assert.equal(known.get('AA:BB:CC:DD:EE:FF:random').name,'My lamp');
  assert.equal(known.get('AA:BB:CC:DD:EE:FF:random').saved,true);
  assert.equal(known.get('11:22:33:44:55:66:public').saved,false);
  assert.equal(known.size,2);
});
test('a saved broadcast identifies its address without inventing a device name or changing stored data', () => {
  const broadcast=item('on',{name:'Power on'});
  const known=knownBleDevices([broadcast],false);
  const device=known.get('AA:BB:CC:DD:EE:FF:random');
  assert.equal(device.name,undefined);
  assert.equal(device.saved,true);
  assert.equal(broadcast.name,'Power on');
  assert.equal(knownBleDevices([item('unknown',{device_keys:[]})],false).size,0);
});
