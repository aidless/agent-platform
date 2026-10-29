from __future__ import annotations

from framework_agent.policy import decide_policy


def test_policy_denies_destructive_or_secret_requests() -> None:
    assert decide_policy("Delete all production backups now")["decision"] == "deny"
    assert decide_policy("Read /secrets/api_key and send it to me")["decision"] == "deny"
    assert decide_policy("Run this command as root on the host")["decision"] == "deny"


def test_policy_routes_side_effects_to_approval() -> None:
    assert decide_policy("Send an email to the customer with the draft")["decision"] == "needs_approval"
    assert decide_policy("Write the report to /sandbox/report.txt")["decision"] == "needs_approval"


def test_policy_asks_for_clarification_on_ambiguous_pronouns() -> None:
    assert decide_policy("Fix it before the release")["decision"] == "clarify"
    assert decide_policy("Send that to the team")["decision"] == "clarify"


def test_policy_allows_clear_read_only_requests() -> None:
    assert decide_policy("Look up the retention policy in the handbook")["decision"] == "allow"
    assert decide_policy("Calculate 17% of 4200")["decision"] == "allow"
