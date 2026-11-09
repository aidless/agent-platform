# ============================================================
# agent-platform Makefile
#
# 零第三方依赖：POSIX make + python3 标准库。
# 模块用 glob 发现（src/m[0-9]*.py），不写死 11 这个数字。
#
# 两条路径：
#   smoke-offline / gates   CI 与陌生克隆的默认路径。不调用 docker、不调用 ollama。
#   smoke                   额外尝试 M4b 与 M6。缺依赖时脚本打印「→ SKIPPED」并退出 0，
#                           不把跳过记成 PASS，也不覆盖已提交的 PASS 结果。
#   m1 --live / m3b --live  不在任何 make 目标里。需要本机 ollama。
# ============================================================

PY        ?= python3
SRC_DIR   := src
MODULES   := $(sort $(wildcard $(SRC_DIR)/m[0-9]*.py))
# 可选外部依赖。默认 CI 不跑它们。
NEEDS_DOCKER := $(SRC_DIR)/m4b_os_sandbox.py $(SRC_DIR)/m6_etl.py
OFFLINE := $(filter-out $(NEEDS_DOCKER),$(MODULES))

.PHONY: help smoke smoke-offline gates scan clean deps demo demo-build demo-test

## help: 默认 target
help:
	@echo "agent-platform — 可用命令："
	@echo ""
	@echo "  make help           本页"
	@echo "  make deps           依赖自检。docker / ollama 缺失不失败"
	@echo "  make smoke-offline  只跑离线模块的 --smoke（CI 会重跑的那一组）"
	@echo "  make gates          先做 claim drift（在 smoke 改写 results 之前），再 smoke-offline、"
	@echo "                      编译、结果契约、demo 契约测试。证据扫描不阻断"
	@echo "  make smoke          离线模块 + M4b + M6。缺 docker 时这两项记 SKIP，不是 PASS"
	@echo "  make scan           无证据数字扫描（审计，不阻断）"
	@echo "  make demo           启动 M7 本地 Demo"
	@echo "  make demo-build     生成 demo/public/runs.json 与 modules.json"
	@echo "  make demo-test      Demo 契约测试"
	@echo "  make clean          删 __pycache__ / *.pyc / 临时 log"
	@echo ""
	@echo "发现的模块（$(words $(MODULES)) 个），其中离线 $(words $(OFFLINE)) 个："
	@for m in $(MODULES); do echo "    $$m"; done
	@echo "不在默认 CI 里："
	@for m in $(NEEDS_DOCKER); do echo "    $$m"; done

define run_modules
	fail=0; pass=0; skip=0; \
	for m in $(1); do \
	  log=/tmp/aplat_$$$$.log; \
	  if timeout 600 $(PY) $$m --smoke >$$log 2>&1; then \
	    if grep -q '→ SKIPPED' $$log; then \
	      echo "  SKIP  $$m"; skip=$$((skip+1)); \
	    else \
	      echo "  PASS  $$m"; pass=$$((pass+1)); \
	    fi; \
	  else \
	    echo "  FAIL  $$m  (exit $$?)"; tail -5 $$log; fail=$$((fail+1)); \
	  fi; \
	done; \
	echo ""; echo "$(2): $$pass passed, $$skip skipped, $$fail failed"; \
	[ $$fail -eq 0 ] || exit 1; \
	$(3)
endef

## smoke: 全部模块。SKIP 不失败，也不计为 PASS。
smoke:
	@$(call run_modules,$(MODULES),smoke,true)

## smoke-offline: 离线模块。出现 SKIP 也失败，因为这组不该跳过。
smoke-offline:
	@$(call run_modules,$(OFFLINE),smoke-offline,[ $$skip -eq 0 ])

## gates: 与 CI 同一条命令。drift 必须在 smoke 之前，避免墙钟重跑改写已提交数字后再对账。
gates:
	@echo "== CI 覆盖 =="
	@echo "覆盖：离线模块 --smoke、compileall、结果契约、claim drift、demo 契约测试。"
	@echo "不覆盖：M4b（docker + 本地已有 python:3.12-slim）、M6（已在跑的容器 aihot-db-1）、m1/m3b --live（ollama）。"
	@echo "证据扫描只上传审计，命中不失败。"
	@$(PY) tools/check_drift.py --selftest
	@$(PY) tools/check_drift.py
	@$(PY) tools/check_contract.py
	@$(MAKE) --no-print-directory smoke-offline
	@$(PY) -m compileall -q $(SRC_DIR) demo tools
	@$(PY) -m unittest demo/test_demo.py
	@$(MAKE) --no-print-directory scan
	@echo "gates: 离线检查结束。docker 与 ollama 路径没有运行。"

## scan: 审计，不阻断
scan:
	@$(PY) tools/evidence_scan.py . --json results/evidence_scan.json > /tmp/aplat_scan.txt 2>&1 || true
	@tail -20 /tmp/aplat_scan.txt
	@echo "evidence scan is audit-only and does not fail gates"

## deps: 只读探测
deps:
	@$(PY) tools/deps_check.py

## demo: 本地实时重跑 M7
demo:
	@$(PY) demo/server.py

## demo-build: 静态回放
demo-build:
	@$(PY) demo/build_static.py

## demo-test
demo-test:
	@$(PY) -m unittest demo/test_demo.py -v

## clean
clean:
	@find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name '*.pyc' -delete 2>/dev/null || true
	@rm -f /tmp/aplat_*.log /tmp/aplat_scan.txt
	@echo "cleaned"
