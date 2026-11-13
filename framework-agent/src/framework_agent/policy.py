from __future__ import annotations

import re

DENY_PATTERNS = (
    r"\bdelete all production backups\b",
    r"\bsend private customer data\b",
    r"/secrets/",
    r"\bprint the value\b",
    r"\brun\b.*\bas root\b",
    r"\bdisable audit logging\b",
    r"\btransfer funds\b",
    r"\bbypass approval\b",
    r"\bexfiltrate\b",
    r"\bdrop production database\b",
    r"\badmin token\b.*\bdelete\b",
)

APPROVAL_PATTERNS = (
    r"\bsend an email\b",
    r"\bwrite .* to /sandbox/",
    r"\bopen a ticket\b",
    r"\bsilence the production alert\b",
    r"\bupdate ticket\b",
    r"\binstall a package\b",
    r"\bchange the production .* configuration\b",
    r"\bpost a webhook\b",
)

AMBIGUOUS_RE = re.compile(
    r"\b(it|that|thing|other file|the other one|from earlier|right place|one instead)\b",
    re.IGNORECASE,
)


def decide_policy(user_request: str) -> dict[str, str]:
    text = " ".join(user_request.lower().split())
    for pattern in DENY_PATTERNS:
        if re.search(pattern, text):
            return {"decision": "deny", "reason": f"matched deny rule: {pattern}"}
    for pattern in APPROVAL_PATTERNS:
        if re.search(pattern, text):
            return {"decision": "needs_approval", "reason": f"matched approval rule: {pattern}"}
    if AMBIGUOUS_RE.search(text):
        return {"decision": "clarify", "reason": "request lacks a concrete target or action"}
    return {"decision": "allow", "reason": "clear read-only request with no high-risk side effect"}
