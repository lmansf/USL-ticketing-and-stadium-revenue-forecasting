"""match_id stability and uniqueness.

Two namespaces: 'fs:' + the provider's id when the frame carries one, and the
natural key 'nk:' + sha1(season|date|home_raw|away_raw)[:16] when it does not.
The first three tests exercise the fallback on tiny_season, which has no id.

Doc: docs/phases/01-ingest-to-raw.md, exercise 1.2
"""

from __future__ import annotations

import hashlib

import pandas as pd
import pytest

from usl.ingest.footystats import add_match_id

RENAMES = {"home_club_id": "home_raw", "away_club_id": "away_raw"}


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


def test_natural_key_is_pinned_not_positional(tiny_season: pd.DataFrame) -> None:
    """The nk hash is a function of the four fields, not of row order or index.

    Pinning the digest for one row means a change to the recipe - a different
    separator, a different field order - fails here rather than silently
    turning every stored row into an insert on the next load.
    """
    expected = "nk:" + hashlib.sha1(b"2024|2024-03-02|club_a|club_b").hexdigest()[:16]
    forward = add_match_id(tiny_season.rename(columns=RENAMES))
    shuffled = add_match_id(tiny_season.rename(columns=RENAMES).iloc[::-1].reset_index(drop=True))

    assert forward.loc[forward["match_id"] == expected, "home_raw"].tolist() == ["club_a"]
    assert sorted(forward["match_id"]) == sorted(shuffled["match_id"])


def test_provider_id_gets_the_fs_namespace() -> None:
    """The API's own id, as text, behind 'fs:'."""
    out = add_match_id(pd.DataFrame({"id": [453873, 453874], "homeID": [149, 157]}))
    assert list(out["match_id"]) == ["fs:453873", "fs:453874"]
    assert out["match_id"].is_unique


def test_provider_id_wins_over_the_natural_key(tiny_season: pd.DataFrame) -> None:
    """A frame carrying both keys uses the provider id: it survives a rebrand, the hash does not."""
    df = tiny_season.rename(columns=RENAMES).assign(id=range(1, 7))
    assert add_match_id(df)["match_id"].str.startswith("fs:").all()


def test_namespaces_cannot_collide(tiny_season: pd.DataFrame) -> None:
    """An fs: id and an nk: id are never equal, whatever the underlying text."""
    natural = add_match_id(tiny_season.rename(columns=RENAMES))["match_id"]
    provider = add_match_id(pd.DataFrame({"id": list(range(1, 7))}))["match_id"]

    assert natural.str.startswith("nk:").all()
    assert provider.str.startswith("fs:").all()
    assert set(natural).isdisjoint(set(provider))

    # Even a provider id whose text equals a natural-key digest stays apart.
    digest = natural.iloc[0][len("nk:") :]
    same_text = add_match_id(pd.DataFrame({"id": [digest]}))["match_id"].iloc[0]
    assert same_text == "fs:" + digest
    assert same_text != natural.iloc[0]


def test_float_upcast_provider_id_is_stable() -> None:
    """pandas turns an int column with a gap into floats; 453873.0 must still be fs:453873."""
    out = add_match_id(pd.DataFrame({"id": [453873.0]}))
    assert out["match_id"].iloc[0] == "fs:453873"


def test_null_provider_id_is_rejected() -> None:
    """A null id would hash every such row to the same key and silently merge matches."""
    with pytest.raises(ValueError) as exc:
        add_match_id(pd.DataFrame({"id": [1, None]}))
    assert "null" in str(exc.value)


def test_frame_with_neither_key_raises_naming_what_is_needed() -> None:
    """Neither an id nor the four natural-key columns: an error, not a guess."""
    with pytest.raises(ValueError) as exc:
        add_match_id(pd.DataFrame({"season": [2024], "date": ["2024-03-02"]}))
    message = str(exc.value)
    assert "'id'" in message
    assert "home_raw" in message and "away_raw" in message


def test_add_match_id_does_not_mutate_its_input(tiny_season: pd.DataFrame) -> None:
    """A copy comes back; the caller's frame keeps whatever ids it had."""
    df = tiny_season.rename(columns=RENAMES).drop(columns=["match_id"])
    out = add_match_id(df)
    assert out["match_id"].str.startswith("nk:").all()
    assert "match_id" not in df.columns

    # And a frame that already carries a match_id has it replaced on the copy only.
    fixture = tiny_season.rename(columns=RENAMES)
    replaced = add_match_id(fixture)
    assert replaced["match_id"].str.startswith("nk:").all()
    assert list(fixture["match_id"]) == ["m1", "m2", "m3", "m4", "m5", "m6"]
