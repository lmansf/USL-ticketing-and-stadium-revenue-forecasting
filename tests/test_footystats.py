"""FootyStats client: archiving, key handling, and schema drift.

Runs against a committed example-key fixture, so it needs no subscription and
keeps working forever.

Doc: docs/phases/00-data-access-and-the-clock.md
     docs/phases/01-ingest-to-raw.md
"""

from __future__ import annotations

import pytest

from usl.ingest.footystats import SchemaDriftError, parse_season_matches


def test_response_is_archived_before_parsing(tmp_path) -> None:
    """A malformed payload must still land on disk.

    The whole point of archive-before-parse: a JSONDecodeError costs you a
    debugging session, not a request you cannot get back.
    """
    pytest.skip("TODO: feed the client a body that fails to parse, assert the file exists")


def test_archived_request_is_not_refetched(tmp_path) -> None:
    """During the subscription, never spend a request twice."""
    pytest.skip("TODO")


def test_runs_from_archive_with_no_api_key(tmp_path) -> None:
    """The acceptance test for the whole data-access phase.

    With no key set, an archived request must still be served. If this passes,
    the subscription can lapse and the project survives - and anyone cloning the
    repo can run it without paying anything.
    """
    pytest.skip("TODO: unset the key, assert an archived request still resolves")


def test_unarchived_request_without_key_raises_named_error(tmp_path) -> None:
    """The one genuinely bad case, made legible.

    A code path asking for something nobody pulled, discovered after access is
    gone. NoSubscriptionError says that; a bare 401 does not.
    """
    pytest.skip("TODO")


def test_api_key_never_appears_in_archive_filenames(tmp_path) -> None:
    """The archive goes into git. The key must not travel with it."""
    pytest.skip("TODO")


def test_api_key_never_appears_in_logs(caplog) -> None:
    """The key is in every request URL, so a logger recording full URLs leaks it.

    logs/ is gitignored, but one careless 'git add -f' is all it takes, and a
    paid credential in a public repo is someone else's month on your card.
    """
    pytest.skip("TODO: assert no emitted record contains the key")


def test_missing_required_field_raises_naming_both_sides() -> None:
    """JSON removes the positional-column trap but not schema drift.

    The match-detail endpoint is undocumented, so it carries no versioning
    promise and will not announce a field-set change.
    """
    with pytest.raises(SchemaDriftError) as exc:
        parse_season_matches({"data": [{"id": 1}]}, season_id=1625)
    assert "homeID" in str(exc.value), "must name what is missing"


def test_extra_fields_are_kept_not_dropped() -> None:
    """A field discarded inside the 30-day window cannot be recovered outside it."""
    pytest.skip("TODO")


def test_match_id_is_namespaced() -> None:
    """'fs:' prefix, so a second source cannot collide and a log line says where
    an id came from."""
    pytest.skip("TODO: exercise usl.ingest.footystats.add_match_id")


@pytest.mark.fixture_required
def test_parses_committed_example_fixture() -> None:
    """A real archived example-key response parses to the expected row count.

    The EPL 2018/19 season has 380 matches, which is a number you can check
    against the published season rather than against your own parser.
    """
    pytest.skip("TODO: archive league-matches season 1625 and pin its row count")
