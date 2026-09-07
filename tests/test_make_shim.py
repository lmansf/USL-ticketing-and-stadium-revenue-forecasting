"""make.py runs the Makefile's targets where make is not installed.

The shim parses the real Makefile, so these tests double as a check that the
Makefile stays inside the subset it understands: variables, targets with
dependencies, tab-indented recipe lines, $(NAME) references, @ and echo.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from usl import config

MAKE_PY = config.PROJECT_ROOT / "make.py"


@pytest.fixture(scope="module")
def shim() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_shim", MAKE_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_makefile_target_parses_with_its_recipe(shim: ModuleType) -> None:
    makefile = shim.parse()
    names = list(makefile.targets)
    for expected in ("help", "check", "backfill", "transform", "train", "export", "weekly", "test"):
        assert expected in names
    assert ".PHONY" not in names
    assert makefile.targets["check"].deps == ["lint", "typecheck", "test"]
    assert makefile.targets["check"].recipe == []
    assert makefile.targets["weekly"].recipe == ["$(PYTHON) -m usl.run weekly --db $(DB)"]
    assert makefile.variables["PYTHON"]
    assert makefile.variables["DB"] == "data/usl.duckdb"
    # every non-help target has something to run, directly or through a dependency
    for target in makefile.targets.values():
        assert target.recipe or target.deps, target.name


def test_variables_expand_and_overrides_win(
    shim: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PYTHON", raising=False)
    makefile = shim.parse()
    assert makefile.expand("$(PYTHON) -m usl.run weekly --db $(DB)") == (
        f"{makefile.variables['PYTHON']} -m usl.run weekly --db data/usl.duckdb"
    )
    assert makefile.expand("$(DB)", {"DB": "x.duckdb"}) == "x.duckdb"
    assert makefile.expand("$(MISSING)") == ""
    monkeypatch.setenv("PYTHON", "py -3.11")
    assert shim.parse().variables["PYTHON"] == "py -3.11"


def test_dependencies_run_first_and_a_failure_stops_the_target(shim: ModuleType) -> None:
    makefile = shim.parse()
    ran: list[str] = []

    def runner(line: str) -> int:
        ran.append(line)
        return 0

    assert shim.run(makefile, ["check"], runner=runner, echo=lambda _: None) == 0
    assert [line.split(" -m ")[1].split()[0] for line in ran] == ["ruff", "mypy", "pytest"]

    ran.clear()

    def failing(line: str) -> int:
        ran.append(line)
        return 3 if "mypy" in line else 0

    assert shim.run(makefile, ["check"], runner=failing, echo=lambda _: None) == 3
    assert len(ran) == 2  # pytest never ran


def test_unknown_target_is_named_not_swallowed(
    shim: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    makefile = shim.parse()
    assert shim.run(makefile, ["nonsense"], runner=lambda _: 0) == 2
    assert "no target named 'nonsense'" in capsys.readouterr().out


def test_echo_lines_print_without_quotes_and_silent_lines_are_not_echoed(
    shim: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    makefile = shim.parse()
    echoed: list[str] = []
    assert shim.run(makefile, ["help"], echo=echoed.append) == 0
    out = capsys.readouterr().out
    assert "make check          lint + typecheck + test" in out
    assert '"' not in out
    assert echoed == []  # every help line is @-silent


def test_the_shim_runs_as_a_script(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(MAKE_PY), "help"],
        cwd=config.PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "make backfill" in result.stdout
    override = subprocess.run(
        [sys.executable, str(MAKE_PY), "archive", "PYTHON=" + sys.executable],
        cwd=config.PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert override.returncode == 0
    assert "season ids" in override.stdout
