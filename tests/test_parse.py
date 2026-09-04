"""Parser and schema-drift guard.

Doc: docs/phases/01-ingest-to-raw.md, exercise 1.1
"""

from __future__ import annotations

import pytest

from usl.scrape.parse import SchemaDriftError, normalize_column, parse_season, pick_match_table


def test_normalize_column_collapses_whitespace_and_case() -> None:
    """Header text varies in whitespace and case across pages of the same site."""
    assert normalize_column("  Attendance ") == "attendance"
    assert normalize_column("Home\nTeam") == "home team"


def test_missing_column_raises_naming_both_sides() -> None:
    """A missing expected column must raise, and the message must name what it found.

    The message is the deliverable. A bare KeyError tells you nothing about what
    the page now looks like, which is the only thing you need to fix it.
    """
    html = "<table><tr><th>date</th><th>home</th></tr><tr><td>x</td><td>y</td></tr></table>"
    with pytest.raises(SchemaDriftError) as exc:
        parse_season(html, season=2024, source_url="http://example.invalid")
    message = str(exc.value)
    assert "attendance" in message, "must name what is missing"
    assert "date" in message, "must name what it found"


def test_extra_column_warns_but_does_not_raise(caplog: pytest.LogCaptureFixture) -> None:
    """The site adding a column must not break Tuesday's run, but must be logged.

    This asymmetry - missing raises, extra warns - is the design.
    """
    pytest.skip("TODO: build a fixture with an extra column and assert on the warning")


@pytest.mark.fixture_required
def test_parses_saved_fixture() -> None:
    """A saved page from the live site parses to the expected row count.

    Save one real season page into demo/fixtures/ and pin its row count here.
    That number is checkable against the published season length.
    """
    pytest.skip("TODO: save a fixture into demo/fixtures/ and assert on it")


def test_pick_match_table_rejects_ambiguity() -> None:
    """Selecting by position is the same trap as reading columns by position.

    Zero matching tables and two matching tables are both failures, and both
    should say so rather than silently returning the first thing they found.
    """
    with pytest.raises(SchemaDriftError):
        pick_match_table([])
