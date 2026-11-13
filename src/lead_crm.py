"""Per-user lead lists. DNC numbers are not dialed.

Agents see their own rows. An admin sees every row. Statuses are the
working set for truck-dispatch and any other list: new, called, no answer,
callback, interested, booked, not interested, and do-not-call.
"""
from __future__ import annotations

import csv
import io
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from src.paths import runtime_base

STATUSES = (
    "new",
    "called",
    "no_answer",
    "callback",
    "interested",
    "booked",
    "not_interested",
    "dnc",
)
_LOCK = threading.Lock()


def leads_path() -> Path:
    return runtime_base() / "data" / "lead_crm.json"


def _digits(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _read() -> list[dict]:
    path = leads_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = raw.get("leads", raw) if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _write(rows: list[dict]) -> None:
    path = leads_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"leads": rows}, indent=2), encoding="utf-8")


def _public(row: dict) -> dict:
    status = str(row.get("status") or "new")
    if status not in STATUSES:
        status = "new"
    return {
        "id": str(row.get("id") or ""),
        "owner": str(row.get("owner") or ""),
        "list_name": str(row.get("list_name") or ""),
        "phone": str(row.get("phone") or ""),
        "name": str(row.get("name") or ""),
        "company": str(row.get("company") or ""),
        "status": status,
        "notes": str(row.get("notes") or ""),
        "follow_up": str(row.get("follow_up") or ""),
        "updated_at": str(row.get("updated_at") or ""),
    }


def list_leads(*, owner: str = "", include_all: bool = False) -> list[dict]:
    with _LOCK:
        rows = [_public(row) for row in _read()]
    if include_all:
        return rows
    key = (owner or "").strip().lower()
    return [row for row in rows if row["owner"].lower() == key]


def import_csv(text: str, *, owner: str, list_name: str) -> int:
    """Add rows from a CSV with a phone column. Existing DNC numbers stay DNC."""
    owner = (owner or "").strip()
    list_name = (list_name or "default").strip() or "default"
    if not owner:
        raise ValueError("Assign the list to a user")
    reader = csv.DictReader(io.StringIO(text or ""))
    if not reader.fieldnames:
        raise ValueError("The CSV needs a header row")
    fields = {name.strip().lower(): name for name in reader.fieldnames if name}
    phone_key = fields.get("phone") or fields.get("phone_number") or fields.get("mobile")
    if not phone_key:
        raise ValueError("The CSV needs a phone column")
    name_key = fields.get("name") or fields.get("first_name") or fields.get("contact")
    company_key = fields.get("company") or fields.get("business")
    added = 0
    with _LOCK:
        rows = _read()
        dnc = {_digits(str(row.get("phone") or "")) for row in rows if str(row.get("status")) == "dnc"}
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for record in reader:
            phone = str(record.get(phone_key) or "").strip()
            if len(_digits(phone)) < 10:
                continue
            status = "dnc" if _digits(phone) in dnc else "new"
            rows.append({
                "id": uuid.uuid4().hex,
                "owner": owner,
                "list_name": list_name,
                "phone": phone,
                "name": str(record.get(name_key) or "").strip() if name_key else "",
                "company": str(record.get(company_key) or "").strip() if company_key else "",
                "status": status,
                "notes": "",
                "follow_up": "",
                "updated_at": now,
            })
            added += 1
        _write(rows)
    return added


def update_lead(lead_id: str, *, actor: str, is_admin: bool, **fields) -> dict:
    with _LOCK:
        rows = _read()
        match = next((row for row in rows if str(row.get("id")) == lead_id), None)
        if match is None:
            raise ValueError("Lead not found")
        if not is_admin and str(match.get("owner") or "").lower() != actor.lower():
            raise PermissionError("That lead belongs to another user")
        if "status" in fields:
            status = str(fields["status"] or "").strip().lower().replace(" ", "_")
            if status not in STATUSES:
                raise ValueError("Unknown lead status")
            match["status"] = status
        if "notes" in fields:
            match["notes"] = str(fields["notes"] or "")
        if "follow_up" in fields:
            match["follow_up"] = str(fields["follow_up"] or "")
        if is_admin and "owner" in fields and str(fields["owner"] or "").strip():
            match["owner"] = str(fields["owner"]).strip()
        match["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write(rows)
        return _public(match)


def assign_list(list_name: str, owner: str) -> int:
    list_name = (list_name or "").strip()
    owner = (owner or "").strip()
    if not list_name or not owner:
        raise ValueError("List name and user are required")
    changed = 0
    with _LOCK:
        rows = _read()
        for row in rows:
            if str(row.get("list_name") or "") == list_name:
                row["owner"] = owner
                changed += 1
        _write(rows)
    return changed


def dnc_numbers() -> set[str]:
    with _LOCK:
        return {_digits(str(row.get("phone") or "")) for row in _read() if str(row.get("status")) == "dnc" and _digits(str(row.get("phone") or ""))}


def is_dnc(phone: str) -> bool:
    digits = _digits(phone)
    return bool(digits) and digits in dnc_numbers()


def filter_callable(contacts: list[dict]) -> tuple[list[dict], int]:
    blocked = dnc_numbers()
    if not blocked:
        return list(contacts), 0
    kept = []
    skipped = 0
    for contact in contacts:
        phone = str(contact.get("phone") or contact.get("Phone") or "")
        if _digits(phone) in blocked:
            skipped += 1
            continue
        kept.append(contact)
    return kept, skipped


def user_stats(owner: str = "") -> dict:
    rows = list_leads(owner=owner, include_all=not owner)
    def count(pred) -> int:
        return sum(1 for row in rows if pred(row["status"]))
    spoke = {"interested", "booked", "callback", "not_interested"}
    return {
        "owner": owner or "all",
        "leads": len(rows),
        "calls": count(lambda status: status != "new"),
        "connects": count(lambda status: status in spoke),
        "booked": count(lambda status: status == "booked"),
        "callbacks": count(lambda status: status == "callback"),
        "dnc": count(lambda status: status == "dnc"),
    }
