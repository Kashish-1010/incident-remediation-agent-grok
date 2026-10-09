"""Read-only tools for the investigation phase.

The model can read_file, search, and list_dir. Paths must stay inside the
workspace or be the incident log file. Nothing here writes or runs commands.
"""

from __future__ import annotations

import json
from pathlib import Path

MAX_READ_CHARS = 100_000
MAX_MATCHES = 40
ALLOWED_TOOLS = ("read_file", "search", "list_dir")


class Toolset:
    def __init__(self, workspace: Path, incident_log: Path) -> None:
        self.workspace = workspace.resolve()
        self.incident_log = incident_log.resolve()

    def schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "name": "read_file",
                "description": "Read a UTF-8 text file inside the investigation workspace, or the incident log.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Workspace-relative path or the incident log path"}},
                    "required": ["path"],
                },
            },
            {
                "type": "function",
                "name": "search",
                "description": "Search text files under the workspace for a literal string. Returns path, line number, and line text.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string", "description": "Optional directory under the workspace. Defaults to the workspace root."},
                    },
                    "required": ["query"],
                },
            },
            {
                "type": "function",
                "name": "list_dir",
                "description": "List the names in one directory under the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Directory under the workspace. Use . for the workspace root."}},
                    "required": ["path"],
                },
            },
        ]

    def execute(self, name: str, arguments: object) -> str:
        # Unknown names, including write or shell tools, never touch the filesystem.
        if name not in ALLOWED_TOOLS:
            return _error(f"unknown tool: {name}")
        if not isinstance(arguments, dict):
            return _error("tool arguments must be a JSON object")
        try:
            if name == "read_file":
                return self.read_file(str(arguments.get("path", "")))
            if name == "search":
                path = arguments.get("path")
                return self.search(str(arguments.get("query", "")), None if path is None else str(path))
            return self.list_dir(str(arguments.get("path", "")))
        except PermissionError as exc:
            return _error(str(exc))
        except OSError as exc:
            return _error(str(exc))

    def read_file(self, path: str) -> str:
        target = self._resolve_read(path)
        if not target.is_file():
            return _error(f"not a file: {path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + "\n... truncated ..."
        return text

    def search(self, query: str, path: str | None) -> str:
        if not query:
            return _error("query is required")
        root = self.workspace if path is None else self._resolve_workspace(path)
        if not root.is_dir():
            return _error(f"not a directory: {path}")
        matches: list[str] = []
        for file in sorted(root.rglob("*")):
            if not file.is_file():
                continue
            if file.suffix not in {".py", ".json", ".jsonl", ".md", ".txt"}:
                continue
            resolved = file.resolve()
            if not _inside(resolved, self.workspace):
                continue
            for number, line in enumerate(resolved.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if query in line:
                    relative = resolved.relative_to(self.workspace)
                    matches.append(f"{relative}:{number}:{line}")
                    if len(matches) >= MAX_MATCHES:
                        return "\n".join(matches)
        return "\n".join(matches) if matches else "no matches"

    def list_dir(self, path: str) -> str:
        target = self._resolve_workspace(path or ".")
        if not target.is_dir():
            return _error(f"not a directory: {path}")
        names = sorted(entry.name for entry in target.iterdir())
        return "\n".join(names)

    def _resolve_read(self, path: str) -> Path:
        if not path:
            raise PermissionError("path is required")
        candidate = self._candidate(path)
        log = self.incident_log
        if candidate == log and log.is_file():
            return candidate
        return self._require_workspace(candidate, path)

    def _resolve_workspace(self, path: str) -> Path:
        if not path:
            raise PermissionError("path is required")
        return self._require_workspace(self._candidate(path), path)

    def _candidate(self, path: str) -> Path:
        raw = Path(path)
        if raw.is_absolute():
            return raw.resolve()
        return (self.workspace / raw).resolve()

    def _require_workspace(self, candidate: Path, original: str) -> Path:
        if not _inside(candidate, self.workspace):
            raise PermissionError(f"path is outside the workspace: {original}")
        return candidate


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _error(message: str) -> str:
    return json.dumps({"error": message})
