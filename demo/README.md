# M7 工单链路 Demo

[**Live demo**](https://aidless.github.io/agent-platform/) · 本地回放与实时重跑见下。

**一句话**：一条客服工单穿过网关、订单库、工具沙箱、记忆压缩、LLM 和可观测层；
页面上的每个数字都来自一次确定性离线运行，本地模式可以现场重跑。

这个目录只新增 Demo 层，不复制 M7 的业务逻辑。它直接 import
`src/m7_e2e.py` 里的 `TICKETS`、`ScriptedUpstream`、`TicketPipeline`、
`summarize`、`REFUTES` 和 `ensure_order_db`，跑完后从 SQLite 读回
`gateway_request`、`tool_audit`、`memory_item` 的真实字段。

## 30 秒开始

```bash
cd agent-platform
python3 demo/server.py                 # http://127.0.0.1:8000/
```

本地模式会先跑一遍 M7 离线链路，然后提供：

- `GET /api/health`：服务状态
- `GET /api/runs`：当前 payload
- `POST /api/run`：重新执行整批工单
- `GET /runs.json`：与静态站同构的 payload
- `GET /config.json`：`{"mode": "live"}`

静态回放不需要后端：

```bash
python3 demo/build_static.py           # 写 demo/public/runs.json
python3 -m http.server -d demo/public   # 任意静态服务器
```

构建脚本会把当前 `results/m7_e2e.json` 与本次运行做 36 项对账；
对不上会以非零退出码结束，不会静默生成一份“看起来对”的页面。

## 测试

```bash
python3 -m unittest demo/test_demo.py -v
```

测试覆盖：

- payload 结构、三张关键路径（工具被拒、静默空回答、降级 fallback）
- 两次运行的确定性（延迟字段除外）
- 与 `results/m7_e2e.json` 的逐项对账
- 已提交的 `runs.json` 与当前代码一致
- UI 不硬编码指标（故意注入 `9/10` 会让扫描器红）
- 无密钥、无绝对路径、离线无外呼
- 本地 HTTP API 的 contract

## 页面怎么读

1. **顶部证据卡**：端到端成功、上下文 token 节省、pinned 约束、工具被拒、静默空回答。
2. **左侧工单**：T02 是 degraded fallback，T04 是静默空回答，T05 是工具被拒，T09 是查无此单。
3. **中间时间线**：每一步都有状态、数值和来源。点 T05 看白名单真的拦住了 `issue_refund`；点 T04 看 HTTP 200 但回答为空如何被分类。
4. **右侧证据**：每个数值对应的 SQLite 表/字段；点“查看本条原始 JSON”可以看到完整 payload。
5. **底部**：M7 自己声明的 `refutes`，说明这个 Demo 不能证明什么。

## 三分钟面试讲法

1. 先讲问题：单模块 smoke 全绿，不代表端到端能跑；工具被拒、空回答、超时在单点指标里都像正常。
2. 点 T05：模型尝试 `issue_refund`，白名单拒绝；端到端仍要回答，所以出现 `answered_without_tool_data`。
3. 点 T04：HTTP 200 + 空 content 被记成 `silent_empty_answer`，不是成功。
4. 点 T02：primary 超时后走 fallback，结果标记 `degraded`。
5. 点摘要卡：压缩 1181 → 620 tokens，pinned 约束 10/10 保留。
6. 打开原始 JSON：每个数字都能追到 `gateway_request` / `tool_audit` / `memory_item`。

## 真实与模拟

| 类别 | 说明 |
|---|---|
| 真实 | M7 链路代码、SQLite 订单库（合成数据）、工具白名单、记忆压缩、网关记账、脚本化失败注入 |
| 模拟 | LLM 回答由 `ScriptedUpstream` 生成；失败率由脚本按调用次序注入；工单是手写的；延迟是观测值，不是 SLO |

## 文件

```text
demo/
├── pipeline_adapter.py   # 跑 M7、读 SQLite、生成 UI payload
├── build_static.py       # 生成 public/runs.json
├── server.py             # stdlib HTTP，静态页 + /api/run
├── public/
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   ├── config.json       # 静态模式
│   └── runs.json         # 提交进仓库的构建产物
├── test_demo.py
└── README.md
```

## 边界

- `results/` 是生成物，不参与 `worktree_dirty` 判断；`runs.json` 自己也不参与。
- 静态页是回放，不是实时服务；页面会标“静态回放”并显示构建 commit。
- 公开部署只发布 `demo/public/`，不暴露本地服务。
- 这个 Demo 不证明真实客服流量、真实 LLM 可用率或 SLO；这些边界来自 M7 的 `refutes`，页面上直接展示。
