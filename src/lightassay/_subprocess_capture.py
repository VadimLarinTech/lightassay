"""Helpers for subprocess execution with optional live stderr forwarding."""

from __future__ import annotations

import subprocess
import sys
import threading
from dataclasses import dataclass


@dataclass
class CapturedSubprocess:
    returncode: int
    stdout: str
    stderr: str


def run_text_subprocess(
    command: list[str],
    *,
    input_text: str,
    env: dict[str, str] | None = None,
    live_stderr: bool = False,
) -> CapturedSubprocess:
    """Run *command* and capture text stdout/stderr.

    When ``live_stderr`` is true, stderr lines are forwarded to the
    current process stderr as they arrive while still being accumulated
    for later error handling.
    """
    if not live_stderr:
        result = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            env=env,
        )
        return CapturedSubprocess(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        bufsize=1,
    )

    stderr_chunks: list[str] = []

    def _pump_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            stderr_chunks.append(line)
            sys.stderr.write(line)
            sys.stderr.flush()

    stderr_thread = threading.Thread(target=_pump_stderr, daemon=True)
    stderr_thread.start()

    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(input_text)
    process.stdin.close()
    stdout = process.stdout.read()
    returncode = process.wait()
    stderr_thread.join()
    process.stdout.close()
    process.stderr.close()

    return CapturedSubprocess(
        returncode=returncode,
        stdout=stdout,
        stderr="".join(stderr_chunks),
    )
