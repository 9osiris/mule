"""ast search and multi-engine web search tools."""
import ast
import hashlib
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request

from tools import _ddg_results, DDG_LITE, MAX_FETCH

CACHE_TTL = 24 * 3600
MAX_RESULTS = 10
BING_URL = "https://www.bing.com/search?q="
BRAVE_URL = "https://search.brave.com/search?q="


def _fetch_page(url):
    # one place to fetch a search page, tests stub this
    req = urllib.request.Request(url, headers={"User-Agent": "mule/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read(MAX_FETCH).decode("utf-8", errors="replace")


def _cache_dir(root):
    # cache lives under the project root when possible
    return os.path.join(root, ".mule", "cache")


def _cache_path(root):
    return os.path.join(_cache_dir(root), "search.json")


def _cache_key(query, engine):
    # stable key from the query and the engine name
    raw = "%s|%s" % (engine, query)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_load(root):
    # read the cache file, tolerate a missing or broken one
    try:
        with open(_cache_path(root)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _cache_save(root, data):
    # write the cache file, best effort only
    try:
        os.makedirs(_cache_dir(root), exist_ok=True)
        with open(_cache_path(root), "w") as f:
            json.dump(data, f)
    except OSError:
        pass


def _cache_get(root, query, engine):
    # a fresh entry (24h ttl) or None
    entry = _cache_load(root).get(_cache_key(query, engine))
    if not isinstance(entry, dict):
        return None
    if time.time() - float(entry.get("ts", 0)) > CACHE_TTL:
        return None
    results = entry.get("results")
    if not isinstance(results, list):
        return None
    return results


def _cache_put(root, query, engine, results):
    # store results with a timestamp for the ttl check
    data = _cache_load(root)
    data[_cache_key(query, engine)] = {
        "ts": time.time(), "results": results}
    _cache_save(root, data)


def _parse_links(page, limit, skip):
    # generic html link parser for bing and brave pages
    found = []
    seen = set()
    for href, title in re.findall(
            r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S | re.I):
        url = html.unescape(href.strip())
        if not url.startswith("http"):
            continue
        host = urllib.parse.urlparse(url).netloc.lower()
        if any(host == s or host.endswith("." + s) for s in skip):
            continue
        if url in seen:
            continue
        text = re.sub(r"<[^>]+>", " ", title)
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()
        if not text:
            continue
        seen.add(url)
        found.append((text, url, ""))
        if len(found) >= limit:
            break
    return found


def _format_results(results, cached=False):
    # numbered title, url, snippet lines like web_search
    lines = []
    for i, item in enumerate(results, 1):
        title, link, snippet = item[0], item[1], item[2] if len(item) > 2 else ""
        lines.append("%d. %s\n   %s" % (i, title, link))
        if snippet:
            lines.append("   %s" % snippet)
    if cached:
        lines.append("(cached result)")
    return "\n".join(lines)


def register(toolset):
    # ast search over python definitions in the project
    def ast_search(pattern="", path=".", kind=""):
        if not pattern:
            return "error: empty pattern"
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return "error: bad pattern: %s" % e
        try:
            base = toolset._resolve(path)
        except ValueError as e:
            return "error: %s" % e
        if not os.path.isdir(base):
            return "error: not a directory: %s" % path
        want = (kind or "").strip().lower()
        if want and want not in ("function", "class", "method"):
            return "error: bad kind %r, use function, class, or method" % kind
        hits = []
        for dirpath, _, files in os.walk(base):
            for name in sorted(files):
                if not name.endswith(".py"):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, toolset.root)
                try:
                    with open(full, "r", errors="replace") as f:
                        tree = ast.parse(f.read(), filename=rel)
                except (OSError, SyntaxError):
                    continue
                # walk with class context so methods are
                # reported once, under their class
                def walk(node, cls=None):
                    for child in ast.iter_child_nodes(node):
                        if isinstance(child, ast.ClassDef):
                            if want in ("", "class") \
                                    and rx.search(child.name):
                                hits.append("%s:%d: class %s"
                                            % (rel, child.lineno,
                                               child.name))
                            walk(child, child.name)
                        elif isinstance(child, (ast.FunctionDef,
                                               ast.AsyncFunctionDef)):
                            if cls is not None:
                                if want in ("", "method") \
                                        and rx.search(child.name):
                                    hits.append("%s:%d: method %s.%s"
                                                % (rel, child.lineno, cls,
                                                   child.name))
                            elif want in ("", "function") \
                                    and rx.search(child.name):
                                hits.append("%s:%d: function %s"
                                            % (rel, child.lineno,
                                               child.name))
                            walk(child, cls)
                        else:
                            walk(child, cls)

                walk(tree)
                if len(hits) >= 100:
                    break
            if len(hits) >= 100:
                break
        if not hits:
            return "no matches for %r" % pattern
        out = "\n".join(sorted(hits)[:100])
        if len(hits) >= 100:
            out += "\n...[truncated]"
        return out

    # web search with an engine choice and a 24h local cache
    def web_search2(query="", engine="duckduckgo", count="5"):
        if not query:
            return "error: empty query"
        eng = (engine or "duckduckgo").strip().lower()
        if eng not in ("duckduckgo", "bing", "brave"):
            return ("error: unknown engine %r, use duckduckgo, "
                    "bing, or brave" % engine)
        try:
            n = max(1, min(int(count), MAX_RESULTS))
        except (TypeError, ValueError):
            n = 5
        cached = _cache_get(toolset.root, query, eng)
        if cached is not None:
            return _format_results(cached[:n], cached=True)
        try:
            if eng == "duckduckgo":
                page = _fetch_page(DDG_LITE + "?q="
                                   + urllib.parse.quote_plus(query))
                results = _ddg_results(page, n)
            elif eng == "bing":
                page = _fetch_page(BING_URL + urllib.parse.quote_plus(query))
                results = _parse_links(page, n, ("bing.com", "microsoft.com"))
            else:
                page = _fetch_page(BRAVE_URL + urllib.parse.quote_plus(query))
                results = _parse_links(page, n, ("brave.com", "search.brave.com"))
        except Exception as e:
            return "error: search failed: %s" % e
        if not results:
            return "no results for %r" % query
        _cache_put(toolset.root, query, eng,
                   [[t, u, s] for t, u, s in results])
        return _format_results(results)

    toolset.tools["ast_search"] = {
        "description": "find python definitions by name pattern, "
                       "returns file:line:name matches",
        "parameters": {"pattern": "regex to match definition names",
                       "path": "where to search, default '.'",
                       "kind": "function, class, or method, default all"},
        "run": ast_search,
    }
    toolset.tools["web_search2"] = {
        "description": "web search with an engine choice and a 24h "
                       "local cache, engines: duckduckgo, bing, brave",
        "parameters": {"query": "what to search for",
                       "engine": "duckduckgo, bing, or brave",
                       "count": "how many results, default 5"},
        "run": web_search2,
    }
