# Fixtures

Saved HTML pages, so no test and no demo depends on the live site being up or
unchanged. Commit these.

| File | Purpose |
|---|---|
| `season_<year>.html` | A real saved season page. Parser tests pin its row count |
| `season_<year>_drifted.html` | The same page with one column header renamed. Feeds `show_schema_drift.py` and `tests/test_parse.py` |

Both are TODO - save them once you have a working fetch, and keep them out of
`data/cache/`, which is gitignored and gets cleared.

Saving a page is not scraping it repeatedly. One saved copy, committed, is
politer to the source than a test suite that fetches on every run.
