import time

from src.live_call_status import clear_live_call, get_live_call, publish_live_call


def test_publish_and_read_live_call(tmp_path, monkeypatch):
    status_path = tmp_path / "live_call_status.json"
    monkeypatch.setattr("src.live_call_status.STATUS_PATH", status_path)

    clear_live_call()
    idle = get_live_call()
    assert idle["phase"] == "idle"

    publish_live_call(phase="dialing", phone="+15551234567", name="Sam", index=1)
    live = get_live_call()
    assert live["phase"] == "dialing"
    assert live["phone"] == "+15551234567"
    assert live["active"] is True

    publish_live_call(
        phase="result",
        phone="+15551234567",
        name="Sam",
        outcome="voicemail",
        hold_result=True,
        hold_seconds=2.0,
    )
    result = get_live_call()
    assert result["phase"] == "result"
    assert result["outcome"] == "voicemail"
    assert result["result_until"] > time.time()


def test_result_expires_to_idle(tmp_path, monkeypatch):
    status_path = tmp_path / "live_call_status.json"
    monkeypatch.setattr("src.live_call_status.STATUS_PATH", status_path)

    publish_live_call(
        phase="result",
        phone="+15550001111",
        outcome="ended",
        hold_result=True,
        hold_seconds=0.15,
    )
    time.sleep(0.4)
    cleared = get_live_call()
    assert cleared["phase"] == "idle"
