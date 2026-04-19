"""Canonical exit codes (see `.claude/plans/00-plan-index.md` §6)."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    USAGE = 1
    LINT = 2
    RENDER = 3
    PROVIDER = 4


class FrameCraftExit(Exception):
    """Raised by a command to exit with a specific code and stderr message."""

    def __init__(self, code: ExitCode, message: str) -> None:
        super().__init__(message)
        self.code = int(code)
        self.message = message


class ToolchainError(FrameCraftExit):
    """Missing external tool or non-zero exit from one. Exit 1."""

    def __init__(self, message: str, *, stderr: str | None = None, returncode: int | None = None) -> None:
        super().__init__(ExitCode.USAGE, message)
        self.stderr = stderr
        self.returncode = returncode
