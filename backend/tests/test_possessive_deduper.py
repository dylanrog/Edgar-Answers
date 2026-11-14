import random

import pytest

from api.generate import PossessiveDeduper, collapse_possessive

CASES = [
    ("Microsoft's Microsoft Cloud revenue", "Microsoft Cloud revenue"),
    ("Apple's Apple Music", "Apple Music"),
    ("Microsoft's Microsoft 365", "Microsoft 365"),
    ("Microsoft\u2019s Microsoft Cloud", "Microsoft Cloud"),
    ("grew at Microsoft's Microsoft", "grew at Microsoft"),
    ("Apple's Apple and Microsoft's Microsoft Cloud.", "Apple and Microsoft Cloud."),
    ("Microsoft's Cloud", "Microsoft's Cloud"),
    ("Microsoft's microsoft", "Microsoft's microsoft"),
    ("Microsofts Microsoft", "Microsofts Microsoft"),
    ("AMicrosoft's Microsoft", "AMicrosoft's Microsoft"),
    ("Apple's Applebee", "Apple's Applebee"),
    ("Microsoft's  Microsoft", "Microsoft's  Microsoft"),
    ("Johnson & Johnson's Johnson", "Johnson & Johnson"),
    ("AT&T's AT&T Mobility", "AT&T Mobility"),
    ("Net sales were $391.0 billion [1].", "Net sales were $391.0 billion [1]."),
    ("", ""),
]


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_one_shot_transform(text, expected):
    assert collapse_possessive(text) == expected


def stream(text, sizes):
    d = PossessiveDeduper()
    out, i = [], 0
    for n in sizes:
        out.append(d.feed(text[i : i + n]))
        i += n
    out.append(d.feed(text[i:]))
    out.append(d.finish())
    return out


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_every_single_split_matches_one_shot(text, expected):
    for cut in range(len(text) + 1):
        assert "".join(stream(text, [cut])) == expected


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_char_by_char_and_random_chunks_match_one_shot(text, expected):
    assert "".join(stream(text, [1] * len(text))) == expected
    rng = random.Random(1234)
    for _ in range(50):
        sizes, left = [], len(text)
        while left:
            n = rng.randint(1, 5)
            sizes.append(min(n, left))
            left -= sizes[-1]
        assert "".join(stream(text, sizes)) == expected


def test_unresolved_tail_is_held_and_the_rest_emitted():
    d = PossessiveDeduper()
    assert d.feed("Revenue at Microsoft") == "Revenue at "
    assert d.feed("'") == ""
    assert d.feed("s") == ""
    assert d.feed(" Micro") == ""
    assert d.feed("soft") == ""
    assert d.feed(" Cloud") == "Microsoft "
    assert d.finish() == "Cloud"


def test_plain_text_is_not_delayed_beyond_an_unresolved_word():
    d = PossessiveDeduper()
    assert d.feed("net sales were 391 ") == "net sales were 391 "
    assert d.feed("billion") == "billion"
    assert d.feed(" [1]. ") == " [1]. "


def test_lowercase_prefix_blocks_a_match_across_chunks():
    d = PossessiveDeduper()
    out = d.feed("a") + d.feed("Microsoft's Microsoft") + d.finish()
    assert out == "aMicrosoft's Microsoft"
