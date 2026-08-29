from api.normalize import normalize


def test_casefolds_and_collapses_whitespace():
    text, _ = normalize("Total  Net\n\tSales")
    assert text == "total net sales"


def test_straightens_curly_quotes_and_dashes():
    text, _ = normalize("“Apple’s” year—over–year")
    assert text == '"apple\'s" year-over-year'


def test_offset_map_has_one_entry_per_normalized_char():
    original = "The  “Quick” Brown"
    text, offsets = normalize(original)
    assert len(offsets) == len(text)


def test_offset_map_points_at_the_producing_character():
    original = "Net   sales"
    text, offsets = normalize(original)
    assert text == "net sales"
    assert offsets[text.index("sales")] == original.index("sales")


def test_collapsed_whitespace_run_maps_to_its_first_character():
    original = "a \n b"
    text, offsets = normalize(original)
    assert text == "a b"
    assert offsets[1] == 1


def test_multi_character_expansion_maps_every_char_to_one_source_index():
    # NFKC expands the ligature; casefold expands the eszett.
    text, offsets = normalize("ﬁnß")
    assert text == "finss"
    assert offsets == [0, 0, 1, 2, 2]


def test_empty_text_is_stable():
    assert normalize("") == ("", [])


def test_currency_spacing_is_ignored_when_matching():
    """Filing tables read '$ 383,285'; a model naturally writes '$383,285'.
    Verification punished formatting rather than unfaithfulness."""
    from api.normalize import normalize

    assert normalize("$ 383,285")[0] == normalize("$383,285")[0]
    assert normalize("(3) %")[0] == normalize("(3)%")[0]


def test_word_spacing_is_still_significant():
    """The narrow rule must not become 'ignore all whitespace' -- that would
    let a quote match across word boundaries that never existed."""
    from api.normalize import normalize

    assert normalize("net sales")[0] != normalize("netsales")[0]
    assert normalize("net sales")[0] == "net sales"


def test_offset_map_survives_a_dropped_space():
    from api.normalize import normalize

    text = "Total net sales $ 383,285 fell"
    normalized, offsets = normalize(text)
    index = normalized.find("$383,285")
    assert index != -1
    assert len(offsets) == len(normalized)
    assert text[offsets[index]] == "$"
    end = offsets[index + len("$383,285") - 1] + 1
    assert text[offsets[index]:end] == "$ 383,285"


def test_a_quote_written_without_the_space_now_verifies():
    from api.verify import find_quote

    source = "Rest of Asia Pacific 29,615 1 % Total net sales $ 383,285 (3) % $ 394,328"
    assert find_quote(source, "Total net sales $383,285") is not None
    assert find_quote(source, "Total net sales $ 383,285") is not None
    assert find_quote(source, "Total net sales $ 999,999") is None
