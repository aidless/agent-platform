# tests/

这个目录**有意留空**——本仓库不跑 pytest / unittest 单元测试。

## 那怎么验证？

门禁在 `Makefile` 里，不是在这个目录里：

```bash
make smoke-offline    # 逐个模块跑 --smoke 冒烟（10 个模块，各带超时）
make gates           # smoke-offline + compileall + check_contract + scan
make scan            # 跨仓"无证据数字"扫描（审计用，不阻断）
```

| 目标 | 作用 | 是否阻断 |
|---|---|---|
| `smoke-offline` | 遍历 `src/` 下模块，逐个以 `--smoke` 运行，超时 600s | 是 |
| `gates` | 上述冒烟 + `compileall` 静态编译 + `tools/check_contract.py` 契约检查 + `scan` | 是 |
| `scan` | 扫描仓库内"没有证据支撑的数字"（审计用途） | 否 |

## 为什么不用 tests/ 放 pytest？

每个模块自带 `--smoke` 入口，冒烟即验证。再包一层 pytest 只是把同一件事写两遍。
真需要单元测试时，测试文件放在这里，命名 `test_*.py`。

## 相关

- 契约检查逻辑在 `tools/check_contract.py`，支持 `--selftest` 自证
- 冒烟不覆盖需要 Docker 的模块，见 `Makefile` 的 `NEEDS_DOCKER`
