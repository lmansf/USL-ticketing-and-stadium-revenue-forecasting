# usl/

The package. Ships as stubs: signatures, docstrings, type hints, and TODO markers
pointing at the doc that explains each one.

```
usl/
+-- config.py         Seasons, paths, feature lists, tunables. Judgement calls marked
+-- logging_setup.py  Run and check logging. A feature, not an afterthought
+-- db.py             DuckDB connection. Contains the one unguided exercise
+-- run.py            CLI. One command per stage, plus 'weekly'
+-- ingest/           footystats.py (API client), archive.py (durable raw store)
+-- scrape/           Attendance fallback only. Delete once the API is confirmed
+-- load/             raw.py - upsert into raw_matches
+-- sql/              The three-tier SQL layer, one .sql per model
+-- transform/        runner.py (materialise in order), checks.py (data quality)
+-- features/         definitions.py - the feature families and their evidence class
+-- models/           train.py, metrics.py
+-- export/           extracts.py - Tableau CSV and Hyper
+-- weather/          Phase two stubs, Open-Meteo
+-- ref/              Four hand-maintained CSVs. Code, not data
```

## Design notes worth knowing before you start

**The two models differ only by column selection.** `features/definitions.py` holds
the families; `models/train.py` selects from them. There is no second mart and no
second pipeline. That is what makes any difference in error attributable to the
pro-rel features rather than to a difference in the data.

**Checks are plain functions returning a `CheckResult`.** Not assertions. Two
reasons: every result gets logged whether it passed or failed, and the same body
becomes a Dagster asset check in phase two with only a decorator change. An
assertion cannot make that trip.

**Raw accumulates; everything below it is derived and disposable.** `raw_matches`
upserts, because it must not lose history the source no longer serves. Every SQL
model is `CREATE OR REPLACE`, because a full rebuild at this data size costs
nothing and is idempotent for free. Being able to explain that distinction is
worth more than either implementation.

**Every API response is archived before it is parsed.** `data/raw_archive/` is
committed and `data/usl.duckdb` is not, which is the opposite of the usual rule and
is deliberate: the subscription runs for one month, and the archive is the only copy
of the source data afterwards. With no API key set, the pipeline runs entirely from
it. See [phase 00](../docs/phases/00-data-access-and-the-clock.md).

**`db.py::connect_for_write` has no worked solution.** It is the one unguided
exercise. The contract is in the docstring; the strategy is yours.
