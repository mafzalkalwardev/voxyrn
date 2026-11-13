import json
import sys

import pytest

import src.gv_accounts as accounts


def test_account_password_is_encrypted_and_redacted(tmp_path, monkeypatch):
    account_file = tmp_path / "gv_accounts.json"
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", account_file)
    saved = accounts.save_account({
        "name": "Line One", "email": "line@example.com",
        "password": "not-plain-text", "profile_name": "line_one",
        "loopback_device": "Cable A Input", "capture_device": "Cable A Output",
        "enabled": True,
    })
    assert saved["has_password"] is True
    assert "password" not in saved
    assert "not-plain-text" not in account_file.read_text(encoding="utf-8")
    assert accounts.load_accounts(include_password=True)[0]["password"] == "not-plain-text"


def test_windows_cable_a_is_stored_as_the_linux_route(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", tmp_path / "gv_accounts.json")
    saved = accounts.save_account({
        "email": "line@example.com",
        "profile_name": "line_one",
        "loopback_device": "CABLE-A Input",
        "capture_device": "CABLE-A Output",
    })
    if sys.platform.startswith("linux"):
        assert saved["loopback_device"] == "voxyrn_to_gv"
        assert saved["capture_device"] == "voxyrn_from_gv.monitor"
    else:
        assert saved["loopback_device"] == "CABLE-A Input"
        assert saved["capture_device"] == "CABLE-A Output"


def test_a_second_google_voice_account_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", tmp_path / "gv_accounts.json")
    saved = accounts.save_account({"email": "one@example.com", "profile_name": "main"})
    assert saved["name"] == "Main line"
    with pytest.raises(ValueError, match="one Google Voice account"):
        accounts.save_account({"email": "two@example.com", "profile_name": "other"})
    updated = accounts.save_account({
        "id": saved["id"],
        "email": "one@example.com",
        "profile_name": "main",
        "name": "Main line",
    })
    assert updated["id"] == saved["id"]
    assert len(accounts.load_accounts()) == 1


def test_delete_preserves_chrome_profile_data(tmp_path, monkeypatch):
    account_file = tmp_path / "gv_accounts.json"
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", account_file)
    saved = accounts.save_account({"email": "one@example.com", "profile_name": "one"})
    assert accounts.delete_account(saved["id"]) is True
    assert json.loads(account_file.read_text(encoding="utf-8")) == []
