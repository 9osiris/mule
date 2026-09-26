import os
import subprocess

MAX_READ = 100_000  # don't dump giant files into context
MAX_OUTPUT = 20_000


class ToolSet:
    def __init__(self, root):
        self.root = os.path.abspath(root)
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

    def write_file(self, path, content=""):
        full = self._resolve(path)
        os.makedirs(os.path.dirname(full) or self.root, exist_ok=True)
        with open(full, "w") as f:
            f.write(content or "")
        return "wrote %d bytes to %s" % (len(content or ""), path)

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
