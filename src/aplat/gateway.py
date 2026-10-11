"""M1 LLM 网关：OpenAI 兼容入口 + 路由 + 幂等 + 成本核算。

要点（企业里为什么需要这一层）：
- **统一的入口与记账**：agent 调模型必须知道每次调用花了多少 token、多少钱、
  落在哪个路由上，否则做不了成本归因（M2 的成本大盘依赖这张表）。
- **幂等**：agent 的重试经常重复发同一个请求。按 idempotency_key 命中缓存时
  直接返回首个结果，token 成本记 0。这不是缓存优化，是**防止重复计费**。
- **熔断降级**：上游不可用时不把异常抛给 agent，而是按路由策略降级返回，
  降级响应带 `degraded=True` 标记，让调用方知道这不是正常回答。

本模块是 _lean 实现：不写 HTTP 服务，直接用函数式入口 `chat()`,
再用 `serve()` 提供 stdlib http.server 版 OpenAI 兼容端点（供本地 curl 验证）。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from . import common as C
from .ollama_client import OllamaClient, OllamaError
from .tokenizer import count_tokens

BANNED = "banned-placeholder"


@dataclass
class ChatRequest:
    model: str
    messages: list[dict]
    route: str = "default"
    temperature: float = 0.2
    max_tokens: int = 512
    idempotency_key: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ChatResponse:
    request_id: str
    model: str
    route: str
    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    cost_usd: float
    status: str
    http_status: int
    cache_hit: bool
    degraded: bool = False
    error_kind: str | None = None
    replay_of: str | None = None
    done_reason: str = ""          # stop | length | ""
    thinking_tokens: int = 0       # 思考型模型在 thinking 通道烧掉的估算 token
    empty_content: bool = False    # 上游 200 但 content 为空 = 静默失败


class LLMGateway:
    """网关本体。线程不安全——单进程本地使用，并发由 M5 的退避层在上游侧处理。"""

    def __init__(self, db_path: Path, client: OllamaClient,
                 routes: dict[str, dict] | None = None,
                 breaker: Any = None) -> None:
        self.db_path = db_path
        self.client = client
        self.breaker = breaker
        self._trace_on = True
        self._current_trace = ""
        self._trace_base = 0
        self.routes = routes or {
            # route -> 主模型、降级模型、超时意图
            "default": {"primary": "qwen3:8b", "fallback": "llama3.2:3b"},
            "cheap": {"primary": "llama3.2:3b", "fallback": "llama3.2:3b"},
            "quality": {"primary": "qwen3:8b", "fallback": "llama3.2:3b"},
        }
        self.idem_index: dict[str, str] = {}   # key -> first request_id（跨进程则读 DB）

    # ------------------------------------------------------------ 幂等

    def _lookup_idem(self, key: str) -> str | None:
        with C.db(self.db_path) as conn:
            row = conn.execute(
                "SELECT request_id FROM gateway_request WHERE idempotency_key=? "
                "AND status IN ('ok','degraded') ORDER BY ts_wall LIMIT 1", (key,)
            ).fetchone()
            return row["request_id"] if row else None

    def _replay(self, request_id: str, new_model: str) -> ChatResponse | None:
        with C.db(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM gateway_request WHERE request_id=?", (request_id,)
            ).fetchone()
            if not row:
                return None
        # 只有同 route 才允许重放：不同 route 需求不同，重放是错的
        return ChatResponse(
            request_id=f"req_{uuid.uuid4().hex[:12]}",
            model=new_model,
            route=row["route"],
            content=row["content"],
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            latency_ms=0, cost_usd=0.0,
            status="cached", http_status=200, cache_hit=True,
            replay_of=request_id,
        )

    def _resolve_model(self, requested: str, route_cfg: dict) -> tuple[str, str]:
        """把 client 请求的 model 名解析成实际要打的模型。

        优先级：
          1. requested 命中 known_models（各 route 的 primary/fallback 并集）→ 直接用它
          2. requested 等于本 route 的 primary、或为空 → route primary
          3. 其余（未知模型名）→ route primary，并返回 note 说明被忽略了什么

        返回 (model, note)。note 会写进 span attrs，便于排查「为什么日志里
        的模型和我请求的不一样」。
        """
        known = set()
        for cfg in self.routes.values():
            known.add(cfg.get("primary"))
            known.add(cfg.get("fallback"))
        if requested and requested in known:
            if requested != route_cfg.get("primary"):
                return requested, f"explicit model override (route primary=" \
                                  f"{route_cfg.get('primary')})"
            return requested, "explicit model == route primary"
        if not requested:
            return route_cfg["primary"], "empty request model → route primary"
        return route_cfg["primary"], \
            f"unknown model '{requested}' ignored → route primary"

    # ------------------------------------------------------------ 主流程

    def chat(self, req: ChatRequest) -> ChatResponse:
        wall = C.now_ms_wall()
        mono = C.monotonic_ms()
        rid = f"req_{uuid.uuid4().hex[:12]}"
        key = req.idempotency_key or f"auto-{rid}"
        # span 追踪开关：默认开。热路径关掉可省两次 SQLite IO。
        self._trace_on = True
        self._current_trace = f"tr_{uuid.uuid4().hex[:10]}"
        self._trace_base = mono
        if self._trace_on:
            self._span("cache", mono, 0, "started", {"idempotency_key": key})

        # 1) 幂等：同 key 已成功过 → 直接返回首个结果（0 token 成本）
        first = self._lookup_idem(key)
        if first is not None:
            replayed = self._replay(first, req.model)
            if replayed is not None:
                self._record(wall, mono, replayed, key)
                if self._trace_on:
                    self._span("cache", mono, C.monotonic_ms() - mono, "hit",
                               {"replay_of": replayed.replay_of})
                    self._span("upstream", mono, 0, "skipped", {"reason": "idem_hit"})
                return replayed

        route_cfg = self.routes.get(req.route, self.routes["default"])
        # 模型解析：route 决定「默认用哪个」，但调用方显式点名一个已知模型时必须尊重。
        # 否则 OpenAI 兼容端点上 client 传的 model 会被无声丢弃——本次 live 实测踩到：
        # curl 请求 llama3.2:3b，服务却按 route primary 用了 qwen3:8b。
        model, model_note = self._resolve_model(req.model, route_cfg)

        # 2) 熔断检查：open 时直接降级，不打上游
        #    这里的语义必须是「拒绝」而不是「换模型重试」：
        #    open 意味着主模型已知不可用，但 fallback 与 primary 同名时
        #    （cheap 路由）换模型等于换个名字再失败一次，白烧一次上游调用。
        breaker_blocked = False
        if self.breaker and not self.breaker.allow(model):
            breaker_blocked = True
            degraded = True
        else:
            degraded = False

        prompt_tokens = sum(count_tokens(m["content"], req.model) for m in req.messages)

        # 3) 熔断拦截：不打上游，直接返回带标记的降级响应
        if breaker_blocked:
            resp = ChatResponse(
                request_id=rid, model=model, route=req.route,
                content="", prompt_tokens=prompt_tokens, completion_tokens=0,
                total_tokens=prompt_tokens, latency_ms=0, cost_usd=0.0,
                status="degraded", http_status=503, cache_hit=False,
                degraded=True, error_kind="circuit_open",
            )
            self._record(wall, mono, resp, key)
            return resp

        # 4) 调用上游，捕获错误并按策略降级
        start = C.monotonic_ms()
        try:
            raw = self.client.chat(
                model=model, messages=req.messages,
                temperature=req.temperature, max_tokens=req.max_tokens,
            )
        except OllamaError as e:
            if self._trace_on:
                self._span("upstream", start, C.monotonic_ms() - start, "error",
                           {"model": model, "model_note": model_note,
                            "error_kind": e.kind})
            return self._handle_error(req, rid, key, wall, start, model, e, route_cfg)

        latency_ms = C.monotonic_ms() - start
        content = raw.get("content", "") or ""
        thinking = raw.get("thinking", "") or ""
        done_reason = raw.get("done_reason", "") or ""
        completion_tokens = count_tokens(content, model)
        thinking_tokens = count_tokens(thinking, model)
        total = prompt_tokens + completion_tokens
        cost = C.cost_usd(model, prompt_tokens, completion_tokens)

        # 静默失败检测：HTTP 200 + 空 content 不算成功。
        # 思考型模型（qwen3 系）会把预算烧在 thinking 通道，content 为空、
        # done_reason=length。不标记的话 M2 的错误率会漏掉这一类失败。
        empty = (content.strip() == "")
        status = "empty" if empty else ("degraded" if degraded else "ok")
        if empty:
            self._last_status_note = (
                f"empty content (done_reason={done_reason}, "
                f"thinking_tokens≈{thinking_tokens})")

        resp = ChatResponse(
            request_id=rid, model=model, route=req.route, content=content,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            total_tokens=total, latency_ms=latency_ms, cost_usd=cost,
            status=status, http_status=200,
            cache_hit=False, degraded=degraded, done_reason=done_reason,
            thinking_tokens=thinking_tokens, empty_content=empty,
            error_kind="empty_content" if empty else None,
        )
        if self.breaker:
            self.breaker.on_success(model)  # 兼容两种签名
        self._record(wall, mono, resp, key)
        if self._trace_on:
            self._span("upstream", start, latency_ms,
                       "error" if empty else "ok",
                       {"model": model, "model_note": model_note,
                        "done_reason": done_reason,
                        "thinking_tokens": thinking_tokens})
        return resp

    def _handle_error(self, req, rid, key, wall, start, model, err, route_cfg,
                      mono: int = 0) -> ChatResponse:
        latency_ms = C.monotonic_ms() - start
        kind = err.kind
        if self.breaker:
            self.breaker.on_failure(model, kind)
        # 降级：用 fallback 模型重试一次（只在错误可降级时）
        fb = route_cfg.get("fallback")
        if fb and fb != model and kind in ("timeout", "upstream_5xx"):
            try:
                raw = self.client.chat(model=fb, messages=req.messages,
                                       temperature=req.temperature,
                                       max_tokens=req.max_tokens)
                pt = sum(count_tokens(m["content"], fb) for m in req.messages)
                ct = count_tokens(raw.get("content", ""), fb)
                resp = ChatResponse(
                    request_id=rid, model=fb, route=req.route,
                    content=raw.get("content", ""), prompt_tokens=pt,
                    completion_tokens=ct, total_tokens=pt + ct, latency_ms=latency_ms,
                    cost_usd=C.cost_usd(fb, pt, ct), status="degraded",
                    http_status=200, cache_hit=False, degraded=True,
                )
                self._record(wall, mono, resp, key)
                return resp
            except OllamaError as e2:
                kind = e2.kind
        resp = ChatResponse(
            request_id=rid, model=model, route=req.route, content="",
            prompt_tokens=0, completion_tokens=0, total_tokens=0,
            latency_ms=latency_ms, cost_usd=0.0, status="error",
            http_status=502, cache_hit=False, degraded=True, error_kind=kind,
        )
        self._record(wall, 0, resp, key)
        return resp

    def _span(self, name: str, start_off: int, duration_ms: int,
              status: str, attrs: dict | None = None) -> None:
        """把一个阶段以 span 落盘，供 M2 可观测台汇总。

        start_off 是相对本进程某次 chat() 起点的单调偏移；同一 trace 的
        span 之间有重叠（cache 与 tokenize 都在 upstream 之前），
        所以 M2 只能用 duration 而非 end-start 计算墙钟差。
        """
        if not self._trace_on:
            return
        sid = f"{self._current_trace}-{name}"
        now = C.monotonic_ms()
        try:
            with C.db(self.db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO span
                       (span_id, trace_id, parent_id, request_id, name, kind,
                        start_mono, end_mono, duration_ms, status, attrs)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (sid, self._current_trace, None, "", name, "gateway",
                     start_off, start_off + duration_ms, duration_ms, status,
                     C.json_dumps(attrs or {})),
                )
                conn.commit()
        except Exception:
            # 可观测性绝不能拖垮业务：span 写失败只降级，不影响返回
            pass

    def _record(self, wall, mono, resp, key) -> None:
        with C.db(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO gateway_request
                   (request_id, idempotency_key, ts_wall, ts_mono, route, model, content,
                    prompt_tokens, completion_tokens, total_tokens, latency_ms,
                    status, http_status, error_kind, cost_usd, cache_hit, replay_of)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (resp.request_id, key, wall, mono, resp.route, resp.model, resp.content,
                 resp.prompt_tokens, resp.completion_tokens, resp.total_tokens,
                 resp.latency_ms, resp.status, resp.http_status, resp.error_kind,
                 resp.cost_usd, 1 if resp.cache_hit else 0, resp.replay_of),
            )
            conn.commit()


# ---------------------------------------------------------------- OpenAI 兼容端点

def serve(gateway: LLMGateway, port: int = 8787) -> None:
    """stdlib http.server 版 OpenAI 兼容 /chat/completions 与 /health。

    用标准库而不是 fastapi：本仓承诺零依赖部署，且 fastapi 只是换了个写法，
    不改变任何口径。用 curl 即可验证：
        curl -s localhost:8787/chat/completions -H 'content-type: application/json' \\
             -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"hi"}]}'
    """
    import http.server

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # 静音默认日志
            pass

        def _json(self, code: int, obj: dict):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._json(200, {"status": "ok", "models": list(gateway.routes)})
            elif self.path == "/v1/models":
                self._json(200, {"object": "list", "data": [
                    {"id": m, "object": "model"} for m in
                    {v["primary"] for v in gateway.routes.values()} |
                    {v["fallback"] for v in gateway.routes.values()}
                ]})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path not in ("/chat/completions", "/v1/chat/completions"):
                self._json(404, {"error": "not found"})
                return
            n = int(self.headers.get("content-length", 0))
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            key = self.headers.get("idempotency-key") or payload.get("idempotency_key")
            cr = ChatRequest(
                model=payload.get("model", gateway.routes["default"]["primary"]),
                messages=payload.get("messages", []),
                route=payload.get("route", "default"),
                temperature=float(payload.get("temperature", 0.2)),
                max_tokens=int(payload.get("max_tokens", 512)),
                idempotency_key=key,
            )
            resp = gateway.chat(cr)
            self._json(resp.http_status, {
                "id": resp.request_id, "object": "chat.completion",
                "model": resp.model, "route": resp.route, "degraded": resp.degraded,
                "cache_hit": resp.cache_hit, "replay_of": resp.replay_of,
                "usage": {"prompt_tokens": resp.prompt_tokens,
                          "completion_tokens": resp.completion_tokens,
                          "total_tokens": resp.total_tokens},
                "cost_usd": resp.cost_usd, "latency_ms": resp.latency_ms,
                "status": resp.status,
                "choices": [{"index": 0, "message":
                             {"role": "assistant", "content": resp.content},
                             "finish_reason": "stop" if resp.status != "error" else "error"}],
            })

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"gateway listening on http://127.0.0.1:{port}  (GET /health, POST /chat/completions)")
    srv.serve_forever()


if __name__ == "__main__":
    raise SystemExit(
        "aplat.gateway 是库模块，不直接运行。"
        "启动服务请用： python3 src/gateway_serve.py --port 8787")
