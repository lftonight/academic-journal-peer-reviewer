"""Offline regression tests. Test fixtures are synthetic and make no requests."""
import contextlib
from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler
from urllib.response import addinfourl

MODULE_PATH = Path(__file__).with_name("batch_doi_verify.py")
SPEC = importlib.util.spec_from_file_location("peer_doi", MODULE_PATH)
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


def crossref_record(doi="10.5555/example"):
    return {"status": "ok", "message-type": "work", "message": {
        "DOI": doi, "title": ["A sample publication"],
        "author": [{"given": "Ada", "family": "Example"}],
        "container-title": ["Example Journal"],
        "published-print": {"date-parts": [[2024, 6]]},
        "published-online": {"date-parts": [[2023, 12]]},
        "volume": "4", "issue": "2", "page": "10-15"
    }}


class FakeResponse(io.BytesIO):
    status = 200


class CrossrefBoundary:
    """A specific external boundary: reject wrong hosts and any request body."""
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.urls = []
        self.timeouts = []

    def __call__(self, request, timeout):
        if not request.full_url.startswith("https://api.crossref.org/works/"):
            raise AssertionError("Unexpected external endpoint")
        if request.data is not None:
            raise AssertionError("Manuscript data must not be sent")
        self.urls.append(request.full_url)
        self.timeouts.append(timeout)
        if not self.outcomes:
            raise AssertionError("Unexpected additional network attempt")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if not isinstance(outcome, bytes):
            outcome = json.dumps(outcome).encode("utf-8")
        return FakeResponse(outcome)


def http_error(status, headers=None):
    return HTTPError("https://api.crossref.org/works/10.5555%2Fexample",
                     status, "Synthetic response", headers or {}, None)


class DoiSemanticsTests(unittest.TestCase):
    def setUp(self):
        # Every unplanned request fails even when a test forgets its fixture.
        self.network_guard = patch("socket.create_connection",
                                   side_effect=AssertionError("Real network disabled in tests"))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)

    def lookup(self, doi="10.5555/example", outcomes=None, **kwargs):
        boundary = CrossrefBoundary(*(outcomes or [crossref_record()]))
        waits = []
        result = subject.verify_doi(doi, opener=boundary, sleep=waits.append, **kwargs)
        return result, boundary, waits

    def test_metadata_hit_is_not_bibliography_or_claim_verification(self):
        result, boundary, _ = self.lookup()
        self.assertEqual(result["status"], "metadata-only")
        self.assertEqual(result["title"], "A sample publication")
        self.assertEqual(result["year"], "2024")
        self.assertFalse(result["claim_checked"])
        self.assertFalse(result["bibliography_compared"])
        self.assertEqual(boundary.urls, ["https://api.crossref.org/works/10.5555%2Fexample"])

    def test_crossref_404_does_not_conclude_absence_or_fabrication(self):
        result, boundary, waits = self.lookup(outcomes=[http_error(404)])
        self.assertEqual(result["status"], "crossref-not-found")
        self.assertIsNone(result["work_exists"])
        self.assertFalse(result["claim_checked"])
        self.assertEqual(len(boundary.urls), 1)
        self.assertEqual(waits, [])

    def test_doi_prefix_is_normalized_without_arbitrary_url_access(self):
        result, boundary, _ = self.lookup("  DOI: 10.5555/example  ")
        self.assertEqual(result["doi"], "10.5555/example")
        self.assertEqual(boundary.urls, ["https://api.crossref.org/works/10.5555%2Fexample"])

    def test_resolver_url_is_decoded_once_then_safely_encoded(self):
        result, boundary, _ = self.lookup("https://doi.org/10.5555/a%3Fb%23c%3Cd%3E%3Be",
            outcomes=[crossref_record("10.5555/a?b#c<d>;e")])
        self.assertEqual(result["status"], "metadata-only")
        self.assertEqual(boundary.urls,
                         ["https://api.crossref.org/works/10.5555%2Fa%3Fb%23c%3Cd%3E%3Be"])

    def test_raw_doi_percent_is_literal_and_is_not_double_decoded(self):
        result, boundary, _ = self.lookup("10.5555/a%2Fb", outcomes=[crossref_record("10.5555/a%2Fb")])
        self.assertEqual(result["status"], "metadata-only")
        self.assertEqual(boundary.urls, ["https://api.crossref.org/works/10.5555%2Fa%252Fb"])

    def test_invalid_or_non_doi_input_never_reaches_network(self):
        for value in ("", "manuscript text", "https://example.com/private", "10.5555/has space",
                      "10.5555/a\n10.5555/b", "https://doi.org.evil.example/10.5555/example",
                      "https://doi.org/10.5555/example?tracking=1", "10.١٢٣٤/example"):
            with self.subTest(value=value):
                result, boundary, _ = self.lookup(value)
                self.assertEqual(result["status"], "invalid-input")
                self.assertEqual(boundary.urls, [])

    def test_mismatched_returned_doi_is_lookup_error(self):
        result, boundary, _ = self.lookup(outcomes=[crossref_record("10.5555/another-work")])
        self.assertEqual(result["status"], "lookup-error")
        self.assertNotIn("title", result)
        self.assertEqual(len(boundary.urls), 1)

    def test_case_differences_do_not_create_false_doi_mismatch(self):
        result, _, _ = self.lookup("10.5555/EXAMPLE", outcomes=[crossref_record("10.5555/example")])
        self.assertEqual(result["status"], "metadata-only")

    def test_malformed_json_or_non_work_payload_is_not_metadata_success(self):
        for payload in (b"<html>error</html>", b"[]", {"status": "ok", "message": {}},
                        {"status": "ok", "message-type": "work", "message": []},
                        {"status": "ok", "message-type": "work", "message": {"DOI": "10.5555/example"}}):
            with self.subTest(payload=payload):
                result, boundary, _ = self.lookup(outcomes=[payload])
                self.assertEqual(result["status"], "lookup-error")
                self.assertEqual(len(boundary.urls), 1)

    def test_timeout_is_retried_with_bounded_delays_then_recovers(self):
        result, boundary, waits = self.lookup(outcomes=[socket.timeout("timeout"), crossref_record()])
        self.assertEqual(result["status"], "metadata-only")
        self.assertEqual(result["attempts"], 2)
        self.assertNotIn("error", result)
        self.assertEqual(len(waits), 1)
        self.assertTrue(0 <= waits[0] <= 5)
        self.assertTrue(all(0 < value <= 120 for value in boundary.timeouts))

    def test_repeated_transient_failures_stop_after_three_attempts(self):
        result, boundary, waits = self.lookup(outcomes=[URLError("temporary failure") for _ in range(3)])
        self.assertEqual(result["status"], "lookup-error")
        self.assertEqual(result["attempts"], 3)
        self.assertEqual(len(boundary.urls), 3)
        self.assertEqual(len(waits), 2)

    def test_rate_limit_retry_after_is_capped_then_recovers(self):
        result, _, waits = self.lookup(outcomes=[http_error(429, {"Retry-After": "9999"}), crossref_record()])
        self.assertEqual(result["status"], "metadata-only")
        self.assertEqual(waits, [5.0])

    def test_server_error_is_retried_but_forbidden_is_not(self):
        result, _, _ = self.lookup(outcomes=[http_error(503), crossref_record()])
        self.assertEqual(result["status"], "metadata-only")
        result, boundary, waits = self.lookup(outcomes=[http_error(403)])
        self.assertEqual(result["status"], "lookup-error")
        self.assertEqual(len(boundary.urls), 1)
        self.assertEqual(waits, [])

    def test_redirect_does_not_cause_a_second_endpoint_request(self):
        result, boundary, _ = self.lookup(outcomes=[http_error(302, {"Location": "https://other.example/private"})])
        self.assertEqual(result["status"], "lookup-error")
        self.assertEqual(len(boundary.urls), 1)

    def test_real_opener_refuses_cross_host_redirect_before_transport(self):
        urls = []
        def transport(request):
            urls.append(request.full_url)
            if request.full_url != "https://api.crossref.org/works/10.5555%2Fexample":
                raise AssertionError("Redirect reached an unauthorized transport host")
            headers = Message()
            headers["Location"] = "https://other.example/private"
            response = addinfourl(io.BytesIO(b""), headers, request.full_url, 302)
            response.msg = "Found"
            return response
        with patch.object(HTTPSHandler, "https_open", side_effect=transport):
            result = subject.verify_doi("10.5555/example")
        self.assertEqual(result["status"], "lookup-error")
        self.assertEqual(urls, ["https://api.crossref.org/works/10.5555%2Fexample"])

    def test_invalid_unicode_input_is_reported_without_network(self):
        result, boundary, _ = self.lookup("10.5555/\ud800")
        self.assertEqual(result["status"], "invalid-input")
        self.assertEqual(boundary.urls, [])

    def test_oversized_remote_response_fails_closed(self):
        result, _, _ = self.lookup(outcomes=[b" " * (2 * 1024 * 1024 + 1)])
        self.assertEqual(result["status"], "lookup-error")

    def test_invalid_retry_options_cannot_create_unbounded_requests(self):
        for options in ({"max_attempts": 0}, {"max_attempts": 100}, {"timeout": 0}, {"timeout": float("nan")}):
            with self.subTest(options=options):
                result, boundary, _ = self.lookup(**options)
                self.assertEqual(result["status"], "invalid-input")
                self.assertEqual(boundary.urls, [])


class CliFileSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.network_guard = patch("socket.create_connection",
                                  side_effect=AssertionError("Real network disabled in tests"))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)

    def run_cli(self, args, outcomes=None):
        output, errors = io.StringIO(), io.StringIO()
        boundary = CrossrefBoundary(*(outcomes or [crossref_record()]))
        with patch.object(subject.CROSSREF_HTTP, "open", side_effect=boundary), \
             patch.object(subject.time, "sleep"), contextlib.redirect_stdout(output), \
             contextlib.redirect_stderr(errors):
            code = subject.main(args)
        return code, output.getvalue(), errors.getvalue(), boundary

    def test_existing_cli_flags_write_atomic_json_from_bom_list(self):
        source, destination = self.root / "dois.txt", self.root / "results.json"
        source.write_text("\ufeff10.5555/example\n\n", encoding="utf-8")
        destination.write_text("old output", encoding="utf-8")
        code, output, _, _ = self.run_cli(["--doi-list", str(source), "--output", str(destination)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8"))[0]["status"], "metadata-only")
        self.assertEqual(json.loads(output)[0]["doi"], "10.5555/example")
        self.assertEqual(source.read_text(encoding="utf-8"), "\ufeff10.5555/example\n\n")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["dois.txt", "results.json"])

    def test_output_same_as_input_preserves_source_before_any_request(self):
        source = self.root / "dois.txt"
        source.write_text("10.5555/example\n", encoding="utf-8")
        code, _, errors, boundary = self.run_cli(["--doi-list", str(source), "--output", str(source)])
        self.assertEqual(code, 2)
        self.assertEqual(source.read_text(encoding="utf-8"), "10.5555/example\n")
        self.assertEqual(boundary.urls, [])
        self.assertTrue(errors.strip())

    def test_output_alias_to_input_is_also_rejected(self):
        source = self.root / "dois.txt"
        source.write_text("10.5555/example\n", encoding="utf-8")
        alias = self.root / "alias.json"
        try:
            alias.hardlink_to(source)
        except OSError as error:
            self.skipTest(f"Hard links unavailable: {error}")
        code, _, _, boundary = self.run_cli(["--doi-list", str(source), "--output", str(alias)])
        self.assertEqual(code, 2)
        self.assertEqual(boundary.urls, [])
        self.assertEqual(source.read_text(encoding="utf-8"), "10.5555/example\n")

    def test_missing_or_non_utf8_input_has_friendly_failure(self):
        source = self.root / "bad.txt"
        for contents in (None, b"\xff\xfe\x00"):
            if contents is not None:
                source.write_bytes(contents)
            code, _, errors, boundary = self.run_cli(["--doi-list", str(source)])
            self.assertEqual(code, 2)
            self.assertTrue(errors.strip())
            self.assertNotIn("Traceback", errors)
            self.assertEqual(boundary.urls, [])

    def test_empty_input_is_rejected_without_network(self):
        source = self.root / "empty.txt"
        source.write_text("\n \n", encoding="utf-8")
        code, _, _, boundary = self.run_cli(["--doi-list", str(source)])
        self.assertEqual(code, 2)
        self.assertEqual(boundary.urls, [])

    def test_missing_output_directory_is_detected_before_network(self):
        destination = self.root / "missing" / "result.json"
        code, _, errors, boundary = self.run_cli(["--doi", "10.5555/example", "--output", str(destination)])
        self.assertEqual(code, 2)
        self.assertEqual(boundary.urls, [])
        self.assertNotIn("Traceback", errors)

    def test_atomic_replace_failure_preserves_old_output_and_cleans_temporary_file(self):
        destination = self.root / "result.json"
        destination.write_text("original output", encoding="utf-8")
        with patch.object(subject.os, "replace", side_effect=PermissionError("destination locked")):
            code, _, errors, _ = self.run_cli(["--doi", "10.5555/example", "--output", str(destination)])
        self.assertEqual(code, 2)
        self.assertEqual(destination.read_text(encoding="utf-8"), "original output")
        self.assertEqual([p.name for p in self.root.iterdir()], ["result.json"])
        self.assertNotIn("Traceback", errors)

    def test_partial_lookup_failure_is_saved_but_exit_code_is_nonzero(self):
        code, output, _, _ = self.run_cli(["--doi", "10.5555/example"], outcomes=[http_error(403)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)[0]["status"], "lookup-error")

    def test_crossref_not_found_is_completed_lookup_with_unknown_existence(self):
        code, output, _, _ = self.run_cli(["--doi", "10.5555/example"], outcomes=[http_error(404)])
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(output)[0]["work_exists"])

    def test_invalid_doi_outputs_machine_readable_state_without_network(self):
        code, output, _, boundary = self.run_cli(["--doi", "not a DOI"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)[0]["status"], "invalid-input")
        self.assertEqual(boundary.urls, [])

    def test_non_ascii_metadata_is_usable_on_legacy_console_encoding(self):
        record = crossref_record()
        record["message"]["title"] = ["教学研究 β"]
        output = io.BytesIO()
        console = io.TextIOWrapper(output, encoding="ascii", write_through=True)
        boundary = CrossrefBoundary(record)
        with patch.object(subject.CROSSREF_HTTP, "open", side_effect=boundary), \
             contextlib.redirect_stdout(console), contextlib.redirect_stderr(io.StringIO()):
            code = subject.main(["--doi", "10.5555/example"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue().decode("ascii"))[0]["title"], "教学研究 β")


if __name__ == "__main__":
    unittest.main()
