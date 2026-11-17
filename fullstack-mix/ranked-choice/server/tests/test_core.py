"""Tests for the tabulation core: parsing, counting, pairwise analysis."""

import pytest

import core
from core import (
    Ballot,
    InputError,
    condorcet_winner,
    derive_candidates,
    pairwise_matrix,
    parse_ballots,
    parse_candidates,
    run,
    tabulate,
)

ROSTER = ["Aditi", "Bharat", "Chen"]


def count(text: str, candidates=None) -> dict:
    """Parse and tabulate in one step, the way the API does."""
    return run(text, ROSTER if candidates is None else candidates)


# ---------------------------------------------------------------------------
# Candidate rosters
# ---------------------------------------------------------------------------


def test_candidates_from_a_list_keep_declaration_order():
    assert parse_candidates(["Chen", "Aditi", "Bharat"]) == ["Chen", "Aditi", "Bharat"]


def test_candidates_from_a_string_accept_commas_and_newlines():
    assert parse_candidates("Chen, Aditi\nBharat") == ["Chen", "Aditi", "Bharat"]


def test_blank_candidate_entries_are_skipped():
    assert parse_candidates("Aditi, , Bharat") == ["Aditi", "Bharat"]


def test_candidate_names_are_whitespace_collapsed():
    assert parse_candidates(["  Aditi   Rao "]) == ["Aditi Rao"]


def test_duplicate_candidate_is_rejected_case_insensitively():
    with pytest.raises(InputError, match="declared twice"):
        parse_candidates(["Aditi", "aditi"])


@pytest.mark.parametrize("bad", ["A > B", "A, B", "A: B"])
def test_reserved_characters_are_rejected(bad):
    with pytest.raises(InputError):
        parse_candidates([bad])


def test_candidate_name_length_is_bounded():
    with pytest.raises(InputError, match="longer than"):
        parse_candidates(["x" * (core.MAX_NAME_LENGTH + 1)])


def test_candidate_count_is_bounded():
    with pytest.raises(InputError, match="at most"):
        parse_candidates([f"Candidate {index}" for index in range(core.MAX_CANDIDATES + 1)])


def test_non_string_candidate_is_rejected():
    with pytest.raises(InputError, match="must be a string"):
        parse_candidates(["Aditi", 7])


def test_candidates_of_an_unsupported_type_are_rejected():
    with pytest.raises(InputError, match="expected a list"):
        parse_candidates({"Aditi": 1})


# ---------------------------------------------------------------------------
# Ballot parsing
# ---------------------------------------------------------------------------


def test_ballots_accept_both_separators():
    ballots, names = parse_ballots("Aditi > Bharat > Chen\nAditi, Chen, Bharat", ROSTER)
    assert names == ROSTER
    assert ballots[0].ranking == ("Aditi", "Bharat", "Chen")
    assert ballots[1].ranking == ("Aditi", "Chen", "Bharat")


def test_ballots_ignore_comments_and_blank_lines():
    ballots, _ = parse_ballots("# a comment\n\nAditi > Bharat\n   \n# another", ROSTER)
    assert len(ballots) == 1
    assert ballots[0].line == 3


def test_ballots_match_the_roster_case_insensitively():
    ballots, _ = parse_ballots("ADITI > bharat", ROSTER)
    assert ballots[0].ranking == ("Aditi", "Bharat")


def test_ballot_weight_prefix_repeats_the_ballot():
    ballots, _ = parse_ballots("3: Aditi > Bharat", ROSTER)
    assert len(ballots) == 1
    assert ballots[0].weight == 3


def test_zero_weight_is_rejected():
    with pytest.raises(InputError, match="at least 1"):
        parse_ballots("0: Aditi", ROSTER)


def test_oversized_weight_is_rejected():
    with pytest.raises(InputError, match="may not exceed"):
        parse_ballots(f"{core.MAX_WEIGHT + 1}: Aditi", ROSTER)


def test_weight_prefix_without_candidates_is_rejected():
    with pytest.raises(InputError, match="no candidates"):
        parse_ballots("4:", ROSTER)


def test_unknown_candidate_names_the_line():
    with pytest.raises(InputError, match=r"line 2: 'Divya' is not a declared candidate"):
        parse_ballots("Aditi > Bharat\nAditi > Divya", ROSTER)


def test_a_candidate_ranked_twice_is_rejected():
    with pytest.raises(InputError, match="ranked twice"):
        parse_ballots("Aditi > Bharat > Aditi", ROSTER)


def test_empty_entry_between_separators_is_rejected():
    with pytest.raises(InputError, match="empty entry"):
        parse_ballots("Aditi >> Bharat", ROSTER)


def test_a_bare_separator_is_rejected():
    with pytest.raises(InputError, match="empty entry"):
        parse_ballots(">", ROSTER)


def test_ballots_must_be_a_string():
    with pytest.raises(InputError, match="expected a string"):
        parse_ballots(["Aditi"], ROSTER)


def test_roster_is_derived_from_ballots_when_omitted():
    ballots, names = parse_ballots("Chen > aditi\nBharat > Chen")
    assert names == ["Chen", "aditi", "Bharat"]
    assert ballots[0].ranking == ("Chen", "aditi")


def test_derive_candidates_reports_first_appearance_order():
    ballots = [
        Ballot(("Chen", "Aditi")),
        Ballot(("Bharat", "Chen")),
    ]
    assert derive_candidates(ballots) == ["Chen", "Aditi", "Bharat"]


# ---------------------------------------------------------------------------
# First-round results
# ---------------------------------------------------------------------------


def test_outright_first_round_majority_wins_immediately():
    report = count("Aditi > Bharat\nAditi > Chen\nAditi\nBharat > Aditi\nChen > Aditi")
    assert report["winner"] == "Aditi"
    assert report["reason"] == "majority"
    assert len(report["rounds"]) == 1
    assert report["rounds"][0]["majority_needed"] == 3


def test_majority_is_strict_not_plurality():
    # 2 of 4 is exactly half: no majority, so the count continues.
    report = count("Aditi > Bharat\nAditi > Bharat\nBharat > Aditi\nChen > Aditi")
    assert report["rounds"][0]["tally"] == {"Aditi": 2, "Bharat": 1, "Chen": 1}
    assert report["rounds"][0]["majority_needed"] == 3
    assert report["winner"] == "Aditi"


def test_transfer_of_an_eliminated_candidate_decides_the_winner():
    text = "\n".join(
        [
            "Aditi > Bharat",
            "Bharat > Aditi",
            "Bharat > Aditi",
            "Chen > Bharat",
            "Chen > Bharat",
        ]
    )
    report = count(text)
    assert report["rounds"][0]["tally"] == {"Aditi": 1, "Bharat": 2, "Chen": 2}
    assert report["rounds"][0]["eliminated"] == "Aditi"
    assert report["rounds"][1]["tally"] == {"Bharat": 3, "Chen": 2}
    assert report["winner"] == "Bharat"


def test_skipped_preferences_transfer_to_the_next_living_choice():
    # Bharat's voters put Chen second, but Chen is already out, so their
    # ballots skip a dead preference and land on Aditi third.
    text = "\n".join(
        [
            "Chen > Aditi",
            "Bharat > Chen > Aditi",
            "Bharat > Chen > Aditi",
            "Aditi > Bharat",
            "Aditi > Bharat",
            "Aditi > Bharat",
            "Divya > Aditi",
            "Divya > Aditi",
        ]
    )
    report = count(text, ["Aditi", "Bharat", "Chen", "Divya"])
    assert report["rounds"][0]["eliminated"] == "Chen"
    assert report["rounds"][1]["eliminated"] == "Bharat"
    assert report["rounds"][2]["tally"] == {"Aditi": 6, "Divya": 2}
    assert report["winner"] == "Aditi"


# ---------------------------------------------------------------------------
# Exhaustion
# ---------------------------------------------------------------------------


def test_exhausted_ballots_lower_the_majority_bar():
    text = "\n".join(
        [
            "Aditi > Bharat",
            "Aditi > Bharat",
            "Bharat > Aditi",
            "Bharat > Aditi",
            "Chen",
        ]
    )
    report = count(text)
    first, second = report["rounds"][0], report["rounds"][1]
    assert first["majority_needed"] == 3
    assert first["eliminated"] == "Chen"
    assert second["exhausted"] == 1
    assert second["active_votes"] == 4
    # Four continuing votes need three to win; two apiece is still a tie.
    assert second["majority_needed"] == 3
    assert report["winner"] is None
    assert report["reason"] == "tie"
    assert report["tied"] == ["Aditi", "Bharat"]


def test_active_and_exhausted_always_sum_to_the_total_weight():
    text = "\n".join(
        [
            "3: Aditi > Bharat",
            "2: Bharat > Chen",
            "1: Chen",
            "1: Bharat > Aditi > Chen",
        ]
    )
    report = count(text)
    total = report["ballots"]["weight"]
    assert total == 7
    for record in report["rounds"]:
        assert record["active_votes"] + record["exhausted"] == total


# ---------------------------------------------------------------------------
# Ties and tie-breaks
# ---------------------------------------------------------------------------


def test_a_tie_for_last_place_eliminates_one_candidate_and_says_so():
    text = "Aditi > Bharat\nAditi > Bharat\nBharat > Aditi\nChen > Aditi"
    report = count(text)
    first = report["rounds"][0]
    assert first["tally"] == {"Aditi": 2, "Bharat": 1, "Chen": 1}
    # Nobody is last alone: the record shows the whole tie, in break order.
    assert first["tie_break"] == ["Bharat", "Chen"]
    assert first["eliminated"] == "Bharat"
    assert report["winner"] == "Aditi"
    assert report["reason"] == "majority"


def test_tie_break_prefers_the_candidate_with_fewest_first_preferences():
    # Round two leaves Bharat and Chen tied on three, but Bharat started with
    # three first choices to Chen's two, so Chen goes out. Alphabetical order
    # alone would have picked Bharat, so this pins the actual rule.
    text = "\n".join(
        [
            "Aditi > Bharat",
            "Aditi > Bharat",
            "Aditi > Bharat",
            "Aditi > Bharat",
            "Bharat > Aditi",
            "Bharat > Aditi",
            "Bharat > Aditi",
            "Chen > Bharat",
            "Chen > Bharat",
            "Divya > Chen",
        ]
    )
    report = count(text, ["Aditi", "Bharat", "Chen", "Divya"])
    assert report["rounds"][0]["eliminated"] == "Divya"
    assert report["rounds"][1]["tally"] == {"Aditi": 4, "Bharat": 3, "Chen": 3}
    assert report["rounds"][1]["tie_break"] == ["Chen", "Bharat"]
    assert report["rounds"][1]["eliminated"] == "Chen"
    assert report["winner"] == "Bharat"


def test_a_dead_heat_ends_without_a_winner():
    report = count("Aditi > Bharat\nBharat > Aditi")
    assert report["winner"] is None
    assert report["reason"] == "tie"
    assert report["tied"] == ["Aditi", "Bharat"]
    assert report["rounds"][-1]["tie_break"] == ["Aditi", "Bharat"]


def test_three_way_dead_heat_is_reported_sorted():
    report = count("Aditi > Bharat\nBharat > Chen\nChen > Aditi")
    assert report["winner"] is None
    assert report["reason"] == "tie"
    assert report["tied"] == ["Aditi", "Bharat", "Chen"]


# ---------------------------------------------------------------------------
# Small and weighted elections
# ---------------------------------------------------------------------------


def test_a_single_candidate_wins_as_the_last_one_standing():
    report = run("Aditi\nAditi", ["Aditi"])
    assert report["winner"] == "Aditi"
    assert report["reason"] == "last_standing"
    assert len(report["rounds"]) == 1


def test_weights_are_counted_as_votes():
    report = count("4: Aditi > Bharat\n3: Bharat > Aditi\n2: Chen > Bharat")
    assert report["ballots"]["lines"] == 3
    assert report["ballots"]["weight"] == 9
    assert report["rounds"][0]["tally"] == {"Aditi": 4, "Bharat": 3, "Chen": 2}
    assert report["rounds"][0]["majority_needed"] == 5
    assert report["rounds"][0]["eliminated"] == "Chen"
    assert report["rounds"][1]["tally"] == {"Aditi": 4, "Bharat": 5}
    assert report["winner"] == "Bharat"


def test_distinct_rankings_are_counted():
    report = count("Aditi > Bharat\nAditi > Bharat\nBharat > Aditi")
    assert report["ballots"]["distinct_rankings"] == 2


def test_ballot_lines_are_reported():
    report = count("Aditi > Bharat\n\n# note\nBharat")
    assert report["ballots"]["lines"] == 2


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------


def test_no_ballots_is_an_input_error():
    with pytest.raises(InputError, match="no ballots"):
        run("# only a comment", ROSTER)


def test_empty_input_is_an_input_error():
    with pytest.raises(InputError, match="nothing to count"):
        run("", [])


def test_a_ballot_naming_an_undeclared_candidate_is_rejected_by_tabulate():
    with pytest.raises(InputError, match="not a declared candidate"):
        tabulate(ROSTER, [Ballot(("Aditi", "Divya"))])


# ---------------------------------------------------------------------------
# Pairwise analysis
# ---------------------------------------------------------------------------


def test_pairwise_matrix_counts_both_directions():
    ballots, names = parse_ballots("Aditi > Bharat\nBharat > Aditi\nAditi > Bharat", ROSTER)
    matrix = pairwise_matrix(names, ballots)
    assert matrix["Aditi"]["Bharat"] == 2
    assert matrix["Bharat"]["Aditi"] == 1
    assert matrix["Aditi"]["Chen"] == 0


def test_pairwise_matrix_ignores_ballots_that_rank_only_one_of_a_pair():
    ballots, names = parse_ballots("Aditi\nAditi > Chen", ROSTER)
    matrix = pairwise_matrix(names, ballots)
    assert matrix["Aditi"]["Chen"] == 1
    assert matrix["Chen"]["Aditi"] == 0
    assert matrix["Aditi"]["Bharat"] == 0


def test_pairwise_matrix_respects_weights():
    ballots, names = parse_ballots("5: Aditi > Bharat", ROSTER)
    matrix = pairwise_matrix(names, ballots)
    assert matrix["Aditi"]["Bharat"] == 5


def test_condorcet_winner_is_found_when_one_exists():
    roster = ["Aditi", "Bharat"]
    ballots, names = parse_ballots("Aditi > Bharat\nAditi > Bharat\nBharat > Aditi", roster)
    assert condorcet_winner(names, pairwise_matrix(names, ballots)) == "Aditi"


def test_an_unranked_candidate_denies_everyone_the_condorcet_crown():
    # Aditi is never compared head-to-head with Chen, so nobody beats "every
    # other candidate" and the report stays honest about it.
    ballots, names = parse_ballots("Aditi > Bharat\nBharat > Aditi", ROSTER)
    assert condorcet_winner(names, pairwise_matrix(names, ballots)) is None


def test_condorcet_winner_is_none_on_a_cycle():
    # Rock, paper, scissors: each beats exactly one other candidate.
    ballots, names = parse_ballots(
        "Aditi > Bharat\nBharat > Chen\nChen > Aditi", ROSTER
    )
    assert condorcet_winner(names, pairwise_matrix(names, ballots)) is None


def test_condorcet_winner_is_none_on_a_pairwise_tie():
    ballots, names = parse_ballots("Aditi > Bharat\nBharat > Aditi", ROSTER)
    assert condorcet_winner(names, pairwise_matrix(names, ballots)) is None


def test_report_flags_agreement_between_irv_and_condorcet():
    text = "Aditi > Bharat\nAditi > Bharat\nAditi > Bharat\nBharat > Aditi"
    report = count(text, ["Aditi", "Bharat"])
    assert report["condorcet"] == "Aditi"
    assert report["condorcet_agrees"] is True


def test_report_leaves_agreement_unset_without_a_winner():
    report = count("Aditi > Bharat\nBharat > Aditi")
    assert report["condorcet"] is None
    assert report["condorcet_agrees"] is None


def test_condorcet_can_disagree_with_instant_runoff():
    # Bharat beats everyone head-to-head, but finishes third on first choices
    # and is knocked out before the transfers that would have elected him.
    text = "\n".join(
        [
            "Aditi > Bharat > Chen",
            "Aditi > Bharat > Chen",
            "Aditi > Bharat > Chen",
            "Aditi > Bharat > Chen",
            "Chen > Bharat > Aditi",
            "Chen > Bharat > Aditi",
            "Chen > Bharat > Aditi",
            "Bharat > Aditi > Chen",
            "Bharat > Aditi > Chen",
        ]
    )
    report = count(text)
    assert report["rounds"][0]["eliminated"] == "Bharat"
    assert report["winner"] == "Aditi"
    assert report["condorcet"] == "Bharat"
    assert report["condorcet_agrees"] is False
