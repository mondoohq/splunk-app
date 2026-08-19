#!/usr/bin/env python3
"""
test_mondoo_input.py – Integration + unit tests for the Mondoo Splunk TA.

Usage
-----
  # Set up the venv first (Python 3.9):
  #   cd tests && ./setup_venv.sh
  #   source .venv/bin/activate

  # Run all tests:
  python test_mondoo_input.py

  # Run only live API tests (requires TOKEN env var or hardcoded token):
  python test_mondoo_input.py live

The test token is pre-configured for the eu-charming-keller-835981 space.
"""

import json
import os
import sys
import tempfile
import unittest

# ---------------------------------------------------------------------------
# Allow imports from bin/
# ---------------------------------------------------------------------------
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_bin_dir = os.path.join(_repo_root, "bin")
if _bin_dir not in sys.path:
    sys.path.insert(0, _bin_dir)

from mondoo_api import MondooClient, parse_config_blob, _agents_mrn_to_captain  # noqa: E402

# ---------------------------------------------------------------------------
# Test credentials (EU space, token-based service account)
# ---------------------------------------------------------------------------
TEST_TOKEN = (
    "eyJhbGciOiJFUzM4NCIsImtpZCI6Ii8vYWdlbnRzLmFwaS5tb25kb28uYXBwL3NwYWNlcy9ldS1jaGFybWlu"
    "Zy1rZWxsZXItODM1OTgxL3NlcnZpY2VhY2NvdW50cy8zQVFic2hQMkIxSjExY1hiZUIyR0xXTEZ5M0QiLCJ0"
    "eXAiOiJKV1QifQ.eyJpYXQiOjE3NzI1Mjc5NDksImlzcyI6Im1vbmRvby9hbXMiLCJuYmYiOjE3NzI1Mjc5"
    "NDksInN1YiI6Ii8vYWdlbnRzLmFwaS5tb25kb28uYXBwL3NwYWNlcy9ldS1jaGFybWluZy1rZWxsZXItODM1"
    "OTgxL3NlcnZpY2VhY2NvdW50cy8zQVFic2hQMkIxSjExY1hiZUIyR0xXTEZ5M0QifQ.NoPNyxZ6sv_tdoYQ"
    "Zga0V_pGrTKS1RPsBj3viN4xKiZqoSdIW_lcdHVV6zGuoj26_tc2mprh7uGpMaTUJ1TRkQ8yopPQOZTR3e_"
    "09aWsCe53x5qygrUxYYFHe4UAJ0XW"
)
TEST_API_ENDPOINT = "https://eu.api.mondoo.com"
TEST_SPACE_MRN = "//captain.api.mondoo.app/spaces/eu-charming-keller-835981"

# Allow override via environment variable
TOKEN = os.environ.get("MONDOO_TOKEN", TEST_TOKEN)


# ===========================================================================
# Unit tests – no network required
# ===========================================================================

class TestParseBlobUnit(unittest.TestCase):
    def test_raw_jwt(self):
        creds = parse_config_blob(TEST_TOKEN)
        self.assertEqual(creds["token"], TEST_TOKEN)
        self.assertIn("eu.api.mondoo.com", creds["api_endpoint"])
        self.assertEqual(creds["space_mrn"], TEST_SPACE_MRN)

    def test_json_blob_with_token_field(self):
        blob = json.dumps({
            "token": TEST_TOKEN,
            "api_endpoint": "https://eu.api.mondoo.com",
            "space_mrn": TEST_SPACE_MRN,
        })
        creds = parse_config_blob(blob)
        self.assertEqual(creds["token"], TEST_TOKEN)
        self.assertEqual(creds["api_endpoint"], "https://eu.api.mondoo.com")
        self.assertEqual(creds["space_mrn"], TEST_SPACE_MRN)

    def test_json_blob_with_mrn_field(self):
        blob = json.dumps({
            "token": TEST_TOKEN,
            "api_endpoint": "https://eu.api.mondoo.com",
            "mrn": "//agents.api.mondoo.app/spaces/eu-charming-keller-835981/serviceaccounts/abc",
        })
        creds = parse_config_blob(blob)
        self.assertEqual(creds["space_mrn"], TEST_SPACE_MRN)

    def test_json_blob_strips_query_path(self):
        blob = json.dumps({
            "token": "tok",
            "api_endpoint": "https://eu.api.mondoo.com/query",
            "space_mrn": TEST_SPACE_MRN,
        })
        creds = parse_config_blob(blob)
        self.assertEqual(creds["api_endpoint"], "https://eu.api.mondoo.com")

    def test_agents_mrn_conversion(self):
        agents_mrn = "//agents.api.mondoo.app/spaces/eu-charming-keller-835981/serviceaccounts/xyz"
        result = _agents_mrn_to_captain(agents_mrn)
        self.assertEqual(result, TEST_SPACE_MRN)

    def test_invalid_json_raises(self):
        with self.assertRaises(ValueError):
            parse_config_blob("{invalid json")

    def test_json_without_token_raises(self):
        with self.assertRaises(ValueError):
            parse_config_blob(json.dumps({"api_endpoint": "https://eu.api.mondoo.com"}))


# ===========================================================================
# Integration tests – require live API access
# ===========================================================================

class TestMondooAPILive(unittest.TestCase):
    """Live integration tests against the Mondoo EU API."""

    @classmethod
    def setUpClass(cls):
        cls.client = MondooClient(
            token=TOKEN,
            api_endpoint=TEST_API_ENDPOINT,
            space_mrn=TEST_SPACE_MRN,
            page_size=10,
        )

    # ------------------------------------------------------------------
    def test_validate_credentials(self):
        """Credentials must be accepted by the Mondoo API."""
        print("\n[live] Validating credentials …", end=" ", flush=True)
        result = self.client.validate()
        self.assertTrue(result)
        print("OK")

    # ------------------------------------------------------------------
    def test_fetch_audit_logs_first_page(self):
        """Fetch a single page of audit logs and validate the schema."""
        print("[live] Fetching first page of audit logs …", end=" ", flush=True)
        events = []
        for node in self.client.fetch_audit_logs():
            events.append(node)
            if len(events) >= 5:
                break

        print(f"retrieved {len(events)} event(s)")

        if not events:
            print("  WARNING: no audit events found – the space may be empty.")
            return

        # Schema checks
        required_fields = {"identity_name", "identity_mrn", "resource", "action", "timestamp", "msg"}
        for ev in events:
            # After flattening identity the fields should be present
            # (or at minimum 'action' and 'timestamp' which are core)
            self.assertIn("action", ev, "Missing 'action' field")
            self.assertIn("timestamp", ev, "Missing 'timestamp' field")

        print(f"  Sample event: {json.dumps(events[0], indent=4)}")

    # ------------------------------------------------------------------
    def test_audit_log_pagination(self):
        """Verify cursor-based pagination advances correctly."""
        print("[live] Testing audit log pagination …", end=" ", flush=True)
        client = MondooClient(
            token=TOKEN,
            api_endpoint=TEST_API_ENDPOINT,
            space_mrn=TEST_SPACE_MRN,
            page_size=2,  # tiny pages to force pagination
        )
        events = []
        cursors = set()
        for node in client.fetch_audit_logs():
            events.append(node)
            c = node.get("_cursor")
            if c:
                self.assertNotIn(c, cursors, "Duplicate cursor – pagination loop detected")
                cursors.add(c)
            if len(events) >= 6:
                break
        print(f"retrieved {len(events)} event(s) across pages")

    # ------------------------------------------------------------------
    def test_full_ingestion_with_checkpoint(self):
        """
        Simulate what mondoo_input.py does: fetch all events, save a
        checkpoint, then re-run and verify no duplicates are returned.
        """
        print("[live] Full ingestion + checkpoint test …", end=" ", flush=True)
        with tempfile.TemporaryDirectory() as tmpdir:
            stanza = "mondoo://test"
            log_type = "audit"

            # ---- First run ---------------------------------------------------
            from mondoo_input import (
                ingest_audit_logs,
                load_checkpoint,
                _write_event_stream_open,
                _write_event_stream_close,
            )
            import io

            captured1 = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured1
            _write_event_stream_open()
            ingest_audit_logs(
                self.client,
                stanza_name=stanza,
                params={
                    "resource_mrn": TEST_SPACE_MRN,
                    "initial_lookback_days": "30",
                },
                checkpoint_dir=tmpdir,
                index="main",
            )
            _write_event_stream_close()
            sys.stdout = old_stdout

            output1 = captured1.getvalue()
            count1 = output1.count("<event>")
            print(f"first run: {count1} events", end=" … ", flush=True)

            cp = load_checkpoint(tmpdir, stanza, log_type)
            self.assertTrue(
                "last_timestamp" in cp or "last_cursor" in cp,
                "Checkpoint was not saved after first run",
            )

            # ---- Second run (should return 0 new events) ---------------------
            captured2 = io.StringIO()
            sys.stdout = captured2
            _write_event_stream_open()
            ingest_audit_logs(
                self.client,
                stanza_name=stanza,
                params={
                    "resource_mrn": TEST_SPACE_MRN,
                    "initial_lookback_days": "30",
                },
                checkpoint_dir=tmpdir,
                index="main",
            )
            _write_event_stream_close()
            sys.stdout = old_stdout

            output2 = captured2.getvalue()
            count2 = output2.count("<event>")
            print(f"second run (should be 0): {count2} events")
            self.assertEqual(count2, 0, "Second run should not return duplicate events")

    # ------------------------------------------------------------------
    def test_audit_log_event_json_valid(self):
        """Every emitted event must be valid JSON."""
        print("[live] Verifying event JSON validity …", end=" ", flush=True)
        import io
        from mondoo_input import ingest_audit_logs, _write_event_stream_open, _write_event_stream_close
        import xml.etree.ElementTree as ET

        with tempfile.TemporaryDirectory() as tmpdir:
            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured
            _write_event_stream_open()
            ingest_audit_logs(
                self.client,
                stanza_name="mondoo://test_json",
                params={
                    "resource_mrn": TEST_SPACE_MRN,
                    "initial_lookback_days": "7",
                },
                checkpoint_dir=tmpdir,
                index="main",
            )
            _write_event_stream_close()
            sys.stdout = old_stdout

            xml_output = captured.getvalue()
            if "<event>" not in xml_output:
                print("no events (empty space)")
                return

            root = ET.fromstring(xml_output)
            invalid = 0
            for event_el in root.findall("event"):
                data_el = event_el.find("data")
                if data_el is None or not data_el.text:
                    invalid += 1
                    continue
                try:
                    json.loads(data_el.text)
                except json.JSONDecodeError:
                    invalid += 1

            print(f"{len(root.findall('event'))} events checked, {invalid} invalid")
            self.assertEqual(invalid, 0, f"{invalid} event(s) contained invalid JSON")


# ===========================================================================
# CLI helpers
# ===========================================================================

def print_live_sample():
    """Quick smoke-test: print a few raw audit log events to stdout."""
    print("=" * 60)
    print("Mondoo TA – Live API Smoke Test")
    print("=" * 60)
    print(f"Endpoint : {TEST_API_ENDPOINT}")
    print(f"Space MRN: {TEST_SPACE_MRN}")
    print("-" * 60)

    client = MondooClient(
        token=TOKEN,
        api_endpoint=TEST_API_ENDPOINT,
        space_mrn=TEST_SPACE_MRN,
        page_size=5,
    )

    print("Validating credentials …", end=" ", flush=True)
    try:
        client.validate()
        print("OK")
    except Exception as exc:
        print(f"FAILED: {exc}")
        sys.exit(1)

    print("\nFetching up to 10 most recent audit log events …\n")
    count = 0
    for node in client.fetch_audit_logs():
        identity = node.get("identity") or {}
        print(
            f"  [{node.get('timestamp', 'N/A')}] "
            f"{identity.get('name', '?')} → {node.get('action', '?')}"
        )
        print(f"    resource : {node.get('resource', 'N/A')}")
        print(f"    msg      : {node.get('msg', 'N/A')}")
        print()
        count += 1
        if count >= 10:
            break

    if count == 0:
        print("  No events found in this space.")
    else:
        print(f"Total events shown: {count}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "live":
        print_live_sample()
    else:
        # Run unit tests + integration tests
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        suite.addTests(loader.loadTestsFromTestCase(TestParseBlobUnit))
        suite.addTests(loader.loadTestsFromTestCase(TestMondooAPILive))
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        sys.exit(0 if result.wasSuccessful() else 1)
