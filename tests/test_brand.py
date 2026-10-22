"""AI Seen Lab brand assets: sanitized SVGs, real images, and the release check only lets these exact files through."""
import hashlib
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "brand"
FILES = ["aiseenlab_icon.svg", "aiseenlab_icon.png", "favicon-16.png", "favicon-32.png", "apple-touch-icon.png", "favicon.ico"]


class TestBrand(unittest.TestCase):
    def test_files_are_valid_and_clean(self):
        for f in FILES:
            self.assertTrue((BRAND / f).is_file(), f)
        svg = (BRAND / "aiseenlab_icon.svg").read_text()
        self.assertNotRegex(svg, re.compile(r"<script|\son\w+=|href=|url\(|<image|<use|<foreignObject|<style|DRAFT", re.I))
        tags = {e.tag.split("}")[-1] for e in ET.fromstring(svg).iter()}
        self.assertLessEqual(tags, {"svg", "title", "rect", "path", "circle"})
        for f, size in (("aiseenlab_icon.png", (512, 512)), ("favicon-16.png", (16, 16)), ("favicon-32.png", (32, 32)),
                        ("apple-touch-icon.png", (180, 180))):
            im = Image.open(BRAND / f)
            im.verify()
            self.assertEqual(Image.open(BRAND / f).size, size)
        qr = Path("/workspace/payment/wechat_pay_qr.jpg")
        if qr.is_file():
            h = hashlib.sha256(qr.read_bytes()).hexdigest()
            self.assertFalse(any(hashlib.sha256((BRAND / f).read_bytes()).hexdigest() == h for f in FILES))

    def test_release_check_allows_only_brand_files(self):
        with tempfile.TemporaryDirectory() as d:
            site = Path(d)
            (site / "brand").mkdir()
            for f in FILES:
                shutil.copyfile(BRAND / f, site / "brand" / f)
            shutil.copyfile(BRAND / "favicon.ico", site / "favicon.ico")
            ok = subprocess.run(["python3", str(ROOT / "scripts" / "check_release.py"), d], capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stdout)
            Image.new("RGB", (64, 64), "black").save(site / "brand" / "aiseenlab_icon.png")   # same name, other picture
            bad = subprocess.run(["python3", str(ROOT / "scripts" / "check_release.py"), d], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)
            self.assertIn("isn't a brand/ file", bad.stdout)

    def test_landing_references_brand(self):
        for name in ("index.html", "terms.html", "privacy.html"):
            html = (ROOT / "landing" / name).read_text()
            self.assertIn('href="/brand/aiseenlab_icon.svg" type="image/svg+xml"', html)
            self.assertIn('href="https://aiseenlab.com/"', html)
            self.assertIn("by AI Seen Lab", html)
