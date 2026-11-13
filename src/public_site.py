"""Public landing copy, robots, sitemap, and llms.txt.

Every feature sentence matches behavior in this repository: Selenium against
Google Voice, the realtime speech loop, leads, recordings, and the carrier CRM.
Nothing here is a customer count, a rating, a testimonial, or a price.
"""
from __future__ import annotations

import json
from xml.sax.saxutils import escape

ORIGIN = "https://voxyrn.induswebagency.com"
CANONICAL = ORIGIN + "/"
AGENCY_URL = "https://induswebagency.com"
AGENCY_NAME = "INDUS Web Agency"
TITLE = "Voxyrn — self-hosted AI auto dialer and carrier CRM"
DESCRIPTION = (
    "Voxyrn is a self-hosted AI auto dialer and carrier CRM for outbound "
    "Google Voice campaigns, with voicemail detection, call recording, leads, "
    "and local or Groq speech."
)

FEATURES = [
    {
        "title": "Google Voice outbound dialer",
        "text": (
            "Selenium drives voice.google.com in a persistent browser profile. "
            "Linux servers use Chromium. Windows keeps Google Chrome."
        ),
    },
    {
        "title": "Call-state gate",
        "text": (
            "Ringing, connected, ended, failed, and voicemail only move after "
            "minimum ring time and repeated checks of the Google Voice page."
        ),
    },
    {
        "title": "Voice agent on the call",
        "text": (
            "The live loop uses voice activity detection, faster-whisper (CPU int8) "
            "or Groq speech-to-text, a local GGUF model or Groq for the reply, "
            "and Kokoro, Piper, or Edge text-to-speech."
        ),
    },
    {
        "title": "Voicemail detection",
        "text": (
            "A beep, mailbox wording, or a saved greeting fingerprint can mark "
            "an answering machine. A prepared voicemail can be played. A short "
            "human hello is not treated as a mailbox by itself."
        ),
    },
    {
        "title": "Logs, transcripts, and recordings",
        "text": (
            "Each attempt can write a call log, a connected-call row, a transcript, "
            "and a WAV recording of both sides."
        ),
    },
    {
        "title": "Leads",
        "text": (
            "The lead row stores interest, callback time, and follow-up status. "
            "The list exports to CSV."
        ),
    },
    {
        "title": "Carrier CRM",
        "text": (
            "Carrier profiles keep company, phone, MC, DOT, truck type, lanes, "
            "notes, follow-up, and the assigned dispatcher, with CSV and JSON export."
        ),
    },
    {
        "title": "Contacts and preflight",
        "text": (
            "Contacts import from a workbook or CSV (MC, legal name, states, "
            "address, phone, email). Preflight checks the environment before a live run."
        ),
    },
    {
        "title": "Operator accounts",
        "text": (
            "The console requires a signed-in user. An admin creates operator "
            "accounts. Passwords are hashed. Sessions use a signed cookie and a CSRF token."
        ),
    },
]

USE_CASES = [
    {
        "title": "Freight dispatch and carrier outreach",
        "text": (
            "A dispatcher loads carriers, and the agent qualifies equipment and "
            "lanes on the connected call. The CRM keeps MC, DOT, truck type, "
            "lanes, notes, and the next follow-up."
        ),
    },
    {
        "title": "Sales teams",
        "text": (
            "A team runs an outbound list from one Google Voice number, reviews "
            "call logs and recordings, and works the leads that asked for a callback."
        ),
    },
    {
        "title": "Agencies",
        "text": (
            "An agency can host the console for a campaign, give each operator "
            "a login, and keep the speech models on the server when Groq is left off."
        ),
    },
]

HOWTO = [
    {
        "name": "Sign in",
        "text": "Open the console and sign in with the account an administrator created.",
    },
    {
        "name": "Import contacts",
        "text": "Upload the contact workbook or CSV on the Contacts page.",
    },
    {
        "name": "Run preflight",
        "text": "Open Preflight and confirm the voice models, audio route, and callback number.",
    },
    {
        "name": "Confirm Google Voice",
        "text": (
            "The Google Voice profile must already be signed in. On a Linux server "
            "that sign-in is a one-time headed Chromium session or a copied browser profile."
        ),
    },
    {
        "name": "Start the live run",
        "text": "Open Live Run, choose the contact file and limit, and start the run.",
    },
]

FAQ = [
    {
        "q": "What is Voxyrn?",
        "a": (
            "Voxyrn is a self-hosted AI auto dialer and carrier CRM for outbound "
            "Google Voice campaigns. It dials from Chromium or Chrome, talks with "
            "a voice agent, detects voicemail, records the call, and files the lead."
        ),
    },
    {
        "q": "Does Voxyrn use Twilio, Asterisk, or FreeSWITCH?",
        "a": (
            "No. The phone line is the operator's Google Voice account in the browser. "
            "Twilio, Asterisk, and FreeSWITCH are not part of the dial path."
        ),
    },
    {
        "q": "Can speech stay on the server?",
        "a": (
            "Yes. Piper or Kokoro speaks, faster-whisper transcribes on CPU with int8, "
            "and a local GGUF model can write the reply. Groq is used only when a key "
            "is set and the speech or answer backend is switched to Groq."
        ),
    },
    {
        "q": "How does call audio work on a Linux server?",
        "a": (
            "Voxyrn creates two PulseAudio or PipeWire null sinks. Text-to-speech "
            "plays into the sink Chromium uses as its microphone, and speech-to-text "
            "records the sink Chromium uses as its speaker. A headless server has no "
            "room speakers; listen in the Recordings tab. The virtual route can fail "
            "if Chromium does not attach to those devices, so the audio test in the "
            "console should be run before a live campaign."
        ),
    },
    {
        "q": "Can Google Voice sign in by itself on the server?",
        "a": (
            "No. The first sign-in is manual. Use the localhost noVNC session or copy "
            "a Chromium profile that is already signed in. A cookie file is only a "
            "best-effort import and Google often rejects it. Headless Chromium cannot "
            "finish a Google challenge or two-factor prompt."
        ),
    },
    {
        "q": "Does Voxyrn answer inbound return calls?",
        "a": (
            "On a connected outbound call, the agent listens and answers. It does not "
            "run a separate inbound queue. Callback times are stored on the lead and "
            "the carrier profile."
        ),
    },
    {
        "q": "Who hosts the public console?",
        "a": (
            "INDUS Web Agency hosts this installation at voxyrn.induswebagency.com. "
            "Operator accounts are created by an admin. There is no public self-serve signup."
        ),
    },
]


def json_ld() -> dict:
    features = [item["title"] + ": " + item["text"] for item in FEATURES]
    return {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Organization",
                "@id": AGENCY_URL + "/#organization",
                "name": AGENCY_NAME,
                "url": AGENCY_URL,
            },
            {
                "@type": "WebSite",
                "@id": CANONICAL + "#website",
                "name": "Voxyrn",
                "url": CANONICAL,
                "description": DESCRIPTION,
                "publisher": {"@id": AGENCY_URL + "/#organization"},
                "inLanguage": "en",
            },
            {
                "@type": "SoftwareApplication",
                "@id": CANONICAL + "#app",
                "name": "Voxyrn",
                "applicationCategory": "BusinessApplication",
                "operatingSystem": "Linux, Windows",
                "url": CANONICAL,
                "description": DESCRIPTION,
                "featureList": features,
                "provider": {"@id": AGENCY_URL + "/#organization"},
                "image": ORIGIN + "/static/landing/og.jpg",
            },
            {
                "@type": "BreadcrumbList",
                "@id": CANONICAL + "#breadcrumb",
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "position": 1,
                        "name": "Voxyrn",
                        "item": CANONICAL,
                    }
                ],
            },
            {
                "@type": "HowTo",
                "@id": CANONICAL + "#howto",
                "name": "How to launch an outbound Voxyrn campaign",
                "description": "Sign in, import contacts, pass preflight, confirm Google Voice, and start a live run.",
                "step": [
                    {
                        "@type": "HowToStep",
                        "position": index,
                        "name": step["name"],
                        "text": step["text"],
                        "url": CANONICAL + "#launch",
                    }
                    for index, step in enumerate(HOWTO, start=1)
                ],
            },
            {
                "@type": "FAQPage",
                "@id": CANONICAL + "#faq",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": item["q"],
                        "acceptedAnswer": {"@type": "Answer", "text": item["a"]},
                    }
                    for item in FAQ
                ],
            },
        ],
    }


def json_ld_text() -> str:
    return json.dumps(json_ld(), ensure_ascii=False, indent=2)


def landing_context() -> dict:
    return {
        "title": TITLE,
        "description": DESCRIPTION,
        "canonical": CANONICAL,
        "origin": ORIGIN,
        "agency_url": AGENCY_URL,
        "agency_name": AGENCY_NAME,
        "features": FEATURES,
        "use_cases": USE_CASES,
        "howto": HOWTO,
        "faq": FAQ,
        "json_ld": json_ld_text(),
    }


_APP_PATHS = (
    "/api",
    "/dashboard",
    "/login",
    "/setup",
    "/logout",
    "/admin",
    "/preflight",
    "/settings",
    "/contacts",
    "/audio",
    "/run",
    "/logs",
    "/leads",
    "/recordings",
    "/connected-calls",
    "/carrier-crm",
)

_BOTS = (
    "*",
    "Googlebot",
    "Bingbot",
    "DuckDuckBot",
    "GPTBot",
    "OAI-SearchBot",
    "ChatGPT-User",
    "Google-Extended",
    "Applebot",
    "Applebot-Extended",
    "ClaudeBot",
    "anthropic-ai",
    "PerplexityBot",
    "Amazonbot",
    "CCBot",
)


def robots_txt() -> str:
    blocks = []
    for bot in _BOTS:
        lines = [f"User-agent: {bot}", "Allow: /"]
        for path in _APP_PATHS:
            lines.append(f"Disallow: {path}")
        blocks.append("\n".join(lines))
    blocks.append(f"Sitemap: {ORIGIN}/sitemap.xml")
    return "\n\n".join(blocks) + "\n"


def sitemap_xml() -> str:
    loc = escape(CANONICAL)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n"
        f"    <loc>{loc}</loc>\n"
        "    <changefreq>weekly</changefreq>\n"
        "    <priority>1.0</priority>\n"
        "  </url>\n"
        "</urlset>\n"
    )


def llms_txt() -> str:
    lines = [
        "# Voxyrn",
        "",
        f"> {DESCRIPTION}",
        "",
        "Voxyrn is a self-hosted AI auto dialer and carrier CRM. Selenium places "
        "outbound calls through the operator's Google Voice account. The voice agent "
        "can run on the server with Piper or Kokoro, faster-whisper int8, and a local "
        "GGUF model, or it can use Groq when an API key is configured.",
        "",
        f"Canonical page: {CANONICAL}",
        f"Full description: {ORIGIN}/llms-full.txt",
        f"Operator login: {ORIGIN}/login",
        f"Host: {AGENCY_NAME} ({AGENCY_URL})",
        "",
        "## What is Voxyrn?",
        "",
        FAQ[0]["a"],
        "",
        "## Links",
        "",
        f"- [Home]({CANONICAL})",
        f"- [Log in]({ORIGIN}/login)",
        f"- [INDUS Web Agency]({AGENCY_URL})",
        "",
    ]
    return "\n".join(lines)


def llms_full_txt() -> str:
    parts = [
        llms_txt().rstrip(),
        "",
        "## Features",
        "",
    ]
    for item in FEATURES:
        parts.append(f"- {item['title']}: {item['text']}")
    parts.extend(["", "## Use cases", ""])
    for item in USE_CASES:
        parts.append(f"- {item['title']}: {item['text']}")
    parts.extend(["", "## How to launch an outbound campaign", ""])
    for index, step in enumerate(HOWTO, start=1):
        parts.append(f"{index}. {step['name']}: {step['text']}")
    parts.extend(["", "## Questions", ""])
    for item in FAQ:
        parts.append(item["q"])
        parts.append(item["a"])
        parts.append("")
    parts.extend(
        [
            "## Limits that are still true",
            "",
            "- The first Google Voice sign-in is manual (noVNC on localhost, or a copied Chromium profile).",
            "- Headless Chromium does not complete Google two-factor or robot challenges.",
            "- Call audio depends on PulseAudio or PipeWire null sinks. Test it before a campaign.",
            "- A headless VPS has no physical speakers. Listen-in does not play into a room; use recordings.",
            "- Voxyrn does not run an inbound call queue.",
            "- There is no public price list on this page. Hosting is arranged with INDUS Web Agency.",
            "",
            f"Source repository features are the operator console in this app. Contact: {AGENCY_URL}",
            "",
        ]
    )
    return "\n".join(parts)
