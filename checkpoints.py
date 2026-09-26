"""tar snapshots of the project root, for --checkpoint/--restore."""
import os
import tarfile

SKIPS = {".git", "__pycache__", ".mule"}


def checkpoint_dir():
    d = os.path.join(os.path.expanduser("~"), ".mule", "checkpoints")
    os.makedirs(d, exist_ok=True)
    return d


def _path(name):
    safe = "".join(c for c in name if c.isalnum() or c in "-_.")
    if not safe:
        raise ValueError("bad checkpoint name: %r" % name)
    return os.path.join(checkpoint_dir(), safe + ".tar.gz")


def save_checkpoint(root, name):
    # tar the project dir, skipping .git, caches, and mule's own files
    root = os.path.abspath(root)
    path = _path(name)

    def keep(ti):
        parts = ti.name.replace("\\", "/").split("/")
        if any(p in SKIPS or p.endswith(".pyc") for p in parts):
            return None
        return ti

    with tarfile.open(path, "w:gz") as tar:
        tar.add(root, arcname=".", filter=keep, recursive=True)
    return path


def restore_checkpoint(root, name):
    # unpack a checkpoint back over the project dir
    root = os.path.abspath(root)
    path = _path(name)
    if not os.path.isfile(path):
        raise ValueError("no such checkpoint: %s" % name)
    os.makedirs(root, exist_ok=True)
    with tarfile.open(path, "r:gz") as tar:
        def safe(members):
            for m in members:
                dest = os.path.abspath(os.path.join(root, m.name))
                if os.path.commonpath([dest, root]) != root:
                    continue
                yield m
        tar.extractall(root, members=safe(tar.getmembers()))
    return path
