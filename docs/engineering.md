# 工程化说明（engineering.md）

> 这一章写的是「门禁为什么长这样」和「哪些数字经得起追问」。
> 不讲情怀，只讲命令、口径和边界。

## 1. 一条命令看全状态

```bash
cd agent-platform
make help          # 列出所有命令与发现的模块
make deps          # 依赖可用性自检（缺 docker/ollama 只影响对应模块）
make smoke-offline # 只重跑 9 个离线模块；这是 gates 的一部分，不是整条 CI
make gates         # 与 CI 相同：drift + 离线 smoke + 契约 + demo 测试；证据扫描不阻断
make clean
```

模块**不写死**：`Makefile` 用 `$(wildcard src/m[0-9]*.py)` 发现模块，
新增 `src/m9_*.py` 后 `make` 自动纳入，不需要改 Makefile。

## 2. 阶段划分

| 阶段 | 需要什么 | 跑哪些 | 不证明什么 |
|---|---|---|---|
| 离线，也是 CI | Python 3.10+ 标准库 | `m1 m2 m3 m3b m4 m5 m7 m8 m9` 的 `--smoke`，共 9 个 | M4b、M6、任何 live 分数 |
| 可选 docker | 本机已有 `python:3.12-slim`；另有一个已经在跑的容器 `aihot-db-1` | `m4b`，以及 `m6` / `m6 --drift` | 仓库不拉取镜像，也没有 compose |
| 可选 ollama | 本机 ollama 与足够显存 | `m1 --live`、`m3b --live` | 显存不够时上游被杀，是环境限制 |

`make smoke` 在缺 docker 时把 M4b / M6 记成 SKIP，不是 PASS。

## 3. CI 跑的是哪一档

`.github/workflows/gates.yml` 只跑 `make gates`。
这条命令先对账已提交的 `results/*.json`，再跑 9 个离线模块、编译、
结果契约和 demo 契约测试。

GitHub-hosted runner 没有 `aihot-db-1`，也没有 ollama，仓库自己也不拉取
`python:3.12-slim`。把这三样放进默认门禁会得到一台机器永远红、另一台
机器靠缓存变绿的构建。所以它们留在可选路径。

证据扫描作为**审计步骤**上传，不设 `exit 1`：
它扫的是「声称的业绩数字是否都有证据」，命中数量多不代表代码错，
只代表这些位置需要人工核对。`make gates` 会打印摘要。

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
- 表格里的数字同样会命中；上面第 6 节已经说过，这里不再写成「不扫表格」。
- 有误报：讨论「把 p95 优化到 X」的方法论段落也会命中。

## 7. 复现顺序指南（陌生人视角）

```bash
git clone <repo> && cd agent-platform
make gates             # 对账已提交结果，并重跑 9 个离线模块
# M4b：本机已有 python:3.12-slim 时再跑 src/m4b_os_sandbox.py --smoke
# M6：已有容器 aihot-db-1 时再跑 src/m6_etl.py --smoke
# live：有 ollama 时再跑 m1 --live / m3b --live
```

`results/*.json`（除 `evidence_scan*.json`）是提交进仓库的冻结证据，不是示例。
克隆之后可以直接读。`make smoke-offline` 会重算离线模块，并改写墙钟字段；
README 表里的加速比和 live 延迟以提交文件为准，不以你机器上的新秒数为准。
