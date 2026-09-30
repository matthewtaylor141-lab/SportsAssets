"""The pronunciation normaliser: the stored transcript -> the spoken text.

PURE. Money, cents, percentages, percentage points, labelled probabilities,
negative amounts and decimals are spoken as words; an unlabelled decimal is
NOT turned into a percentage; citations and the Sources line are not spoken.
"""

import pytest

from sportsassets.agents.speech_text import (integer_words, money_words,
                                             normalise)


@pytest.mark.parametrize("text,spoken", [
    ("$0.50", "fifty cents"),
    ("50¢", "fifty cents"),
    ("$1,000", "one thousand dollars"),
    ("$180", "one hundred eighty dollars"),
    ("18%", "eighteen percent"),
    ("9 pp", "nine percentage points"),
    ("9pp", "nine percentage points"),
    ("9 percentage points", "nine percentage points"),
    ("+9.00 pp", "nine percentage points"),
    ("1 pp", "one percentage point"),
    ("$2,200.50", "two thousand two hundred dollars and fifty cents"),
    ("-$200", "minus two hundred dollars"),
    ("−$1,000", "minus one thousand dollars"),
    ("$1.00", "one dollar"),
    ("$0.01", "one cent"),
    ("$1", "one dollar"),
    ("-2 pp", "minus two percentage points"),
    ("-3%", "minus three percent"),
    ("$0.0826", "eight point two six cents"),
])
def test_amounts_and_rates(text, spoken):
    assert normalise(text) == spoken


def test_a_probability_is_a_percentage_only_when_labelled():
    assert normalise("probability 0.59") == "probability fifty-nine percent"
    assert normalise("0.59 probability") == "fifty-nine percent probability"
    assert normalise("0.59") == "zero point five nine"
    assert normalise("the ratio was 0.59") == "the ratio was zero point five "\
        "nine"
    # a list after one label, within the clause
    assert normalise("internal probability 0.60, Pinnacle 0.58, blended "
                     "0.59.") == ("internal probability sixty percent, "
                                  "Pinnacle fifty-eight percent, blended "
                                  "fifty-nine percent.")
    # the label does not leak into the next sentence
    assert normalise("probability 0.59. The ratio 0.50 held.") == (
        "probability fifty-nine percent. The ratio zero point five held.")
    assert normalise("chance of 0.595") == "chance of fifty-nine point five "\
        "percent"


def test_money_is_never_read_as_a_probability():
    assert normalise("probability 0.59 against a price of $0.50") == (
        "probability fifty-nine percent against a price of fifty cents")


def test_citations_sources_and_disclosures_are_not_spoken():
    got = normalise("[Records-only mode — no language model]\nEdge 9 pp "
                    "[F9] [derek_entry_decisions:drk-1].\nSources: "
                    "derek_entry_decisions:drk-1")
    assert got == "Edge nine percentage points."


def test_the_demonstration_walkthrough_speaks_cleanly():
    got = normalise("This is the DEMONSTRATION position [F1]. $1,000 on the "
                    "Yankees ML at $0.50, which is 2,000 contracts [F3]. Red "
                    "Sox +2.5 for $800 at $0.40. 2,000 × 0.09 = $180.")
    assert got == ("This is the demonstration position. one thousand dollars"
                   " on the Yankees moneyline at fifty cents, which is two "
                   "thousand contracts. Red Sox plus two point five for eight"
                   " hundred dollars at forty cents. two thousand times zero "
                   "point zero nine equals one hundred eighty dollars.")
    assert "[" not in got and "$" not in got


def test_number_words():
    assert integer_words(0) == "zero"
    assert integer_words(59) == "fifty-nine"
    assert integer_words(180) == "one hundred eighty"
    assert integer_words(2200) == "two thousand two hundred"
    assert integer_words(1_000_000) == "one million"
    assert money_words("2200.50") == ("two thousand two hundred dollars and "
                                      "fifty cents")
    assert money_words("200", negative=True) == "minus two hundred dollars"
