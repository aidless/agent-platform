from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .service_factory import build_default_service

MAX_BODY = 1_000_000


def create_server(service, static_dir: Path, address=("127.0.0.1", 8765)):
    static_root = static_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def _json(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def _read_json(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY:
                # Drain the request so clients can reliably receive the 413 response.
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(65_536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                self._json(413, {"error": "payload_too_large"}); return None
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict): raise ValueError("object required")
                return payload
            except (json.JSONDecodeError, ValueError):
                self._json(400, {"error": "invalid_json"}); return None

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/health": return self._json(200, {"status": "ok"})
            relative = "index.html" if path == "/" else path.lstrip("/")
            target = (static_root / relative).resolve()
            if static_root not in target.parents or not target.is_file(): return self._json(404, {"error": "not_found"})
            body = target.read_bytes(); self.send_response(200); self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_POST(self):
            payload = self._read_json()
            if payload is None: return
            path = urlparse(self.path).path
            try:
                if path == "/api/cases": return self._json(201, service.start(payload).to_dict())
                prefix, suffix = "/api/cases/", "/resume"
                if path.startswith(prefix) and path.endswith(suffix):
                    case_id = path[len(prefix):-len(suffix)]
                    return self._json(200, service.resume(case_id, payload).to_dict())
                return self._json(404, {"error": "not_found"})
            except KeyError:
                return self._json(404, {"error": "case_not_found"})
            except ValueError as error:
                return self._json(400, {"error": str(error)})

    return ThreadingHTTPServer(address, Handler)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=Path(".support/cases.db")); parser.add_argument("--corpus", type=Path, default=Path("support_data/corpus-v1.jsonl")); parser.add_argument("--static", type=Path, default=Path("support_demo"))
    args = parser.parse_args(); server = create_server(build_default_service(args.db, args.corpus), args.static, (args.host, args.port))
    print(f"LangGraph Support Agent -> http://{args.host}:{args.port}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0


if __name__ == "__main__": raise SystemExit(main())
