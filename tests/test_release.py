"""Release regression checks: no real key, API request or original study write."""
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

from financial_table_verifier import cli, paper
from financial_table_verifier.openrouter import APIError, ConnectError, HTTPTransport, OutcomeUnknown, _NoRedirect
from financial_table_verifier.pipeline import run_experiment
from financial_table_verifier.schema import Experiment


class ReleaseSafetyTests(unittest.TestCase):
    def test_live_api_requires_explicit_programmatic_opt_in_before_any_work(self):
        cfg = Experiment(model="test/model", manifest=Path("missing"), output=Path("unused"))
        with patch("financial_table_verifier.pipeline._snapshot") as snapshot:
            with self.assertRaisesRegex(RuntimeError, "allow_paid"):
                run_experiment(cfg, None)
            snapshot.assert_not_called()

    def test_cli_paid_guard_precedes_config_or_key_reads(self):
        with patch("financial_table_verifier.cli.load_config") as load, redirect_stderr(io.StringIO()) as error:
            self.assertEqual(cli.main(["run", "--config", "missing.toml"]), 1)
        load.assert_not_called()
        self.assertIn("--allow-paid", error.getvalue())

    def test_rejected_config_value_is_not_echoed(self):
        canary = "SYNTHETIC_PRIVATE_CONFIG_VALUE"
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.toml"
            p.write_text('model="test/model"\nmanifest="missing"\noutput="unused"\napi_key="'+canary+'"\n')
            with redirect_stderr(io.StringIO()) as error:
                self.assertEqual(cli.main(["plan", "--config", str(p)]), 1)
            self.assertNotIn(canary, error.getvalue())
            self.assertIn("extra_forbidden", error.getvalue())

    def transport(self):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "synthetic-test-token"}, clear=True):
            return HTTPTransport(2)

    def test_tls_and_timeout_are_unknown_not_assumed_free(self):
        for reason in [ssl.SSLError("synthetic TLS failure"), TimeoutError("synthetic timeout")]:
            with self.subTest(reason=type(reason).__name__):
                opener = Mock();opener.open.side_effect = urllib.error.URLError(reason)
                with patch("urllib.request.build_opener", return_value=opener):
                    with self.assertRaises(OutcomeUnknown):
                        self.transport().request("POST", "chat/completions", {})

    def test_dns_and_connection_refusal_are_pre_admission(self):
        for reason in [socket.gaierror("synthetic DNS failure"), ConnectionRefusedError("synthetic refusal")]:
            opener = Mock();opener.open.side_effect = urllib.error.URLError(reason)
            with patch("urllib.request.build_opener", return_value=opener):
                with self.assertRaises(ConnectError):
                    self.transport().request("POST", "chat/completions", {})

    def test_redirect_cannot_forward_credentials(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://invalid.test"))
        opener = Mock();opener.open.side_effect = urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions", 302, "redirect", {}, None)
        with patch("urllib.request.build_opener", return_value=opener) as build:
            with self.assertRaises(APIError) as caught:
                self.transport().request("POST", "chat/completions", {})
            self.assertIsInstance(build.call_args.args[0], _NoRedirect)
        self.assertEqual(caught.exception.status, 302)

    def test_endpoint_and_malformed_key_rejected_without_exposing_value(self):
        with patch("urllib.request.build_opener") as build:
            with self.assertRaisesRegex(ValueError, "endpoint"):
                self.transport().request("POST", "https://invalid.test", {})
            build.assert_not_called()
        canary = "SYNTHETIC\nPRIVATE"
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": canary}, clear=True):
            with self.assertRaises(RuntimeError) as error:
                HTTPTransport(2)
        self.assertNotIn(canary, str(error.exception))


class PaperReleaseTests(unittest.TestCase):
    def test_frozen_sources_and_exact_active_procedure(self):
        self.assertEqual(paper.info()["actual_csv_procedure_sha256"],
                         "59d762e497c652a2bc0cc770fa6a463bfe25ea86080fffec99f466b8e375cac4")

    def test_actual_csv_fixture_and_fixed_scorer_work_without_network(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("socket.create_connection", side_effect=AssertionError("No network")):
                result = paper.demo(Path(td))
            self.assertEqual(result["paid_requests"], 0)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["numeric_transcription"]["correct_signed_printed_values"], 1)
            p = Path(td) / "portable-score-input.json"
            data = json.loads(p.read_text()); data["records"][0]["raw_value"] = "999"
            p.write_text(json.dumps(data))
            wrong = paper.score(p, Path(td) / "wrong-score.json")
            self.assertEqual(wrong["results"]["populations"]["BSIS"]["numeric_transcription"]["correct_signed_printed_values"], 0)
            with self.assertRaisesRegex(ValueError, "overwrite"):
                paper.score(p, Path(td) / "wrong-score.json")

    def test_source_manifest_refuses_path_escape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "source-manifest.json").write_text(json.dumps({"files": [
                {"runtime_path": "../outside.py", "sha256": "0" * 64}]}))
            with patch.object(paper, "RUNTIME", root):
                with self.assertRaisesRegex(ValueError, "source path"):
                    paper.verify_sources()


if __name__ == "__main__":
    unittest.main()
