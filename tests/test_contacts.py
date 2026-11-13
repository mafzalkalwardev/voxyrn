import pandas as pd

from src.contacts import load_contacts


def test_load_contacts_uses_legal_name_column(tmp_path):
    path = tmp_path / "contacts.csv"
    pd.DataFrame(
        [{"legal name": "Acme Dispatch", "Phone": "(555) 123-4567"}]
    ).to_csv(path, index=False)

    contacts = load_contacts(path)

    assert contacts[0]["phone"] == "+15551234567"
    assert contacts[0]["name"] == "Acme Dispatch"


def test_load_contacts_matches_phone_column_case_insensitively(tmp_path):
    path = tmp_path / "contacts.csv"
    pd.DataFrame(
        [{"phone number": "555.123.4567", "Company Name": "North Star"}]
    ).to_csv(path, index=False)

    contacts = load_contacts(path)

    assert contacts[0]["phone"] == "+15551234567"
    assert contacts[0]["name"] == "North Star"


def test_load_contacts_keeps_mc_states_address_email(tmp_path):
    path = tmp_path / "carriers.xlsx"
    pd.DataFrame(
        [{
            "MC Number": "MC-1778293\n",
            "Legal Name": "C&D PRIME LOGISTICS LLC",
            "States": "AL",
            "Address": "275 FORESTDALE DR\nOXFORD, AL 36203",
            "Phone": "(256) 294-6377",
            "Email": "ARBORISTS22@GMAIL.COM",
        }]
    ).to_excel(path, index=False)

    contacts = load_contacts(path)

    assert contacts[0]["mc_number"] == "MC-1778293"
    assert contacts[0]["name"] == "C&D PRIME LOGISTICS LLC"
    assert contacts[0]["states"] == "AL"
    assert "OXFORD" in contacts[0]["address"]
    assert contacts[0]["email"] == "ARBORISTS22@GMAIL.COM"
    assert contacts[0]["phone"] == "+12562946377"
