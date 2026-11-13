import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from framework_agent.support.api import create_server
from framework_agent.support.service_factory import build_default_service

ROOT = Path(__file__).resolve().parents[2]


def request(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_api_health_case_and_resume(tmp_path):
    service = build_default_service(tmp_path / "cases.db", ROOT / "support_data/corpus-v1.jsonl")
    server = create_server(service, ROOT / "support_demo", ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert request(base + "/api/health") == (200, {"status": "ok"})
        status, first = request(base + "/api/cases", {"case_id": "api-1", "question": "Checkpoint fails"})
        assert status == 201 and first["status"] == "needs_clarification"
        status, resumed = request(base + "/api/cases/api-1/resume", {"versions": {"langgraph": "0.5.1"}, "logs": "missing thread_id"})
        assert status == 200 and resumed["status"] == "completed"
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_api_rejects_malformed_and_oversized_payload(tmp_path):
    service = build_default_service(tmp_path / "cases.db", ROOT / "support_data/corpus-v1.jsonl")
    server = create_server(service, ROOT / "support_demo", ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        req = urllib.request.Request(base + "/api/cases", data=b"{bad", headers={"Content-Type":"application/json"}, method="POST")
        try: urllib.request.urlopen(req)
        except urllib.error.HTTPError as error: assert error.code == 400
        req = urllib.request.Request(base + "/api/cases", data=b"x" * 1_100_000, method="POST")
        try: urllib.request.urlopen(req)
        except urllib.error.HTTPError as error: assert error.code == 413
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_demo_has_no_network_dependent_assets():
    html = (ROOT / "support_demo/index.html").read_text(encoding="utf-8")
    assert "https://" not in html and "http://" not in html
