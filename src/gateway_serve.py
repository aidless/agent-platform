#!/usr/bin/env python3
"""把 LLMGateway 起成本地 OpenAI 兼容服务，方便手工 curl 验证 M1。

    python3 src/gateway_serve.py --port 8787
    curl -s localhost:8787/health
    curl -s localhost:8787/chat/completions \\
         -H 'content-type: application/json' \\
         -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"hi"}]}'

默认打真实 ollama（127.0.0.1:11434）。加 --offline 则不发网络，
用于只验证路由本身是否活着。
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aplat.gateway import LLMGateway, serve                        # noqa: E402
from aplat.ollama_client import OllamaClient                       # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--offline", action="store_true",
                    help="不发网络，只验证 HTTP 路由活着")
    ap.add_argument("--db", default="")
    a = ap.parse_args()
    if not a.db:
        a.db = str(Path(tempfile.mkdtemp(prefix="ap_serve_")) / "gateway.db")
    gw = LLMGateway(Path(a.db), OllamaClient(offline=a.offline))
    serve(gw, a.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
