PYTHON ?= python3
.PHONY: test support-smoke support-eval support-demo

test:
	PYTHONPATH=src $(PYTHON) -m pytest -q

support-smoke:
	PYTHONPATH=src $(PYTHON) -c "import tempfile; from pathlib import Path; from framework_agent.support.service_factory import build_default_service; d=tempfile.TemporaryDirectory(); s=build_default_service(Path(d.name)/'cases.db',Path('support_data/corpus-v1.jsonl')); r=s.start({'question':'How do I resume an interrupt using the same thread?','versions':{'langgraph':'0.5.1'},'logs':'interrupt paused'}); assert r.status=='completed' and r.citations; print(r.to_dict())"

support-eval:
	PYTHONPATH=src $(PYTHON) -m framework_agent.support.evaluate --output-dir support_evidence/offline-v1

support-demo:
	PYTHONPATH=src $(PYTHON) -m framework_agent.support.api
