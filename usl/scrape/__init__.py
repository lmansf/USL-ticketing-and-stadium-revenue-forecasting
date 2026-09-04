"""Attendance fallback scraper. DEMOTED - may be dead code.

The primary source is now the FootyStats API (usl/ingest/). This package
survives only because one question is open: does the API return per-match
attendance for USL Championship?

  - If it does, this package is dead. Delete it, drop lxml and beautifulsoup4
    from requirements, and delete the scraper branch from phase 01.
  - If it does not, this supplies attendance and the API supplies everything
    else, joined on season + date + club. The two sources share no key.

Resolve it on day one of the subscription and delete whichever branch loses.
Carrying both "just in case" is how a project ends up with two half-maintained
ingest paths.

See docs/phases/00-data-access-and-the-clock.md#the-open-question-you-must-resolve-on-day-one
"""
