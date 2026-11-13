"""Admin CRM, Ashley's seed, DNC, and one-account Chrome lines."""
import json
from pathlib import Path

from src.auth import verify_password


def test_ashley_is_seeded_without_leaking_the_password(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    secret_file = tmp_path / "ashley.password"
    monkeypatch.setenv("VOXYRN_USERS_FILE", str(users))
    monkeypatch.setenv("VOXYRN_ASHLEY_PASSWORD_FILE", str(secret_file))
    from src.auth import ashley_password_path, ensure_ashley, list_public_users

    ensure_ashley()
    ashley = next(row for row in list_public_users() if row["username"] == "Ashley")
    assert ashley["role"] == "agent"
    assert ashley["status"] == "active"
    assert ashley["script_id"] == "truck-dispatch-sales"
    assert "password" not in ashley
    stored = json.loads(users.read_text(encoding="utf-8"))["users"]
    record = next(row for row in stored if row["username"] == "Ashley")
    secret = ashley_password_path().read_text(encoding="utf-8").strip()
    assert verify_password(secret, record["password_hash"])
    assert secret_file.stat().st_mode & 0o077 == 0
    first = secret_file.read_bytes()
    ensure_ashley()
    assert secret_file.read_bytes() == first


def test_csv_import_keeps_dnc_and_scopes_the_agent(tmp_path, monkeypatch):
    monkeypatch.setattr("src.lead_crm.leads_path", lambda: tmp_path / "leads.json")
    from src.lead_crm import filter_callable, import_csv, is_dnc, list_leads, update_lead, user_stats

    import_csv(
        "phone,name,company\n+13175550100,Sam,Northline\n+13175550199,Pat,Other\n",
        owner="Ashley",
        list_name="truck-dispatch",
    )
    import_csv("phone,name\n+13175550999,Blocked\n", owner="other.agent", list_name="other")
    ashley = list_leads(owner="Ashley")
    assert {row["name"] for row in ashley} == {"Sam", "Pat"}
    blocked = next(row for row in list_leads(include_all=True) if row["name"] == "Blocked")
    update_lead(blocked["id"], actor="admin", is_admin=True, status="dnc", notes="asked to stop", follow_up="")
    sam = next(row for row in ashley if row["name"] == "Sam")
    update_lead(sam["id"], actor="Ashley", is_admin=False, status="booked", notes="packet sent", follow_up="2026-11-12")
    try:
        update_lead(blocked["id"], actor="Ashley", is_admin=False, notes="nope")
    except PermissionError:
        pass
    else:
        raise AssertionError("Ashley edited another user's lead")
    assert is_dnc("+1 (317) 555-0999")
    kept, skipped = filter_callable([
        {"phone": "3175550100", "name": "Sam"},
        {"phone": "3175550999", "name": "Blocked"},
    ])
    assert skipped == 1
    assert kept[0]["name"] == "Sam"
    stats = user_stats("Ashley")
    assert stats["booked"] == 1
    assert stats["calls"] == 1


def test_lines_share_one_account_and_warn_past_capacity(tmp_path, monkeypatch):
    monkeypatch.setenv("VOXYRN_LINES", "7")
    monkeypatch.setenv("VOXYRN_DISPLAY", ":98")
    monkeypatch.delenv("VOXYRN_SEPARATE_DISPLAYS", raising=False)
    monkeypatch.setenv("VOXYRN_CPU_COUNT", "4")
    monkeypatch.setenv("VOXYRN_RESERVED_CORES", "1")
    monkeypatch.setenv("VOXYRN_RAM_GB", "24")
    monkeypatch.setenv("VOXYRN_RESERVED_RAM_GB", "8")
    monkeypatch.setattr("src.gv_lines._profiles_root", lambda: tmp_path)
    (tmp_path / "tony").mkdir()
    (tmp_path / "tony" / "Cookie").write_text("keep", encoding="utf-8")
    (tmp_path / "tony" / ".gv_session_ok").write_text("ok", encoding="utf-8")
    from src.gv_lines import capacity_warning, ensure_line_profiles, ensure_profile, line_plan, reclone_profile

    ready = line_plan(1)
    assert ready[0]["signed_in"] is True
    assert ready[0]["status"] == "ready"
    names = ensure_line_profiles(3)
    assert names == ["tony", "tony_2", "tony_3"]
    assert (tmp_path / "tony_3" / ".gv_session_ok").is_file()
    plan = line_plan()
    assert len(plan) == 7
    assert {row["gv_account"] for row in plan} == {"Main line"}
    assert len({row["profile_name"] for row in plan}) == 7
    assert len({row["loopback_device"] for row in plan}) == 7
    assert all(row["display"] == ":98" for row in plan)
    assert all(row["vnc_port"] not in {5901, 5902, 6080, 6081} for row in plan)
    assert capacity_warning(7)
    assert ensure_profile(2) == "tony_2"
    assert (tmp_path / "tony_2" / "Cookie").read_text(encoding="utf-8") == "keep"
    monkeypatch.setenv("VOXYRN_SEPARATE_DISPLAYS", "1")
    assert line_plan(1)[0]["display"] == ":100"
    (tmp_path / "tony_2" / "Cookie").write_text("stale", encoding="utf-8")
    monkeypatch.setattr("src.gv_lines.profile_in_use", lambda name: name == "tony_2")
    try:
        reclone_profile(2)
    except RuntimeError as exc:
        assert "Close Chrome" in str(exc)
    else:
        raise AssertionError("clone ran while Chrome was open")
    monkeypatch.setattr("src.gv_lines.profile_in_use", lambda name: False)
    assert reclone_profile(2) == "tony_2"
    assert (tmp_path / "tony_2" / "Cookie").read_text(encoding="utf-8") == "keep"


def test_old_session_marker_still_counts(tmp_path, monkeypatch):
    monkeypatch.setattr("src.gv_lines._profiles_root", lambda: tmp_path)
    folder = tmp_path / "tony"
    folder.mkdir()
    (folder / ".voxyrn_session").write_text("ok", encoding="utf-8")
    from src.gv_lines import line_plan, session_ready

    assert session_ready("tony") is True
    monkeypatch.setenv("VOXYRN_LINES", "1")
    monkeypatch.setenv("VOXYRN_DISPLAY", ":98")
    assert line_plan(1)[0]["status"] == "ready"


def test_signin_opens_the_profile_on_display_98(tmp_path, monkeypatch):
    monkeypatch.setattr("src.gv_lines._profiles_root", lambda: tmp_path)
    (tmp_path / "tony").mkdir()
    monkeypatch.setattr("src.browser_launch.find_chromium_binary", lambda: "/usr/bin/chromium")
    from src.gv_lines import signin_command

    command = signin_command(2)
    assert command[0] == "/usr/bin/chromium"
    assert f"--user-data-dir={tmp_path / 'tony_2'}" in command
    assert "https://voice.google.com" in command
    assert ":99" not in " ".join(command)
    launched = {}

    def fake_popen(cmd, env=None, **kwargs):
        launched["display"] = (env or {}).get("DISPLAY")
        launched["cmd"] = cmd
        return object()

    monkeypatch.setattr("src.gv_lines._ensure_signin_display", lambda index: None)
    monkeypatch.setattr("src.gv_lines.profile_in_use", lambda name: False)
    monkeypatch.setattr("src.gv_lines.subprocess.Popen", fake_popen)
    from src.gv_lines import open_signin

    opened = open_signin(2)
    assert launched["display"] == ":98"
    assert opened["display"] == ":98"
    assert opened["vnc_port"] == 5912


def test_audio_service_loads_env_for_numbered_sinks():
    text = (Path(__file__).resolve().parents[1] / "deploy/linux/voxyrn-audio.service").read_text(
        encoding="utf-8"
    )
    assert "EnvironmentFile=-@APP_DIR@/.env" in text
    assert text.index("Environment=XDG_RUNTIME_DIR=/run/voxyrn") < text.index("EnvironmentFile=")
