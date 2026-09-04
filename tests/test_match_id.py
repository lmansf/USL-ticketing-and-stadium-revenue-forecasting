"""match_id stability and uniqueness.

Doc: docs/phases/01-ingest-to-raw.md, exercise 1.2
"""

from __future__ import annotations

import pandas as pd

from usl.scrape.parse import add_match_id


def test_match_id_is_stable_across_reparses(tiny_season: pd.DataFrame) -> None:
    """The same input must produce the same ids. Without this, nothing upserts."""
    renames = {"home_club_id": "home_raw", "away_club_id": "away_raw"}
    a = add_match_id(tiny_season.rename(columns=renames))
    b = add_match_id(tiny_season.rename(columns=renames))
    assert list(a["match_id"]) == list(b["match_id"])


def test_match_id_is_unique_within_a_season(tiny_season: pd.DataFrame) -> None:
    """Two matches in one season must not collide.

    The natural key is season, date, home, away. A collision means the key is
    wrong, and the symptom downstream is an upsert quietly overwriting a real
    match with a different one.
    """
    renames = {"home_club_id": "home_raw", "away_club_id": "away_raw"}
    df = add_match_id(tiny_season.rename(columns=renames))
    assert df["match_id"].is_unique


def test_match_id_distinguishes_reverse_fixtures(tiny_season: pd.DataFrame) -> None:
    """A at home to B is a different match from B at home to A.

    A key that sorts the two club names would collide these, and they have
    different attendance - which is the entire subject of this project.
    """
    df = tiny_season.rename(columns={"home_club_id": "home_raw", "away_club_id": "away_raw"})
    forward = add_match_id(df.iloc[[0]].copy())["match_id"].iloc[0]
    reversed_ = df.iloc[[0]].copy()
    reversed_[["home_raw", "away_raw"]] = reversed_[["away_raw", "home_raw"]].to_numpy()
    assert add_match_id(reversed_)["match_id"].iloc[0] != forward
