from unittest.mock import MagicMock, patch

from src.google_voice import GoogleVoiceBrowser


def _browser() -> GoogleVoiceBrowser:
    b = GoogleVoiceBrowser.__new__(GoogleVoiceBrowser)
    b.driver = MagicMock()
    b.avoid_page_reload = True
    return b


def test_answered_panel_text_and_voicemail_cue():
    b = _browser()
    button = MagicMock()
    button.is_displayed.return_value = True
    button.get_attribute.side_effect = lambda name: "Hang up" if name == "aria-label" else ""
    b.driver.find_elements.return_value = [button]
    with patch.object(b, "_active_call_dom_text", return_value=""):
        assert "Hang up" in b.answered_call_panel_text()
    with patch.object(b, "_voicemail_cue_present", return_value=True), \
         patch.object(b, "_page_contains_voicemail", return_value=False):
        assert b.voicemail_dom_cue() is True


def test_is_call_screening_active_from_page_source():
    b = _browser()
    b.driver.page_source = "<html>Please record your name and reason for calling</html>"
    with patch.object(b, "_any_present", return_value=False):
        assert b.is_call_screening_active() is True


def test_open_calls_page_skips_reload_when_on_calls():
    b = _browser()
    b.driver.current_url = "https://voice.google.com/u/0/calls"
    with patch.object(b, "_find_first", return_value=MagicMock()):
        assert b._open_calls_page() is True
    b.driver.get.assert_not_called()
