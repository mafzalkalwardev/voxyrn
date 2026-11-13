"""Owner limits: one Google Voice line, English and Spanish, one B2B example."""
import os

from src.campaign_scripts import STRUCTURE, TEMPLATES, objection_reply, preview_script
from src.capacity import capacity_report, google_voice_concurrent_calls
from src.voice_catalog import CATALOG, language_of_voice, voices_for


def test_one_google_voice_call_and_headroom_is_not_admission(monkeypatch):
    assert google_voice_concurrent_calls() == 1
    monkeypatch.setenv("VOXYRN_RAM_GB", "24")
    monkeypatch.setenv("VOXYRN_CPU_COUNT", "4")
    monkeypatch.setenv("VOXYRN_RESERVED_RAM_GB", "8")
    monkeypatch.setenv("VOXYRN_RESERVED_CORES", "1")
    monkeypatch.setenv("MAX_AGENTS", "auto")
    monkeypatch.setenv("STT_LOCAL_MODEL", "tiny")
    from src.capacity import cpu_agent_limit, max_agents, ram_agent_limit

    assert ram_agent_limit() > cpu_agent_limit()
    assert cpu_agent_limit() == 4
    assert max_agents() == 1
    report = capacity_report()
    assert "1 live call" in report
    assert "headroom" in report
    assert "not admitted" in report
    assert "4" in report


def test_voice_catalog_is_english_and_spanish_only():
    languages = {item.language for item in CATALOG}
    assert languages == {"en", "es"}
    ids = {item.voice_id for item in CATALOG}
    assert "en_US-ryan-high" in ids
    assert "en_GB-alba-medium" in ids
    assert "es_MX-ald-medium" in ids
    assert "es_ES-davefx-medium" in ids
    assert "ef_dora" in ids and "em_alex" in ids
    assert not any(item.startswith(("fr_", "de_", "pt_", "it_", "ff_")) for item in ids)
    assert len(voices_for("en", "piper")) >= 4
    assert len(voices_for("es", "piper")) >= 2
    assert language_of_voice("es-MX-DaliaNeural") == "es"
    try:
        voices_for("fr")
    except ValueError as exc:
        assert "English and Spanish" in str(exc)
    else:
        raise AssertionError("French was offered")


def test_only_generic_b2b_appointment_templates():
    assert set(TEMPLATES) == {"b2b-appointment", "b2b-appointment-es"}
    rendered = preview_script()
    assert list(rendered) == list(STRUCTURE)
    assert "Thursday" in rendered["close"]
    assert "Indus" not in " ".join(rendered.values())
    spanish = preview_script("b2b-appointment-es", agent="Lucía", company="Taller Norte", day="jueves")
    assert "Lucía" in spanish["opener"]
    assert objection_reply("please stop calling", "en").startswith("I will mark")
    assert objection_reply("no me interesa", "es")


def test_script_engine_stays_on_script_and_blocks_invented_prices():
    from src.script_engine import builtin_templates, evaluate_script, guard_reply

    ids = {item.script_id for item in builtin_templates()}
    assert "b2b-appointment" in ids
    assert "truck-dispatch-sales" in ids
    assert "truck-dispatch-sales-es" in ids
    assert not any(item.startswith("indus-") for item in ids)
    script = next(item for item in builtin_templates() if item.script_id == "b2b-appointment")
    assert "Indus" not in script.compliance
    assert "MailForge" not in script.product
    variables = {
        "first_name": "Sam",
        "company": "INDUS Web Agency",
        "product": script.product,
        "agent_name": "Alex",
    }

    def inventing_model(_prompt: str) -> str:
        return "The website package is $499 a month and we guarantee page one."

    guarded = evaluate_script(script, variables, llm=inventing_model)
    assert guarded["pass_rate"] == 1.0
    price = next(row for row in guarded["details"] if "cost" in row["utterance"])
    assert "$" not in price["line"]
    assert guard_reply("It is $50.", script, variables, "no price here") is None

    plain = evaluate_script(script, variables, llm=None)
    assert plain["pass_rate"] == 1.0


def test_custom_script_saves_and_indus_templates_stay_out(tmp_path, monkeypatch):
    from src import script_engine as scripts

    monkeypatch.delenv("ACTIVE_SCRIPT_ID", raising=False)
    path = tmp_path / "call_scripts.json"
    path.write_text(
        '{"active_script_id":"indus-web","scripts":[{"script_id":"indus-seo","name":"INDUS SEO","product":"rankings"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(scripts, "scripts_path", lambda: path)
    loaded = {item.script_id for item in scripts.load_scripts()}
    assert "b2b-appointment" in loaded
    assert "truck-dispatch-sales" in loaded
    assert "indus-seo" not in loaded
    assert scripts.active_script_id() == ""
    custom = scripts.CallScript(
        script_id="clinic-outreach",
        name="Clinic outreach",
        product="a short visit",
        opener="Am I speaking with {first_name}?",
    )
    scripts.save_script(custom, use_on_calls=True)
    assert scripts.get_script("clinic-outreach").name == "Clinic outreach"
    assert scripts.active_script_id() == "clinic-outreach"
    try:
        scripts.save_script(scripts.CallScript(script_id="indus-mailforge", name="MailForge"))
    except ValueError as exc:
        assert "INDUS" in str(exc)
    else:
        raise AssertionError("INDUS template was saved")


def test_fleet_admits_only_the_main_line(monkeypatch):
    monkeypatch.setenv("MAX_AGENTS", "4")
    monkeypatch.setenv("VOXYRN_CPU_COUNT", "4")
    monkeypatch.setenv("VOXYRN_RESERVED_CORES", "1")
    monkeypatch.setenv("VOXYRN_RAM_GB", "24")
    monkeypatch.setenv("VOXYRN_RESERVED_RAM_GB", "8")
    from src.agent_fleet import Fleet

    accounts = [
        {"id": str(i), "email": f"a{i}@example.com", "profile_name": f"p{i}", "enabled": True}
        for i in range(1, 5)
    ]
    plan = Fleet().plan(accounts)
    assert len(plan.slots) == 1
    assert plan.max_agents == 1
    assert plan.slots[0].email == "a1@example.com"
    assert plan.slots[0].loopback_device == "voxyrn_to_gv_1"
    assert plan.slots[0].capture_device == "voxyrn_from_gv_1.monitor"
    assert plan.queued_accounts == []
    assert plan.slots[0].active_calls == 0


def test_account_pacer_enforces_gap_and_hourly_cap(monkeypatch):
    monkeypatch.setenv("ACCOUNT_MIN_GAP_SECONDS", "45")
    monkeypatch.setenv("ACCOUNT_MAX_CALLS_PER_HOUR", "2")
    from src.account_pacing import AccountPacer

    pacer = AccountPacer()
    assert pacer.allow("line", now=1000)[0] is True
    pacer.mark("line", now=1000)
    ok, reason = pacer.allow("line", now=1010)
    assert ok is False
    assert "wait" in reason
    pacer.mark("line", now=1100)
    ok, reason = pacer.allow("line", now=1200)
    assert ok is False
    assert "hour" in reason


def test_spanish_prompt_is_opt_in(monkeypatch):
    from src.conversation_agent import ConversationAgent

    monkeypatch.setenv("CALL_LANGUAGE", "en")
    agent = ConversationAgent.__new__(ConversationAgent)
    agent.agent_name = "Alex"
    agent.company_name = "Northwind"
    agent.company_website = ""
    agent.callback_number = "+15551212"
    agent.company_context = "A studio."
    agent.contact_name = "Sam"
    from src.dispatcher_intelligence import DispatcherConversationState
    agent.state = DispatcherConversationState()
    english = agent._build_system_prompt()
    assert "Speak only Spanish" not in english
    monkeypatch.setenv("CALL_LANGUAGE", "es")
    spanish = agent._build_system_prompt()
    assert "Speak only Spanish" in spanish
    os.environ.pop("CALL_LANGUAGE", None)
