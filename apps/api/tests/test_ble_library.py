"""Persistence and non-emitting reconstruction of named Bluetooth messages."""
import asyncio
import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sigsense_api.ble_library import broadcast_key, prepare_replay, validate_packets
from sigsense_api.signal_store import SignalStore
from sigsense_api.sdr import LiveSDRService
from sigsense_ml.ble import encode_adv_pdu_crc, decode_ble_iq


def packet(**changes):
    payload = "010000000002020106"
    raw = encode_adv_pdu_crc(header_hex="0009", payload_hex=payload)
    return dict(header_hex="0009", payload_hex=payload, crc_hex=raw[-3:].hex(),
                crc_ok=True, access_address="0x8E89BED6", phy="LE1M", channel=37,
                packet_kind="advertising", pdu_type=0, pdu_type_name="ADV_IND",
                length=9, adv_address="02:00:00:00:00:01", **changes)


class BleLibraryTests(unittest.TestCase):
    def test_save_single_dedup_label_and_device_snapshot_persist(self):
        with TemporaryDirectory() as root:
            store = SignalStore(root)
            a = packet(capture_id="a")
            b = {**a, "capture_id": "b", "channel": 39}
            single = store.save_ble([a], "Lamp on")
            duplicate = store.save_ble([b], "Power on")
            self.assertEqual(single["id"], duplicate["id"])
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(duplicate["name"], "Power on")
            device = store.save_ble([a, b], "Desk lamp", "device")
            self.assertEqual(device["message_count"], 2)
            self.assertEqual(device["broadcast_count"], 1)
            self.assertNotIn("packets", store.list_ble()[0])
            store.db.close()
            store = SignalStore(root)
            self.assertEqual(store.get_ble(device["id"])["packets"], [a, b])
            self.assertEqual(store.rename_ble(single["id"], "On button")["name"], "On button")
            store.db.close()

    def test_existing_records_gain_identity_without_altering_saved_content(self):
        with TemporaryDirectory() as root:
            store = SignalStore(root)
            broadcast = store.save_ble([packet()], "Lamp ON")
            device = store.save_ble([packet()], "Lamp", "device")
            before = {row["id"]: tuple(row) for row in store.db.execute(
                "SELECT id,name,created,kind,digest,packets FROM ble_collections")}
            for row in store.db.execute("SELECT id,metadata FROM ble_collections").fetchall():
                metadata = json.loads(row["metadata"])
                metadata.pop("device_keys", None)
                store.db.execute("UPDATE ble_collections SET metadata=? WHERE id=?", (json.dumps(metadata), row["id"]))
            store.db.commit()
            store.db.close()
            store = SignalStore(root)
            items = {item["id"]: item for item in store.list_ble()}
            self.assertEqual(items[broadcast["id"]]["device_keys"], items[device["id"]]["device_keys"])
            self.assertEqual(items[device["id"]]["device_keys"], ["02:00:00:00:00:01:public"])
            after = {row["id"]: tuple(row) for row in store.db.execute(
                "SELECT id,name,created,kind,digest,packets FROM ble_collections")}
            self.assertEqual(before, after)
            store.db.close()

    def test_identity_separates_address_types_and_uses_connection_evidence(self):
        from sigsense_api.ble_library import device_key, device_keys
        public = packet()
        random = {**public, "header_hex": "4009"}
        self.assertNotEqual(device_key(public), device_key(random))
        scan = {**public, "header_hex": "8309"}
        self.assertEqual(device_key(scan), device_key(random))
        data = {**random, "packet_kind": "data", "header_hex": "0109"}
        self.assertIsNone(device_key(data))
        data["adv_address_type"] = "random"
        self.assertEqual(device_key(data), device_key(random))
        self.assertEqual(device_keys([{**public, "adv_address": None}]), [])

    def test_invalid_input_and_missing_records(self):
        with TemporaryDirectory() as root:
            store = SignalStore(root)
            for packets in ([], [{**packet(), "crc_ok": False}], [{**packet(), "payload_hex": "zz"}]):
                with self.assertRaises(ValueError):
                    store.save_ble(packets, "Invalid")
            with self.assertRaises(ValueError):
                store.save_ble([packet(), packet()], "Not single", "broadcast")
            with self.assertRaises(KeyError):
                store.get_ble("missing")
            store.db.close()

    def test_replay_unique_preserves_first_order_and_raw_keeps_repeats(self):
        first = packet()
        second = {**first, "payload_hex": "010000000002020105", "channel": 38}
        repeated = {**first, "channel": 39}
        self.assertEqual(broadcast_key(first), broadcast_key(repeated))
        blocks, unique = prepare_replay([first, second, repeated], unique=True)
        self.assertEqual(unique["messages"], 2)
        self.assertEqual([metadata["center_freq_hz"] for _, metadata in blocks], [2402e6, 2426e6])
        _, full = prepare_replay([first, second, repeated], unique=False, repeats=2)
        self.assertEqual(full["messages"], 6)
        decoded = decode_ble_iq(blocks[0][0], sample_rate=4e6, center_freq_hz=2402e6)
        self.assertTrue(any(p["payload_hex"] == first["payload_hex"] for p in decoded["packets"]))

    def test_data_extended_and_wrong_phy_kept_but_not_transmitted(self):
        a = packet()
        unsupported = [
            {**a, "packet_kind": "data"}, {**a, "phy": "LE2M"},
            {**a, "header_hex": "0709"}, {**a, "channel": 3},
        ]
        blocks, result = prepare_replay([a, *unsupported])
        self.assertEqual(result["messages"], 1)
        self.assertEqual(result["skipped"], 4)
        with self.assertRaises(ValueError):
            prepare_replay(unsupported)
        self.assertEqual(len(validate_packets(unsupported)), 4)
        with patch("sigsense_api.sdr.transmit_iq", side_effect=AssertionError("Must not emit RF")):
            service = LiveSDRService()
            for iq, metadata in blocks:
                dry = asyncio.run(service.transmit_saved(iq, metadata, dry_run=True))
                self.assertFalse(dry["transmitted"])

    def test_oversized_replay_rejected_before_transmission(self):
        with self.assertRaisesRegex(ValueError, "10 million"):
            prepare_replay([packet()] * 10_000, unique=False, repeats=20)


class BleEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_list_rename_inspect_and_replay_dry_run(self):
        from sigsense_api import main
        with TemporaryDirectory() as root:
            store = SignalStore(root)
            with patch.object(main, "signal_store", store), patch.object(
                main.sdr, "transmit_saved", side_effect=AssertionError("Dry-run must not call transmitter")
            ):
                saved = await main.save_ble_messages(main.SaveBleRequest(
                    name="Lamp button", packets=[packet()], kind="broadcast"))
                self.assertNotIn("packets", saved)
                self.assertEqual(main.list_saved_ble()["items"][0]["id"], saved["id"])
                inspected = main.get_saved_ble(saved["id"])
                self.assertEqual(len(inspected["packets"]), 1)
                renamed = main.rename_saved_ble(saved["id"], main.RenameRequest(name="Lamp ON"))
                self.assertEqual(renamed["name"], "Lamp ON")
                dry = await main.replay_saved_ble(saved["id"], main.SavedBleReplayRequest())
                self.assertFalse(dry["transmitted"])
                self.assertEqual(dry["messages"], 1)
                with self.assertRaises(main.HTTPException) as error:
                    await main.save_ble_messages(main.SaveBleRequest(name="Bad", packets=[{**packet(), "crc_ok": False}]))
                self.assertEqual(error.exception.status_code, 400)
                with self.assertRaises(main.HTTPException) as missing:
                    main.get_saved_ble("missing")
                self.assertEqual(missing.exception.status_code, 404)
            store.db.close()


if __name__ == "__main__":
    unittest.main()
