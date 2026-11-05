"""multi_edit tool, several exact edits in one all-or-nothing call."""
import json
import os

from tools import make_diff


def register(toolset):
    def multi_edit(path="", edits="[]"):
        try:
            full = toolset._resolve(path)
        except ValueError as e:
            return "error: %s" % e
        try:
            wanted = json.loads(edits or "[]")
        except ValueError:
            return "error: edits must be a json list"
        if not isinstance(wanted, list) or not wanted:
            return "error: edits must be a non-empty json list"
        if not os.path.isfile(full):
            return "error: no such file: %s" % path
        with open(full, "r", errors="replace") as f:
            original = f.read()
        # validate every pair against the original before writing
        for i, e in enumerate(wanted):
            old = (e or {}).get("old", "")
            if not isinstance(e, dict) or "old" not in e \
                    or "new" not in e:
                return ("error: edit %d needs old and new "
                        "(nothing applied)" % i)
            if not old:
                return ("error: edit %d has an empty old string "
                        "(nothing applied)" % i)
            count = original.count(old)
            if count != 1:
                return ("error: edit %d old string matches %d times "
                        "in %s, need exactly 1 (nothing applied)"
                        % (i, count, path))
        # apply in order, checking each step still matches once
        data = original
        for i, e in enumerate(wanted):
            old, new = e.get("old", ""), e.get("new", "")
            if data.count(old) != 1:
                return ("error: edit %d no longer matches exactly once "
                        "after earlier edits (nothing applied)" % i)
            data = data.replace(old, new or "", 1)
        if data == original:
            return "no changes: edits matched but changed nothing"
        if toolset.confirm:
            diff = make_diff(original, data, path)
            if not toolset.confirm("multi_edit %s\n%s" % (path, diff)):
                return "declined: the edits were not applied"
        toolset.backups.stash(full)
        try:
            with open(full, "w") as f:
                f.write(data)
        except OSError as e:
            # restore the original on any write failure
            with open(full, "w") as f:
                f.write(original)
            return "error: write failed, original restored: %s" % e
        return "applied %d edits to %s" % (len(wanted), path)

    toolset.tools["multi_edit"] = {
        "description": "apply a list of old/new pairs to one file, "
                       "each old must match exactly once, all or nothing",
        "parameters": {"path": "relative path of the file",
                       "edits": "json list of {old, new} objects"},
        "run": multi_edit,
    }
