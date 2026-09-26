import datetime
import difflib
import html
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request

MAX_READ = 100_000  # don't dump giant files into context
MAX_OUTPUT = 20_000
MAX_FETCH = 200_000  # cap on downloaded pages


def make_diff(old, new, path="file"):
    # unified diff of old -> new, factored out for tests
    lines = difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile="a/" + path, tofile="b/" + path, lineterm="")
    return "\n".join(lines)


class BackupStore:
    # copies of files before they get modified, newest last
    def __init__(self, backup_dir=None):
        self.dir = backup_dir or os.path.join(
            os.path.expanduser("~"), ".mule", "backups")
        os.makedirs(self.dir, exist_ok=True)
        self.stack = []

    def stash(self, full_path):
        # copy an existing file aside before it changes
        if not os.path.isfile(full_path):
            return None
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        dest = os.path.join(self.dir,
                            "%s-%s" % (stamp, os.path.basename(full_path)))
        shutil.copy2(full_path, dest)
        self.stack.append((full_path, dest))
        return dest

    def undo_last(self):
        # restore the most recently stashed file
        if not self.stack:
            return None
        full_path, dest = self.stack.pop()
        shutil.copy2(dest, full_path)
        return full_path


class ToolSet:
    def __init__(self, root, confirm=None):
        self.root = os.path.abspath(root)
        # confirm(command) -> bool, asked before every shell command
        self.confirm = confirm
        self.backups = BackupStore()
        self.tools = {
            "read_file": {
                "description": "read a text file, path relative to project root",
                "parameters": {"path": "relative path of the file"},
                "run": self.read_file,
            },
            "write_file": {
                "description": "write content to a file, creating parent dirs. overwrites.",
                "parameters": {"path": "relative path", "content": "full file content"},
                "run": self.write_file,
            },
            "edit_file": {
                "description": "replace one exact string in a file. old must match exactly once.",
                "parameters": {"path": "relative path", "old": "string to find",
                               "new": "replacement"},
                "run": self.edit_file,
            },
            "list_dir": {
                "description": "list files in a directory, relative to project root",
                "parameters": {"path": "relative path, default '.'"},
                "run": self.list_dir,
            },
            "run_shell": {
                "description": "run a shell command in the project root, returns output",
                "parameters": {"command": "the command", "timeout": "seconds, default 30"},
                "run": self.run_shell,
            },
            "fetch_url": {
                "description": "fetch a web page, returns the text with html stripped",
                "parameters": {"url": "http or https url"},
                "run": self.fetch_url,
            },
        }

    def schemas(self):
        out = []
        for name, t in self.tools.items():
            props = {k: {"type": "string"} for k in t["parameters"]}
            out.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": t["description"],
                    "parameters": {"type": "object", "properties": props},
                },
            })
        return out

    def call(self, name, args):
        tool = self.tools.get(name)
        if not tool:
            return "error: unknown tool '%s'" % name
        try:
            return tool["run"](**{k: v for k, v in args.items()
                                  if k in tool["parameters"]})
        except TypeError as e:
            return "error: bad arguments: %s" % e
        except Exception as e:
            return "error: %s" % e

    # path stays inside the project root or it doesn't happen
    def _resolve(self, path):
        full = os.path.abspath(os.path.join(self.root, path or "."))
        if os.path.commonpath([full, self.root]) != self.root:
            raise ValueError("path escapes project root: %s" % path)
        return full

    def read_file(self, path):
        full = self._resolve(path)
        if not os.path.isfile(full):
            return "error: no such file: %s" % path
        with open(full, "r", errors="replace") as f:
            data = f.read(MAX_READ + 1)
        if len(data) > MAX_READ:
            data = data[:MAX_READ] + "\n...[truncated]"
        return data

    def undo_last(self):
        # restore the most recently changed file, for /undo and --undo
        restored = self.backups.undo_last()
        if restored is None:
            return "nothing to undo"
        return "restored %s" % os.path.relpath(restored, self.root)

    def _confirm_write(self, action, path, old_text, new_text):
        # in --ask mode, show what the write would change first
        if not self.confirm:
            return True
        prompt = action
        diff = make_diff(old_text, new_text, path)
        if diff:
            prompt += "\n" + diff
        return self.confirm(prompt)

    def write_file(self, path, content=""):
        full = self._resolve(path)
        content = content or ""
        old_text = ""
        if os.path.isfile(full):
            with open(full, "r", errors="replace") as f:
                old_text = f.read()
        if not self._confirm_write("write_file %s" % path, path,
                                   old_text, content):
            return "declined: the write was not applied"
        if os.path.isfile(full):
            self.backups.stash(full)
        os.makedirs(os.path.dirname(full) or self.root, exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        return "wrote %d bytes to %s" % (len(content), path)

    def edit_file(self, path, old="", new=""):
        # patch one exact string. must match exactly once or it bails
        full = self._resolve(path)
        if not os.path.isfile(full):
            return "error: no such file: %s" % path
        if not old:
            return "error: old string is empty, nothing to replace"
        with open(full, "r", errors="replace") as f:
            data = f.read()
        count = data.count(old)
        if count == 0:
            return "error: old string not found in %s" % path
        if count > 1:
            return ("error: old string matches %d times in %s, "
                    "be more specific" % (count, path))
        new_data = data.replace(old, new or "", 1)
        if not self._confirm_write("edit_file %s" % path, path,
                                   data, new_data):
            return "declined: the edit was not applied"
        self.backups.stash(full)
        with open(full, "w") as f:
            f.write(new_data)
        return "edited %s" % path

    def list_dir(self, path="."):
        full = self._resolve(path)
        if not os.path.isdir(full):
            return "error: no such directory: %s" % path
        lines = []
        for name in sorted(os.listdir(full)):
            p = os.path.join(full, name)
            if os.path.isdir(p):
                lines.append(name + "/")
            else:
                lines.append("%s (%d bytes)" % (name, os.path.getsize(p)))
        return "\n".join(lines) or "(empty)"

    def run_shell(self, command, timeout="30"):
        if self.confirm and not self.confirm(command):
            return "declined: the command was not run"
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = 30
        timeout = min(max(timeout, 1), 300)
        try:
            proc = subprocess.run(
                command, shell=True, cwd=self.root,
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return "error: command timed out after %ss" % timeout
        out = (proc.stdout or "") + (proc.stderr or "")
        if len(out) > MAX_OUTPUT:
            out = out[:MAX_OUTPUT] + "\n...[truncated]"
        out = out.rstrip() or "(no output)"
        return "exit %d\n%s" % (proc.returncode, out)

    def fetch_url(self, url):
        # grab a page, strip the html down to rough text
        scheme = urllib.parse.urlparse(url or "").scheme
        if scheme not in ("http", "https"):
            return "error: only http and https urls, got: %s" % scheme
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "mule/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read(MAX_FETCH + 1)
        except Exception as e:
            return "error: fetch failed: %s" % e
        if len(data) > MAX_FETCH:
            data = data[:MAX_FETCH]
        text = data.decode("utf-8", errors="replace")
        text = re.sub(r"<script.*?</script>", " ", text,
                      flags=re.S | re.I)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text or "(empty page)"
