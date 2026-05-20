from swissdatamcp.text import clean_text, ensure_list, pick_localized


def test_pick_localized_prefers_requested_language():
    value = {"de": "Geburten", "en": "Births"}

    assert pick_localized(value, "en") == "Births"


def test_clean_text_removes_html_and_extra_space():
    assert clean_text("<p>Hello&nbsp; Swiss   data</p>") == "Hello Swiss data"


def test_ensure_list_handles_tag_dicts():
    value = [{"display_name": "population"}, {"name": "births"}]

    assert ensure_list(value) == ["population", "births"]

