from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_PAGES = [
    ROOT / "index.html",
    ROOT / "experiment" / "index.html",
    ROOT / "country" / "index.html",
    ROOT / "country" / "today.html",
    ROOT / "country" / "history.html",
    ROOT / "country" / "assembly.html",
    ROOT / "country" / "community.html",
    ROOT / "country" / "places.html",
    ROOT / "country" / "map.html",
    ROOT / "country" / "government.html",
    ROOT / "country" / "services.html",
    ROOT / "country" / "economy.html",
    ROOT / "country" / "infrastructure.html",
    ROOT / "country" / "culture.html",
    ROOT / "country" / "life.html",
    ROOT / "country" / "media.html",
    ROOT / "country" / "learn.html",
    ROOT / "country" / "data.html",
    ROOT / "privacy.html",
    ROOT / "accessibility.html",
]
COUNTRY_PAGES = [p for p in PUBLIC_PAGES if "country" in p.parts]


class AuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.h1 = 0
        self.external_scripts: list[str] = []
        self.inline_scripts = 0
        self.skip_link = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "h1":
            self.h1 += 1
        if tag == "a" and a.get("href", "").startswith("#") and "skip-link" in a.get("class", ""):
            self.skip_link = True
        if tag == "script":
            src = a.get("src")
            if src and src.startswith(("http://", "https://", "//")):
                self.external_scripts.append(src)
            if not src:
                self.inline_scripts += 1


class PublicSiteTests(unittest.TestCase):
    def test_public_pages_exist(self):
        for page in PUBLIC_PAGES:
            self.assertTrue(page.exists(), page)

    def test_every_page_has_one_h1_skip_link_and_no_external_script(self):
        for page in PUBLIC_PAGES:
            parser = AuditParser()
            parser.feed(page.read_text(encoding="utf-8"))
            self.assertEqual(parser.h1, 1, page)
            self.assertTrue(parser.skip_link, page)
            self.assertEqual(parser.external_scripts, [], page)
            self.assertEqual(parser.inline_scripts, 0, page)

    def test_favicon_declared_on_every_public_page(self):
        for page in PUBLIC_PAGES:
            text = page.read_text(encoding="utf-8")
            self.assertIn('rel="icon"', text, page)
        self.assertTrue((ROOT / "favicon.svg").exists())

    def test_public_data_uses_semantic_list_container(self):
        text = (ROOT / "country" / "data.html").read_text(encoding="utf-8")
        self.assertIn('<ul class="source-list">', text)
        self.assertNotIn('<div class="source-list">', text)

    def test_country_pages_disclose_fiction(self):
        for page in COUNTRY_PAGES:
            self.assertIn("fictional", page.read_text(encoding="utf-8").lower(), page)

    def test_country_navigation_contains_static_atlas_link(self):
        for page in COUNTRY_PAGES:
            text = page.read_text(encoding="utf-8")
            self.assertIn('href="map.html">Atlas</a>', text, page)

    def test_country_front_door_keeps_backstage_controls_backstage(self):
        corpus = "\n".join(
            (ROOT / rel).read_text(encoding="utf-8").lower()
            for rel in ("country/index.html", "country/today.html", "country/map.html")
        )
        for token in (
            "retained design evidence",
            "fresh crown commission",
            "governed editorial calendar",
            "world-state",
            "runtime restart",
            "readiness gate",
            "commissions subjects",
        ):
            self.assertNotIn(token, corpus)

    def test_index_preserves_two_clocks_and_two_facets(self):
        text = (ROOT / "index.html").read_text(encoding="utf-8")
        for required in (
            "data-computing-time",
            "data-human-time",
            "experiment/",
            "country/",
            "Aldernia begins when time becomes shared.",
            "Then someone calls it today.",
            "Aldernia is a fictional country and participatory editorial world.",
        ):
            self.assertIn(required, text)

    def test_no_tracking_or_account_code_in_public_assets(self):
        corpus = "\n".join(p.read_text(encoding="utf-8").lower() for p in PUBLIC_PAGES + [ROOT / "assets" / "site.js", ROOT / "assets" / "today.js"])
        blocked = ("google-analytics.com", "googletagmanager.com", "facebook.com/tr", "hotjar", "segment.io")
        for token in blocked:
            self.assertNotIn(token, corpus)

    def test_build_identity_is_single_json_source(self):
        build = json.loads((ROOT / "aldernia" / "build.json").read_text(encoding="utf-8"))
        self.assertEqual(build["schema_version"], 1)
        self.assertEqual(build["id"], "ALD-CROWN-FOUNDING-11")
        self.assertEqual(build["facets"], ["experiment", "country"])
        self.assertIn("today", build["country_layers"])
        self.assertIn("atlas", build["country_layers"])
        self.assertIn("data", build["country_layers"])
        self.assertIn("assembly", build["country_layers"])
        self.assertIn("community", build["country_layers"])

    def test_calendar_is_governed_fiction_not_implicit_news(self):
        cal = json.loads((ROOT / "aldernia" / "calendar.json").read_text(encoding="utf-8"))
        self.assertEqual(cal["schema_version"], 2)
        self.assertEqual(cal["timezone"], "Europe/London")
        self.assertEqual(cal["reality"], "public-fictional")
        self.assertTrue(cal["authority"])
        ids = set()
        for event in cal["events"]:
            self.assertNotIn(event["id"], ids)
            ids.add(event["id"])
            self.assertRegex(event["date"], r"^2026-\d{2}-\d{2}$")
            self.assertIn(event["status"], {"SCHEDULED", "COMPLETE", "CANCELLED", "DEFERRED"})
            self.assertIn("public-fictional", event["reality"])
            self.assertTrue(event["title"])
            self.assertTrue(event["summary"])

        recurring = cal["recurring_weekly"]
        self.assertEqual(recurring["effective_from"], "2026-09-28")
        beats = recurring["beats"]
        self.assertEqual(len(beats), 7)
        self.assertEqual({beat["weekday"] for beat in beats}, set(range(1, 8)))
        for beat in beats:
            self.assertIn("public-fictional", beat["reality"])
            self.assertTrue(beat["title"])
            self.assertTrue(beat["summary"])

    def test_atlas_is_accessible_schematic_not_false_precision(self):
        text = (ROOT / "country" / "map.html").read_text(encoding="utf-8")
        lower = text.lower()
        self.assertIn('aria-labelledby="atlas-title atlas-desc"', text)
        self.assertIn("schematic", lower)
        self.assertIn("not a surveyed map", lower)
        self.assertIn('tabindex="0"', text)
        self.assertIn('role="region"', text)
        for label in ("St Aurelia", "Merrow", "Northmere", "Bracken Coast", "Eastvale", "High Alder", "Southmarch", "St Brigid", "Kestrels"):
            self.assertIn(label, text)

    def test_national_visual_tokens_exist_without_external_asset_dependency(self):
        css = (ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
        for token in ("--alder-deep", "--alder-harbour", "--alder-wheat", ".aldernia-map", ".country-surface"):
            self.assertIn(token, css)

    def test_real_participation_and_money_remain_gated(self):
        build = json.loads((ROOT / "aldernia" / "build.json").read_text(encoding="utf-8"))
        posture = build["privacy_posture"].lower()
        for word in ("accounts", "payments", "donations", "comments", "public voting"):
            self.assertIn(word, posture)


    def test_country_shell_is_static_and_consistent(self):
        required_nav = (
            'href="./">Home</a>',
            'href="today.html">Today</a>',
            'href="map.html">Atlas</a>',
            'href="places.html">Places</a>',
            'href="life.html">Life</a>',
            'href="government.html">Government</a>',
            'href="media.html">Media</a>',
            'href="learn.html">Learn</a>',
        )
        for page in COUNTRY_PAGES:
            text = page.read_text(encoding="utf-8")
            self.assertIn('<body class="country-surface country-zone-', text, page)
            self.assertIn('aria-label="Country"', text, page)
            for nav in required_nav:
                self.assertIn(nav, text, page)

    def test_country_pages_have_deliberate_journey_handoffs(self):
        for page in COUNTRY_PAGES:
            if page.name == "index.html":
                continue
            text = page.read_text(encoding="utf-8")
            self.assertIn('class="section journey-section"', text, page)
            self.assertIn('aria-label="Continue through Aldernia"', text, page)

    def test_public_country_copy_keeps_internal_runtime_backstage(self):
        corpus = "\n".join(page.read_text(encoding="utf-8").lower() for page in COUNTRY_PAGES)
        for token in (
            "computational-bus operating lease",
            "central aldernia clock can supply",
            "timer can wake the system",
            "fresh crown commission",
            "current fictional-world publication state",
        ):
            self.assertNotIn(token, corpus)

    def test_country_home_routes_by_human_question_not_flat_directory(self):
        text = (ROOT / "country" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Start with the question you actually have.", text)
        self.assertIn("What is happening?", text)
        self.assertIn("Where am I?", text)
        self.assertIn("How does it work?", text)
        self.assertNotIn("Choose a door", text)


    def test_internal_public_links_resolve(self):
        from urllib.parse import urlsplit
        repo_prefix = "/The-Crown-Dynasty-Of-Aldernia/"
        for page in PUBLIC_PAGES:
            parser_text = page.read_text(encoding="utf-8")
            for href in __import__("re").findall(r"""href=["']([^"']+)["']""", parser_text):
                if not href or href.startswith(("#", "http://", "https://", "mailto:", "tel:")):
                    continue
                clean = urlsplit(href).path
                if clean.startswith(repo_prefix):
                    target = ROOT / clean[len(repo_prefix):]
                elif clean.startswith("/"):
                    continue
                else:
                    target = (page.parent / clean).resolve()
                if clean.endswith("/"):
                    target = target / "index.html"
                self.assertTrue(target.exists(), f"{page}: broken internal href {href} -> {target}")


if __name__ == "__main__":
    unittest.main()
