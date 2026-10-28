import type { SavedBleMessages } from "./bleTypes";

export type SavedDeviceGroup = {
  key: string;
  name: string;
  address: string | null;
  demo: boolean;
  items: SavedBleMessages[];
};

export function groupSavedMessages(items: SavedBleMessages[]): SavedDeviceGroup[] {
  const groups = new Map<string, SavedDeviceGroup>();
  for (const item of items) {
    const identity = item.device_keys?.length === 1 ? item.device_keys[0] : null;
    // Unknown and multi-device records remain separate, never guessed from names.
    const key = identity ? `${item.demo ? "demo" : "rf"}:${identity}` : `saved:${item.id}`;
    let group = groups.get(key);
    if (!group) {
      group = {key, name: identity ?? item.name, address: identity,
        demo: item.demo, items: []};
      groups.set(key, group);
    }
    group.items.push(item);
  }
  for (const group of groups.values()) {
    const devices = group.items.filter((item) => item.kind === "device")
      .sort((a, b) => b.created_at - a.created_at);
    group.name = devices[0]?.name ?? (group.address ? "Saved Bluetooth device" : group.name);
    group.items.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === "device" ? -1 : 1;
      return b.created_at - a.created_at;
    });
  }
  return [...groups.values()];
}

export function savedBroadcastLabels(items: SavedBleMessages[]): Record<string, string> {
  const labels: Record<string, string> = {};
  for (const item of [...items].sort((a, b) => b.created_at - a.created_at))
    if (item.kind === "broadcast" && item.broadcast_key && !labels[item.broadcast_key])
      labels[item.broadcast_key] = item.name;
  return labels;
}

export type KnownBleDevice = {
  key: string;
  name?: string;
  saved: boolean;
  broadcastNames: string[];
};

/** Build a source-scoped index once when saved identities change. */
export function knownBleDevices(
  items: SavedBleMessages[], demo: boolean, deviceNames: Record<string, string> = {},
): Map<string, KnownBleDevice> {
  const known = new Map<string, KnownBleDevice>();
  for (const group of groupSavedMessages(items.filter((item) => item.demo === demo))) {
    if (!group.address) continue;
    known.set(group.address, {
      key: group.address,
      name: group.items.some((item) => item.kind === "device") ? group.name : undefined,
      saved: true,
      broadcastNames: group.items.filter((item) => item.kind === "broadcast").map((item) => item.name),
    });
  }
  // Legacy manually named tracks have no source marker; use them for real RF.
  if (!demo) for (const [id, name] of Object.entries(deviceNames)) {
    if (!id.startsWith("ble:") || !/:(public|random)$/.test(id)) continue;
    const key = id.slice(4);
    const previous = known.get(key);
    known.set(key, {key, name, saved: previous?.saved ?? false,
      broadcastNames: previous?.broadcastNames ?? []});
  }
  return known;
}
