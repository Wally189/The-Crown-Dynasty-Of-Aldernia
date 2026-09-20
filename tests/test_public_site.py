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
    ROOT / "country" / "places.html",
    ROOT / "country" / "government.html",
    ROOT / "country" / "life.html",
    ROOT / "country" / "media.html",
    ROOT / "country" / "learn.html",
    ROOT / "privacy.html",
    ROOT / "accessibility.html",
]
COUNTRY_PAGES = [p for p in PUBLIC_PAGES if "country" in p.parts]


class AuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.h1 = 0
        self.title = False
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

    def handle_data(self, data):
        if data.strip():
            self.title = self.title or False


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

    def test_country_pages_disclose_fiction(self):
        phrase = "fictional"
        for page in COUNTRY_PAGES:
            text = page.read_text(encoding="utf-8").lower()
            self.assertIn(phrase, text, page)

    def test_index_preserves_two_clocks_and_two_facets(self):
        text = (ROOT / "index.html").read_text(encoding="utf-8")
        for required in ("data-computing-time", "data-human-time", "experiment/", "country/"):
            self.assertIn(required, text)

    def test_no_tracking_or_account_code_in_public_assets(self):
        corpus = "\n".join(p.read_text(encoding="utf-8").lower() for p in PUBLIC_PAGES + [ROOT / "assets" / "site.js"])
        blocked = ("google-analytics.com", "googletagmanager.com", "facebook.com/tr", "hotjar", "segment.io")
        for token in blocked:
            self.assertNotIn(token, corpus)

    def test_build_identity_is_single_json_source(self):
        build = json.loads((ROOT / "aldernia" / "build.json").read_text(encoding="utf-8"))
        self.assertEqual(build["schema_version"], 1)
        self.assertTrue(build["id"].startswith("ALD-"))
        self.assertEqual(build["facets"], ["experiment", "country"])


if __name__ == "__main__":
    unittest.main()
