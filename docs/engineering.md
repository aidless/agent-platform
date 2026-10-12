# 工程化说明（engineering.md）

> 这一章写的是「门禁为什么长这样」和「哪些数字经得起追问」。
> 不讲情怀，只讲命令、口径和边界。

## 1. 一条命令看全状态

```bash
cd agent-platform
make help          # 列出所有命令与发现的模块
make deps          # 依赖可用性自检（缺 docker/ollama 只影响对应模块）
make smoke-offline # CI 同款：只跑不依赖外部服务的模块
make gates         # smoke + 静态检查 + 证据扫描（本地全量）
make clean
```

模块**不写死**：`Makefile` 用 `$(wildcard src/m[0-9]*.py)` 发现模块，
新增 `src/m9_*.py` 后 `make` 自动纳入，不需要改 Makefile。

## 2. 阶段划分（决定你能不能跑 M6）

| 阶段 | 需要什么 | 能跑哪些 | 跑不了什么 |
|---|---|---|---|
| 离线 | python3 标准库 | `m1 m2 m3 m3b m4 m4b m5 m7 m8` | — |
| 需 docker | docker 分页能力 | + `m6`（真 PostgreSQL 17） | live |
| 需 ollama | ollama + ≥6G 可用显存 | + `m1 --live` / `m3b --live` | — |

**live 跑不了不是代码缺陷**：宿主机有 AIE 训练任务持续占 ~3.4G GPU，
本仓清空 cgroup 也无法加载模型（详见 README 第 4 节边界）。

## 3. CI 跑的是哪一档

`.github/workflows/gates.yml` 只跑 `make smoke-offline`。
理由：GitHub-hosted runner 没有 docker PostgreSQL、没有 ollama，
把需要它们的模块放进去会得到**永远红的构建**——那比没有门禁更糟。

证据扫描作为**审计步骤**上传，不设 `exit 1`：
它扫的是「声称的业绩数字是否都有证据」，命中数量多不代表代码错，
只代表这些位置需要人工核对。本地 `make gates` 会打印摘要。

## 4. 结果契约（results/*.json）

每个模块写出的 JSON 必须同时有 `findings`（说了什么）和 `refutes`（不说什么 /
承认什么）。缺任一字段，`make gates` 报 FAIL。
`refutes` 不是免责话，是把结论的外沿钉住，例如 `m8_scale.json` 里写明了
「1k/10k/100k 是合成数据，查询计划在倾斜分布下可能不同」。

## 5. 静态检查的三种口径

| 检查 | 命令（内联） | 拦什么 |
|---|---|---|
| 语法 | `python3 -m compileall -q src` | 提交即坏的代码 |
| 绝对路径 | `grep -rnE '(/home/\|/root/\|/data1)' src` | 把开发机路径带进仓库 |
| 本机 token | `grep -rn "ELEVEN\|KIMI\|sk-" src` | 泄漏密钥/模型名 |

`make gates` 用的是同样的三条，可直接复制粘贴进任意 CI。

## 6. 证据扫描器自己怎么验证

```bash
python3 tools/evidence_scan.py --selftest     # 应该输出 3/3 PASS
python3 tools/evidence_scan.py .              # 扫描本仓
python3 tools/evidence_scan.py ../enterprise-rag ../mini-agent-runtime-bench
```

`--selftest` 用 3 个合成样本验证检测器本身：含 `p95=42ms` 的样本、
含 `3.2×` 的样本、干净样本。**它只证明检测器活着，不证明仓库干净**。

已知能力边界（诚实清单）：
- 不分辨「% 提升」是真实测量还是拍脑袋——那需要人工看上下文；
- **会命中 markdown 表格**：本仓 README 的 M3 策略对比表里 `69%` / `60%`
  就被报了。这不是漏报也不是错报，是表里的数字确实没有测量命令跟上。
  我选择保留表格扫描而不是把表格排除掉——「数字有没有证据」在表格里同样成立。
- 目标目录不存在时记 `target_missing` 而不是静默返回空。
  （第一条实现是静默返回 0 命中，看着像「干净」，实测后改的。）
- 不扫图表里的数字，只扫文本行。

**它证明了什么、没证明什么**：`--selftest` 证明检测器活着；
扫描报告证明「这些位置需要人工核对」。
**一次 0 命中不等于仓库干净**——只等于这次没扫到无证据数字。

## 6b. 一条我自己在写 target 时抓到的分类 bug

`tools/deps_check.py` 初版把 `("docker", ...)` 同时放进了 CLI 检查表和
Python import 表。后果：`import docker`（Python SDK，本仓用不到）失败被报成
「docker 不可用」，而 `shutil.which('docker')` 明明找到了 `/usr/bin/docker`——
同一个名字在两处给出相反结论。

**根因**：M4b 走的是 `subprocess` 调 `docker` **命令行**，不是 SDK。
把两个不同的东西用同一个名字检查，就会得到自相矛盾的报告。
**修法**：CLI 与 Python 模块两张表分开，docker 只在 CLI 表出现一次。

这类 bug 不会让任何测试变红，只会让报告说谎——所以我把它记在这里，
而不是删掉了当没发生过。
- 不覆盖表格里的数字（只扫正文行）；
- 有误报：论文里讨论「把 p95 优化到 X」的方法论段落也会命中。

## 7. 复现顺序指南（陌生人视角）

```bash
git clone <repo> && cd agent-platform
make smoke-offline     # 10 个模块里 9 个能跑，约 3-5 分钟
# 想看 LLM 摘要（M3b）：需要能用的 ollama 与显存
# 想看 OS 隔离（M4b）/ 数据管道（M6）：需要 docker
```

`results/` 下的 JSON 是本仓跑出来的**实际输出**，不是示例；
`results/` 被 `.gitignore` 排除，所以 clone 后是空的，重跑才有——
这保证了任何人看到的数字都是自己机器上真实的。
