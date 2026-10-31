"""AST-isolated operator runner probes: no budget module, key reads or network."""
import ast
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from ftv_csv_reconstruction.contracts import CSVConfig
from test_csv_first import fixture

RUNNER = Path(__file__).resolve().parents[2] / "csv_development_runner.py"


def selected_definitions(names, namespace):
    source = ast.parse(RUNNER.read_text())
    nodes = [n for n in source.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(RUNNER), "exec"), namespace)
    return namespace


class RunnerTests(unittest.TestCase):
    def test_source_only_guard_rejects_obsolete_schema_before_inner_call(self):
        scope = selected_definitions({"StopAfterSource", "SourceOnlyTransport"}, {})
        class Fake:
            def __init__(self): self.calls = []
            def request(self, *args): self.calls.append(args); return {}
        inner = Fake()
        guard = scope["SourceOnlyTransport"](inner)
        for name in ("document_generated_schema", "generated_csv_schema", "financial_csv_page", "auxiliary_html_page"):
            with self.assertRaises(scope["StopAfterSource"]):
                guard.request("POST", "chat/completions", {"response_format": {"json_schema": {"name": name}}})
        self.assertEqual(inner.calls, [])
        guard.request("POST", "chat/completions", {"response_format": {"json_schema": {"name": "document_survey_chunk"}}})
        guard.request("POST", "chat/completions", {"response_format": {"json_schema": {"name": "document_structure"}}})
        self.assertEqual(len(inner.calls), 2)

    def test_plan_fingerprint_checked_before_transport_construction(self):
        calls = []
        scope = {"read": lambda p: {"procedure_sha256": "wrong"}, "DEST": Path("unused"), "PLAN": Path("unused/plan-v2.json"),
                 "procedure_hash": lambda: "correct", "SafeRetryTransport": lambda p: calls.append(p)}
        selected_definitions({"run_one"}, scope)
        with self.assertRaises(RuntimeError): scope["run_one"]("finite-job", "csv")
        self.assertEqual(calls, [])

    def test_config_fingerprint_checked_before_transport_construction(self):
        calls = []
        scope = {"read": lambda p: {"procedure_sha256": "correct", "jobs": [{"id": "finite-job", "config": "unused", "config_sha256": "expected"}]},
                 "DEST": Path("unused"), "PLAN": Path("unused/plan-v2.json"), "procedure_hash": lambda: "correct", "sha": lambda p: "changed",
                 "SafeRetryTransport": lambda p: calls.append(p)}
        selected_definitions({"run_one"}, scope)
        with self.assertRaises(RuntimeError): scope["run_one"]("finite-job", "csv")
        self.assertEqual(calls, [])

    def test_html_config_json_paths_and_print_flush_are_executable_offline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = fixture(root)
            path = root / "config.json"
            path.write_text(json.dumps(config.model_dump(mode="json")))
            captured, saved, transport_owners = [], [], []
            plan = {"procedure_sha256": "correct", "jobs": [{"id": "synthetic-test", "source_tag": "synthetic",
                "config": str(path), "config_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]}
            scope = {"DEST": root, "PLAN": root / "execution-plan-v2.json", "HTML": root / "html-v2", "HTML_CONFIGS": root / "html-configs-v2",
                "read": lambda p: plan, "procedure_hash": lambda: "correct",
                "sha": lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest(), "Path": Path, "json": json,
                "CSVConfig": CSVConfig, "save": lambda p, d: saved.append((p, d)),
                "SafeRetryTransport": lambda p: transport_owners.append(p) or "canned",
                "extract_html": lambda cfg, primary, transport: captured.append((cfg, primary, transport)) or {"status": "complete"}}
            selected_definitions({"run_one"}, scope)
            with redirect_stdout(io.StringIO()) as stdout:
                scope["run_one"]("synthetic-test", "html")
            self.assertEqual(captured[0][0].selected_pages, [2])
            self.assertIsInstance(captured[0][0].output, Path)
            self.assertEqual(captured[0][1], config.output)
            self.assertEqual(captured[0][2], "canned")
            self.assertEqual(len(saved), 1)
            self.assertEqual(len(transport_owners), 1)
            self.assertEqual(json.loads(stdout.getvalue())["stage"], "html")


if __name__ == "__main__": unittest.main()
