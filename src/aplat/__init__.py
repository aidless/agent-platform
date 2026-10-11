"""
Enterprise Agent Platform — 本地可复现的六个企业级能力模块。

设计原则（硬约束，违反任何一条都算本轮失败）：
1. **零 API key**：模型推理走本机 ollama（llama3.2:3b / qwen3:8b）；没有网络调用。
2. **数字全部落盘**：每个模块产出 results/*.json，命令与结论写进 docs/。
3. **拒绝拍脑袋**：任何"这样更好"的说法必须附同一输入下的前后对比数字。
4. **不伪造规模**：本机 6G 显存 + SQLite，跑不了生产规模，报告里必须写明口径。

模块地图：
  M1 gateway   LLM 网关：OpenAI 兼容 /chat/completions、路由、幂等、成本核算、熔断降级
  M2 observe   可观测性：span 落盘、p50/p95/p99、SLO 烧毁率、trace 瀑布、成本归因
  M3 memory    上下文压缩 + 会话记忆：tag 召回、compaction、token 预算、badcase
  M4 tools     工具沙箱：allowlist、隔离执行、重试、死信队列、审计链
  M5 resilience 韧性层：故障注入、退避、熔断、降级、SLO 判定
  M6 dataops   数据管道：postgres→SQLite 抽取、schema drift、质量门禁、慢查询优化

所有模块共用 src/platform/*.py，见同目录模块 docstring。
"""

__version__ = "0.1.0"
LAB = "agent-platform"
