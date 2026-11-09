# tests/

门禁不在这个目录里。默认命令是：

```bash
make gates            # 与 CI 相同
make smoke-offline    # 只重跑 9 个离线模块
make scan             # 无证据数字扫描，不阻断
```

| 目标 | 作用 | 是否阻断 |
|---|---|---|
| `tools/check_drift.py` | README、`modules.json` 与已提交 `results/*.json` 一致 | 是 |
| `smoke-offline` | 9 个不需要 docker / ollama 的 `--smoke` | 是 |
| `demo/test_demo.py` | M7 回放与模块证据的契约测试 | 是 |
| `scan` | 扫描没有证据指针对着的数字 | 否 |

M4b 和 M6 不在默认门禁里。缺依赖时它们打印 `→ SKIPPED`，这个退出码不是 PASS。
