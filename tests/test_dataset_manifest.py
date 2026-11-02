import json
import tempfile
import unittest
from pathlib import Path

from dataset_manifest import build_manifest, verify_manifest


class DatasetManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.dataset = self.root / "orders.jsonl"
        self.generator = self.root / "generator.py"
        self.manifest_path = self.root / "manifest.json"
        self.dataset.write_text('{"id":1}\n{"id":2}\n', encoding="utf-8")
        self.generator.write_text("# generator fixture\n", encoding="utf-8")
        self.manifest = build_manifest(self.dataset, self.generator)
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_manifest_records_count_size_hash_and_default_parameters(self):
        self.assertEqual(self.manifest["dataset"]["record_count"], 2)
        self.assertEqual(self.manifest["dataset"]["size_bytes"], self.dataset.stat().st_size)
        self.assertEqual(len(self.manifest["dataset"]["sha256"]), 64)
        self.assertEqual(self.manifest["generator"]["reproduction_defaults"]["seed"], 42)
        self.assertFalse(self.manifest["generator"]["original_invocation_captured"])

    def test_check_accepts_unchanged_dataset_and_generator(self):
        verify_manifest(self.dataset, self.generator, self.manifest_path)

    def test_check_rejects_changed_dataset(self):
        self.dataset.write_text('{"id":3}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Manifest mismatch"):
            verify_manifest(self.dataset, self.generator, self.manifest_path)

    def test_check_rejects_changed_generator(self):
        self.generator.write_text("# modified generator\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Manifest mismatch"):
            verify_manifest(self.dataset, self.generator, self.manifest_path)

    def test_blank_lines_are_rejected(self):
        self.dataset.write_text('{"id":1}\n\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Blank JSONL record"):
            build_manifest(self.dataset, self.generator)


if __name__ == "__main__":
    unittest.main()
