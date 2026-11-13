import pandas as pd
from pathlib import Path

VALID_PHONE_COLUMNS = ["Phone", "phone", "PHONE", "Phone Number", "Mobile", "Number"]
VALID_NAME_COLUMNS = [
    "Name",
    "name",
    "Client",
    "legal name",
    "Legal Name",
    "Company",
    "Company Name",
    "Business Name",
]
VALID_MC_COLUMNS = ["MC Number", "MC", "MC#", "MC Num", "Account ID", "USDOT", "DOT"]
VALID_EMAIL_COLUMNS = ["Email", "E-mail", "Email Address"]
VALID_STATE_COLUMNS = ["States", "State", "ST", "Region"]
VALID_ADDRESS_COLUMNS = ["Address", "Street Address", "Full Address", "Location"]


def _find_column(columns, candidates):
    wanted = {c.strip().lower() for c in candidates}
    return next((c for c in columns if c.strip().lower() in wanted), None)


def _clean_cell(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if text.lower() in {"nan", "none", "nat"}:
        return ""
    return " ".join(text.split())


def normalize_phone(phone: str) -> str:
    phone = str(phone or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return digits


def load_contacts(path: str):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Contacts file not found: {path}")

    if path.suffix.lower() in [".xlsx", ".xls"]:
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)

    df.columns = df.columns.str.strip()
    phone_col = _find_column(df.columns, VALID_PHONE_COLUMNS)
    if phone_col is None:
        raise ValueError(f"No phone column found. Available: {list(df.columns)}")
    name_col = _find_column(df.columns, VALID_NAME_COLUMNS)
    mc_col = _find_column(df.columns, VALID_MC_COLUMNS)
    email_col = _find_column(df.columns, VALID_EMAIL_COLUMNS)
    state_col = _find_column(df.columns, VALID_STATE_COLUMNS)
    address_col = _find_column(df.columns, VALID_ADDRESS_COLUMNS)

    df = df[df[phone_col].notna()]
    df[phone_col] = df[phone_col].astype(str)
    contacts = []
    for _, row in df.iterrows():
        number = normalize_phone(row[phone_col])
        if not number:
            continue
        name = _clean_cell(row.get(name_col) if name_col else "") or "Unknown"
        contacts.append(
            {
                "phone": number,
                "name": name,
                "mc_number": _clean_cell(row.get(mc_col) if mc_col else ""),
                "email": _clean_cell(row.get(email_col) if email_col else ""),
                "states": _clean_cell(row.get(state_col) if state_col else ""),
                "address": _clean_cell(row.get(address_col) if address_col else ""),
            }
        )

    return contacts
