"""in-memory multi-level undo for file edits, no disk writes."""


class UndoStack:
    def __init__(self, max_size=20):
        # entries are (path, content) pairs, oldest first
        try:
            size = int(max_size)
        except (TypeError, ValueError):
            size = 20
        self.max_size = size if size > 0 else 20
        self._entries = []

    def snapshot(self, path, content):
        # push the content a file had before an edit
        self._entries.append((path, content))
        while len(self._entries) > self.max_size:
            self._entries.pop(0)

    def undo(self, path):
        # most recent snapshot for this path, or None
        for i in range(len(self._entries) - 1, -1, -1):
            if self._entries[i][0] == path:
                return self._entries.pop(i)[1]
        return None

    def depth(self):
        # number of snapshots currently held
        return len(self._entries)

    def clear(self):
        # drop every snapshot
        self._entries = []
