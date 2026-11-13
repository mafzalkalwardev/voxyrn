from src.speech_style import expand_dispatch_terms, humanize_for_speech


def test_expands_paperwork_and_entity_terms():
    text = expand_dispatch_terms(
        "Send the MC Authority Letter, W-9 Form, Insurance Certificate, and LLC docs."
    )
    assert "M C Authority Letter" in text
    assert "W nine form" in text
    assert "insurance certificate" in text
    assert "L L C" in text


def test_expands_common_truck_abbreviations():
    text = expand_dispatch_terms("Need COI, NOA, BOL, CDL, USDOT, and OTR lanes.")
    assert "certificate of insurance" in text
    assert "notice of assignment" in text
    assert "bill of lading" in text
    assert "C D L" in text
    assert "U S D O T" in text
    assert "over the road" in text


def test_humanize_softens_delivery_and_phones():
    spoken = humanize_for_speech(
        "Ashley: Hi — please call +13172100495 about the W9 and MC number."
    )
    assert spoken.startswith("Hi,")
    assert "W nine" in spoken
    assert "M C number" in spoken
    assert "three one seven" in spoken
    assert "Ashley:" not in spoken
