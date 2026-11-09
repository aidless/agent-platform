# 这些结论不支持什么

STATUS: WORK-IN-PROGRESS — not A yet。

这个仓库是 11 个可离线核对的教学模块，不是一个做深的单一产品。版本号停在 0.1.0。下面每条都是已提交结果或代码路径直接排除的结论。

## 离 A 还差什么

- 模块是并排的最小实验，没有一条产品级的运行、发布和回滚故事把它们收成一个系统。
- 没有外部用户，也没有生产部署可以引用。
- CI 不打真实模型，因此没有可以对外引用的 live 模型分数。
- `framework-agent/` 有自己的脚本化轨迹和一份待人工标注的安全对照。那些数字不属于这 11 个模块，也不能用来宣布本仓库已经是一个支持平台。

## CI 重跑了什么

`make gates` 与 `.github/workflows/gates.yml` 只重跑 9 个离线模块：

`m1_gateway.py`、`m2_observability.py`、`m3_context_compact.py`、`m3b_llm_summary.py`、`m4_tool_sandbox.py`、`m5_resilience.py`、`m7_e2e.py`、`m8_scale.py`、`m9_exec_eval.py`。

它们使用假上游或本地 SQLite。M9 另外调用本机 `bash`，求和不用 `bc`。通过只说明这次控制流和已写明的断言成立。

同一条命令还检查：结果 JSON 有非空 findings 和 refutes、README / `demo/public/modules.json` 与这些 JSON 一致、demo 契约测试、`src/` 能编译。证据扫描有命中也不失败。

## CI 没有重跑什么

- M4b。需要本机已经存在的镜像 `python:3.12-slim`。仓库不拉取镜像。没有镜像时命令打印 `→ SKIPPED`，不写新的 PASS 文件。已提交的 9 条 PASS 是某一次有 Docker 的运行，不是 CI 的新鲜结果。
- M6。需要宿主机上已经在跑的容器 `aihot-db-1`。仓库没有 compose 文件，不会创建这个容器。没有它时同样是 SKIPPED。
- `m1 --live` 和 `m3b --live`。需要本机 ollama 和足够显存。`results/m1_gateway_live.json` 是一份历史记录：3 次 status=ok，思考型探针 36350ms。

SKIPPED 不是 PASS。`make smoke` 会把这两项记成 SKIP，而不是成功。

## 数字不能怎么读

- 65 条 PASS、2 条预期失败、7 条说明、0 条未预期失败，是 findings 文本的分类计数，不是外部审计，也不是生产验收。
- 没有 `→ PASS` / `→ FAIL` 的行是说明。M8 的月成本就在这一类里。$5.81/月 到 $49.03/月 只按 `m7_e2e.json` 的 token 合计乘公开价目，未计折扣、缓存和电费。`m1_gateway_live.json` 没有 `summary.tokens`，不在这个合计里。
- M8 已提交的索引加速是 `[5.48, 22.44, 42.01]`。refute 里的 `3.83→7.51` 和 `38×–47×` 是另外三次墙钟。10k 档没有记录过区间。墙钟重跑会变，CI 里的新秒数不自动成为 README 的数字。
- M7 的 1181 → 620、48%、`{'denied': 1, 'ok': 5}` 来自 10 张手写工单和脚本注入的失败。它不是客服流量，也不是 SLO。
- M3b 的「省 68%」和「224 tokens」比较的是假摘要器与首句拼接。它不支持「真 LLM 摘要质量」这句话。对话是 10 轮，不是 6 轮。
- M3 的 69% / 60% / 21% / 44% 是一段 12 轮合成会话上的估算 token，不是 BPE，也不能外推。
- M6 的 D2b 说明 v2 指纹看得见索引变化。同步拒不拒绝仍由 v1（列名 + 类型）决定。v2 没有接到拒同步判定。
- M4 的白名单拒绝不是 OS 安全边界。M9 的 5 题通过不是模型能力分，而且动作没有放进 M4b。
- M2 和 M7 的 p95 是小样本描述统计。

## 以前写错过、现在以文件为准的地方

- smoke 结果里曾经写过一套和 live 文件对不上的思考型耗时。live 文件里的探针是 36350ms。README 只引用后者。
- M3b 的 refute 曾经写 6 轮，同一文件的 `dialogue_turns` 是 10。
- M8 的 `source` 曾经说月成本来自 live 与 M7 两份文件。`basis.sources` 里只有 `m7_e2e.json`。
- README 曾经把 M6 写成「现在用的是 v2」。门禁代码仍调用 v1 的 `fingerprint()`。
- 文档曾经说 `results/` 被 gitignore 排除、所以克隆后是空的。模块 JSON 是提交的；被忽略的是 `results/evidence_scan*.json`。
