from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

# Structured answers from the local Claude Code CLI (`claude -p`, its own
# login - no API key here): no tools, no MCP, no project settings, the reply
# validated against a JSON schema (`structured_output`).

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 300


def default_claude_bin() -> str:
    # systemd --user has no ~/.local/bin in PATH
    return shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def run_structured(
    prompt: str,
    schema: dict,
    *,
    model: str,
    claude_bin: Optional[str] = None,
    timeout: int = TIMEOUT_SECONDS,
) -> dict:
    """structured_output of one `claude -p` run; RuntimeError on failure."""
    result = subprocess.run(
        [
            claude_bin or default_claude_bin(),
            "-p",
            "--model", model,
            "--no-session-persistence",
            "--tools", "",
            "--strict-mcp-config",
            "--setting-sources", "",
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
        ],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=os.path.expanduser("~"),
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude exit {result.returncode}: {(result.stderr or result.stdout)[:300]}")
    payload = json.loads(result.stdout)
    log.info(
        "claude %s: %.1f s, $%s",
        model,
        (payload.get("duration_ms") or 0) / 1000,
        payload.get("total_cost_usd"),
    )
    output = payload.get("structured_output")
    if not isinstance(output, dict):
        raise RuntimeError(f"claude: no structured_output ({str(payload.get('result'))[:200]})")
    return output
