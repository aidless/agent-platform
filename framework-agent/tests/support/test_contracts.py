import pytest
from framework_agent.support.contracts import SupportCase, SupportResult


def test_support_case_normalizes_optional_fields_and_bounds_logs():
    case = SupportCase.from_dict({"question": "  Why does invoke loop?  ", "logs": "x" * 25000})
    assert case.question == "Why does invoke loop?"
    assert len(case.logs) == 20000
    assert case.versions == {}
    assert case.environment == {}


def test_support_case_rejects_empty_question():
    with pytest.raises(ValueError, match="question"):
        SupportCase.from_dict({"question": "  "})


def test_result_serializes_stable_contract():
    result = SupportResult(case_id="c1", status="needs_clarification", missing_information=["langgraph version"])
    payload = result.to_dict()
    assert payload["case_id"] == "c1"
    assert payload["citations"] == []
    assert payload["missing_information"] == ["langgraph version"]
