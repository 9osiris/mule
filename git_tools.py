"""git commit tool, sandboxed to the project root."""
import json
import os
import subprocess


def _git(toolset, *args):
    # run git in the root, return (returncode, stdout, stderr)
    try:
        proc = subprocess.run(
            ["git"] + list(args), cwd=toolset.root,
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "", "git failed: %s" % e
    return proc.returncode, proc.stdout, proc.stderr


def _status_lines(toolset):
    # porcelain status lines, empty list when not a repo
    code, out, _ = _git(toolset, "status", "--porcelain")
    if code is None or code != 0:
        return None
    return [line for line in out.splitlines() if line.strip()]


def _parse_paths(raw):
    # json list, or a comma separated list, or a single path
    if not raw:
        return []
    text = str(raw).strip()
    if text.startswith("["):
        try:
            data = json.loads(text)
        except ValueError:
            return None
        if not isinstance(data, list):
            return None
        return [str(p) for p in data]
    return [p.strip() for p in text.split(",") if p.strip()]


def register(toolset):
    def git_commit(message="", paths="", all="false"):
        if not (message or "").strip():
            return "error: empty commit message, refusing to commit"
        code, _, err = _git(toolset, "rev-parse", "--git-dir")
        if code is None or code != 0:
            return "error: not a git repository: %s" % (err.strip()
                                                       or "no repo")
        lines = _status_lines(toolset)
        if lines is None:
            return "error: not a git repository"
        # merge conflicts are never committed through this tool
        if any(line[:2].strip() in ("UU", "AA", "DD", "DU", "UD", "AU")
               or line[:2] in ("UU", "AA", "DD", "DU", "UD", "AU")
               for line in lines):
            return "error: refusing to commit with merge conflicts present"
        stage_all = str(all).lower() in ("true", "1", "yes")
        wanted = _parse_paths(paths)
        if wanted is None:
            return "error: paths must be a json list or comma separated"
        if not stage_all and not wanted:
            return ("error: give paths to stage, or set all=true "
                    "to stage everything")
        # every path must exist and stay inside the root
        resolved = []
        for p in wanted:
            try:
                full = toolset._resolve(p)
            except ValueError as e:
                return "error: %s" % e
            if not os.path.exists(full):
                return "error: no such file or directory: %s" % p
            resolved.append(os.path.relpath(full, toolset.root))
        if not stage_all:
            # tracked files dirty outside the given paths mean the
            # repo is dirty in unexpected ways, refuse the subset
            tracked = set()
            for line in lines:
                status = line[:2]
                name = line[3:].strip()
                if "->" in name:
                    name = name.split("->")[-1].strip()
                if status.strip() == "??":
                    continue
                tracked.add(name)
            unexpected = sorted(tracked - set(resolved))
            if unexpected:
                return ("error: refusing to commit, these tracked files "
                        "are also dirty: %s" % ", ".join(unexpected))
        if not lines and not stage_all:
            return "error: nothing to commit"
        if stage_all:
            code, _, err = _git(toolset, "add", "-A")
            if code != 0:
                return "error: git add failed: %s" % err.strip()
        else:
            code, _, err = _git(toolset, "add", "--", *resolved)
            if code != 0:
                return "error: git add failed: %s" % err.strip()
        # staging may have left nothing to commit
        code, out, _ = _git(toolset, "diff", "--cached", "--quiet")
        if code == 0:
            return "error: nothing to commit"
        code, out, err = _git(toolset, "commit", "-m", message.strip())
        if code != 0:
            return "error: git commit failed: %s" % (
                (err.strip() or out.strip()))
        first = (out.strip().splitlines() or ["committed"])[0]
        return "committed: %s" % first

    toolset.tools["git_commit"] = {
        "description": "stage given paths (or all with all=true) and "
                       "commit with a message, sandboxed to the root",
        "parameters": {"message": "commit message, required",
                       "paths": "json list or comma separated paths",
                       "all": "true to stage everything, default false"},
        "run": git_commit,
    }
