# PREREG — agent-platform 预注册/规格说明

> 本文件把 agent-platform 的真实工程假设固化为「可证伪的断言 + 评测方案 +
> 成功标准 + 明确边界」。它不声称任何**新**结果——每个模块的 `results/*.json`
> 已有 `findings`（PASS/FAIL 断言）与 `refutes`（本模块不能证明的事），
> 本文件只把这些**已在仓内**的断言契约显式预注册，并钉住「证明什么 / 不证明什么」。

## 1. 研究/工程问题

> 企业 LLM 平台不是单点服务，而是「网关 + 可观测 + 上下文压缩 + 工具沙箱 +
> 韧性 + 数据管道」六层协同。多数 demo 只证明其中一层能跑；本仓要证明：
> 这六层能各自闭环，且能串成一条真链路（M7 客服工单端到端，10 张工单）。

## 2. 可证伪假设（每个对应真实模块与断言）

- **H-M1（网关契约）**：`LLMGateway.chat` 满足幂等重放、熔断不白烧调用、
  超时降级、记账四性质。**判据**：`src/m1_gateway.py --smoke` 的 5 项断言 +
  1 项记账核对全部 PASS；`--live` 真打 ollama 3/3 成功。
- **H-M2（可观测口径）**：span 派生指标满足 span 覆盖、口径自洽、缓存记账、
  错误率自洽四条口径断言。**判据**：`src/m2_observability.py --smoke` 4 项断言 PASS。
- **H-M3（上下文压缩）**：五种压缩策略中，`sliding`（带 pinned）在 token 降幅
  与关键约束保留间取得平衡，`naive_sliding` 是有意保留的**负例基准**（丢约束）。
  **判据**：5 策略对比 + 1 条负例基准，见 `results/m3_context_compact.json`。
- **H-M4（工具沙箱）**：白名单外调用必须被拒且不进执行路径、每次调用在
  `tool_audit` 留 append-only 行、可重试错误真的重试到底。
  **判据**：`src/m4_tool_sandbox.py --smoke` 8 项断言 PASS，审计 14 行逐行可核对。
- **H-M4b（OS 隔离）**：代码被关进受限容器后，网络/只读/non-root/CapEff/
  内存/fork/超时/无残留 8 条攻击断言全 PASS，I0 为非空性对照。
  **判据**：`results/m4b_os_sandbox.json`。
- **H-M5（韧性层）**：`Retrier` 指数退避 + full jitter、`CircuitBreaker`
  half_open 只放一个探测、`Degrader` 降级链全空时显式抛错。
  **判据**：`src/m5_resilience.py --smoke` 7 项 PASS。
- **H-M6（数据管道）**：PostgreSQL→SQLite 单向同步满足契约/漂移/指纹覆盖度/
  确定性/慢查询/质量 7 项断言；`--drift` 真往上游 ADD/DROP COLUMN 证明门禁会拒绝。
  **判据**：`results/m6_etl.json`, `m6_etl_drift.json`。
- **H-M7（端到端）**：六模块串联跑通 10 张工单（压缩省 48%、pinned 10/10、
  失败分类 1 个）。**判据**：`results/m7_e2e.json`。
- **H-M8（成本与规模）**：索引把查询从 SCAN→USING INDEX，加速比随运行在
  3.7–7.5 / 17–21 / 38–47× 之间。**判据**：`results/m8_scale.json`（区间口径，
  不报单一倍数）。
- **H-M9（execution-based 评测）**：5 题由 verify 退出码判对 + 错误动作必须判
  False。**判据**：`results/m9_exec_eval.json`。

## 3. 评测方案

- 每个模块一个 `src/m*.py` 入口，`--smoke` 跑离线断言、`--live` 打真服务。
- `results/*.json` 落盘 `findings` + `refutes`；缺任一字段，`make gates` 报 FAIL。
- `Makefile` 用 `$(wildcard src/m[0-9]*.py)` 自动发现模块。
- `tools/evidence_scan.py` 跨仓扫「无证据数字」（`--selftest` 证明检测器活着）。
- CI（`.github/workflows/gates.yml`）只跑 `make smoke-offline`——GitHub runner
  无 docker/ollama，放进需要它们的模块会得到永远红的构建。

## 4. 成功标准

- 离线档 `make smoke-offline`（CI 同款）**10/10 模块 PASS**。
- demo 回放 **65 PASS / 2 预期失败 / 0 未预期失败**（11 个模块）。
- 每个模块的 `results/*.json` 同时含 `findings` 与 `refutes` 字段。
- `make gates`（smoke + 静态检查 + 证据扫描）全过。

## 5. 明确边界（本仓**不**证明什么，逐条对应 `refutes` / README「明确不做」）

1. **M2/M7 的 p95 不是 SLO**：n=40（合成）/n=3（live）时 p95 只是描述统计；
   M7 墙钟计时连跑三次 p50=47.0/45.0/39.0ms，同代码同种子，要当区间看。
2. **token 数是估算值**（CJK/拉丁规则），不是 BPE。
3. **多副本下熔断是进程内的**：`provider_health` 不是分布式信号量。
4. **M6 依赖宿主机 docker 权限**；CSV 管道不做 NULL/二进制语义转换。
5. **本机 LLM 推理受显存/内存制约**（AIE 训练占 3454MiB 时 3B 模型加载失败）——
   环境限制，非代码缺陷；离线 smoke 不依赖它。
6. **M4b 不覆盖容器逃逸与内核漏洞**（需 gVisor/Kata/seccomp 白名单）。
7. **M3 占位摘要仍是负例基准**；`--smoke` 摘要器是假上游，不证明真模型质量；
   tag 匹配是关键词命中，不是 embedding。
8. **证据扫描 0 命中 ≠ 仓库干净**——只等于这次没扫到无证据数字。
