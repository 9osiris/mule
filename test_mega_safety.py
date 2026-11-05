"""tests for safety.py and backoff.py. no api key needed."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backoff import AdaptiveBackoff
from safety import (allowed_roots_for, check_write_content,
                    contains_secret, resolve_within)

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("ok  " if cond else "FAIL") + " " + name)


# contains_secret: one sample per pattern kind

check("contains_secret finds an aws access key",
      contains_secret("key = AKIAIOSFODNN7EXAMPLE") == "aws access key")
check("contains_secret finds an api key",
      contains_secret("token sk-abcdefghijklmnopqrstuv") == "api key")
check("contains_secret finds a github token",
      contains_secret("ghp_" + "a" * 36) == "github token")
check("contains_secret finds a fine-grained github token",
      contains_secret("github_pat_11ABCDEFG0abc") == "github token")
check("contains_secret finds a slack token",
      contains_secret("xoxb-123456789012-abcdef") == "slack token")
check("contains_secret finds a private key",
      contains_secret("-----BEGIN RSA PRIVATE KEY-----") == "private key")
check("contains_secret finds a hardcoded password",
      contains_secret("password: hunter2") == "hardcoded credential")
check("contains_secret finds a hardcoded api_key assignment",
      contains_secret("api_key = abc123") == "hardcoded credential")
check("contains_secret returns None for clean text",
      contains_secret("hello world, no secrets here") is None)
check("contains_secret returns None for empty text",
      contains_secret("") is None)
check("contains_secret returns None for None",
      contains_secret(None) is None)
check("contains_secret returns the first pattern in list order",
      contains_secret("sk-abcdefghijklmnopqrstuv AKIAIOSFODNN7EXAMPLE")
      == "aws access key")
check("contains_secret does not flag a short sk- prefix",
      contains_secret("sk-abc") is None)
check("contains_secret does not flag the word password alone",
      contains_secret("change your password regularly") is None)

# check_write_content: the write-blocking check

ok, reason = check_write_content("x = 1\nprint(x)\n")
check("check_write_content allows clean content",
      ok is True and reason == "")
ok, reason = check_write_content("token = ghp_" + "b" * 36)
check("check_write_content blocks a github token",
      ok is False and "github token" in reason)
ok, reason = check_write_content("aws = AKIAIOSFODNN7EXAMPLE")
check("check_write_content blocks an aws key and names the kind",
      ok is False and "aws access key" in reason)
ok, reason = check_write_content("-----BEGIN EC PRIVATE KEY-----\nabc")
check("check_write_content blocks a private key",
      ok is False and "private key" in reason)
ok, reason = check_write_content("")
check("check_write_content allows empty content",
      ok is True)

# resolve_within: plain paths, escapes, symlinks, missing targets

root = tempfile.mkdtemp()
os.makedirs(os.path.join(root, "sub"))
with open(os.path.join(root, "sub", "real.txt"), "w") as f:
    f.write("hi")

check("resolve_within returns an existing file inside the root",
      resolve_within("sub/real.txt", root)
      == os.path.join(root, "sub", "real.txt"))
check("resolve_within returns a missing file inside the root",
      resolve_within("sub/new.txt", root)
      == os.path.join(root, "sub", "new.txt"))
check("resolve_within returns a missing nested path inside the root",
      resolve_within("a/b/c.txt", root)
      == os.path.join(root, "a", "b", "c.txt"))
check("resolve_within returns the root itself for an empty path",
      resolve_within("", root) == os.path.realpath(root))
check("resolve_within accepts an absolute path inside the root",
      resolve_within(os.path.join(root, "sub", "real.txt"), root)
      == os.path.join(root, "sub", "real.txt"))

for bad in ("../escape.txt", "sub/../../escape.txt",
            "/etc/passwd", os.path.join(root, "..", "escape.txt")):
    try:
        resolve_within(bad, root)
        escaped = False
    except ValueError:
        escaped = True
    check("resolve_within raises ValueError for %s" % bad, escaped)

# symlinks: one pointing outside, one pointing inside

outside = tempfile.mkdtemp()
with open(os.path.join(outside, "secret.txt"), "w") as f:
    f.write("outside")
os.symlink(os.path.join(outside, "secret.txt"),
           os.path.join(root, "link_out.txt"))
os.symlink(os.path.join(root, "sub", "real.txt"),
           os.path.join(root, "link_in.txt"))
os.symlink(outside, os.path.join(root, "link_dir_out"))
os.symlink(os.path.join(root, "sub"), os.path.join(root, "link_dir_in"))

try:
    resolve_within("link_out.txt", root)
    out_blocked = False
except ValueError:
    out_blocked = True
check("resolve_within blocks a symlink pointing outside the root",
      out_blocked)
check("resolve_within allows a symlink pointing inside the root",
      resolve_within("link_in.txt", root)
      == os.path.join(root, "sub", "real.txt"))
try:
    resolve_within("link_dir_out/secret.txt", root)
    dir_blocked = False
except ValueError:
    dir_blocked = True
check("resolve_within blocks a path through an outside symlink dir",
      dir_blocked)
check("resolve_within allows a path through an inside symlink dir",
      resolve_within("link_dir_in/real.txt", root)
      == os.path.join(root, "sub", "real.txt"))
try:
    resolve_within("link_dir_out/new.txt", root)
    missing_blocked = False
except ValueError:
    missing_blocked = True
check("resolve_within blocks a missing file under an outside symlink",
      missing_blocked)
check("resolve_within allows a missing file under an inside symlink",
      resolve_within("link_dir_in/new.txt", root)
      == os.path.join(root, "sub", "new.txt"))

# allowed_roots_for: pure override lookup

check("allowed_roots_for returns the per-tool override",
      allowed_roots_for("read_file", "/default",
                        {"read_file": "/docs"}) == "/docs")
check("allowed_roots_for returns the default without an override",
      allowed_roots_for("write_file", "/default",
                        {"read_file": "/docs"}) == "/default")
check("allowed_roots_for returns the default for empty overrides",
      allowed_roots_for("read_file", "/default", {}) == "/default")
check("allowed_roots_for returns the default for None overrides",
      allowed_roots_for("read_file", "/default", None) == "/default")
check("allowed_roots_for does not leak one tool override to another",
      allowed_roots_for("grep", "/default",
                        {"read_file": "/docs", "grep": "/src"}) == "/src")

# AdaptiveBackoff: growth, retry_after, limited models, reset

plain = AdaptiveBackoff(base=1.0, factor=2.0, jitter=0.0)
check("delay_for attempt 0 equals the base with no jitter",
      plain.delay_for("m", 0) == 1.0)
check("delay_for doubles each attempt with no jitter",
      plain.delay_for("m", 1) == 2.0
      and plain.delay_for("m", 2) == 4.0)
check("delay_for treats a negative attempt as attempt 0",
      plain.delay_for("m", -3) == 1.0)
check("delay_for honours retry_after exactly",
      plain.delay_for("m", 3, retry_after=7.5) == 7.5)
plain.record_rate_limit("m")
check("delay_for honours retry_after even for a limited model",
      plain.delay_for("m", 3, retry_after=2.0) == 2.0)
check("delay_for ignores a zero retry_after and computes a delay",
      plain.delay_for("m", 0, retry_after=0) >= 1.0)
plain.record_success("m")

fresh = AdaptiveBackoff(base=1.0, factor=2.0, jitter=0.25, seed=42)
limited = AdaptiveBackoff(base=1.0, factor=2.0, jitter=0.25, seed=42)
limited.record_rate_limit("gpt-x")
check("a recently limited model waits longer than a fresh one",
      limited.delay_for("gpt-x", 1) > fresh.delay_for("gpt-x", 1))
check("record_rate_limit marks the model limited",
      limited.is_limited("gpt-x") is True)
limited.record_success("gpt-x")
check("record_success clears the limited flag",
      limited.is_limited("gpt-x") is False)
cleared = AdaptiveBackoff(base=1.0, factor=2.0, jitter=0.25, seed=42)
cleared.record_rate_limit("gpt-x")
cleared.record_success("gpt-x")
fresh_same_seed = AdaptiveBackoff(base=1.0, factor=2.0, jitter=0.25,
                                  seed=42)
check("delay matches the fresh model again after success",
      cleared.delay_for("gpt-x", 1)
      == fresh_same_seed.delay_for("gpt-x", 1))

# fresh vs limited with identical seeds: same jitter draw, 2x base
fresh2 = AdaptiveBackoff(base=1.0, factor=2.0, jitter=0.25, seed=7)
limited2 = AdaptiveBackoff(base=1.0, factor=2.0, jitter=0.25, seed=7)
limited2.record_rate_limit("gpt-x")
check("limited delay is twice the fresh delay for the same draw",
      limited2.delay_for("gpt-x", 1)
      == 2.0 * fresh2.delay_for("gpt-x", 1))

seeded = AdaptiveBackoff(base=1.0, factor=2.0, jitter=0.25, seed=123)
check("delay_for stays inside delay_bounds for every attempt",
      all(seeded.delay_bounds("m", a)[0]
          <= seeded.delay_for("m", a)
          <= seeded.delay_bounds("m", a)[1] for a in range(5)))
check("delay_bounds grow with attempt",
      seeded.delay_bounds("m", 0)[1] < seeded.delay_bounds("m", 1)[0])
limited_bounds = AdaptiveBackoff(base=1.0, factor=2.0, jitter=0.25)
limited_bounds.record_rate_limit("m")
check("delay_bounds are wider for a limited model",
      limited_bounds.delay_bounds("m", 1)[0] > 2.0)

capped = AdaptiveBackoff(base=1.0, factor=2.0, jitter=0.0, max_delay=5.0)
check("delay_for never exceeds max_delay",
      capped.delay_for("m", 10) == 5.0)

multi = AdaptiveBackoff(base=1.0, factor=2.0, jitter=0.0)
multi.record_rate_limit("a")
check("limiting one model does not limit another",
      multi.is_limited("a") is True and multi.is_limited("b") is False)
check("an unflagged model keeps the fresh delay",
      multi.delay_for("b", 1) == 2.0)
check("a flagged model gets the doubled delay",
      multi.delay_for("a", 1) == 4.0)
multi.reset()
check("reset clears every model",
      multi.is_limited("a") is False
      and multi.delay_for("a", 1) == 2.0)

first = AdaptiveBackoff(base=1.0, factor=2.0, jitter=0.25, seed=99)
second = AdaptiveBackoff(base=1.0, factor=2.0, jitter=0.25, seed=99)
check("the same seed gives the same delay sequence",
      [first.delay_for("m", a) for a in range(4)]
      == [second.delay_for("m", a) for a in range(4)])
check("different seeds can give different delays",
      AdaptiveBackoff(base=1.0, jitter=0.25,
                      seed=1).delay_for("m", 0)
      != AdaptiveBackoff(base=1.0, jitter=0.25,
                         seed=2).delay_for("m", 0))

print()
print("%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
