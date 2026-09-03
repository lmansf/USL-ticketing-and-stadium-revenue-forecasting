# USL attendance forecasting - task runner.
#
# Windows users: make is not installed by default. Every target below is a
# thin wrapper around a python -m command; run those directly, or use
# scripts/run_weekly.ps1. See docs/mvp/05-mvp-schedule.md.

PYTHON ?= python
DB     ?= data/usl.duckdb

.PHONY: help
help:
	@echo "Setup"
	@echo "  make install        Install runtime dependencies"
	@echo "  make install-dev    Install runtime + dev dependencies"
	@echo ""
	@echo "Pipeline"
	@echo "  make backfill       Scrape all configured seasons into raw_matches"
	@echo "  make scrape         Scrape the current season only"
	@echo "  make transform      Run the SQL layer: staging, intermediate, mart"
	@echo "  make train          Train both models, write metrics and importance"
	@echo "  make export         Write Tableau extracts to tableau/extracts/"
	@echo "  make weekly         The full Tuesday run: scrape, transform, train, export"
	@echo ""
	@echo "Quality"
	@echo "  make test           Run the test suite"
	@echo "  make lint           ruff check"
	@echo "  make format         ruff format"
	@echo "  make typecheck      mypy usl"
	@echo "  make check          lint + typecheck + test"
	@echo ""
	@echo "Demo"
	@echo "  make demo-list      List the break-and-fix scenarios"
	@echo ""
	@echo "Housekeeping"
	@echo "  make clean          Remove caches and build artifacts"
	@echo "  make clean-db       Delete the DuckDB file (rebuildable from scratch)"

.PHONY: install
install:
	$(PYTHON) -m pip install -r requirements.txt

.PHONY: install-dev
install-dev:
	$(PYTHON) -m pip install -r requirements-dev.txt
	$(PYTHON) -m pip install -e .

.PHONY: backfill
backfill:
	$(PYTHON) -m usl.run backfill --db $(DB)

.PHONY: scrape
scrape:
	$(PYTHON) -m usl.run scrape --db $(DB)

.PHONY: transform
transform:
	$(PYTHON) -m usl.run transform --db $(DB)

.PHONY: train
train:
	$(PYTHON) -m usl.run train --db $(DB)

.PHONY: export
export:
	$(PYTHON) -m usl.run export --db $(DB)

.PHONY: weekly
weekly:
	$(PYTHON) -m usl.run weekly --db $(DB)

.PHONY: test
test:
	$(PYTHON) -m pytest

.PHONY: lint
lint:
	$(PYTHON) -m ruff check usl tests

.PHONY: format
format:
	$(PYTHON) -m ruff format usl tests

.PHONY: typecheck
typecheck:
	$(PYTHON) -m mypy usl

.PHONY: check
check: lint typecheck test

.PHONY: demo-list
demo-list:
	@echo "D1  Locked DuckDB file producing a stale run   demo/d1_locked_file.py"
	@echo "D2  404 season URL surfacing as a failed step  demo/d2_dead_url.py"
	@echo "D3  Club rename silently dropping rows         demo/d3_club_rename.py"
	@echo "D4  Null injected into a feature column        demo/d4_null_injection.py"
	@echo ""
	@echo "Working-behaviour demos (not failures):"
	@echo "    Idempotency, schema drift, duplicate rejection"
	@echo "See docs/phases/09-break-and-fix.md"

.PHONY: clean
clean:
	$(PYTHON) -c "import shutil,pathlib; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
	$(PYTHON) -c "import shutil; [shutil.rmtree(d, ignore_errors=True) for d in ['.pytest_cache','.ruff_cache','.mypy_cache','build','dist']]"

.PHONY: clean-db
clean-db:
	$(PYTHON) -c "import pathlib; [p.unlink() for p in pathlib.Path('data').glob('usl.duckdb*')]"
