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
	@echo "  make install-mvp    Install the eight packages the MVP track uses"
	@echo "  make install        Install full-track runtime dependencies"
	@echo "  make install-dev    Install runtime + dev dependencies"
	@echo ""
	@echo "Pipeline"
	@echo "  make backfill       Load every season into raw_matches (from the archive)"
	@echo "  make ingest         Ingest the current season only (needs a live key)"
	@echo "  make archive        Report what data/raw_archive/ holds"
	@echo "  make league-list    List leagues and season ids (needs a key, or an archived response)"
	@echo "  make transform      Run the SQL layer: staging, intermediate, mart"
	@echo "  make train          Train both models, write metrics and importance"
	@echo "  make export         Write Tableau extracts to tableau/extracts/"
	@echo "  make weather        Phase two: match-day weather from Open-Meteo (needs USL_WEATHER_ENABLED=1)"
	@echo "  make weekly         The full Tuesday run: ingest, weather, transform, train, export"
	@echo "  make dagster        Phase two: the same pipeline as a Dagster asset graph (UI on :3000)"
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
	@echo "  make demo-d1        D1: the locked DuckDB file"
	@echo "  make demo-d2        D2: the failed API request"
	@echo "  make demo-d3        D3: the club rename that drops rows silently"
	@echo "  make demo-d4        D4: the null injected into a feature column"
	@echo "  make demo-working   Idempotency, schema drift, duplicate rejection - shown working"
	@echo ""
	@echo "Housekeeping"
	@echo "  make clean          Remove caches and build artifacts"
	@echo "  make clean-db       Delete the DuckDB file (rebuildable from scratch)"

.PHONY: install-mvp
install-mvp:
	$(PYTHON) -m pip install -r requirements-mvp.txt

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

.PHONY: ingest
ingest:
	$(PYTHON) -m usl.run ingest --db $(DB)

.PHONY: archive
archive:
	$(PYTHON) -m usl.run archive

.PHONY: league-list
league-list:
	$(PYTHON) -m usl.run league-list

.PHONY: transform
transform:
	$(PYTHON) -m usl.run transform --db $(DB)

.PHONY: train
train:
	$(PYTHON) -m usl.run train --db $(DB)

.PHONY: export
export:
	$(PYTHON) -m usl.run export --db $(DB)

.PHONY: weather
weather:
	$(PYTHON) -m usl.run weather --db $(DB)

.PHONY: weekly
weekly:
	$(PYTHON) -m usl.run weekly --db $(DB)

# Phase two. The same pipeline as an asset graph with run history and lineage:
# opens the Dagster UI on http://localhost:3000 with the weekly schedule defined.
.PHONY: install-dagster
install-dagster:
	$(PYTHON) -m pip install -e ".[dagster]"

.PHONY: dagster
dagster:
	$(PYTHON) -m dagster dev -m usl.defs

.PHONY: test
test:
	$(PYTHON) -m pytest

.PHONY: lint
lint:
	$(PYTHON) -m ruff check usl tests demo scripts

.PHONY: format
format:
	$(PYTHON) -m ruff format usl tests demo scripts

.PHONY: typecheck
typecheck:
	$(PYTHON) -m mypy usl

.PHONY: check
check: lint typecheck test

# Every demo runs from the committed archive with no key, writes only to a
# scratch copy of the database, and restores whatever it touched in a finally.
# See demo/README.md and docs/phases/09-break-and-fix.md.
.PHONY: demo-list
demo-list:
	@echo "Break and fix"
	@echo "  make demo-d1   D1  Locked DuckDB file: retry, then a message naming the holder   demo/d1_locked_file.py"
	@echo "  make demo-d2   D2  Failed API request: endpoint and status, never the URL        demo/d2_dead_url.py"
	@echo "  make demo-d3   D3  Club rename: the check names the string, the count shows the loss  demo/d3_club_rename.py"
	@echo "  make demo-d4   D4  Null in a feature column: the check fails the run before training  demo/d4_null_injection.py"
	@echo ""
	@echo "Demonstrate working, do not break (make demo-working runs all three)"
	@echo "  Idempotency          demo/show_idempotency.py"
	@echo "  Schema drift         demo/show_schema_drift.py"
	@echo "  Duplicate rejection  demo/show_duplicate_rejection.py"
	@echo ""
	@echo "See docs/phases/09-break-and-fix.md"

.PHONY: demo-d1
demo-d1:
	$(PYTHON) demo/d1_locked_file.py

.PHONY: demo-d2
demo-d2:
	$(PYTHON) demo/d2_dead_url.py

.PHONY: demo-d3
demo-d3:
	$(PYTHON) demo/d3_club_rename.py

.PHONY: demo-d4
demo-d4:
	$(PYTHON) demo/d4_null_injection.py

.PHONY: demo-working
demo-working:
	$(PYTHON) demo/show_idempotency.py
	$(PYTHON) demo/show_schema_drift.py
	$(PYTHON) demo/show_duplicate_rejection.py

.PHONY: clean
clean:
	$(PYTHON) -c "import shutil,pathlib; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__')]"
	$(PYTHON) -c "import shutil; [shutil.rmtree(d, ignore_errors=True) for d in ['.pytest_cache','.ruff_cache','.mypy_cache','build','dist']]"

.PHONY: clean-db
clean-db:
	$(PYTHON) -c "import pathlib; [p.unlink() for p in pathlib.Path('data').glob('usl.duckdb*')]"
