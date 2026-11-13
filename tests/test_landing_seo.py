"""Public landing page, robots, sitemap, and llms.txt."""
import json
from html.parser import HTMLParser

from fastapi.testclient import TestClient

from src.web_app import app

ORIGIN = "https://voxyrn.induswebagency.com"


class _H1Count(HTMLParser):
    def __init__(self):
        super().__init__()
        self.h1 = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "h1":
            self.h1 += 1


def test_landing_is_one_static_h1_with_definition():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    parser = _H1Count()
    parser.feed(response.text)
    assert parser.h1 == 1
    assert "What is Voxyrn?" in response.text
    assert "self-hosted AI auto dialer and carrier CRM" in response.text
    assert 'rel="canonical" href="https://voxyrn.induswebagency.com/"' in response.text
    assert "application/ld+json" in response.text
    assert "FAQPage" in response.text
    assert "HowTo" in response.text
    assert "SoftwareApplication" in response.text
    assert "INDUS Web Agency" in response.text
    lowered = response.text.lower()
    assert "testimonial" not in lowered
    assert "aggregaterating" not in lowered
    for needle in ("$", "★★", "5 stars", "trusted by"):
        assert needle not in lowered
    assert 'fetchpriority="high"' in response.text
    assert 'loading="lazy"' in response.text
    assert 'type="image/avif"' in response.text
    assert 'type="image/webp"' in response.text


def test_json_ld_graph_parses():
    client = TestClient(app)
    html = client.get("/").text
    start = html.index('type="application/ld+json"')
    chunk = html[start:]
    raw = chunk.split(">", 1)[1].split("</script>", 1)[0]
    data = json.loads(raw)
    types = {node["@type"] for node in data["@graph"]}
    assert types >= {
        "Organization",
        "WebSite",
        "SoftwareApplication",
        "BreadcrumbList",
        "HowTo",
        "FAQPage",
    }
    org = next(node for node in data["@graph"] if node["@type"] == "Organization")
    assert org["name"] == "INDUS Web Agency"
    assert org["url"] == "https://induswebagency.com"
    app_node = next(node for node in data["@graph"] if node["@type"] == "SoftwareApplication")
    assert "aggregateRating" not in app_node
    assert "offers" not in app_node
    faq = next(node for node in data["@graph"] if node["@type"] == "FAQPage")
    first = faq["mainEntity"][0]
    assert first["name"] == "What is Voxyrn?"
    assert first["acceptedAnswer"]["text"].startswith("Voxyrn is")


def test_robots_sitemap_and_llms():
    client = TestClient(app)
    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert robots.headers["content-type"].startswith("text/plain")
    body = robots.text
    for bot in ("GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended", "Bingbot"):
        assert bot in body
    assert "Disallow: /api" in body
    assert "Disallow: /dashboard" in body
    assert f"Sitemap: {ORIGIN}/sitemap.xml" in body

    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200
    assert "application/xml" in sitemap.headers["content-type"]
    assert f"<loc>{ORIGIN}/</loc>" in sitemap.text

    short = client.get("/llms.txt")
    full = client.get("/llms-full.txt")
    assert short.status_code == 200
    assert full.status_code == 200
    assert short.headers["content-type"].startswith("text/plain")
    assert "What is Voxyrn?" in short.text
    assert ORIGIN in short.text
    assert "PulseAudio" in full.text or "PipeWire" in full.text
    assert "noVNC" in full.text

    manifest = client.get("/site.webmanifest")
    assert manifest.status_code == 200
    assert "manifest" in manifest.headers["content-type"]
    assert manifest.json()["start_url"] == "/"

    icon = client.get("/favicon.ico")
    assert icon.status_code == 200
    og = client.get("/static/landing/og.jpg")
    assert og.status_code == 200
    assert og.headers["content-type"].startswith("image/")


def test_logged_in_home_goes_to_dashboard():
    from src.auth import ensure_bootstrap_admin, reset_rate_limits

    reset_rate_limits()
    ensure_bootstrap_admin()
    client = TestClient(app)
    login = client.post(
        "/login",
        data={"username": "admin", "password": "test-password-123"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 303
    assert home.headers["location"] == "/dashboard"
