"""Store discovery: normalise the URL, detect the platform, find product pages
and build a small store profile used for questions and fix files."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from lxml import etree

from .http import PoliteSession, BROWSER_UA


def normalize_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    p = urlparse(url)
    if not p.netloc or "." not in p.netloc and not p.netloc.startswith(("localhost", "127.")):
        raise ValueError(f"not a valid store URL: {url!r}")
    return f"{p.scheme.lower()}://{p.netloc.lower()}/"


@dataclass
class Product:
    url: str
    title: str = ""
    handle: str = ""
    vendor: str = ""
    product_type: str = ""
    tags: list = field(default_factory=list)
    description: str = ""
    price: str = ""
    compare_at_price: str = ""
    sku: str = ""
    available: bool | None = None
    image: str = ""
    gtin: str = ""


@dataclass
class StoreProfile:
    base_url: str
    final_url: str = ""
    platform: str = "unknown"
    name: str = ""
    description: str = ""
    currency: str = ""
    language: str = ""
    logo: str = ""
    social: list = field(default_factory=list)
    product_types: list = field(default_factory=list)
    collections: list = field(default_factory=list)
    policy_pages: list = field(default_factory=list)
    products: list = field(default_factory=list)  # list[Product]
    homepage_status: int | None = None
    notes: list = field(default_factory=list)

    @property
    def domain(self) -> str:
        return urlparse(self.final_url or self.base_url).netloc.removeprefix("www.")

    def to_dict(self):
        d = asdict(self)
        d["domain"] = self.domain
        return d


SOCIAL_HOSTS = ("instagram.com", "facebook.com", "tiktok.com", "youtube.com", "x.com",
                "twitter.com", "pinterest.com", "linkedin.com", "weibo.com", "xiaohongshu.com")


def _strip_html(s: str, limit: int = 600) -> str:
    t = BeautifulSoup(s or "", "lxml").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", t)[:limit]


def _brand_from_title(title: str) -> str:
    for sep in (" | ", " – ", " — ", " - ", ": "):
        if sep in title:
            parts = [p.strip() for p in title.split(sep) if p.strip()]
            # brand is usually the shortest segment at the end or start
            return min((parts[-1], parts[0]), key=len)
    return title.strip()


def detect_platform(html: str, headers: dict) -> str:
    h = " ".join(f"{k}:{v}" for k, v in headers.items()).lower()
    if "x-shopid" in h or "x-shopify" in h or "cdn.shopify.com" in html or "Shopify.theme" in html:
        return "shopify"
    if "woocommerce" in html.lower():
        return "woocommerce"
    if "bigcommerce" in html.lower():
        return "bigcommerce"
    if "shoplazza" in html.lower() or "shoplazza" in h:
        return "shoplazza"
    if "static.wixstatic.com" in html:
        return "wix"
    return "unknown"


def parse_homepage(profile: StoreProfile, html: str):
    soup = BeautifulSoup(html, "lxml")
    og = lambda p: (soup.find("meta", property=p) or {}).get("content", "")  # noqa: E731
    title = soup.title.get_text(strip=True) if soup.title else ""
    profile.name = og("og:site_name") or _brand_from_title(title) or profile.domain
    desc = soup.find("meta", attrs={"name": "description"})
    profile.description = (desc.get("content", "") if desc else "") or og("og:description")
    html_tag = soup.find("html")
    profile.language = (html_tag.get("lang", "") if html_tag else "")[:10]
    m = re.search(r'Shopify\.currency\s*=\s*\{"active":"([A-Z]{3})"', html) or \
        re.search(r'"currency(?:Code)?"\s*:\s*"([A-Z]{3})"', html) or \
        re.search(r'"priceCurrency"\s*:\s*"([A-Z]{3})"', html)
    if m:
        profile.currency = m.group(1)
    social = set()
    collections, policies = [], []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        absu = urljoin(profile.final_url or profile.base_url, href)
        host = urlparse(absu).netloc.lower()
        if any(host.endswith(s) for s in SOCIAL_HOSTS):
            path = urlparse(absu).path.strip("/")
            if path and "share" not in path and "intent" not in path:
                social.add(absu.split("?")[0])
        elif host == urlparse(profile.final_url or profile.base_url).netloc.lower():
            path = urlparse(absu).path
            if path.startswith("/collections/") and path.count("/") == 2 and absu not in collections:
                collections.append(absu.split("?")[0])
            if path.startswith("/policies/") or re.search(r"/(pages/)?(faq|shipping|returns?|refund|about)", path):
                u = absu.split("?")[0]
                if u not in policies:
                    policies.append(u)
    profile.social = sorted(social)[:12]
    profile.collections = collections[:15]
    profile.policy_pages = policies[:10]
    logo = soup.find("meta", property="og:image")
    profile.logo = logo.get("content", "") if logo else ""
    if profile.logo.startswith("//"):
        profile.logo = "https:" + profile.logo


def _shopify_products(sess: PoliteSession, base: str, n: int) -> list[Product]:
    f = sess.get(urljoin(base, f"products.json?limit={max(n, 30)}"), ua=BROWSER_UA, ua_label="discovery",
                 accept="application/json")
    if not f.ok:
        return []
    try:
        data = json.loads(f.text)
    except ValueError:
        return []
    out = []
    for p in data.get("products", []):
        v = (p.get("variants") or [{}])[0]
        imgs = p.get("images") or []
        out.append(Product(
            url=urljoin(base, f"products/{p.get('handle')}"),
            title=p.get("title", ""), handle=p.get("handle", ""), vendor=p.get("vendor", ""),
            product_type=p.get("product_type", ""),
            tags=(p.get("tags") if isinstance(p.get("tags"), list) else str(p.get("tags", "")).split(", "))[:10],
            description=_strip_html(p.get("body_html", "")), price=str(v.get("price") or ""),
            compare_at_price=str(v.get("compare_at_price") or ""), sku=v.get("sku") or "",
            available=v.get("available"), image=(imgs[0].get("src") if imgs else ""),
            gtin=v.get("barcode") or ""))
    # skip non-merchandise (gift cards, hidden "content" products, $0 items, no image)
    def junk(x: Product) -> bool:
        h = (x.handle + " " + x.product_type + " " + x.title).lower()
        return ("gift" in h and "card" in h) or "content" in x.handle or not x.image or x.price in ("", "0", "0.00")
    good = [x for x in out if not junk(x)]
    out = good or out
    # prefer available products with a type, spread across types
    out.sort(key=lambda x: (x.available is False, not x.product_type))
    picked, seen_types = [], set()
    for p in out:
        if p.product_type not in seen_types:
            picked.append(p)
            seen_types.add(p.product_type)
    for p in out:
        if p not in picked:
            picked.append(p)
    return picked


def _sitemap_products(sess: PoliteSession, base: str, n: int) -> list[Product]:
    f = sess.get(urljoin(base, "sitemap.xml"), ua=BROWSER_UA, ua_label="discovery", accept="application/xml")
    if not f.ok:
        return []

    def locs(xml: str):
        try:
            root = etree.fromstring(xml.encode("utf-8", "replace"), parser=etree.XMLParser(recover=True))
        except etree.XMLSyntaxError:
            return []
        return [e.text.strip() for e in root.iter() if e.tag.endswith("loc") and e.text]

    urls = locs(f.text)
    child = [u for u in urls if "product" in u.lower() and u.endswith(".xml") or "sitemap_products" in u]
    if child:
        c = sess.get(child[0], ua=BROWSER_UA, ua_label="discovery", accept="application/xml")
        urls = locs(c.text) if c.ok else []
    prods = [u for u in urls if re.search(r"/products?/[^/]+/?$", u)]
    return [Product(url=u, handle=u.rstrip("/").rsplit("/", 1)[-1]) for u in prods[: n * 3]]


def _homepage_products(profile: StoreProfile, html: str) -> list[Product]:
    soup = BeautifulSoup(html, "lxml")
    seen, out = set(), []
    base_host = urlparse(profile.final_url or profile.base_url).netloc
    for a in soup.find_all("a", href=True):
        u = urljoin(profile.final_url or profile.base_url, a["href"]).split("?")[0].split("#")[0]
        if urlparse(u).netloc == base_host and re.search(r"/products?/[^/]+/?$", u) and u not in seen:
            seen.add(u)
            out.append(Product(url=u, handle=u.rstrip("/").rsplit("/", 1)[-1], title=a.get_text(" ", strip=True)[:120]))
    return out


def discover(sess: PoliteSession, url: str, n_products: int = 3) -> tuple[StoreProfile, "object"]:
    """Returns (profile, homepage Fetch with a browser UA)."""
    base = normalize_url(url)
    profile = StoreProfile(base_url=base)
    home = sess.get(base, ua=BROWSER_UA, ua_label="baseline")
    profile.homepage_status = home.status
    profile.final_url = home.final_url if home.status else base
    if not home.ok:
        profile.notes.append(f"homepage returned {home.status or home.error}")
    profile.platform = detect_platform(home.text, home.headers)
    parse_homepage(profile, home.text)
    root = f"{urlparse(profile.final_url).scheme}://{urlparse(profile.final_url).netloc}/"
    products: list[Product] = []
    if profile.platform == "shopify":
        products = _shopify_products(sess, root, n_products)
    def merge(more):
        seen = {p.url.rstrip("/") for p in products}
        for p in more:
            h = p.handle.lower()
            if p.url.rstrip("/") not in seen and not ("gift" in h and "card" in h):
                products.append(p)
                seen.add(p.url.rstrip("/"))
    if len(products) < n_products:
        merge(_homepage_products(profile, home.text))
    if len(products) < n_products:
        merge(_sitemap_products(sess, root, n_products))
    profile.products = products[: max(n_products, 10)]
    # Rank categories by how many products carry them (the first product's type can be a side line, e.g. accessories)
    from collections import Counter
    types = Counter(p.product_type.strip() for p in products if p.product_type.strip())
    profile.product_types = [t for t, _ in types.most_common(8)]
    if not profile.currency:
        profile.notes.append("currency not detected; fix files use a placeholder")
    return profile, home
