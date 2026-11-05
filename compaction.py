"""extractive history compaction, no model call."""
import json


def _content(msg):
    # plain text of one message, empty when there is none
    raw = msg.get("content") if isinstance(msg, dict) else None
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return str(raw)


def _short(text, limit=200):
    # trim one long line to a readable length
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _chars(messages):
    # rough size estimate, same shape as agent.context_size
    try:
        return len(json.dumps(messages))
    except (TypeError, ValueError):
        total = 0
        for msg in messages or []:
            total += len(_content(msg)) + 20
        return total


def should_compact(messages, budget_chars):
    # true when the estimated history size exceeds the budget
    if not messages:
        return False
    if budget_chars is None:
        return False
    return _chars(messages) > budget_chars


def compact_history(messages, keep_recent=10):
    # extractive summary plus the recent tail, no model call.
    # returns (summary_str, kept_messages).
    if not messages:
        return "", []
    if keep_recent is None:
        keep_recent = 10
    try:
        keep_recent = int(keep_recent)
    except (TypeError, ValueError):
        keep_recent = 10
    if keep_recent <= 0:
        kept = []
    elif keep_recent >= len(messages):
        kept = list(messages)
    else:
        kept = list(messages[-keep_recent:])
    task = ""
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "user":
            text = _content(msg).strip()
            if text and not text.startswith("[earlier context summarized]"):
                task = text
                break
    outcomes = []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "tool":
            text = _content(msg).strip()
            if text:
                outcomes.append(_short(text))
    final = ""
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            text = _content(msg).strip()
            if text:
                final = text
                break
    lines = []
    if task:
        lines.append("task: " + _short(task, 500))
    if outcomes:
        lines.append("tool outcomes:")
        shown = outcomes[:10]
        for item in shown:
            lines.append("- " + item)
        if len(outcomes) > len(shown):
            lines.append("- ... and %d more tool results"
                         % (len(outcomes) - len(shown)))
    if final:
        lines.append("final answer: " + _short(final, 500))
    if not lines:
        lines.append("(no summarizable content)")
    return "\n".join(lines), kept
