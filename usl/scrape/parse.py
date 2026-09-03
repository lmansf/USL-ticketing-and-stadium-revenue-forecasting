"""HTML to raw DataFrames.

Land it raw: no cleaning, no type coercion, no renaming. One row per match, as
scraped, plus scraped_at and source_url. Cleaning happens in SQL, where it is
reviewable in a diff and re-runnable without re-fetching.

See docs/phases/01-scrape-to-raw.md
"""

from __future__ import annotations

import pandas as pd


class SchemaDriftError(RuntimeError):
    """The source page no longer has the columns the parser expects.

    The message must name what was found as well as what was missing. A bare
    KeyError tells you nothing about what the page now looks like, which is the
    only thing you need to know to fix it.
    """


def normalize_column(name: str) -> str:
    """Normalise one scraped column header for matching against EXPECTED_COLUMNS.

    Args:
        name: Raw header text as scraped.

    Returns:
        Lowercased, whitespace-collapsed name.

    TODO: implement.
    """
    raise NotImplementedError("TODO")


def pick_match_table(tables: list[pd.DataFrame]) -> pd.DataFrame:
    """Select the match-list table from the tables on a season page.

    A season page carries several tables - navigation, standings, the fixture
    list. Selecting by index is the same trap as reading columns by position:
    it works until the page changes and then it is silently wrong.

    Args:
        tables: Every table parsed from the page.

    Returns:
        The one holding match rows.

    Raises:
        SchemaDriftError: If no table matches, or more than one does.

    TODO: implement. Select on something structural - the presence of the
    expected column names - rather than on position.
    """
    raise NotImplementedError("TODO: see docs/phases/01-scrape-to-raw.md")


def parse_season(html: str, season: int, source_url: str) -> pd.DataFrame:
    """Parse one season's page into a raw match DataFrame.

    Validates the shape by column NAME, not position. Missing columns raise;
    extra columns warn and are ignored - the site adding a column should not
    break Tuesday's run, but you want to know it happened. That asymmetry is the
    design, and it is exercise 1.1.

    Args:
        html: Page HTML.
        season: Four-digit season year, stamped onto every row.
        source_url: Stamped onto every row for provenance.

    Returns:
        One row per match with the raw scraped columns plus season, scraped_at,
        and source_url. No type coercion.

    Raises:
        SchemaDriftError: When an expected column is absent.

    TODO: implement. See docs/phases/01-scrape-to-raw.md, exercise 1.1.
    """
    raise NotImplementedError("TODO: see docs/phases/01-scrape-to-raw.md, exercise 1.1")


def add_match_id(df: pd.DataFrame) -> pd.DataFrame:
    """Add a stable match_id derived from the natural key.

    Natural key: season, date, home club, away club. Hash it for a compact id.

    Note this hashes the RAW club strings, not canonical ids - the id has to be
    computable at load time, before the alias mapping in phase 03 has run. The
    consequence is that a rename in the source changes historical match_ids for
    that club. That is a known trade-off, discussed in
    docs/reference/open-questions.md#match_id-and-rebrands.

    Args:
        df: Raw parsed matches.

    Returns:
        The same frame with a match_id column added.

    TODO: implement. See docs/phases/01-scrape-to-raw.md, exercise 1.2.
    """
    raise NotImplementedError("TODO: see docs/phases/01-scrape-to-raw.md, exercise 1.2")
