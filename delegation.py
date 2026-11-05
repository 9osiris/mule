"""fanout to delegate callables in parallel, in order."""
import inspect
from concurrent.futures import ThreadPoolExecutor


def _call(fn, task):
    # call with the task when the callable takes an argument,
    # otherwise call it with no arguments
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(task)
    if len(params) == 0:
        return fn()
    return fn(task)


def _one(pair, task):
    # one delegate entry, failures are reported, never raised
    if isinstance(pair, dict):
        name = pair.get("name")
        fn = pair.get("fn") or pair.get("callable")
    else:
        name, fn = pair
    try:
        return {"name": name, "ok": True, "output": _call(fn, task)}
    except Exception as e:
        return {"name": name, "ok": False, "output": "error: %s" % e}


def fanout(task, subagents):
    # run N delegate callables in parallel threads and return
    # [{"name", "ok", "output"}] in the input order
    items = list(subagents or [])
    if not items:
        return []
    workers = max(1, len(items))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda pair: _one(pair, task), items))
