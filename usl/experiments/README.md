# Experiments

Working folders for running the guide's steps, one per step. A folder here is a
workspace with a findings log, not a copy of the guide - the reference docs live under
[`docs/`](../../docs/) and win any disagreement.

| Folder | Step | Guide |
|---|---|---|
| `MVP 1` | First MVP pass. Holds the MVP 02 SQL and features step as one runnable file, plus the scripts that first pulled and inspected the example season | [docs/mvp/02](../../docs/mvp/02-mvp-sql-and-features.md) |

**What happened next.** The full track was then built in the package proper - every
stub under `usl/`, every test, the CLI, the demos - against the same example season,
and the archived response moved to `data/raw_archive/` where phase 00 says it
belongs. The scripts here still run against it at that path. What was decided along
the way is in [docs/reference/build-decisions.md](../../docs/reference/build-decisions.md);
the findings below were the input to those decisions.

Each page ends with a **Findings** section. That is the point of the folder: the guide
tells you what to do, and the findings record what actually happened - the real field
names, the attendance coverage numbers, the row counts, what surprised you. Those are
the things you cannot reconstruct later and will want when writing the README and the
delivery email.

## A note on the location

This sits inside the `usl/` Python package directory, which is slightly unusual - it is
documentation and scratch work rather than importable code. It is here because that is
where the folder was asked for.

It does not break packaging: `setuptools` discovers packages by looking for
`__init__.py`, and there is none here, so `usl.experiments` is not importable and the
space in `MVP 1` never has to be a valid Python identifier. If you later want these
folders to hold runnable modules, move them to a top-level `experiments/` instead of
adding `__init__.py` files - a directory name with a space cannot be imported.
