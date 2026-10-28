import tempfile
import unittest
from pathlib import Path

import numpy as np

from sigsense_ml.library import SignalLibrary, artemis_page_id


class LibraryListingTests(unittest.TestCase):
    def test_artemis_page_id_rejects_non_numeric_notes(self):
        self.assertEqual(artemis_page_id("artemis", "Artemis #44 · 129-137 MHz"), "44")
        self.assertIsNone(artemis_page_id("artemis", "Artemis #../etc"))
        self.assertIsNone(artemis_page_id("user", "Artemis #44"))

    def test_list_classes_exposes_artemis_page_id(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library = SignalLibrary(
                root / "library.sqlite",
                root / "library.faiss",
                embedding_dim=4,
            )
            emb = np.ones(4, dtype=np.float32)
            library.enroll(
                emb,
                "ACARS",
                source="artemis",
                notes="Artemis #44 · 129-137 MHz · MSK · 5000 Hz",
            )
            library.enroll(emb, "Custom", source="user", notes="field capture")
            library.enroll(emb, "Broken", source="artemis", notes="Artemis #not-an-id")

            items = {item["label"]: item for item in library.list_classes()}
            self.assertEqual(items["ACARS"]["pageId"], "44")
            self.assertEqual(items["ACARS"]["source"], "artemis")
            self.assertIn("129-137 MHz", items["ACARS"]["notes"])
            self.assertNotIn("pageId", items["Custom"])
            self.assertEqual(items["Custom"]["notes"], "field capture")
            self.assertNotIn("pageId", items["Broken"])
            self.assertIn("CW", items)
            library._conn.close()
