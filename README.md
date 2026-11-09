# agent-platform

解决的是：把网关、工具沙箱、记忆压缩和一条离线工单链路做成 11 个能单独重跑的教学模块；核心依赖只有 Python 3.10+ 标准库和 SQLite，数字以已提交的 `results/*.json` 为准。
不解决：生产接入、真实模型质量、SLO、外部用户，也不是一个做深的单一产品。`framework-agent/` 是另一次离线练习，不算这 11 个模块已经合成平台。
STATUS: WORK-IN-PROGRESS — not A yet。包版本 0.1.0，成熟度停在教学作品。缺口写在 [FAILURE_MODES.md](FAILURE_MODES.md)。

[![CI](https://github.com/aidless/agent-platform/actions/workflows/gates.yml/badge.svg)](https://github.com/aidless/agent-platform/actions/workflows/gates.yml)

这枚徽章只表示 [`.github/workflows/gates.yml`](.github/workflows/gates.yml) 的离线门禁通过。它不是覆盖率徽章，也不是 PyPI 或 star 计数。CI 重跑 9 个离线模块的 `--smoke`，外加编译、结果契约、claim drift 和 demo 契约测试。CI 不跑 M4b、M6，也不跑 `m1 --live` / `m3b --live`。

[**静态 Demo**](https://aidless.github.io/agent-platform/) 回放已提交的 `results/*.json` 与一次离线 M7 运行。页面上的 Docker / ollama 路径没有在公开站重跑。

## 已提交结果里能直接核对的数字

回放合计 **65 条 PASS、2 条预期失败、7 条说明、0 条未预期失败**。状态按每条 findings 末尾的 `→ PASS` / `→ FAIL` 解析；没有这个箭头的行是说明，不是通过。`demo/public/modules.json` 必须与 `results/*.json` 的 findings / refutes 逐字一致，`tools/check_drift.py` 会拦。

| 模块 | 已提交结论 | 证据 |
|---|---|---|
| M1 网关 smoke | 5 条 `→ PASS`，另有 1 条记账说明（没有 PASS 箭头） | `results/m1_gateway.json` |
| M1 live | 3 次 status=ok，另有 1 次 cached 重放。思考型探针 `latency_ms` 为 36350 | `results/m1_gateway_live.json` |
| M2 | 4 条口径 `→ PASS` | `results/m2_observability.json` |
| M3 | token 降幅：naive_sliding=69%、sliding=60%、summarize=21%、tag_filter=44%（最优 naive_sliding=69%）。同一条负例写成 2 条预期失败 | `results/m3_context_compact.json` |
| M3b smoke | raw 215 tokens，摘要器省 68%，占位摘要 224 tokens。摘要器是假上游 | `results/m3b_llm_summary.json` |
| M4 | 8 条 `→ PASS`。这一层仍是进程内白名单 | `results/m4_tool_sandbox.json` |
| M4b | 9 条 `→ PASS`（I0 对照、I1–I7、无残留）。需要本机已有镜像，CI 不重跑 | `results/m4b_os_sandbox.json` |
| M5 | 7 条 `→ PASS` | `results/m5_resilience.json` |
| M6 | `m6_etl.json` 7 条 PASS；drift 文件另有 3 条 PASS 和 3 条 `→ SKIPPED`。同步门禁仍是 v1，v2 没有接到拒同步判定 | `results/m6_etl.json`, `results/m6_etl_drift.json` |
| M7 | 1181 → 620，平均 48%，pinned 10 条，工具判定 `{'denied': 1, 'ok': 5}` | `results/m7_e2e.json` |
| M8 | 这一次 Q2 是三档加速 [5.48, 22.44, 42.01]。另三次墙钟写在 refute 里：3.83→7.51 与 38×–47×；10k 档没有记录过区间。月成本 $5.81/月 到 $49.03/月，只按 `m7_e2e.json` 的 token 外推，Q3 没有 `→ PASS` | `results/m8_scale.json` |
| M9 | 4 条 `→ PASS`。脚本化动作，不测模型能力 | `results/m9_exec_eval.json` |

## 两条运行路径

离线路径不需要 API key、Docker 或 ollama。克隆后在普通 Python 3.10+ 上跑：

```bash
make deps           # docker / ollama 缺失不会失败
make gates          # 与 CI 相同：先对账已提交结果，再重跑 9 个离线模块
```

`make gates` 在 smoke 之前跑 drift。smoke 会改写 `results/*.json` 里的墙钟字段；那些新数字不是上面表里的已提交证据，不要顺手提交，除非你打算更换冻结结果。

可选路径，默认 CI 不跑，缺依赖时打印 `→ SKIPPED` 并以 0 退出，不写成 PASS，也不覆盖已有结果文件：

```bash
make smoke          # 9 个离线模块 + M4b + M6；缺依赖的两项记 SKIP
python3 src/m4b_os_sandbox.py --smoke
# 需要本机已经有 docker，以及已经存在的镜像 python:3.12-slim。仓库不拉取镜像。
python3 src/m6_etl.py --smoke
python3 src/m6_etl.py --drift
# 需要一个已经在跑的容器 aihot-db-1（用户 aihot，库 aplat_etl）。仓库没有 compose，不会创建它。
python3 src/m1_gateway.py --live
python3 src/m3b_llm_summary.py --live
# 需要本机 ollama。显存不够时上游被杀掉，是环境限制，不是离线 smoke 的失败。
```

`make smoke-offline` 只跑那 9 个模块。其中出现 SKIP 会失败，因为这组不应该跳过。

## Demo

```bash
python3 demo/server.py     # http://127.0.0.1:8000/  会现场重跑离线 M7
make demo-build            # 写出 demo/public/runs.json 与 modules.json
make demo-test
```

静态页只发布 `demo/public/`。本地服务会把 M7 再跑一遍并和 `results/m7_e2e.json` 对账。模块区的「重跑」只是重读已提交 JSON，不是重跑 M4b 或 M6。详见 [demo/README.md](demo/README.md)。

## 各模块实际证明了什么

### M1 网关：`src/aplat/gateway.py`

`LLMGateway.chat` 在 `--smoke` 里用假上游检查四件事：同 idempotency key 的重放不再记 token；熔断打开时不打上游；超时会试 fallback，再失败则 `error / 502`；每次调用写一行 `gateway_request`。本机模型单价按 0 记，这是口径，不是记账失败。

`--live` 另有一份已提交记录，不在 CI 里重跑。其中 3 次 status=ok，思考型探针是 `qwen3:8b`、`latency_ms=36350`、`thinking_tokens=102`、`status=empty`。smoke 那一行里不再写第二套 live 秒数。

### M2 可观测台：`src/m2_observability.py`

从合成的 40 条请求派生 span。四条 `→ PASS` 检查 span 覆盖、upstream 时长不超过请求时长、缓存命中的 token 为 0、失败率把 `empty` 算进去。n=40 的 p95 是描述统计，不是 SLO。

### M3 上下文压缩：`src/aplat/memory.py`

同一段 12 轮合成会话上比较 `raw` / `naive_sliding` / `sliding` / `summarize` / `tag_filter`。已提交降幅是：token 降幅：naive_sliding=69%、sliding=60%、summarize=21%、tag_filter=44%（最优 naive_sliding=69%）。`naive_sliding` 丢掉第 5 轮约束，结果文件里写成 2 条预期失败。`summarize` 在这一模块仍是首句截断，不是 LLM 摘要。

### M3b 摘要对照：`src/m3b_llm_summary.py`

`--smoke` 的摘要器是确定性假上游：raw 215 tokens，假摘要上下文省 68%，占位拼接 224 tokens，而且丢了两个词面金标。这证明占位拼接的代价，不证明真模型摘要质量。样本是 10 轮。`--live` 才打 ollama，而且 CI 不跑。

### M4 工具沙箱：`src/aplat/sandbox.py`

8 条 `→ PASS`：白名单外不执行、审计行数对得上、可重试错误会重试、致命错误不重试、死信在耗尽后写入。拒绝 `rm_rf` / `net.fetch` 只是策略层。

### M4b 容器实验：`src/aplat/os_sandbox.py`

已提交的 9 条 PASS 来自一次真实 `docker run`：良性计算必须得到 42，然后检查断网、只读、非 root、CapEff 为 0、内存上限、pid 上限、超时，以及超时后没有残留容器。镜像固定为本地已有的 `python:3.12-slim`。没有镜像时命令打印 `→ SKIPPED`。这不覆盖容器逃逸、内核漏洞或 gVisor/Kata。

### M5 韧性层：`src/aplat/resilience.py`

7 条 `→ PASS` 覆盖无 jitter 的指数退避、full jitter 的上界、4xx 不熔断、5xx 打开熔断、half-open 只放一个探测、降级链、以及全空时显式报错。`probe_sent` 在进程重启后归零，这是写在 refute 里的折中。

### M6 数据管道：`src/m6_etl.py`

已提交运行使用容器 `aihot-db-1` 里的 PostgreSQL，经 `psql` 和 `\copy` 同步到 SQLite，不使用 `psycopg2`。D1–D5 加 D2b 是 7 条 PASS。D2b 证明 v2 指纹能看见「只加一个索引」，而 v1 看不见。同步是否拒绝，仍看 v1。所以 v2 没有接到拒同步判定。`--drift` 那份文件在没有新数据时把 D3/D4/D5 记成 SKIPPED，而不是用空表做成 PASS。

本仓库不创建 `aihot-db-1`。没有这个容器时 `--smoke` 跳过。

### M7 端到端：`src/m7_e2e.py`

10 张手写工单穿过网关、合成订单库、沙箱、记忆、脚本化回答和可观测记录。结果文件的 `chain` 列出 M1、M6 订单库、M4、M3、M2 五个名字；页面上的第六步是脚本化回答，不是第六个独立产品。已提交数字：1181 → 620（平均 48%），pinned 10 条，失败分类 1 个 `silent_empty_answer`，工具判定 `{'denied': 1, 'ok': 5}`。失败率是脚本注入的。

### M8 成本与规模：`src/m8_scale.py`

Q1 用 `sleep(20ms)` 假上游看网关会不会放大调用。Q2 在合成的 1k/10k/100k 行上检查计划从 SCAN 变成 USING INDEX。已提交的这一次是三档加速 [5.48, 22.44, 42.01]。refute 里的 3.83→7.51 与 38×–47× 是另外三次墙钟；10k 档没有记录过区间。不要把这些写成一个倍数。

Q3 把 `m7_e2e.json` 的 token 合计按公开价目外推，得到 $5.81/月 到 $49.03/月。`m1_gateway_live.json` 没有 `summary.tokens`，不进入这个合计。Q3 那一行没有 `→ PASS`。价目不含折扣，也不是本机账单。

### M9 执行式评测：`src/m9_exec_eval.py`

5 道手写题由 verify 退出码判对，另有一条故意写错的动作必须判 False。动作序列是手写的，测的是 harness、审计和判定是否自洽。求和用 bash 算术，不依赖 `bc`。本地 bash 没有套上 M4b。

## 明确不做

1. M4 不是 OS 隔离。M4b 在 Docker 可用时补了一组容器实验，仍不覆盖逃逸和内核漏洞。
2. M3 的摘要不是 LLM 摘要。M3b 的 `--smoke` 也不是；它只量出占位拼接会丢金标，并且 224 tokens 比 raw 215 更长。
3. M6 的 CSV 管道不做 NULL / 二进制转换。同步门禁用 v1 指纹。v2 没有接到拒同步判定。
4. M2 / M7 的 p95 不是 SLO。延迟每次跑都会变。
5. token 数是 `src/aplat/tokenizer.py` 的估算，不是 BPE。
6. 熔断状态在进程内。`provider_health` 不是分布式信号量。
7. 没有外部用户、没有生产流量、没有在 CI 里打真实模型分数。

## 仓库地图

```
src/aplat/                 标准库 + SQLite 的共用代码，版本 0.1.0
src/m1_gateway.py … m9_exec_eval.py
results/*.json             已提交的 findings + refutes。evidence_scan*.json 被 gitignore
Makefile                   deps / smoke-offline / gates / smoke
tools/check_drift.py       README、demo、results 的对账
tools/check_contract.py    findings 与 refutes 非空，且 src/ 无开发机绝对路径
tools/deps_check.py        只读探测。docker 与 ollama 都是可选
tools/evidence_scan.py     无证据数字的审计，不阻断
FAILURE_MODES.md           这些结论不支持什么
docs/engineering.md        门禁为什么只跑离线档
demo/                      M7 离线回放
framework-agent/           单独的离线练习，不计入上面 11 个模块的 PASS 数
.github/workflows/gates.yml
```

License: MIT。
