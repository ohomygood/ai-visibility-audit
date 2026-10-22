"""End-to-end against the local fake shop (no internet)."""
import json
import os
import tempfile
import time
import unittest
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

from tests import fakeshop


class TestE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.url = fakeshop.start()
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["AIV_LOG_DIR"] = cls.tmp.name
        os.environ["AIV_NO_PDF"] = "1"
        os.environ["AIV_NO_RENDER"] = "1"   # keep tests offline/fast; JS rendering is tested with a mock below

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def test_quick(self):
        from aiv.audit import run_audit
        out = Path(self.tmp.name) / "quick"
        r = run_audit(self.url, "quick", engines_spec="stub", out_dir=out, min_interval=0.0)
        self.assertEqual(r["profile"]["platform"], "shopify")
        self.assertEqual(r["profile"]["currency"], "USD")
        self.assertEqual(r["bot_access"]["bots"]["PerplexityBot"]["verdict"], "blocked")   # simulated Bot Fight Mode 403
        self.assertTrue(all(p["status"] == 403 for p in r["bot_access"]["bots"]["PerplexityBot"]["pages"]))
        self.assertIn("cloudflare", r["bot_access"]["cdn"])
        self.assertEqual(r["bot_access"]["bots"]["OAI-SearchBot"]["verdict"], "ok")
        self.assertFalse(r["robots"]["tokens"]["GPTBot"]["allowed"])
        self.assertTrue(r["robots"]["tokens"]["OAI-SearchBot"]["allowed"])
        self.assertFalse(r["llms_txt"]["llms.txt"]["exists"])
        keys = {f["key"] for f in r["findings"]}
        self.assertIn("bot_blocked:PerplexityBot", keys)
        self.assertIn("robots:GPTBot", keys)
        self.assertIn("llms_txt", keys)
        self.assertEqual(len(r["visibility"]["questions"]), 5)
        self.assertEqual(r["llm_calls"], 10)  # 5 questions x 2 engines
        self.assertTrue(r["simulated"])
        html = (out / "report.zh.html").read_text()
        self.assertIn("模拟引擎", html)
        self.assertIn("SIMULATED", (out / "report.md").read_text())
        self.assertFalse((out / "fixpack.zip").exists())
        self.assertLess(r["total_seconds"], 60)

    def test_full(self):
        from aiv.audit import run_audit
        out = Path(self.tmp.name) / "full"
        r = run_audit(self.url, "full", engines_spec="stub", out_dir=out, min_interval=0.0)
        self.assertEqual(r["llm_calls"], 25 * (2 + 3 + 2))  # ChatGPT 2, Gemini 3, Perplexity 2
        self.assertEqual(r["visibility"]["samples_per_question"], {"ChatGPT": 2, "Gemini": 3, "Perplexity": 2})
        self.assertEqual(r["llm_cost_usd"], 0.0)
        z = zipfile.ZipFile(out / "fixpack.zip")
        names = set(z.namelist())
        for n in ("fixpack/robots.txt.snippet", "fixpack/llms.txt", "fixpack/jsonld/organization.jsonld",
                  "fixpack/jsonld/product-wool-sock.jsonld", "fixpack/shopify/aiv-product-jsonld.liquid",
                  "fixpack/cloudflare_allow_ai_crawlers.md", "fixpack/README.md", "fixpack/SIMULATED.txt"):
            self.assertIn(n, names)
        self.assertEqual(len([n for n in names if n.startswith("fixpack/content/")]), 5)
        pj = json.loads(z.read("fixpack/jsonld/product-wool-sock.jsonld"))
        self.assertEqual((pj["offers"]["price"], pj["offers"]["priceCurrency"], pj["gtin"]), ("12.00", "USD", "0123456789012"))
        robots = z.read("fixpack/robots.txt.snippet").decode()
        self.assertIn("User-agent: GPTBot", robots)
        self.assertIn("Currently blocked on your site: GPTBot, CCBot", robots)
        llms = z.read("fixpack/llms.txt").decode()
        self.assertTrue(llms.startswith("# Fake Socks Co"))
        self.assertIn("wool-sock", llms)
        # our own generated JSON-LD must pass our own validator
        from aiv.checks import check_schema_page
        page = f'<script type="application/ld+json">{json.dumps(pj)}</script>'
        res = check_schema_page("u", page, "product")
        self.assertFalse(any(i["missing_required"] for i in res["items"]))

    def test_js_only_schema(self):
        from aiv.audit import run_audit
        with mock.patch("aiv.checks.render_dom", return_value=fakeshop.PAGES["/products/wool-sock"]):
            r = run_audit(self.url, "quick", engines_spec="stub", out_dir=Path(self.tmp.name) / "js", min_interval=0.0)
        f = [x for x in r["findings"] if x["key"] == "js_only"]
        self.assertTrue(f)
        self.assertIn("JavaScript", f[0]["en"])

    def test_unreachable_is_inconclusive(self):
        from aiv.audit import run_audit
        srv, url = fakeshop.start(block_all=True)
        try:
            r = run_audit(url, "quick", engines_spec="stub", out_dir=Path(self.tmp.name) / "blocked", min_interval=0.0)
        finally:
            srv.shutdown()
        self.assertTrue(r["site_unreachable"])
        self.assertIsNone(r["score"]["total"])
        keys = {f["key"] for f in r["findings"]}
        self.assertIn("unreachable", keys)
        self.assertNotIn("llms_txt", keys)     # 429 is not evidence that llms.txt is missing
        self.assertNotIn("sitemap", keys)

    def test_server_flow(self):
        from aiv import server
        with mock.patch.dict(os.environ, {"AIV_ALLOW_PRIVATE": "1", "AIV_DEV_UNLOCK": "1", "AIV_ENGINES": "stub"}):
            with mock.patch("aiv.server.DATA_DIR", Path(self.tmp.name) / "data"), \
                 mock.patch("aiv.audit.DATA_DIR", Path(self.tmp.name) / "data"):
                srv = server.make_server("127.0.0.1", 0)
                import threading
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                base = f"http://127.0.0.1:{srv.server_address[1]}"
                try:
                    html = urllib.request.urlopen(base + "/").read().decode()
                    self.assertIn("$19", html)
                    self.assertIn("buy.stripe.com/fZu00k2l84Zz9YibSt0Ny02", urllib.request.urlopen(base + "/config.js").read().decode())
                    self.assertIn("退款政策", html)
                    self.assertIn("7 天", urllib.request.urlopen(base + "/privacy.html").read().decode())
                    server._hits.clear()
                    with mock.patch("aiv.server.run_audit", wraps=lambda u, mode, out_dir: __import__("aiv.audit").audit.run_audit(
                            u, mode=mode, out_dir=out_dir, min_interval=0.0)):
                        req = urllib.request.Request(base + "/api/check", data=json.dumps({"url": self.url}).encode(),
                                                     headers={"Content-Type": "application/json"})
                        j = json.loads(urllib.request.urlopen(req).read())
                        aid = j["audit_id"]
                        self.assertIn("score", j)
                        self.assertIn("SIMULATED", urllib.request.urlopen(base + j["report_url"]).read().decode())
                        with self.assertRaises(urllib.error.HTTPError) as cm:
                            urllib.request.urlopen(base + f"/r/{aid}/full/fixpack.zip")
                        self.assertEqual(cm.exception.code, 402)
                        urllib.request.urlopen(base + f"/dev/unlock/{aid}")
                        for _ in range(60):
                            st = json.loads(urllib.request.urlopen(base + f"/api/status/{aid}").read())
                            if st["full_ready"]:
                                break
                            time.sleep(0.5)
                        self.assertTrue(st["paid"] and st["full_ready"])
                        self.assertEqual(urllib.request.urlopen(base + f"/r/{aid}/full/fixpack.zip").status, 200)
                    # stripe webhook not configured -> 503 (pending approval)
                    req = urllib.request.Request(base + "/api/stripe/webhook", data=b"{}", method="POST")
                    with self.assertRaises(urllib.error.HTTPError) as cm:
                        urllib.request.urlopen(req)
                    self.assertEqual(cm.exception.code, 503)
                finally:
                    srv.shutdown()


if __name__ == "__main__":
    unittest.main()
