"""Run pytest against a workspace copy. The model never receives a shell."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PYTEST_TIMEOUT_SECONDS = 60


def run_pytest(workspace: Path, arguments: list[str] | None = None, timeout: int = PYTEST_TIMEOUT_SECONDS) -> tuple[int, str]:
    command = [sys.executable, "-m", "pytest", "-q", *(arguments or [])]
    env = os.environ.copy()
    previous = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(workspace) if not previous else str(workspace) + os.pathsep + previous
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=env,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        output = f"{exc.stdout or ''}{exc.stderr or ''}\npytest timed out after {timeout} seconds\n"
        return 124, output
    return completed.returncode, f"{completed.stdout}{completed.stderr}"
