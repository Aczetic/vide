"""Screenplay parsing, covering the format variants real scripts arrive with.

Each fixture here was a case that broke an earlier version of the parser.
"""

from __future__ import annotations


import pytest

from vide.pipeline.ingest import clean_text, parse_screenplay



# -- heading variants --------------------------------------------------------


@pytest.mark.parametrize(
    ("heading", "int_ext", "location", "sub_area", "time", "cast"),
    [
        (
            "SC 1 – INT. HARLOW HOUSE – DAY – AVERY, DELIA, NOELLE",
            "INT", "HARLOW HOUSE", None, "DAY", ["AVERY", "DELIA", "NOELLE"],
        ),
        (
            "SCENE 2: EXT. HARLOW HOUSE – DRIVEWAY/LAWN – DAY – HARLOW, AVERY",
            "EXT", "HARLOW HOUSE", "DRIVEWAY/LAWN", "DAY", ["HARLOW", "AVERY"],
        ),
        (
            # En dash inside the INT/EXT token, which is also the field separator.
            "SCENE 1: EXT–INT. ROAD SIDE – CAR – DAY – MARIN, DEKKER",
            "INT/EXT", "ROAD SIDE", "CAR", "DAY", ["MARIN", "DEKKER"],
        ),
        (
            "SCENE 1/A: INT. DIRECTOR'S OFFICE – NIGHT",
            "INT", "DIRECTOR'S OFFICE", None, "NIGHT", [],
        ),
        (
            "SCENE 2A – INT. ANY LOCATION – NIGHT – COP",
            "INT", "ANY LOCATION", None, "NIGHT", ["COP"],
        ),
    ],
)
def test_heading_variants(heading, int_ext, location, sub_area, time, cast):
    parsed = parse_screenplay(f"EPISODE 1\n{heading}\nSome action line.\n")
    scene = parsed.scenes[0]
    assert scene.int_ext == int_ext
    assert scene.location_raw == location
    assert scene.sub_area_raw == sub_area
    assert scene.time_of_day == time
    assert scene.characters_raw == cast


@pytest.mark.parametrize(
    ("heading", "label"),
    [
        ("SCENE 2A – INT. X – DAY", "2A"),
        ("SCENE 2/A: INT. X – DAY", "2A"),
        ("SCENE 12 – INT. X – DAY", "12"),
    ],
)
def test_scene_number_labels(heading, label):
    parsed = parse_screenplay(f"EPISODE 1\n{heading}\nAction.\n")
    assert parsed.scenes[0].number_label == label


def test_placeholder_location_is_flagged():
    parsed = parse_screenplay("EPISODE 1\nSCENE 1 – INT. ANY LOCATION – DAY – COP\nAction.\n")
    assert any("placeholder" in g for g in parsed.scenes[0].gaps)


# -- dialogue ----------------------------------------------------------------


def test_delivery_cue_splits_action_from_emotion():
    parsed = parse_screenplay(
        "EPISODE 1\nSCENE 1 – INT. X – DAY – A\n"
        "AVERY (Snatches the bread, Dismissive): Peace?\n"
    )
    line = parsed.scenes[0].dialogue[0]
    assert line.speaker_raw == "AVERY"
    assert line.delivery_action == "Snatches the bread"
    assert line.delivery_emotion == "Dismissive"
    assert line.line == "Peace?"


def test_two_emotions_yield_no_action():
    """`(Hurt, Confused)` is two emotions, not an action plus an emotion."""
    parsed = parse_screenplay(
        "EPISODE 1\nSCENE 1 – INT. X – DAY – A\nNOELLE (Hurt, Confused): Why?\n"
    )
    line = parsed.scenes[0].dialogue[0]
    assert line.delivery_action is None
    assert line.delivery_emotion == "Hurt, Confused"
    assert line.delivery_raw == "Hurt, Confused"


def test_dialogue_without_a_cue():
    parsed = parse_screenplay("EPISODE 1\nSCENE 1 – INT. X – DAY – A\nHARLOW: Get out.\n")
    line = parsed.scenes[0].dialogue[0]
    assert line.speaker_raw == "HARLOW"
    assert line.delivery_raw is None


def test_action_lines_are_not_mistaken_for_dialogue():
    parsed = parse_screenplay(
        "EPISODE 1\nSCENE 1 – INT. X – DAY – A\n"
        "Avery turns sharply and strides toward the main door.\n"
        "AVERY (Curt): Alright.\n"
    )
    scene = parsed.scenes[0]
    assert len(scene.action_lines) == 1
    assert len(scene.dialogue) == 1


# -- structure ---------------------------------------------------------------


def test_intercut_pair_shares_a_group():
    """Two scenes with the same number across INT CUT play simultaneously."""
    parsed = parse_screenplay(
        "EPISODE 4\n"
        "SCENE 1: INT. HOTEL ROOM – NIGHT – AVERY, ODETTE\n"
        "They lie in bed.\n"
        "INT CUT\n"
        "SCENE 1: INT. HARLOW HOUSE – NIGHT – HARLOW\n"
        "Harlow dials.\n"
    )
    a, b = parsed.scenes
    assert a.intercut_group is not None
    assert a.intercut_group == b.intercut_group
    assert a.location_raw != b.location_raw


def test_missing_episode_is_flagged():
    parsed = parse_screenplay(
        "EPISODE 1\nSCENE 1 – INT. X – DAY – A\nAction.\n"
        "EPISODE 3\nSCENE 1 – INT. Y – DAY – A\nAction.\n"
    )
    assert any("[2]" in g for g in parsed.gaps)


def test_time_jump_is_recorded():
    parsed = parse_screenplay(
        "EPISODE 1\nSCENE 1 – INT. X – DAY – A\nAction.\n"
        "ONE WEEK LATER\n"
        "SCENE 2 – INT. X – DAY – A\nAction.\n"
    )
    after = parsed.scenes[1]
    assert after.time_jump_before == "ONE WEEK LATER"
    assert any("time jump" in g for g in after.gaps)
    # and not on the scene before it
    assert parsed.scenes[0].time_jump_before is None


def test_transition_is_captured():
    parsed = parse_screenplay(
        "EPISODE 1\nSCENE 1 – INT. X – DAY – A\nAction.\nFREEZE\n"
    )
    assert parsed.scenes[0].transition_out == "FREEZE"


# -- normalisation -----------------------------------------------------------


def test_clean_text_preserves_the_en_dash_separator():
    """Collapsing en dashes to hyphens would destroy every scene heading."""
    assert "–" in clean_text("SCENE 1 – INT. X – DAY")


def test_clean_text_normalises_curly_quotes():
    assert clean_text("DIRECTOR’S OFFICE") == "DIRECTOR'S OFFICE\n"


# -- the real screenplay -----------------------------------------------------





def test_scene_uids_are_unique_across_intercut_pairs():
    """Episode + scene number is NOT unique — an intercut pair shares a number.

    Keying on it collapses two different scenes into one and misattributes
    every location, character and costume in the second.
    """
    parsed = parse_screenplay(
        "EPISODE 4\n"
        "SCENE 1: INT. HOTEL ROOM – NIGHT – AVERY\n"
        "They lie in bed.\n"
        "INT CUT\n"
        "SCENE 1: INT. HARLOW HOUSE – NIGHT – HARLOW\n"
        "Harlow dials.\n"
    )
    a, b = parsed.scenes
    assert a.uid != b.uid
    assert a.uid == "4:1"
    assert b.uid == "4:1b"

