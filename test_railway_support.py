import tempfile
import unittest
from pathlib import Path

from railway_support import SQLiteRailwayStore, build_response, classify_issue


class RailwaySupportTests(unittest.TestCase):
    def test_classifies_common_support_issues(self):
        self.assertEqual(classify_issue("The train is delayed"), "delay")
        self.assertEqual(classify_issue("I need wheelchair assistance"), "accessibility")
        self.assertEqual(classify_issue("Can I get a refund?"), "refund")

    def test_memory_persists_and_changes_followup_response(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "railway.sqlite3"
            store = SQLiteRailwayStore(database)
            issue_id = store.teach(
                "PAX-1042",
                "Aarav",
                "DEMO-7842",
                "12123",
                "Pune Junction",
                "Mumbai CSMT",
                "2026-09-26",
                "My train is delayed and I need help.",
                "Wheelchair assistance",
            )
            store.close()

            reopened_store = SQLiteRailwayStore(database)
            context = reopened_store.get_context("PAX-1042", "It's still delayed. What should I do?")
            answer = build_response("It's still delayed. What should I do?", context)
            reopened_store.record_followup("PAX-1042", issue_id, "It's still delayed.", answer)
            persisted = reopened_store.get_context("PAX-1042")
            reopened_store.close()

        self.assertIn("PNR DEMO-7842", answer)
        self.assertIn("Pune Junction to Mumbai CSMT", answer)
        self.assertIn("Wheelchair assistance", answer)
        self.assertEqual(persisted["issue"]["id"], issue_id)
        self.assertEqual(len(persisted["interactions"]), 2)


if __name__ == "__main__":
    unittest.main()