from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from kyc.documents.refine import installed_languages
from kyc.readiness import BLOCKING, COMPONENTS, STATUSES, classify, report


class ReadinessTests(unittest.TestCase):
    def test_every_entry_uses_the_closed_vocabulary(self):
        for item in COMPONENTS:
            self.assertIn(item.status, STATUSES, item.name)
            if item.critical and item.status in BLOCKING:
                self.assertTrue(item.blocker, f"{item.name} blocks production without saying why")

    def test_current_matrix_is_not_ready(self):
        self.assertEqual(classify(), "NOT_READY")

    def test_one_blocking_critical_component_prevents_ready(self):
        ready = [replace(item, status="PRODUCTION_READY") for item in COMPONENTS]
        self.assertEqual(classify(ready), "READY")
        for status in ("UNCALIBRATED", "MOCK_ONLY", "NOT_REAL_WORLD_TESTED", "BROKEN", "INSECURE", "SECURITY_RISK"):
            first_critical = next(i for i, item in enumerate(ready) if item.critical)
            changed = list(ready)
            changed[first_critical] = replace(changed[first_critical], status=status)
            self.assertEqual(classify(changed), "NOT_READY", status)

    def test_non_critical_gap_is_ready_with_limitations(self):
        ready = [replace(item, status="PRODUCTION_READY") for item in COMPONENTS]
        index = next(i for i, item in enumerate(ready) if not item.critical)
        ready[index] = replace(ready[index], status="MOCK_ONLY")
        self.assertEqual(classify(ready), "READY_WITH_LIMITATIONS")

    def test_report_script_writes_json_and_markdown(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as out:
            subprocess.run([sys.executable, str(root / "scripts/readiness_report.py"), "-o", out], check=True, capture_output=True)
            data = json.loads((Path(out) / "readiness.json").read_text())
            text = (Path(out) / "READINESS.md").read_text()
        self.assertEqual(data["overall"], report()["overall"])
        self.assertIn("**Overall: NOT_READY**", text)
        self.assertIn("| Face Match | yes | UNCALIBRATED |", text)


class InstalledLanguageTests(unittest.TestCase):
    def test_script_models_resolve_across_tessdata_layouts(self):
        self.assertEqual(installed_languages(("script/Khmer",), {"script/Khmer"}), ("script/Khmer",))
        self.assertEqual(installed_languages(("script/Khmer",), {"Khmer", "eng"}), ("Khmer",))
        self.assertIsNone(installed_languages(("script/Khmer",), {"khm", "eng"}))
        self.assertIsNone(installed_languages(("eng", "script/Khmer"), {"eng"}))


if __name__ == "__main__":
    unittest.main()
