"""mega loop helpers: compaction, memory, personas, budgets, undo,
delegation, plan gate, capabilities. no api key needed."""
import datetime
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("ok  " if cond else "FAIL") + " " + name)


from compaction import compact_history, should_compact
from memory import load_memory, append_memory, memory_context
from personas import load_persona, list_personas
from budgets import approx_tokens, TokenBudget, extend_pricing
from budgets import plan_approval_gate, CAPABILITIES, capabilities_for
from budgets import unsupported_tools
from undo_stack import UndoStack
from delegation import fanout
from cost import PRICING, cost_for

# compaction: extractive summary, recent tail kept

msgs = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "add a login page"},
    {"role": "assistant", "content": "",
     "tool_calls": [{"id": "c1", "type": "function",
                     "function": {"name": "write_file",
                                  "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "c1",
     "content": "wrote login.py with the form"},
    {"role": "assistant", "content": "login page is done"},
]
summary, kept = compact_history(msgs, keep_recent=2)
check("compact keeps the original task in the summary",
      "add a login page" in summary)
check("compact keeps key tool outcomes in the summary",
      "wrote login.py" in summary)
check("compact keeps the final answer in the summary",
      "login page is done" in summary)
check("compact kept_messages is the last keep_recent messages",
      kept == msgs[-2:])
check("compact default keep_recent is 10",
      compact_history(msgs)[1] == msgs)
check("compact empty history gives empty summary and no kept",
      compact_history([]) == ("", []))
check("compact keep_recent 0 keeps nothing",
      compact_history(msgs, keep_recent=0)[1] == [])
check("compact with no tool results still has the task",
      "add a login page" in compact_history(msgs[:2])[0])
check("compact with no final answer still summarizes",
      "add a login page" in compact_history(msgs[:4])[0])

fat = [{"role": "user", "content": "x" * 500} for _ in range(10)]
check("should_compact true over budget",
      should_compact(fat, 100) is True)
check("should_compact false under budget",
      should_compact(fat, 100000) is False)
check("should_compact false on empty history",
      should_compact([], 10) is False)
check("should_compact exact budget is not over",
      should_compact([{"role": "user", "content": "hi"}],
                     100000) is False)

# memory: load, append, tail within budget

mroot = tempfile.mkdtemp()
check("load_memory absent file returns empty string",
      load_memory(mroot) == "")
check("memory_context absent file returns empty string",
      memory_context(mroot, 100) == "")
line = append_memory(mroot, "build with make test")
today = datetime.date.today().isoformat()
check("append_memory writes a dated fact line",
      today in line and "build with make test" in line)
check("load_memory reads back the fact",
      "build with make test" in load_memory(mroot))
append_memory(mroot, "uses python 3.12")
check("append_memory keeps earlier facts",
      "build with make test" in load_memory(mroot)
      and "uses python 3.12" in load_memory(mroot))
check("memory_context returns the tail within budget",
      memory_context(mroot, 1000) == load_memory(mroot))
tail = memory_context(mroot, 20)
check("memory_context tail fits the budget",
      len(tail) <= 20 and "uses python 3.12" in tail)
check("memory_context zero budget is empty",
      memory_context(mroot, 0) == "")

cap_root = tempfile.mkdtemp()
for i in range(50):
    append_memory(cap_root, "fact number %d" % i, max_chars=200)
capped = load_memory(cap_root)
check("append_memory keeps the file under the size cap",
      len(capped) <= 200)
check("append_memory cap drops the oldest lines first",
      "fact number 0" not in capped
      and "fact number 49" in capped)
check("memory file is created by append_memory",
      os.path.isfile(os.path.join(cap_root, "MEMORY.md")))

# personas: builtins, file path, unknown name

names = list_personas()
check("list_personas has at least 4 names", len(names) >= 4)
check("list_personas includes the four named personas",
      "careful reviewer" in names
      and "terse implementer" in names
      and "explainer" in names
      and "tester" in names)
check("load_persona careful reviewer returns its prompt",
      "reviewer" in load_persona("careful reviewer").lower())
check("load_persona terse implementer is distinct from explainer",
      load_persona("terse implementer")
      != load_persona("explainer"))
check("load_persona explainer mentions explaining",
      "explain" in load_persona("explainer").lower())
check("load_persona tester mentions tests",
      "test" in load_persona("tester").lower())
check("load_persona normalizes underscores and case",
      load_persona("Careful_Reviewer")
      == load_persona("careful reviewer"))
try:
    load_persona("no such persona")
    check("load_persona unknown name raises ValueError", False)
except ValueError:
    check("load_persona unknown name raises ValueError", True)

pfile = os.path.join(tempfile.mkdtemp(), "persona.md")
open(pfile, "w").write("custom persona text")
check("load_persona reads an existing file path",
      load_persona(pfile) == "custom persona text")

# budgets: approx tokens, TokenBudget, pricing

check("approx_tokens empty is zero", approx_tokens("") == 0)
check("approx_tokens none is zero", approx_tokens(None) == 0)
check("approx_tokens uses chars over 4",
      approx_tokens("a" * 100) == 25)
check("approx_tokens rounds up a partial chunk",
      approx_tokens("abcde") == 2)

budget = TokenBudget(100)
check("TokenBudget starts at zero",
      budget.total_tokens == 0 and budget.exceeded() is False)
budget.add(60, 30)
check("TokenBudget add updates totals",
      budget.prompt_tokens == 60 and budget.completion_tokens == 30
      and budget.total_tokens == 90)
check("TokenBudget not exceeded under the cap",
      budget.exceeded() is False)
check("TokenBudget summary names the totals",
      "90" in budget.summary() and "100" in budget.summary())
budget.add(10, 0)
check("TokenBudget exactly at the cap is not exceeded",
      budget.total_tokens == 100 and budget.exceeded() is False)
budget.add(0, 1)
check("TokenBudget exceeded past the cap",
      budget.exceeded() is True)
check("TokenBudget add tolerates None counts",
      TokenBudget(10).add(None, None) == 0)

extend_pricing("test-model-x", 1.0, 2.0)
check("extend_pricing adds a row to the pricing table",
      PRICING.get("test-model-x") == (1.0, 2.0))
check("extended pricing feeds cost math",
      abs(cost_for("test-model-x", 1_000_000, 1_000_000) - 3.0) < 1e-12)
del PRICING["test-model-x"]
check("extend_pricing row can be replaced",
      extend_pricing("gpt-4o-mini", 0.15, 0.60) == (0.15, 0.60))

# plan approval gate: pure logic, no prompting

check("plan gate approves when approve_fn returns True",
      plan_approval_gate("do x", lambda p: True) is True)
check("plan gate rejects when approve_fn returns False",
      plan_approval_gate("do x", lambda p: False) is False)
check("plan gate rejects a non-True return",
      plan_approval_gate("do x", lambda p: "y") is False)
check("plan gate passes the plan text to approve_fn",
      plan_approval_gate("the plan",
                         lambda p: p == "the plan") is True)
check("plan gate rejects when approve_fn raises",
      plan_approval_gate("do x", lambda p: 1 / 0) is False)
check("plan gate rejects a None approve_fn",
      plan_approval_gate("do x", None) is False)

# capabilities: table, lookup, unsupported tools

check("CAPABILITIES maps patterns to feature rows",
      "gpt-4o" in CAPABILITIES
      and CAPABILITIES["gpt-4o"]["vision"] is True)
caps = capabilities_for("gpt-4o-mini")
check("capabilities_for matches a model pattern",
      caps == {"vision": True, "json_schema": True,
               "streaming": True})
check("capabilities_for is case-insensitive",
      capabilities_for("GPT-4O")["vision"] is True)
check("capabilities_for defaults all-true for unknown models",
      capabilities_for("some-local-model")
      == {"vision": True, "json_schema": True, "streaming": True})
check("capabilities_for returns a copy, not the table row",
      capabilities_for("gpt-4o") is not CAPABILITIES["gpt-4o"])
check("capabilities_for knows a visionless model",
      capabilities_for("o3-mini")["vision"] is False)
check("unsupported_tools flags read_image without vision",
      unsupported_tools("o3-mini", ["read_file", "read_image"])
      == ["read_image"])
check("unsupported_tools keeps everything on a vision model",
      unsupported_tools("gpt-4o", ["read_file", "read_image"]) == [])
check("unsupported_tools defaults all-true for unknown models",
      unsupported_tools("some-local-model", ["read_image"]) == [])

# undo stack: multi-level, per path, capped

stack = UndoStack()
check("UndoStack starts empty", stack.depth() == 0)
stack.snapshot("a.py", "v1")
stack.snapshot("a.py", "v2")
stack.snapshot("b.py", "b1")
check("UndoStack depth counts snapshots", stack.depth() == 3)
check("UndoStack undo returns the most recent content",
      stack.undo("a.py") == "v2")
check("UndoStack undo again returns the earlier content",
      stack.undo("a.py") == "v1")
check("UndoStack undo is per path",
      stack.undo("b.py") == "b1")
check("UndoStack undo with nothing left returns None",
      stack.undo("a.py") is None)
check("UndoStack undo of an unknown path returns None",
      stack.undo("nope.py") is None)
check("UndoStack depth drops after undos", stack.depth() == 0)

small = UndoStack(max_size=3)
for i in range(5):
    small.snapshot("f.py", "v%d" % i)
check("UndoStack caps at the configured size",
      small.depth() == 3)
check("UndoStack cap drops the oldest snapshot",
      small.undo("f.py") == "v4"
      and small.undo("f.py") == "v3"
      and small.undo("f.py") == "v2"
      and small.undo("f.py") is None)
default_stack = UndoStack()
check("UndoStack default cap is 20",
      default_stack.max_size == 20)

# delegation: parallel fanout, order kept, failures isolated

def fast(task):
    return "fast: " + task


def slow(task):
    time.sleep(0.05)
    return "slow: " + task


def boom(task):
    raise RuntimeError("kaboom")


results = fanout("build it", [("fast", fast), ("slow", slow),
                              ("boom", boom)])
check("fanout returns one entry per delegate",
      len(results) == 3)
check("fanout keeps the input order",
      [r["name"] for r in results] == ["fast", "slow", "boom"])
check("fanout successful entries report ok",
      results[0]["ok"] is True
      and results[0]["output"] == "fast: build it")
check("fanout slow delegate still completes",
      results[1]["ok"] is True
      and results[1]["output"] == "slow: build it")
check("fanout failing delegate reports ok False",
      results[2]["ok"] is False)
check("fanout failing delegate does not kill the others",
      results[0]["ok"] is True and results[1]["ok"] is True)
check("fanout failing entry carries the error",
      "kaboom" in str(results[2]["output"]))
check("fanout empty list returns empty list",
      fanout("x", []) == [])
check("fanout supports no-arg callables",
      fanout("x", [("plain", lambda: "no args")])[0]["output"]
      == "no args")

print()
print("%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
