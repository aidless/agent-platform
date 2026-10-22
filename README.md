# agent-platform

[**Live demo**](https://aidless.github.io/agent-platform/) · [Demo 文档](demo/README.md)

企业 LLM 平台的十一个最小可运行模块。**零第三方依赖**：核心只用 Python 3.10+
标准库 + SQLite；唯一的外部依赖是 M6 数据管道，它用 Docker 里的 PostgreSQL
作为真实上游（不用 `psycopg2`，靠 `psql` + `\copy` CSV 管道）。

本仓刻意只做「能证明的那部分」。每个模块的 `results/*.json` 里都有
`findings`（带 PASS/FAIL 的断言）和 `refutes`（这个模块**不能**证明的事）。
README 这里只转述结论，不复述全部细节。

## 30 秒状态

| 模块 | 结论 | 证据 |
|---|---|---|
| M1 LLM 网关 | smoke 5 项断言 PASS + 1 项记账核对；live 真打 ollama 3/3 成功 + 现场复现思考型模型静默失败 | `results/m1_gateway.json`, `m1_gateway_live.json` |
| M2 可观测台 | span 派生指标 + 4 项口径断言 PASS | `results/m2_observability.json` |
| M3 上下文压缩 | 5 策略对比，含 1 条负例基准 | `results/m3_context_compact.json` |
| **M3b 真 LLM 摘要** | 对比占位首句拼接：占位丢 2/2 金标且自身 224tokens 比 raw 215 还胖；摘要器命中 2/2、省 68% | `results/m3b_llm_summary.json` |
| M4 工具沙箱 | 白名单/审计/重试/死信 8 项断言 PASS | `results/m4_tool_sandbox.json` |
| **M4b OS 级隔离** | 8 条攻击性断言全 PASS（网络/只读/non-root/CapEff/内存/fork/超时/无残留），I0 为非空性对照 | `results/m4b_os_sandbox.json` |
| M5 韧性层 | 退避/熔断/half_open 单探测/降级链 7 项 PASS | `results/m5_resilience.json` |
| M6 数据管道 | 契约/漂移/指纹覆盖度/确定性/慢查询/质量 7 项 PASS | `results/m6_etl.json`, `m6_etl_drift.json` |
| **M7 端到端** | 六模块串联跑通 10 张工单：压缩省 48%、pinned 10/10、失败分类 1 个、M4 判定 {ok:5, denied:1} | `results/m7_e2e.json` |
| **M8 成本与规模** | 三档索引全部 SCAN→USING INDEX，加速比随运行在 **3.7–7.5 / 17–21 / 38–47×** 之间（描述计划变化，不报单一倍数）；云成本 $5.81–49.03/月 | `results/m8_scale.json` |
| **M9 execution-based 评测** | 5 题由 verify 退出码判对 + 错误动作必须判 False；脚本化 agent，测 harness/沙箱/判定链路，不测模型能力 | `results/m9_exec_eval.json` |

加分项不是「又加了一个模块」，而是三件事：
1. **M7 把六个孤立模块串成了一条真链路**（客服工单问答），端到端口径与单点口径的差异自己暴露出来。
2. **M4b 把 README 里声明的「不是 OS 隔离」补成真 OS 隔离**（`python:3.12-slim` + 8 条攻击用例），其中"无残留容器"一条抓出了我自己的真 bug（`docker kill` 异步导致残留）。
3. **M3b/M6-v2 把另两条已声明边界也补了**。

## Demo：M7 工单链路

`demo/` 把 M7 的离线链路做成一页可回放的证据面板：左侧选工单，中间看六步链路，
右侧每个数字都能追到 SQLite 表/字段；本地模式可以现场重跑。

```bash
python3 demo/server.py                  # http://127.0.0.1:8000/
make demo-build                         # 生成静态回放 demo/public/runs.json
make demo-test                          # Demo 契约测试
```

静态回放只发布 `demo/public/`，不需要后端。详见 [demo/README.md](demo/README.md)。

## 复现（一条命令，全部离线）

```bash
cd agent-platform
make help           # 列出所有命令 + 自动发现的模块清单
make deps           # 依赖可用性自检（缺 docker/ollama 只影响对应模块）
make smoke-offline  # CI 同款：只跑不依赖外部服务的模块（11 个里的 10 个）
make gates          # 上面 + 静态检查（编译 / 绝对路径 / 结果契约）+ 证据扫描
make smoke          # 含需要 docker 的 M6，本地全量
```

模块**不写死**在 Makefile 里——用 `$(wildcard src/m[0-9]*.py)` 发现，
新增 `src/m9_*.py` 后自动纳入。`tools/deps_check.py` 可单独跑。

想逐个手跑（结果与上面等价）：

```bash
python3 src/m1_gateway.py --smoke        # 幂等/记账/降级/熔断/模型解析/静默失败
python3 src/m2_observability.py --smoke   # 合成 40 请求，验证指标口径
python3 src/m3_context_compact.py --smoke # 5 种压缩策略 + 负例基准
python3 src/m3b_llm_summary.py --smoke    # 真 LLM 摘要 vs 占位拼接（离线假上游）
python3 src/m4_tool_sandbox.py --smoke    # 白名单/审计/重试/死信
python3 src/m4b_os_sandbox.py --smoke     # 真 docker 隔离，8 条攻击断言
python3 src/m5_resilience.py --smoke      # 退避形状/熔断状态机/降级
python3 src/m6_etl.py --smoke             # 契约/漂移/指纹/确定性/慢查询/质量
python3 src/m7_e2e.py --smoke             # 六模块端到端，10 张工单
python3 src/m8_scale.py --smoke           # 并发/索引规模/云成本推演
```

补充说明：
- `m4b` 与 `m6` 需要本机有 `python:3.12-slim` 镜像 / `aihot-db-1` 容器
  （PostgreSQL）。缺失时 m4b 会记 **SKIPPED 而不是假 PASS**。
- `m1 --live` / `m3b --live` 需要 ollama 与可用显存；本机显存被别的训练
  任务占满时 llama-server 会被 SIGKILL，**这不是本仓缺陷**，见下文环境记录。

M1 的 live 模式要单独跑，且每次用干净 db（复用旧库会让幂等键命中历史，
把首请求显示成 cached——这是本次真实踩到的坑）：

```bash
python3 src/m1_gateway.py --live    # 需要 ollama serve 在 127.0.0.1:11434
```

## 核心模块各自证明了什么

### M1 网关：`src/aplat/gateway.py`

`LLMGateway.chat(ChatRequest) -> ChatResponse`。四个企业刚需性质都有断言兜底：

- **幂等重放**：同 `idempotency_key` 命中时返回 `status=cached`、`total_tokens=0`、
  `cost_usd=0.0`、`replay_of` 指向首个 `request_id`。
- **熔断不白烧调用**：`CircuitBreaker` 判 open 时直接返回
  `status=degraded / http_status=503 / error_kind=circuit_open`，**不打上游**。
  （初版在 open 时把 primary 换成 fallback，而 cheap 路由两者同名 → 等于换个
  名字再失败一次，白烧一次上游调用。smoke 断言抓到了这个 bug。）
- **超时降级**：timeout 时用 `fallback` 模型重试；仍失败落 `error / 502`。
- **记账**：每次调用写 `gateway_request` 一行，含 prompt/completion/total tokens、
  单调时钟延迟、成本、`cache_hit`、`replay_of`。本机模型单价记为 0（这是口径，
  不是记账失败）；外部模型价（gpt-4o-mini 等）只用于对照。

**两个只有真跑才会暴露的 bug**（smoke 全绿时它们都藏着，靠 `--live` + 真服务抓出来）：

1. **client 指定的 model 被无声丢弃**。`chat()` 原来直接取 `route_cfg["primary"]`，
   于是 OpenAI 兼容端点上 `curl` 请求 `llama3.2:3b`、服务实际用了 `qwen3:8b`，
   调用方毫不知情。修法是 `_resolve_model()`：显式点名**已知模型**时尊重请求，
   未知模型名才回落到 route primary，并把决断理由写进 span 的 `model_note`。
2. **思考型模型的空回答被记成成功**。qwen3 系把 token 预算烧在 `thinking` 通道，
   `message.content` 是空串、`done_reason=length`，而 HTTP 仍是 200。原网关记
   `status=ok`，M2 的错误率因此漏掉整类失败。修法：`ollama_client` 透传
   `thinking`/`done_reason`，网关检测空 content → `status="empty"`、
   `error_kind="empty_content"`，span 记 `error`，M2 的失败率把 `empty` 计入。

现场复现（`results/m1_gateway_live.json` 的 `thinking_model_probe`）：
显式点名 `qwen3:8b`、`num_predict=64` → `status=empty`、`empty_content=true`、
`done_reason=length`、`thinking≈102 tokens`、用时 **36.35s**。

附带一个 stdlib `http.server` 版 OpenAI 兼容 `/chat/completions` 与 `/health`
（`python3 -m aplat.gateway --serve`），不引 fastapi 以保持零依赖。

### M2 可观测台：`src/m2_observability.py`

从 `gateway_request` + `span` 派生指标。四条口径断言：

- span 覆盖：每条 trace 至少 cache + upstream 两个 span。
- 口径自洽：`upstream` span 的 `duration_ms` 不超过请求总延迟。
- 缓存记账：`cache_hit=1` 的行 `total_tokens` 必须为 0。
- 错误率与注入的失败率自洽。

延迟一律用 `monotonic_ms()` 差值，不用墙钟差（会被 NTP/手改时间污染）。
`span.start_mono` 是**相对单次 chat() 起点的偏移**，所以跨请求只能比
`duration_ms`，不能比绝对值——这一点写进了 `results` 的 `_口径` 字段。

### M3 上下文压缩 + 会话记忆：`src/aplat/memory.py`

五种策略在同一 12 轮会话上对比：`raw` / `naive_sliding` / `sliding` /
`summarize` / `tag_filter`。

关键结论是**没有哪个策略同时最优**：

| 策略 | token 降幅 | 关键约束保留 |
|---|---|---|
| naive_sliding | 69% | **1/2（丢了）** |
| sliding（带 pinned） | 60% | 2/2 |
| summarize | 21% | 2/2 |
| tag_filter | 44% | 2/2 |

`naive_sliding` 是**有意保留的负例基准**：只看最近 k 轮，第 5 轮的关键约束
（"必须过滤教辅类"）必被截掉。它的存在证明 `pinned` 机制不是装饰。
默认策略取 `sliding`：多花 24 tokens 换回 1 条关键约束。

### M4 工具沙箱 + 重试 + 审计：`src/aplat/sandbox.py`

三条同时成立才有企业价值：白名单外调用必须被拒且不进执行路径、每次调用
（含被拒）在 `tool_audit` 留 append-only 行、可重试错误真的重试到底。

审计粒度是**一次尝试一行**：4 次白名单判定 + 1 次合法执行 + flaky 2 错 1 对
+ fatal 1 错 + 死信 5 错 = 14 行，逐行可核对。越权调用的 `attempt` 恒为 1。

### M5 韧性层：`src/aplat/resilience.py`

`Retrier`（指数退避 + full jitter，可注入 sleep）、`CircuitBreaker`
（closed/open/half_open，**half_open 只放一个探测请求**防惊群）、
`Degrader`（降级链全空时显式抛错，不静默返回空）。

`CircuitBreaker` 用 `provider_health` 表持久化状态，另有 `probe_sent` 列
记录本窗口是否已放过探测。进程重启后 `probe_sent` 归零——一个已知且被接受
的折中：宁可多放一个探测，也不永久卡死。

### M6 数据管道 + schema 漂移门禁：`src/m6_etl.py`

PostgreSQL → SQLite 单向同步。`--drift` 会真往上游 `ADD COLUMN` 再 `DROP`，
用来证明门禁**会**拒绝同步（此时 D3/D4/D5 显式记 `SKIPPED`，不用空数据
伪造一个 PASS）。

慢查询用例：12k 行合成表，`customer_id` 查询从 `SCAN etl_orders` 变成
`SEARCH etl_orders USING INDEX ix_etl_cust (customer_id=?)`。

## 明确不做 / 已知边界

写在这里而不是藏在代码注释里，因为它们决定了这个仓能拿去说什么：

1. ~~**M4 沙箱不是 OS 级隔离**~~ → **已于 M4b 补上 OS 级隔离**
   （`src/aplat/os_sandbox.py` + `src/m4b_os_sandbox.py`，8 条攻击断言全 PASS）。
   剩余边界：不覆盖容器逃逸与内核漏洞（那需要 gVisor/Kata/seccomp 白名单）。
   原「进程内白名单」仍作为第一道防线保留，两层并用。
2. ~~**M3 的摘要不是 LLM 摘要**~~ → **M3b 补上了真摘要路径**（`--live` 走本机
   ollama，`--smoke` 走确定性假上游），并量出占位拼接的代价：**丢全部金标
   约束、且自身 224 tokens 比不压缩的 raw 215 还胖**。剩余边界：占位版本仍留在
   M3 里（负例基准），`--smoke` 的摘要器是假上游，不证明真模型质量。tag 匹配也是关键词命中，不是 embedding。
3. **M6 的 CSV 管道不做 NULL/二进制语义转换**。本仓数据无 NULL，这个限制是
   真的限制；生产管道应换 `psycopg2` 或 `COPY TO STDOUT BINARY`。
4. ~~**schema 指纹只覆盖列名 + 类型**~~ → **M6 D2b 补了 v2 指纹**（列的可空性、
   默认值、索引定义、约束定义）。对照实证：只加一个索引时 v1 指纹不变（漏报），
   v2 指纹改变（抓到），DROP 后回到原值。M6 现在用的是 v2。
5. **M2/M7 的 p95 不是 SLO**。n=40（合成）/n=3（live）时 p95 只是描述统计；
   并且 **M7 的墙钟计时跑一次一个样**（连跑三次 p50 = 47.0/45.0/39.0ms，
   p95 = 56.2/138.4/63.0ms、max = 58/210/68ms，同代码同种子）。
   所以本仓任何延迟数字都要当区间看，不能当承诺。
6. **token 数是估算值**（`src/aplat/tokenizer.py` 的 CJK/拉丁规则），不是 BPE。
7. **多副本部署下熔断是进程内的**。`provider_health` 不是分布式信号量。
8. **M6 依赖宿主机 docker 权限**，不是纯本地可跑。
9. **本机 LLM 推理受显存/内存制约**：`m1 --live` / `m3b --live` 在显存被
   其他训练任务占满时 llama-server 会被 SIGKILL（2026-10-12 实测：宿主机
   AIE 训练占 3454MiB GPU，剩余 2.2G 不足以加载 3B 模型，CPU 加载缓冲
   1.3G 也分配失败）。**这是环境限制而非代码缺陷**；离线 smoke 路径全部
   不依赖它。

## 仓库地图

```
src/aplat/common.py        SQLite DDL（9 表）+ 分位数 + bootstrap CI + 价格表
src/aplat/gateway.py       LLMGateway.chat + stdlib OpenAI 兼容 serve()
src/aplat/ollama_client.py urllib 直打 ollama /api/chat
src/aplat/tokenizer.py     CJK/拉丁混合 token 估算
src/aplat/resilience.py    CircuitBreaker / Retrier / Degrader
src/aplat/memory.py        会话记忆（tag 索引 + pinned 保护）
src/aplat/sandbox.py       工具白名单 + 参数校验 + 审计链 + 死信
src/aplat/os_sandbox.py       DockerSandbox：把代码关进受限容器执行（M4b）
src/m1_gateway.py          M1 入口（--smoke / --live）
src/m3b_llm_summary.py     M3b 入口：真 LLM 摘要 vs 占位
src/m4b_os_sandbox.py      M4b 入口：8 条 OS 隔离攻击断言
src/m7_e2e.py              M7 入口：端到端工单链路
src/m8_scale.py            M8 入口：并发/索引/成本
src/m2_observability.py    M2 入口
src/m3_context_compact.py  M3 入口
src/m4_tool_sandbox.py     M4 入口
src/m5_resilience.py       M5 入口
src/m6_etl.py              M6 入口（--drift 制造真漂移）
results/*.json             每个模块的 findings + refutes
Makefile                   help/smoke/smoke-offline/gates/scan/deps/clean（模块自动发现）
tools/evidence_scan.py     跨仓「无证据数字」扫描（--selftest 可自证检测器活着）
tools/deps_check.py        依赖可用性只读探测，不安装不联网
docs/engineering.md        门禁为什么长这样 / 口径 / 边界 / 陌生人复现顺序
.github/workflows/gates.yml CI 只跑离线档（理由见文件头注释与 engineering.md 第 3 节）
```

License: MIT。
