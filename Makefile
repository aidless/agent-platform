# ============================================================
# agent-platform Makefile
#
# 设计原则与 README 一致：
#   - 零第三方依赖，只用 POSIX shell + python3 标准库
#   - 模块用 glob 发现（src/m[0-9]*.py），不写死清单
#     —— 新增 m9_*.py 不用改这里
#   - 区分 smoke（全部）与 smoke-offline（CI 用）
#
# 为什么 CI 只能跑离线档：
#   m6 需要 docker 里的 PostgreSQL（aihot-db-1）；m1 --live / m3b --live
#   需要本机 ollama 与可用显存。GitHub-hosted runner 三者都没有。
#   把需要它们的模块放进 CI 只会得到永远红的构建，等于没有门禁。
# ============================================================

PY        ?= python3
SRC_DIR   := src
MODULES   := $(sort $(wildcard $(SRC_DIR)/m[0-9]*.py))
# 需要真实外部依赖的模块：M6（docker+postgres）及其 drift 变体
NEEDS_DOCKER := $(SRC_DIR)/m6_etl.py

.PHONY: help smoke smoke-offline gates scan clean deps demo demo-build demo-test

## help: 默认 target，列出所有命令
help:
	@echo "agent-platform — 可用命令："
	@echo ""
	@echo "  make help           本页"
	@echo "  make smoke          # 逐个跑所有模块 --smoke，任一失败则退出码非 0"
	@echo "  make smoke-offline  # 只跑不依赖 docker 的模块（CI 用这个）"
	@echo "  make gates          # smoke + 静态检查（compileall / 契约+绝对路径 / 证据扫描）"
	@echo "  make scan           # 只跑跨仓无证据数字扫描"
	@echo "  make clean          # 删 __pycache__ / *.pyc / 临时 db"
	@echo "  make deps           # 打印依赖与可用性自检"
	@echo "  make demo           # 启动 M7 工单链路 Demo（本地实时）"
	@echo "  make demo-build     # 生成 demo/public/runs.json"
	@echo "  make demo-test      # 跑 Demo 契约测试"
	@echo ""
	@echo "发现的模块（$(words $(MODULES)) 个）："
	@for m in $(MODULES); do echo "    $$m"; done

## smoke: 逐个跑所有模块
smoke:
	@fail=0; pass=0; \
	for m in $(MODULES); do \
	  if timeout 600 $(PY) $$m --smoke >/tmp/aplat_$$$$.log 2>&1; then \
	    echo "  PASS  $$m"; pass=$$((pass+1)); \
	  else \
	    echo "  FAIL  $$m  (exit $$?)"; tail -5 /tmp/aplat_$$$$.log; fail=$$((fail+1)); \
	  fi; \
	done; \
	echo ""; echo "smoke: $$pass passed, $$fail failed"; \
	[ $$fail -eq 0 ] || exit 1

## smoke-offline: 排除需要 docker/网络的模块
smoke-offline:
	@fail=0; pass=0; \
	for m in $(filter-out $(NEEDS_DOCKER),$(MODULES)); do \
	  if timeout 600 $(PY) $$m --smoke >/tmp/aplat_off_$$$$.log 2>&1; then \
	    echo "  PASS  $$m"; pass=$$((pass+1)); \
	  else \
	    echo "  FAIL  $$m  (exit $$?)"; tail -5 /tmp/aplat_off_$$$$.log; fail=$$((fail+1)); \
	  fi; \
	done; \
	echo ""; echo "smoke-offline: $$pass passed, $$fail failed"; \
	[ $$fail -eq 0 ] || exit 1

## gates: smoke + 静态检查
##   检查逻辑放在 tools/ 下的独立脚本里，不写在 heredoc 里：
##   Makefile recipe 每行是独立 shell，heredoc 的缩进/结束标记容易被
##   Make 的 tab 规则吃掉（本次实测 heredoc 直接报
##   `make: import: 没有那个文件或目录`）。脚本能单独跑、能进 CI、
##   退出码语义干净，也能被 check_contract 的 --selftest 自证。
gates: smoke
	@echo ""
	@echo "== 静态检查 =="
	@$(PY) -m compileall -q $(SRC_DIR) && echo "  PASS  compileall"
	@$(PY) tools/check_contract.py
	@$(MAKE) --no-print-directory scan

## scan: 跨仓无证据数字扫描（审计，不阻断）
scan:
	@$(PY) tools/evidence_scan.py . \
	   --json results/evidence_scan.json 2>&1 | tail -20

## deps: 依赖与可用性自检
##   单独放 tools/deps_check.py 而不是 heredoc：Makefile 的 heredoc 会被
##   $(...) 与制表符规则折腾，出错难查；文件能单独跑、能进 CI、能有 --help
deps:
	@$(PY) tools/deps_check.py

## demo: 启动本地 Demo（静态页 + /api/run 实时重跑）
demo:
	@$(PY) demo/server.py

## demo-build: 生成静态回放 payload，并与 results/m7_e2e.json 对账
demo-build:
	@$(PY) demo/build_static.py

## demo-test: Demo 契约测试（stdlib only）
demo-test:
	@$(PY) -m unittest demo/test_demo.py -v

## clean
clean:
	@find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name '*.pyc' -delete 2>/dev/null || true
	@rm -f /tmp/aplat_*.log
	@echo "cleaned"
