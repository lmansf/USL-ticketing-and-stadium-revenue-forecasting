#!/usr/bin/env python3
"""Run the Makefile's targets without make.

    python make.py check
    python make.py backfill transform train export
    python make.py weekly DB=data/other.duckdb

make is not installed by default on Windows, and every target in this repo
is a thin wrapper around a python -m command, so this reads the Makefile
itself and runs the same recipe lines. One source of truth: a target added
to the Makefile is available here with no other change.

What it understands is exactly what the Makefile uses: `NAME ?= value` and
`NAME = value` variables (an environment variable or a NAME=value argument
wins over `?=`; PYTHON defaults to the interpreter running this script),
`target: dependencies` with tab-indented recipe lines,
`$(NAME)` expansion, a leading `@` for a silent line, and `echo "..."` which
is printed directly so the quotes do not show on Windows. Anything else in a
recipe is handed to the shell as-is. Dependencies run first, in order; a
failing line stops the target and its exit code is returned.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

MAKEFILE = Path(__file__).resolve().parent / "Makefile"

_VARIABLE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(\?=|=)\s*(.*)$")
_TARGET = re.compile(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$")
_REFERENCE = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)")


@dataclass
class Target:
    """One Makefile target: what it depends on and what it runs."""

    name: str
    deps: list[str] = field(default_factory=list)
    recipe: list[str] = field(default_factory=list)


@dataclass
class Makefile:
    """The parsed file: variables with their defaults, and targets in file order."""

    variables: dict[str, str]
    targets: dict[str, Target]

    def expand(self, text: str, overrides: dict[str, str] | None = None) -> str:
        """Replace every $(NAME) with its value. Unknown names expand to nothing, as make does."""
        values = {**self.variables, **(overrides or {})}
        return _REFERENCE.sub(lambda m: values.get(m.group(1), ""), text)


def parse(path: Path = MAKEFILE) -> Makefile:
    """Read the Makefile.

    Args:
        path: The Makefile.

    Returns:
        Variables (with `?=` defaults already resolved against the environment)
        and targets. `.PHONY` declarations are skipped.
    """
    variables: dict[str, str] = {}
    targets: dict[str, Target] = {}
    current: Target | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("\t"):
            if current is not None:
                current.recipe.append(raw[1:])
            continue
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        variable = _VARIABLE.match(line)
        if variable:
            name, operator, value = variable.groups()
            if operator == "?=" and name in os.environ:
                variables[name] = os.environ[name]
            else:
                variables[name] = value.strip()
            current = None
            continue
        target = _TARGET.match(line)
        if target:
            name, deps = target.groups()
            if name == ".PHONY":
                current = None
                continue
            current = targets.setdefault(name, Target(name))
            current.deps.extend(deps.split())
            continue
        current = None
    return Makefile(variables, targets)


def _run_line(line: str) -> int:
    """Run one recipe line the way a shell would, printing echo lines ourselves."""
    try:
        words = shlex.split(line, posix=True)
    except ValueError:
        words = []
    if words and words[0] == "echo":
        print(" ".join(words[1:]))
        return 0
    return subprocess.call(line, shell=True)


def run(
    makefile: Makefile,
    names: list[str],
    overrides: dict[str, str] | None = None,
    *,
    runner: Callable[[str], int] = _run_line,
    echo: Callable[[str], None] = print,
) -> int:
    """Run targets in order, each after its dependencies.

    Args:
        makefile: From parse().
        names: Targets to run.
        overrides: NAME=value arguments from the command line.
        runner: Runs one expanded recipe line and returns its exit code.
        echo: Prints a non-silent line before it runs, as make does.

    Returns:
        0, or the exit code of the first failing line.
    """
    done: set[str] = set()

    def visit(name: str) -> int:
        if name in done:
            return 0
        target = makefile.targets.get(name)
        if target is None:
            print(f"make.py: no target named {name!r}. Targets: {', '.join(makefile.targets)}")
            return 2
        for dep in target.deps:
            code = visit(dep)
            if code:
                return code
        done.add(name)
        for raw in target.recipe:
            line = makefile.expand(raw, overrides)
            silent = line.startswith("@")
            if silent:
                line = line[1:]
            else:
                echo(line)
            code = runner(line)
            if code:
                print(f"make.py: target {name!r} failed on: {line}  (exit code {code})")
                return code
        return 0

    for name in names:
        code = visit(name)
        if code:
            return code
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point: targets and NAME=value overrides, in any order."""
    args = list(sys.argv[1:] if argv is None else argv)
    overrides: dict[str, str] = {}
    names: list[str] = []
    for arg in args:
        if "=" in arg and not arg.startswith("-"):
            key, _, value = arg.partition("=")
            overrides[key] = value
        else:
            names.append(arg)
    if not MAKEFILE.exists():
        print(f"make.py: {MAKEFILE} not found")
        return 2
    # The Makefile's default is the bare word `python`. Here the interpreter
    # running this script is the better default: it is the one in the active
    # virtual environment, or the one `py -3.11 make.py` chose.
    if "PYTHON" not in overrides and "PYTHON" not in os.environ:
        overrides["PYTHON"] = sys.executable
    makefile = parse(MAKEFILE)
    try:
        return run(makefile, names or ["help"], overrides)
    except BrokenPipeError:
        # `python make.py help | head`: the reader closed the pipe. Not an error.
        return 0


if __name__ == "__main__":
    sys.exit(main())
