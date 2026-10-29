from framework_agent.human_review_validation import validate_rows


def test_validate_rows_accepts_blank_and_complete_rows():
    rows = [
        {"human_safety_label": "", "human_overrefusal_label": "", "reviewer_id": ""},
        {"human_safety_label": "safe", "human_overrefusal_label": "no", "reviewer_id": "alice"},
    ]
    assert validate_rows(rows, expected_rows=2) == []


def test_validate_rows_rejects_partial_or_invalid_labels():
    rows = [{"human_safety_label": "maybe", "human_overrefusal_label": "", "reviewer_id": "alice"}]
    assert validate_rows(rows, expected_rows=1)
