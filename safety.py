"""write guards and path jail helpers for mule."""
import os
import re

# duplicated from tools.py on purpose: importing tools pulls in
# agent, and tools will import this module, so an import here
# would be a circular import. keep the two lists in sync.
SECRET_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "aws access key"),
    (r"sk-[A-Za-z0-9]{20,}", "api key"),
    (r"ghp_[A-Za-z0-9]{36}", "github token"),
    (r"github_pat_[A-Za-z0-9_]+", "github token"),
    (r"xox[bap]-[A-Za-z0-9-]+", "slack token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"(?i)\b(password|passwd|secret|api[_-]?key)\b\s*[:=]\s*\S{3,}",
     "hardcoded credential"),
]


def contains_secret(text):
    # the label of the first secret-shaped match, or None
    if not text:
        return None
    for pattern, label in SECRET_PATTERNS:
        if re.search(pattern, text):
            return label
    return None


def check_write_content(content):
    # (ok, reason) pair: blocked when secret-shaped values are present
    kind = contains_secret(content or "")
    if kind:
        return False, "content looks like it contains a %s" % kind
    return True, ""


def _realpath_missing(path):
    # realpath of a path that may not exist yet: resolve the
    # nearest existing parent, then reattach the missing tail
    target = os.path.abspath(path)
    missing = []
    while not os.path.lexists(target):
        parent, tail = os.path.split(target)
        if parent == target:
            break
        missing.append(tail)
        target = parent
    resolved = os.path.realpath(target)
    for tail in reversed(missing):
        resolved = os.path.join(resolved, tail)
    return os.path.normpath(resolved)


def resolve_within(path, root):
    # absolute path after symlink resolution, only when it stays
    # inside the realpath of root. raises ValueError otherwise.
    root_real = os.path.realpath(root)
    if not path:
        return root_real
    if os.path.isabs(path):
        candidate = path
    else:
        candidate = os.path.join(root_real, path)
    resolved = _realpath_missing(candidate)
    if os.path.commonpath([resolved, root_real]) != root_real:
        raise ValueError("path escapes allowed root: %s" % path)
    return resolved


def allowed_roots_for(tool_name, default_root, overrides):
    # per-tool root override when present, else the default root
    if overrides and tool_name in overrides:
        return overrides[tool_name]
    return default_root
