import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import monitor

class MonitoringTests(unittest.TestCase):
    def success(self):
        return {"ok": True, "http_code": 200, "response_ms": 90, "attempts": 1, "error": ""}

    def failure(self):
        return {"ok": False, "http_code": 503, "response_ms": 120, "attempts": 2, "error": "HTTP 503"}

    def test_outage_requires_two_failed_checks(self):
        url = "https://example.org/"
        first = monitor.update_state({}, self.failure(), "2026-11-04T12:00:00+00:00", url, alerts=False)
        self.assertEqual(first["current_status"], "suspect")
        second = monitor.update_state(first, self.failure(), "2026-11-04T12:05:00+00:00", url, alerts=False)
        self.assertEqual(second["current_status"], "down")
        self.assertEqual(second["consecutive_failures"], 2)
        recovered = monitor.update_state(second, self.success(), "2026-11-04T12:10:00+00:00", url, alerts=False)
        self.assertEqual(recovered["current_status"], "up")
        self.assertEqual(recovered["consecutive_failures"], 0)
        self.assertNotIn("incident_started_utc", recovered)

    def test_recover_closes_issue(self):
        state = {"current_status": "down", "open_issue_number": 23, "consecutive_failures": 5,
                 "incident_started_utc": "2026-11-04T10:00:00+00:00"}
        with patch.object(monitor, "close_incident") as mock_close:
            new = monitor.update_state(state, self.success(), "2026-11-04T10:20:00+00:00", "https://example.org/", True)
            mock_close.assert_called_once()
        self.assertNotIn("open_issue_number", new)

    def test_daily_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(monitor, "LOG_DIR", Path(tmp)):
                for i in (1, 2):
                    monitor.record_check("https://example.org/", self.success(), f"2026-11-04T12:0{i}:00+00:00")
                with (Path(tmp) / "2026-11-04.csv").open(encoding="utf8") as f:
                    rows = list(csv.DictReader(f))
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[1]["status"], "up")

    def test_retry_on_first_failure(self):
        with patch.object(monitor, "fetch_once", side_effect=[self.failure(), self.success()]):
            with patch.object(monitor.time, "sleep"):
                result = monitor.check_site("https://example.org/")
        self.assertTrue(result["ok"])
        self.assertEqual(result["attempts"], 2)

if __name__ == "__main__":
    unittest.main()
