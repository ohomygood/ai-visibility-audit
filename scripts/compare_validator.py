"""Acceptance check: compare our schema extraction with validator.schema.org on sample pages.

Usage: python3 scripts/compare_validator.py URL [URL ...]
Read-only; one validator request every 3 s. Writes samples/validator_comparison.json.
"""
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiv.checks import check_schema_page  # noqa: E402
from aiv.http import BROWSER_UA, PoliteSession  # noqa: E402

SCHEMA_PREFIXES = ("http://schema.org/", "https://schema.org/")
# Types the validator infers itself (SearchAction shorthand strings, untyped nested nodes) - not declared by the page.
IMPLIED = {"Thing", "EntryPoint", "PropertyValueSpecification"}


def validator_types(url: str) -> dict:
    r = requests.post("https://validator.schema.org/validate", data={"url": url},
                      headers={"User-Agent": BROWSER_UA}, timeout=60)
    r.raise_for_status()
    d = json.loads(r.text.split("\n", 1)[1])
    types, errors = set(), 0

    def walk(node):
        nonlocal errors
        for t in node.get("types", []):
            v = t.get("value", "")
            if v and ("/" not in v or v.startswith(SCHEMA_PREFIXES)):
                types.add(v.rsplit("/", 1)[-1])
        errors += len(node.get("errors", []))
        for np in node.get("nodeProperties", []):
            walk(np.get("target", {}))

    for g in d.get("tripleGroups", []):
        errors += g.get("numErrors", 0)
        for n in g.get("nodes", []):
            walk(n)
    return {"types": sorted(types), "num_errors": d.get("numErrors", errors), "fetch_error": d.get("fetchError")}


def main(urls):
    sess = PoliteSession(min_interval=1.0)
    rows = []
    for u in urls:
        f = sess.get(u, ua=BROWSER_UA, ua_label="baseline")
        ours = check_schema_page(u, f.text, "product" if "/products/" in u else "home")
        raw_types = set(ours["jsonld_types"]) | set(ours["microdata_types"])
        rendered = set()
        if "Product" in ours["missing_types"] and "/products/" in u:
            from aiv.checks import render_dom
            dom = render_dom(u)
            if dom:
                r2 = check_schema_page(u, dom, "product")
                rendered = set(r2["jsonld_types"]) | set(r2["microdata_types"])
        our_types = sorted(raw_types | rendered)
        try:
            v = validator_types(u)
        except Exception as e:  # noqa: BLE001
            v = {"types": [], "num_errors": None, "fetch_error": str(e)}
        vt = sorted(set(v["types"]) - IMPLIED)
        rows.append({"url": u, "ours_raw_html": sorted(raw_types), "ours_js_rendered_extra": sorted(rendered - raw_types),
                     "ours": our_types, "validator": vt, "validator_implied_ignored": sorted(set(v["types"]) & IMPLIED),
                     "match": our_types == vt,
                     "only_ours": sorted(set(our_types) - set(vt)),
                     "only_validator": sorted(set(vt) - set(our_types)),
                     "our_parse_errors": ours["parse_errors"], "validator_errors": v["num_errors"],
                     "validator_fetch_error": v.get("fetch_error")})
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
        time.sleep(3)
    out = Path(__file__).resolve().parents[1] / "samples" / "validator_comparison.json"
    out.write_text(json.dumps({"checked_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "pages": rows,
                               "agreement": f"{sum(r['match'] for r in rows)}/{len(rows)}"}, indent=1, ensure_ascii=False))
    print("agreement", sum(r["match"] for r in rows), "/", len(rows), "->", out)


if __name__ == "__main__":
    main(sys.argv[1:])
