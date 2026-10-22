"""Contract tests for the agent-platform demo.

The tests are stdlib-only. They check the payload shape, the three story
paths (denied tool, silent empty answer, degraded fallback), that the
committed static artifact matches a fresh run, and that the UI has no
hand-written metric values or secret/absolute-path leaks.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "demo") not in sys.path:
    sys.path.insert(0, str(ROOT / "demo"))

import server  # noqa: E402
from pipeline_adapter import run_batch, write_payload  # noqa: E402

PUBLIC = ROOT / "demo" / "public"
PUBLIC_FILES = [
    PUBLIC / "index.html",
    PUBLIC / "app.js",
    PUBLIC / "style.css",
    PUBLIC / "config.json",
]
UI_DATA_FILES = [PUBLIC / "index.html", PUBLIC / "app.js"]


def normalize(payload: dict) -> dict:
    """Drop fields that legitimately vary between two runs."""
    data = json.loads(json.dumps(payload))
    data["meta"].pop("generated_at", None)
    data["meta"].pop("commit", None)
    data["meta"].pop("worktree_dirty", None)
    data["meta"].pop("dirty_paths", None)
    data["summary"].pop("latency_ms", None)
    for item in data["items"]:
        item.pop("latency_ms", None)
        for stage in item.get("stages", []):
            for metric in stage.get("metrics", []):
                if metric.get("label") in {"耗时", "端到端耗时"}:
                    metric["value"] = "IGNORED"
    return data


def banned_metric_values(payload: dict) -> set[str]:
    summary = payload["summary"]
    return {
        f"{summary['success']}/{summary['tickets']}",
        f"{summary['context_tokens']['saved_pct_mean'] * 100:.0f}%",
        str(summary["context_tokens"]["saved_tokens"]),
        str(summary["context_tokens"]["compacted_sum"]),
        str(summary["context_tokens"]["uncompacted_sum"]),
        f"{summary['pinned']}/{summary['tickets']}",
    }


def find_banned_metrics(text: str, banned: set[str]) -> list[str]:
    return sorted(value for value in banned if value in text)


def find_secret_patterns(text: str) -> list[str]:
    patterns = {
        "/home/": r"/home/",
        "/Users/": r"/Users/",
        "C-drive": r"C:\\\\",
        "sk-": r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]",
        "ghp_": r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]",
        "AKIA": r"AKIA[0-9A-Z]{16}",
        "private-key": r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY",
    }
    return sorted(name for name, pattern in patterns.items()
                  if re.search(pattern, text))


class DemoPayloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = run_batch()

    def test_payload_shape_and_story_paths(self) -> None:
        payload = self.payload
        self.assertEqual(payload["summary"]["tickets"], 10)
        self.assertEqual(len(payload["items"]), 10)
        self.assertEqual(payload["summary"]["pinned"], 10)
        self.assertEqual(payload["summary"]["tool_verdicts"], {"ok": 5, "denied": 1})
        self.assertTrue(payload["boundaries"])
        for item in payload["items"]:
            self.assertEqual(len(item["stages"]), 6, item["id"])
            for stage in item["stages"]:
                self.assertTrue(stage["metrics"], f"{item['id']}/{stage['key']}")
                for metric in stage["metrics"]:
                    self.assertTrue(metric["source"], f"{item['id']}/{metric['label']}")

        by_id = {item["id"]: item for item in payload["items"]}
        self.assertEqual(by_id["T00"]["failure"], "")
        self.assertTrue(by_id["T00"]["answer"])
        self.assertEqual(by_id["T02"]["llm_status"], "degraded")
        self.assertEqual(by_id["T04"]["failure"], "silent_empty_answer")
        self.assertEqual(by_id["T04"]["answer"], "")
        self.assertEqual(by_id["T05"]["tool_verdict"], "denied")
        self.assertIn("tool_denied", by_id["T05"]["flags"])
        self.assertIn("answered_without_tool_data", by_id["T05"]["flags"])
        self.assertIn("未查到该订单", by_id["T09"]["answer"])

    def test_reproducible_except_observed_latency(self) -> None:
        second = run_batch()
        self.assertEqual(normalize(self.payload), normalize(second))

    def test_matches_committed_m7_result(self) -> None:
        verification = self.payload["verification"]
        self.assertIn(verification["status"], {"MATCH", "NOT_AVAILABLE"})
        self.assertEqual(
            verification["status"], "MATCH",
            f"fresh run diverged from results/m7_e2e.json: "
            f"{verification.get('mismatches')}",
        )

    def test_committed_static_payload_is_current(self) -> None:
        artifact = PUBLIC / "runs.json"
        self.assertTrue(artifact.exists(), "run demo/build_static.py first")
        committed = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(normalize(committed), normalize(self.payload))

    def test_write_payload_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runs.json"
            write_payload(path, self.payload)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["summary"]["tickets"], 10)


class DemoGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = run_batch()
        cls.banned = banned_metric_values(cls.payload)

    def test_ui_has_no_handwritten_metrics(self) -> None:
        for path in UI_DATA_FILES:
            text = path.read_text(encoding="utf-8")
            hits = find_banned_metrics(text, self.banned)
            self.assertEqual(hits, [], f"hardcoded metrics in {path.name}: {hits}")

    def test_metric_scanner_can_go_red(self) -> None:
        self.assertEqual(find_banned_metrics("score 9/10", {"9/10"}), ["9/10"])
        self.assertEqual(find_banned_metrics("nothing here", {"9/10"}), [])

    def test_no_secrets_or_absolute_paths(self) -> None:
        for path in PUBLIC_FILES + [
            ROOT / "demo" / "pipeline_adapter.py",
            ROOT / "demo" / "build_static.py",
            ROOT / "demo" / "server.py",
        ]:
            text = path.read_text(encoding="utf-8")
            hits = find_secret_patterns(text)
            self.assertEqual(hits, [], f"secret/absolute path in {path.name}: {hits}")

    def test_secret_scanner_can_go_red(self) -> None:
        self.assertEqual(find_secret_patterns("/home/alice/x"), ["/home/"])
        self.assertEqual(find_secret_patterns("sk-test"), ["sk-"])
        self.assertEqual(find_secret_patterns("safe text"), [])

    def test_offline_upstream_and_no_outbound_calls(self) -> None:
        from m7_e2e import ScriptedUpstream

        self.assertTrue(getattr(ScriptedUpstream(), "offline", False))
        for name in ("pipeline_adapter.py", "server.py"):
            text = (ROOT / "demo" / name).read_text(encoding="utf-8")
            self.assertNotIn("requests", text, name)
            self.assertNotIn("urllib.request", text, name)
            self.assertNotIn("socket.create_connection", text, name)

    def test_static_index_contract(self) -> None:
        html = (PUBLIC / "index.html").read_text(encoding="utf-8")
        for element_id in (
            "mode-badge", "mode-note", "rerun", "summary", "ticket-list",
            "stage-timeline", "answer", "evidence", "boundaries", "meta",
        ):
            self.assertIn(f'id="{element_id}"', html, element_id)

    def test_provenance_is_complete(self) -> None:
        meta = self.payload["meta"]
        for key in ("repo", "demo", "commit", "generated_at", "seed", "model"):
            self.assertIn(key, meta)
            self.assertIsNotNone(meta[key])
        for item in self.payload["items"]:
            self.assertTrue(item["sources"])
            self.assertIn("answer", item["sources"])
            self.assertIn("tool_verdict", item["sources"])


class DemoServerTest(unittest.TestCase):
    def test_api_health_runs_and_live_rerun(self) -> None:
        httpd, port = server.make_server(port=0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=5) as response:
                health = json.load(response)
            self.assertEqual(health["status"], "ok")
            self.assertEqual(health["mode"], "live")

            with urllib.request.urlopen(f"{base}/config.json", timeout=5) as response:
                config = json.load(response)
            self.assertEqual(config["mode"], "live")

            with urllib.request.urlopen(f"{base}/runs.json", timeout=60) as response:
                payload = json.load(response)
            self.assertEqual(payload["summary"]["tickets"], 10)

            request = urllib.request.Request(
                f"{base}/api/run",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                rerun = json.load(response)
            self.assertEqual(rerun["summary"]["tickets"], 10)
            self.assertEqual(rerun["items"][0]["id"], "T00")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
