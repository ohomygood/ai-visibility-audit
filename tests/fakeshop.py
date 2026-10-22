"""A tiny fake Shopify store for offline tests. PerplexityBot gets a Cloudflare-style 403 challenge
(simulating Bot Fight Mode); robots.txt blocks GPTBot and CCBot."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ORG = {"@context": "https://schema.org", "@type": "Organization", "name": "Fake Socks Co", "url": "http://shop/",
       "logo": "http://shop/logo.png", "sameAs": ["https://instagram.com/fakesocks"]}
PROD_A = {"@context": "https://schema.org", "@type": "Product", "name": "Wool Sock", "image": "x.jpg", "sku": "WS1",
          "brand": {"@type": "Brand", "name": "Fake Socks Co"}, "description": "warm",
          "offers": {"@type": "Offer", "price": "12.00", "priceCurrency": "USD", "availability": "https://schema.org/InStock"},
          "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.7", "reviewCount": "31"}}


def page(body, head=""):
    return f"""<!doctype html><html lang="en"><head><title>Fake Socks Co – Warm socks</title>
<meta name="description" content="Warm merino socks shipped worldwide."><meta property="og:site_name" content="Fake Socks Co">
<script src="https://cdn.shopify.com/s/x.js"></script><script>Shopify.currency = {{"active":"USD","rate":"1.0"}};</script>{head}</head>
<body>{body}</body></html>"""


HOME = page('<a href="/products/wool-sock">Wool</a><a href="/products/cotton-sock">Cotton</a><a href="/products/broken-sock">B</a>'
            '<a href="/collections/socks">Socks</a><a href="/policies/refund-policy">Refund</a>'
            '<a href="https://instagram.com/fakesocks">IG</a>',
            f'<script type="application/ld+json">{json.dumps(ORG)}</script>')
PAGES = {
    "/": HOME,
    "/products/wool-sock": page("<h1>Wool</h1>", f'<script type="application/ld+json">{json.dumps(PROD_A)}</script>'),
    "/products/cotton-sock": page('<div itemscope itemtype="https://schema.org/Product"><span itemprop="name">Cotton Sock</span>'
                                  '<div itemprop="offers" itemscope itemtype="https://schema.org/Offer"><meta itemprop="price" content="9">'
                                  '<meta itemprop="priceCurrency" content="USD"></div></div>'),
    "/products/broken-sock": page("", '<script type="application/ld+json">{"@type":"Product","name":"B",}</script>'
                                      '<script type="application/ld+json">{"aggregateRating":{"@type":"AggregateRating","ratingValue":"4"}}</script>'),
}
PRODUCTS_JSON = {"products": [
    {"title": "Wool Sock", "handle": "wool-sock", "vendor": "Fake Socks Co", "product_type": "Socks", "tags": ["wool"],
     "body_html": "<p>Warm merino</p>", "variants": [{"price": "12.00", "sku": "WS1", "available": True, "barcode": "0123456789012"}],
     "images": [{"src": "https://cdn.shopify.com/wool.jpg"}]},
    {"title": "Cotton Sock", "handle": "cotton-sock", "vendor": "Fake Socks Co", "product_type": "Socks", "tags": [],
     "body_html": "", "variants": [{"price": "9.00", "sku": "", "available": True}], "images": []},
    {"title": "Broken Sock", "handle": "broken-sock", "vendor": "Fake Socks Co", "product_type": "Gift Cards", "tags": [],
     "body_html": "", "variants": [{"price": "5.00", "available": False}], "images": []},
]}
ROBOTS = "User-agent: *\nDisallow: /cart\n\nUser-agent: GPTBot\nUser-agent: CCBot\nDisallow: /\n\nSitemap: {root}sitemap.xml\n"


class H(BaseHTTPRequestHandler):
    block_all = False

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-ShopId", "123")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        ua = self.headers.get("User-Agent", "")
        root = f"http://{self.headers['Host']}/"
        path = self.path.split("?")[0]
        if self.block_all and "GPTBot" not in ua:
            return self._send(429, "Too Many Requests", extra={"Retry-After": "0"})
        if "PerplexityBot" in ua:
            return self._send(403, "<html><title>Just a moment...</title>cf-chl</html>", extra={
                "Server": "cloudflare", "CF-RAY": "abc", "cf-mitigated": "challenge"})
        if path == "/robots.txt":
            return self._send(200, ROBOTS.format(root=root), "text/plain")
        if path == "/products.json":
            return self._send(200, json.dumps(PRODUCTS_JSON), "application/json")
        if path == "/sitemap.xml":
            return self._send(200, f'<?xml version="1.0"?><urlset><url><loc>{root}products/wool-sock</loc></url></urlset>', "application/xml")
        if path in PAGES:
            return self._send(200, PAGES[path])
        return self._send(404, "not found")


def start(block_all=False):
    handler = type("HB", (H,), {"block_all": True}) if block_all else H
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"
