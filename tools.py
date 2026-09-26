import base64
import datetime
import difflib
import fnmatch
import html
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.parse
import urllib.request

from agent import run as _agent_run, last_answer as _agent_last

MAX_READ = 100_000  # don't dump giant files into context
MAX_OUTPUT = 20_000
MAX_FETCH = 200_000  # cap on downloaded pages
MAX_IMAGE = 1_000_000  # biggest image read_image will swallow
DDG_LITE = "https://lite.duckduckgo.com/lite/"
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
TODO_STATES = ("pending", "in_progress", "done")


def _ddg_results(page, limit=8):
    # parse duckduckgo lite html into (title, url, snippet) tuples
    links = re.findall(
        r'<a rel="nofollow"\s+href="([^"]+)"\s+class=\'result-link\'>'
        r"(.*?)</a>",
        page, re.S)
    snips = re.findall(r"class='result-snippet'>(.*?)</td>", page, re.S)
    out = []
    for i, (href, title) in enumerate(links[:limit]):
        url = href
        m = re.search(r"[?&]uddg=([^&]+)", href)
        if m:
            url = urllib.parse.unquote(m.group(1))
        snippet = ""
        if i < len(snips):
            snippet = re.sub(r"\s+", " ", snips[i]).strip()
        out.append((html.unescape(title.strip()), url,
                    html.unescape(snippet)))
    return out


def make_diff(old, new, path="file"):
    # unified diff of old -> new, factored out for tests
    lines = difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile="a/" + path, tofile="b/" + path, lineterm="")
    return "\n".join(lines)


class JobStore:
    # background shell jobs, each runs in its own thread
    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.jobs = {}
        self.next_id = 1

    def start(self, command, timeout=300):
        with self.lock:
            jid = self.next_id
            self.next_id += 1
            job = {"id": jid, "command": command, "done": False,
                   "killed": False, "output": "", "returncode": None}
            self.jobs[jid] = job
        t = threading.Thread(target=self._run, args=(job, timeout),
                             daemon=True)
        t.start()
        return jid

    def _run(self, job, timeout):
        try:
            proc = subprocess.Popen(
                job["command"], shell=True, cwd=self.root,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True)
        except Exception as e:
            job["output"] = "error: could not start: %s" % e
            job["done"] = True
            return
        job["proc"] = proc
        try:
            out, _ = proc.communicate(timeout=timeout)
            job["returncode"] = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
            job["returncode"] = proc.returncode
            out = (out or "") + "\n[job timed out after %ss]" % timeout
        job["output"] = out or ""
        job["done"] = True

    def get(self, jid):
        with self.lock:
            return self.jobs.get(jid)

    def all(self):
        with self.lock:
            return [self.jobs[k] for k in sorted(self.jobs)]

    def kill(self, jid):
        job = self.get(jid)
        if job is None:
            return None
        proc = job.get("proc")
        if proc and not job["done"]:
            try:
                proc.kill()
            except OSError:
                pass
            job["killed"] = True
            return True
        return False


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
    def __init__(self, root, confirm=None, ask=None, make_chat=None,
                 delegate_depth=0, max_delegates=3):
        self.root = os.path.abspath(root)
        # confirm(prompt) -> bool, asked before shell commands and file writes
        self.confirm = confirm
        # ask(question, options) -> str, the human-in-the-loop for ask_user
        self.ask = ask
        # make_chat() -> chat_fn for delegate subagents, None disables it
        self.make_chat = make_chat
        self.delegate_depth = delegate_depth
        self._delegate_sem = threading.Semaphore(max(1, max_delegates))
        self.backups = BackupStore()
        self.jobs = JobStore(self.root)
        self.todos = []  # [{text, status}] the model manages itself
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
                "description": "run a shell command in the project root, returns output. "
                               "pass background=true to get a job id back instead",
                "parameters": {"command": "the command", "timeout": "seconds, default 30",
                               "background": "true to run in the background"},
                "run": self.run_shell,
            },
            "jobs": {
                "description": "list background shell jobs with status and output preview",
                "parameters": {},
                "run": self.jobs_list,
            },
            "job_output": {
                "description": "full output of a background job",
                "parameters": {"id": "the job id"},
                "run": self.job_output,
            },
            "job_kill": {
                "description": "stop a running background job",
                "parameters": {"id": "the job id"},
                "run": self.job_kill,
            },
            "ask_user": {
                "description": "ask the human a question with 2-4 options. "
                               "only works in interactive or --ask mode.",
                "parameters": {"question": "the question",
                               "options": "json list of 2-4 option labels"},
                "run": self.ask_user,
            },
            "delegate": {
                "description": "hand a subtask to a subagent with its own history. "
                               "it runs to completion and returns a summary. "
                               "delegates cannot delegate further.",
                "parameters": {"task": "what the subagent should do",
                               "system": "optional extra instructions"},
                "run": self.delegate,
            },
            "fetch_url": {
                "description": "fetch a web page, returns the text with html stripped",
                "parameters": {"url": "http or https url"},
                "run": self.fetch_url,
            },
            "web_search": {
                "description": "search the web, returns titles, urls, and snippets",
                "parameters": {"query": "what to search for",
                               "count": "how many results, default 5"},
                "run": self.web_search,
            },
            "todo_write": {
                "description": "replace the todo list. statuses: "
                               "pending, in_progress, done",
                "parameters": {"todos": "json list of {text, status} objects"},
                "run": self.todo_write,
            },
            "todo_read": {
                "description": "show the current todo list",
                "parameters": {},
                "run": self.todo_read,
            },
            "read_image": {
                "description": "load an image as a base64 data uri the model "
                               "can look at (png/jpg/gif/webp)",
                "parameters": {"path": "relative path of the image"},
                "run": self.read_image,
            },
            "grep": {
                "description": "search file contents for a regex pattern, "
                               "returns file:line matches",
                "parameters": {"pattern": "regex to search for",
                               "path": "where to search, default '.'",
                               "glob": "filename filter like '*.py', default '*'"},
                "run": self.grep,
            },
            "find": {
                "description": "find files by name pattern, returns relative paths",
                "parameters": {"pattern": "glob like '*.py' or 'test*'",
                               "path": "where to search, default '.'"},
                "run": self.find,
            },
            "tree": {
                "description": "show the directory structure as a tree",
                "parameters": {"path": "where to start, default '.'",
                               "depth": "how deep to go, default 3"},
                "run": self.tree,
            },
            "read_many": {
                "description": "read several files at once, each headed "
                               "by its path",
                "parameters": {"paths": "json list of relative paths"},
                "run": self.read_many,
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
            from ui import color_diff
            prompt += "\n" + color_diff(diff)
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

    def run_shell(self, command, timeout="30", background="false"):
        if self.confirm and not self.confirm(command):
            return "declined: the command was not run"
        if str(background).lower() in ("true", "1", "yes"):
            jid = self.jobs.start(command)
            return "job started: %d (use job_output %d to read it)" % (jid, jid)
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

    def jobs_list(self):
        # every background job, newest last, with an output preview
        rows = self.jobs.all()
        if not rows:
            return "(no background jobs)"
        lines = []
        for j in rows:
            status = "done (exit %s)" % j["returncode"] if j["done"] \
                else "running"
            if j["killed"]:
                status += ", killed"
            preview = (j["output"] or "").strip().split("\n")
            preview = " | ".join(preview[:3])[:200]
            lines.append("#%d [%s] %s%s" % (
                j["id"], status, j["command"][:60],
                (" -- " + preview) if preview else ""))
        return "\n".join(lines)

    def job_output(self, id=""):
        # full output of one job, capped like everything else
        try:
            jid = int(id)
        except (TypeError, ValueError):
            return "error: bad job id: %s" % id
        job = self.jobs.get(jid)
        if job is None:
            return "error: no such job: %s" % id
        out = job["output"] or ("(still running)" if not job["done"]
                                else "(no output)")
        if len(out) > MAX_OUTPUT:
            out = out[:MAX_OUTPUT] + "\n...[truncated]"
        state = "done" if job["done"] else "running"
        return "job #%d [%s]\n%s" % (jid, state, out)

    def job_kill(self, id=""):
        try:
            jid = int(id)
        except (TypeError, ValueError):
            return "error: bad job id: %s" % id
        job = self.jobs.get(jid)
        if job is None:
            return "error: no such job: %s" % id
        if job["done"]:
            return "job #%d already finished" % jid
        self.jobs.kill(jid)
        return "killed job #%d" % jid

    def ask_user(self, question="", options="[]"):
        # the model asks the human something mid-run
        if not question:
            return "error: empty question"
        try:
            opts = json.loads(options or "[]")
        except json.JSONDecodeError:
            return "error: options was not valid json"
        if not isinstance(opts, list) or not 2 <= len(opts) <= 4:
            return "error: give 2 to 4 options as a json list"
        opts = [str(o) for o in opts]
        if not self.ask:
            return ("error: cannot ask the user in non-interactive mode, "
                    "proceed with your best guess and say what you assumed")
        try:
            return self.ask(question, opts)
        except Exception as e:
            return "error: asking failed: %s" % e

    def delegate(self, task="", system=""):
        # farm a subtask out to a fresh agent loop with its own history
        if not task:
            return "error: empty task"
        if self.delegate_depth >= 1:
            return "error: delegates cannot spawn their own delegates"
        if not self.make_chat:
            return "error: delegation is not wired up in this run"
        with self._delegate_sem:
            sub = ToolSet(self.root, make_chat=self.make_chat,
                          delegate_depth=self.delegate_depth + 1)
            try:
                messages = _agent_run(task, self.make_chat(), sub,
                                      system_prompt=(system or "").strip()
                                      or None,
                                      max_steps=15)
            except Exception as e:
                return "error: delegate failed: %s" % e
            answer = _agent_last(messages)
            return answer or "(the delegate said nothing)"

    def todo_write(self, todos="[]"):
        # the model rewrites its whole todo list each time it changes
        try:
            items = json.loads(todos or "[]")
        except json.JSONDecodeError:
            return "error: todos was not valid json"
        if not isinstance(items, list):
            return "error: todos must be a json list"
        cleaned = []
        for it in items:
            if not isinstance(it, dict) or not it.get("text"):
                return "error: every todo needs a text field"
            status = it.get("status") or "pending"
            if status not in TODO_STATES:
                return "error: bad status %r, use %s" % (
                    status, "/".join(TODO_STATES))
            cleaned.append({"text": str(it["text"]),
                            "status": status})
        self.todos = cleaned
        return self.progress_line() or "todo list cleared"

    def todo_read(self):
        # the current list, readable at a glance
        if not self.todos:
            return "(no todos)"
        marks = {"pending": "[ ]", "in_progress": "[>]",
                 "done": "[x]"}
        return "\n".join("%s %s" % (marks[t["status"]], t["text"])
                         for t in self.todos)

    def progress_line(self):
        # compact "[2/5] current thing" line for the loop to print
        if not self.todos:
            return None
        done = sum(1 for t in self.todos if t["status"] == "done")
        cur = next((t for t in self.todos
                    if t["status"] == "in_progress"), None)
        if cur is None:
            cur = next((t for t in self.todos
                        if t["status"] == "pending"), None)
        what = cur["text"] if cur else "all done"
        return "[%d/%d] %s" % (done, len(self.todos), what)

    def read_image(self, path):
        # load an image as a data uri, shaped for vision-capable models
        full = self._resolve(path)
        ext = os.path.splitext(full)[1].lower()
        mime = IMAGE_TYPES.get(ext)
        if not mime:
            return "error: not a supported image (png/jpg/gif/webp): %s" % path
        if not os.path.isfile(full):
            return "error: no such file: %s" % path
        size = os.path.getsize(full)
        if size > MAX_IMAGE:
            return "error: image too big (%d bytes, max %d)" % (
                size, MAX_IMAGE)
        with open(full, "rb") as f:
            data = f.read()
        uri = "data:%s;base64,%s" % (
            mime, base64.b64encode(data).decode("ascii"))
        return ("image %s, %d bytes, %s. show it to the user like this:\n"
                "![%s](%s)" % (path, size, mime, path, uri))

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

    def web_search(self, query="", count="5"):
        # duckduckgo lite search, returns titles, urls, and snippets
        if not query:
            return "error: empty query"
        try:
            n = max(1, min(int(count), 10))
        except (TypeError, ValueError):
            n = 5
        url = DDG_LITE + "?q=" + urllib.parse.quote_plus(query)
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "mule/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                page = resp.read(MAX_FETCH).decode("utf-8",
                                                   errors="replace")
        except Exception as e:
            return "error: search failed: %s" % e
        results = _ddg_results(page, n)
        if not results:
            return "no results for %r" % query
        lines = []
        for i, (title, link, snippet) in enumerate(results, 1):
            lines.append("%d. %s\n   %s" % (i, title, link))
            if snippet:
                lines.append("   %s" % snippet)
        return "\n".join(lines)

    def grep(self, pattern, path=".", glob="*"):
        # walk the tree, print file:line for every line matching
        # the regex. skips binaries, caps at 100 hits.
        try:
            rx = re.compile(pattern or "")
        except re.error as e:
            return "error: bad pattern: %s" % e
        base = self._resolve(path)
        if not os.path.isdir(base):
            return "error: not a directory: %s" % path
        hits = []
        for dirpath, _, files in os.walk(base):
            for name in sorted(files):
                if not fnmatch.fnmatch(name, glob or "*"):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, self.root)
                try:
                    with open(full, "r", errors="replace") as f:
                        for i, line in enumerate(f, 1):
                            if "\x00" in line:
                                break  # binary, skip the file
                            if rx.search(line):
                                hits.append("%s:%d: %s"
                                            % (rel, i, line.rstrip()))
                            if len(hits) >= 100:
                                return "\n".join(hits) + "\n...[truncated]"
                except OSError:
                    continue
        return "\n".join(hits) or "no matches for %r" % pattern

    def find(self, pattern="*", path="."):
        # find files by name, glob against the basename
        base = self._resolve(path)
        if not os.path.isdir(base):
            return "error: not a directory: %s" % path
        found = []
        for dirpath, _, files in os.walk(base):
            for name in sorted(files):
                if fnmatch.fnmatch(name, pattern or "*"):
                    found.append(os.path.relpath(
                        os.path.join(dirpath, name), self.root))
                if len(found) >= 100:
                    return "\n".join(found) + "\n...[truncated]"
        return "\n".join(found) or "no files matching %r" % pattern

    def tree(self, path=".", depth="3"):
        # classic tree view, dirs get a trailing slash
        try:
            max_depth = max(1, min(int(depth), 10))
        except (TypeError, ValueError):
            max_depth = 3
        base = self._resolve(path)
        if not os.path.isdir(base):
            return "error: not a directory: %s" % path
        lines = [os.path.basename(base) or base]

        def walk(dirpath, prefix, level):
            if level > max_depth:
                return
            try:
                entries = sorted(os.listdir(dirpath))
            except OSError:
                return
            # dirs first, like the real tree command
            entries.sort(key=lambda e: not os.path.isdir(
                os.path.join(dirpath, e)))
            for i, name in enumerate(entries):
                last = i == len(entries) - 1
                branch = "└── " if last else "├── "
                full = os.path.join(dirpath, name)
                label = name + "/" if os.path.isdir(full) else name
                lines.append(prefix + branch + label)
                if os.path.isdir(full):
                    walk(full, prefix + ("    " if last else "│   "),
                         level + 1)

        walk(base, "", 1)
        return "\n".join(lines)

    def read_many(self, paths="[]"):
        # read a batch of files in one call, capped at 10
        try:
            wanted = json.loads(paths or "[]")
        except ValueError:
            return "error: paths must be a json list"
        if not isinstance(wanted, list):
            return "error: paths must be a json list"
        parts = []
        for p in wanted[:10]:
            full = self._resolve(p)
            if not os.path.isfile(full):
                parts.append("=== %s ===\nerror: no such file" % p)
                continue
            with open(full, "r", errors="replace") as f:
                data = f.read(MAX_READ + 1)
            if len(data) > MAX_READ:
                data = data[:MAX_READ] + "\n...[truncated]"
            parts.append("=== %s ===\n%s" % (p, data))
        return "\n\n".join(parts) or "(no files given)"
