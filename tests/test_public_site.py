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
        self.assertEqual(build["id"], "ALD-CROWN-FOUNDING-13")
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

    def test_country_home_prioritises_living_editorial_hierarchy(self):
        text = (ROOT / "country" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Today has a shape", text)
        self.assertIn('id="home-events"', text)
        self.assertIn("Three ways further in.", text)
        self.assertIn('class="front-door-links"', text)
        self.assertNotIn('class="question-rails"', text)
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


    def test_website_engine_material_rebuild_architecture(self):
        home = (ROOT / "country" / "index.html").read_text(encoding="utf-8")
        for marker in (
            'class="national-broadside"',
            'class="section country-desk"',
            'class="region-ribbon"',
            'class="section live-desk"',
            'class="front-door-links"',
            "Useful today",
            "The national desk",
        ):
            self.assertIn(marker, home)
        self.assertNotIn('class="grid-3 route-grid"', home)

        for page in COUNTRY_PAGES:
            text = page.read_text(encoding="utf-8")
            self.assertIn('class="country-utility"', text, page)
            self.assertNotIn(r"\\n<div class=\"country-utility\"", text, page)

    def test_subject_pages_use_distinct_compositions(self):
        government = (ROOT / "country" / "government.html").read_text(encoding="utf-8")
        economy = (ROOT / "country" / "economy.html").read_text(encoding="utf-8")
        life = (ROOT / "country" / "life.html").read_text(encoding="utf-8")
        media = (ROOT / "country" / "media.html").read_text(encoding="utf-8")
        self.assertIn('class="power-flow"', government)
        self.assertIn('class="institution-ledger"', government)
        self.assertIn('class="regional-economy"', economy)
        self.assertIn('class="economy-lenses"', economy)
        self.assertIn('class="wrap life-desk"', life)
        self.assertIn('class="day-rhythm"', life)
        self.assertIn('class="newsroom-grid"', media)
        self.assertIn("The desk today", media)
        self.assertIn("Reality class: scheduled public-fictional editorial feature.", media)


    def test_threshold_explains_fiction_and_real_experiment_up_front(self):
        text = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("<strong>Aldernia is a fictional country.</strong>", text)
        self.assertIn("The Aldernian Experiment is the real project behind it", text)
        self.assertLess(text.index('href="country/"'), text.index('href="experiment/"'))

    def test_experiment_exposes_method_falsification_and_measurement_limits(self):
        text = (ROOT / "experiment" / "index.html").read_text(encoding="utf-8")
        for marker in (
            "Research design",
            "What would count against the approach?",
            "A clean controlled comparison",
            "Measurement plan",
            "no result is published until the denominator, method and observation period exist",
            "Human burden",
        ):
            self.assertIn(marker, text)
        self.assertNotIn("success rate: 100%", text.lower())

    def test_media_demonstrates_editorial_work_not_release_notes(self):
        text = (ROOT / "country" / "media.html").read_text(encoding="utf-8")
        self.assertIn("The desk today", text)
        self.assertIn("St Aurelia is more than the government quarter.", text)
        self.assertIn("Reality class: scheduled public-fictional editorial feature.", text)
        self.assertNotIn("Founding archive", text)
        self.assertNotIn("Civic-depth edition", text)

    def test_public_pages_have_canonical_and_share_metadata(self):
        for page in PUBLIC_PAGES:
            text = page.read_text(encoding="utf-8")
            self.assertEqual(text.count('rel="canonical"'), 1, page)
            self.assertEqual(text.count('property="og:title"'), 1, page)
            self.assertEqual(text.count('property="og:description"'), 1, page)
            self.assertEqual(text.count('property="og:url"'), 1, page)
            self.assertEqual(text.count('name="twitter:card"'), 1, page)

    def test_discovery_and_not_found_files_exist(self):
        sitemap = ROOT / "sitemap.xml"
        robots = ROOT / "robots.txt"
        not_found = ROOT / "404.html"
        self.assertTrue(sitemap.exists())
        self.assertTrue(robots.exists())
        self.assertTrue(not_found.exists())
        sitemap_text = sitemap.read_text(encoding="utf-8")
        self.assertIn("https://wally189.github.io/The-Crown-Dynasty-Of-Aldernia/", sitemap_text)
        self.assertIn("/country/media.html", sitemap_text)
        self.assertNotIn("/404.html", sitemap_text)
        self.assertIn("Sitemap:", robots.read_text(encoding="utf-8"))
        not_found_text = not_found.read_text(encoding="utf-8")
        self.assertIn('name="robots" content="noindex"', not_found_text)
        self.assertEqual(not_found_text.count("<h1"), 1)

    def test_material_page_sections_are_balanced(self):
        for page in PUBLIC_PAGES:
            text = page.read_text(encoding="utf-8")
            self.assertEqual(text.count("<section"), text.count("</section>"), page)

    def test_calendar_preview_reuses_governed_calendar_without_new_data_source(self):
        home = (ROOT / "country" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "assets" / "today.js").read_text(encoding="utf-8")
        self.assertIn('data-calendar="../aldernia/calendar.json"', home)
        self.assertIn("home-events", script)
        self.assertIn("public-fictional calendar", script)

    def test_non_clock_pages_do_not_run_one_second_timer(self):
        script = (ROOT / "assets" / "site.js").read_text(encoding="utf-8")
        self.assertIn("hasSecondClock", script)
        self.assertIn("60000", script)
        self.assertIn("if (hasSecondClock)", script)
        self.assertIn("cache: 'no-cache'", script)


if __name__ == "__main__":
    unittest.main()
