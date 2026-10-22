import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from aiv.checks import check_schema_page, parse_robots, robots_allows
from aiv.crawl import normalize_url
from aiv.llm import engines_from_env, ProductionStubError
from aiv.llm.base import CostMeter, BudgetExceeded
from aiv.llm.providers import GeminiProvider, OpenAIProvider, OpenAICompatProvider
from aiv.llm.stub import StubProvider
from aiv.visibility import extract_brands, mentions_store, build_questions
from aiv.crawl import StoreProfile, Product
from aiv import server
from tests import fakeshop


class TestRobots(unittest.TestCase):
    def test_groups_and_wildcards(self):
        g, sm = parse_robots("User-agent: *\nDisallow: /cart\nDisallow: /*?q=\n\nUser-agent: GPTBot\nUser-agent: CCBot\n"
                             "Disallow: /\nAllow: /products/\nSitemap: https://x/sitemap.xml")
        self.assertEqual(sm, ["https://x/sitemap.xml"])
        self.assertFalse(robots_allows(g, "GPTBot", "/")[0])
        self.assertTrue(robots_allows(g, "GPTBot", "/products/a")[0])     # longest match wins
        self.assertTrue(robots_allows(g, "ClaudeBot", "/")[0])            # falls back to *
        self.assertFalse(robots_allows(g, "ClaudeBot", "/search?q=1")[0])  # wildcard
        self.assertFalse(robots_allows(g, "CCBot", "/x")[0])               # multi-agent group

    def test_empty_disallow_and_dollar(self):
        g, _ = parse_robots("User-agent: *\nDisallow:\n\nUser-agent: a\nDisallow: /*.pdf$")
        self.assertTrue(robots_allows(g, "x", "/anything")[0])
        self.assertFalse(robots_allows(g, "a", "/f.pdf")[0])
        self.assertTrue(robots_allows(g, "a", "/f.pdf?x")[0])


class TestSchema(unittest.TestCase):
    def test_complete_product(self):
        r = check_schema_page("u", fakeshop.PAGES["/products/wool-sock"], "product")
        self.assertEqual(r["missing_types"], [])
        self.assertTrue(r["has_aggregate_rating"])
        prod = [i for i in r["items"] if i["type"] == "Product"][0]
        self.assertEqual(prod["missing_required"], [])

    def test_microdata(self):
        r = check_schema_page("u", fakeshop.PAGES["/products/cotton-sock"], "product")
        self.assertEqual(r["microdata_types"], ["Offer", "Product"])
        self.assertEqual(r["missing_types"], [])

    def test_broken_and_contextless(self):
        r = check_schema_page("u", fakeshop.PAGES["/products/broken-sock"], "product")
        self.assertTrue(any("invalid JSON" in e or "not valid JSON" in e for e in r["parse_errors"]))
        self.assertTrue(any("@context" in e for e in r["parse_errors"]))
        self.assertFalse(r["has_aggregate_rating"])  # context-less block is not recognised (validator.schema.org agrees)

    def test_reference_stubs_ignored(self):
        html = ('<script type="application/ld+json">{"@context":"https://schema.org","@type":"ProductGroup","name":"G",'
                '"hasVariant":[{"@type":"Product","url":"/a"},{"@type":"Product","url":"/b"}]}</script>')
        r = check_schema_page("u", html, "product")
        self.assertFalse(any(i["missing_required"] for i in r["items"]))


class TestLLM(unittest.TestCase):
    def test_stub_deterministic(self):
        s = StubProvider("ChatGPT")
        a = s.complete("best socks", sample=1, store_hint="Foo").text
        self.assertEqual(a, s.complete("best socks", sample=1, store_hint="Foo").text)
        self.assertTrue(s.complete("x").simulated)

    def test_default_dev_is_stub(self):
        with mock.patch.dict(os.environ, {"AIV_ENV": "dev"}, clear=False):
            os.environ.pop("AIV_ENGINES", None)
            self.assertTrue(all(e.simulated for e in engines_from_env()))

    def test_production_refuses_stub(self):
        with mock.patch.dict(os.environ, {"AIV_ENV": "production"}):
            os.environ.pop("AIV_ENGINES", None)
            with self.assertRaises(ProductionStubError):
                engines_from_env()
            with self.assertRaises(ProductionStubError):
                engines_from_env("stub")

    def _sess(self, payload):
        resp = mock.Mock()
        resp.json.return_value = payload
        resp.raise_for_status.return_value = None
        s = mock.Mock()
        s.post.return_value = resp
        return s

    def test_gemini_parse(self):
        s = self._sess({"candidates": [{"content": {"parts": [{"text": "Try **Bombas**"}]},
                        "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://r.com/x"}}]}}],
                        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20}})
        r = GeminiProvider(api_key="k", session=s).complete("q")
        self.assertEqual(r.text, "Try **Bombas**")
        self.assertEqual(r.citations, ["https://r.com/x"])
        self.assertFalse(r.simulated)
        self.assertIn("google_search", json.dumps(s.post.call_args.kwargs["json"]))

    def test_openai_parse(self):
        s = self._sess({"output": [{"content": [{"type": "output_text", "text": "hi",
                        "annotations": [{"url": "https://a.com"}]}]}], "usage": {"input_tokens": 5, "output_tokens": 7}})
        r = OpenAIProvider(api_key="k", session=s).complete("q")
        self.assertEqual((r.text, r.citations, r.output_tokens), ("hi", ["https://a.com"], 7))
        self.assertGreater(r.cost_usd, 0)

    def test_compat_errors_dont_crash(self):
        s = mock.Mock()
        s.post.side_effect = RuntimeError("boom")
        r = OpenAICompatProvider(base_url="http://x", api_key="k", model="m", session=s).complete("q")
        self.assertIn("boom", r.error)

    def test_defaults(self):
        from aiv.visibility import DEFAULT_FULL_SAMPLES, samples_for
        self.assertEqual(CostMeter("x").budget_usd, 1.10)
        self.assertEqual([samples_for(StubProvider(n), DEFAULT_FULL_SAMPLES) for n in ("ChatGPT", "Perplexity", "Gemini")], [2, 2, 3])

    def test_budget(self):
        with tempfile.TemporaryDirectory() as d:
            p = OpenAIProvider(api_key="k", session=self._sess({"output": [], "usage": {}}))
            m = CostMeter("t", budget_usd=0.02, log_path=Path(d) / "l.jsonl")
            m.run(p, "q", "t")
            with self.assertRaises(BudgetExceeded):
                m.run(p, "q", "t")
            self.assertEqual(len((Path(d) / "l.jsonl").read_text().splitlines()), 1)


class TestVisibility(unittest.TestCase):
    def setUp(self):
        self.p = StoreProfile(base_url="https://www.allbirds.com/", final_url="https://www.allbirds.com/", name="Allbirds",
                              product_types=["Shoes"], products=[Product(url="u", title="Runner", price="100.00")], currency="USD")

    def test_mentions(self):
        self.assertTrue(mentions_store("I like Allbirds a lot", [], self.p))
        self.assertTrue(mentions_store("nothing", ["https://www.allbirds.com/x"], self.p))
        self.assertFalse(mentions_store("Try Bombas and Nike", [], self.p))

    def test_brands(self):
        b = extract_brands("1. **Bombas** – great\n2. Darn Tough: durable\n- [Smartwool](https://s.com) good\n**Conclusion**")
        self.assertEqual(b, ["Bombas", "Darn Tough", "Smartwool"])

    def test_question_counts(self):
        self.assertEqual(len(build_questions(self.p, 5)), 5)
        self.assertEqual(len(build_questions(self.p, 25)), 25)


class TestMisc(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_url("Allbirds.com/x?y"), "https://allbirds.com/")
        with self.assertRaises(ValueError):
            normalize_url("notaurl")

    def test_stripe_signature(self):
        body, secret, ts = b'{"a":1}', "whsec_test", int(time.time())
        sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        self.assertTrue(server.verify_stripe_signature(body, f"t={ts},v1={sig}", secret))
        self.assertFalse(server.verify_stripe_signature(body, f"t={ts},v1=deadbeef", secret))
        self.assertFalse(server.verify_stripe_signature(body, f"t={ts - 9999},v1={sig}", secret))

    def test_rate_limit(self):
        server._hits.clear()
        now = 1000.0
        results = [server.rate_ok("1.2.3.4", now + i) for i in range(server.FREE_PER_HOUR + 1)]
        self.assertEqual(results.count(False), 1)
        self.assertTrue(server.rate_ok("1.2.3.4", now + 3700))

    def test_ssrf_guard(self):
        with mock.patch.dict(os.environ, {"AIV_ALLOW_PRIVATE": "0"}):
            self.assertFalse(server.is_public_host("http://127.0.0.1/"))
            self.assertFalse(server.is_public_host("http://localhost/"))
            self.assertFalse(server.is_public_host("http://169.254.169.254/"))

    def test_purge(self):
        import importlib.util
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"AIV_DATA_DIR": d, "AIV_LOG_DIR": d}):
                spec = importlib.util.spec_from_file_location("purge", Path(__file__).parents[1] / "scripts/purge_old.py")
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                old = Path(d) / "reports" / "old"; old.mkdir(parents=True); (old / "a.html").write_text("x")
                new = Path(d) / "reports" / "new"; new.mkdir(parents=True); (new / "a.html").write_text("x")
                em = Path(d) / "emails"; em.mkdir(); (em / "m.eml").write_text("x")
                t = time.time() - 8 * 86400
                for f in (old / "a.html", old, em / "m.eml"):
                    os.utime(f, (t, t))
                (Path(d) / "llm_calls.jsonl").write_text(
                    json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(t))}) + "\n" +
                    json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}) + "\n")
                self.assertEqual(len(mod.purge(7, dry=True)), 3)
                mod.purge(7)
                self.assertFalse(old.exists())
                self.assertTrue(new.exists())
                self.assertFalse((em / "m.eml").exists())
                self.assertEqual(len((Path(d) / "llm_calls.jsonl").read_text().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()


class TestRealAnswerParsing(unittest.TestCase):
    """Regression cases taken from real Gemini answers (samples/gemini-real)."""

    def test_markdown_links_and_headings(self):
        from aiv.visibility import extract_brands
        txt = ("### **Best for Bags & Leather Goods**\n* **[Cuyana](https://www.cuyana.com)**: Known for totes.\n"
               "* **Why it’s great:** quality\n### **Multi-Brand & Designer Luxury**\n* **[Farfetch](https://www.farfetch.com):** global.\n"
               "**Tip for international shopping:** check duties.\n1. **[Natasha Denona](https://www.natashadenona.com)** (also at Sephora)\n")
        self.assertEqual(extract_brands(txt), ["Cuyana", "Farfetch", "Natasha Denona"])

    def test_gemini_grounding_redirect_citations(self):
        from aiv.llm.providers import GeminiProvider

        class R:
            status_code, ok, text = 200, True, ""
            def __init__(self, d): self._d = d
            def json(self): return self._d
            def raise_for_status(self): pass

        d = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "x", "thought": True}, {"text": "Try Allbirds."}]},
             "groundingMetadata": {"groundingChunks": [
                 {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc", "title": "nytimes.com"}},
                 {"web": {"uri": "https://example.org/review", "title": "Example"}}]}}],
             "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "thoughtsTokenCount": 7}, "modelVersion": "gemini-x"}

        class S:
            def post(self, *a, **k): return R(d)

        os.environ.setdefault("AIV_GEMINI_RPM", "0")
        g = GeminiProvider(api_key="k", model="gemini-x", session=S())
        g.rpm = 0
        r = g._complete("q")
        self.assertEqual(r.text, "Try Allbirds.")
        self.assertEqual(r.citations, ["https://nytimes.com/", "https://example.org/review"])
        self.assertEqual(r.output_tokens, 12)
        self.assertEqual(g.mode_note, "live Google Search grounding")

    def test_engine_modes(self):
        from aiv.llm import resolve_mode
        env = {k: os.environ.pop(k, None) for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "PERPLEXITY_API_KEY", "AIV_ENGINE_MODE")}
        try:
            os.environ["GEMINI_API_KEY"] = "g"
            self.assertEqual(resolve_mode("gemini-only"), ("gemini", []))
            spec, notes = resolve_mode("full")
            self.assertEqual(spec, "gemini")
            self.assertIn("engine mode 'full' fell back to 'gemini-only'", notes)
            os.environ["OPENAI_API_KEY"] = "o"
            spec, notes = resolve_mode("full")
            self.assertEqual(spec, "openai,gemini")
            self.assertEqual(notes, ["Perplexity skipped: PERPLEXITY_API_KEY not configured"])
            os.environ["PERPLEXITY_API_KEY"] = "__PENDING_APPROVAL__"
            self.assertEqual(resolve_mode("full")[0], "openai,gemini")
        finally:
            for k, v in env.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v


class TestCategory(unittest.TestCase):
    def test_store_wording_beats_narrow_product_types(self):
        from aiv.crawl import StoreProfile
        from aiv.visibility import _category
        p = StoreProfile(base_url="https://x/", name="Gymshark", description="Unlock your potential with workout clothes. Shop gym clothing.")
        self.assertEqual(_category(p), "gym clothing")
        p = StoreProfile(base_url="https://x/", name="ColourPop", description="Shop top-rated makeup for eyes, lips & face.",
                         product_types=["Peel Off Lip Stain", "Shadow Palette & Brush Set"])
        self.assertEqual(_category(p), "makeup")
        p = StoreProfile(base_url="https://x/", name="Acme", product_types=["Widgets"])
        self.assertEqual(_category(p), "widgets")


class TestRedact(unittest.TestCase):
    def test_keys_never_logged(self):
        from aiv.llm.base import redact
        os.environ["AIV_TEST_DUMMY"] = "x"
        self.assertEqual(redact("429 for url: https://h/v1/m:gen?key=AIzaSECRET123&alt=json"), "429 for url: https://h/v1/m:gen?key=REDACTED&alt=json")
