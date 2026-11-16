#!/usr/bin/env python3
"""Download candidate bottle photographs from Wikimedia Commons.

Writes ml/data/raw/<class>/<slug>.jpg plus ml/data/raw/manifest.json with
title, author, license and source URL for every file, so CREDITS.md can be
generated reproducibly.  Requires network; the repository ships the result,
so judges never need to run this.
"""
import json, os, re, sys, time
import urllib.parse, urllib.request

UA = "ClearLot-hackathon/1.0 (student research project; contact: repo owner)"
API = "https://commons.wikimedia.org/w/api.php"
OUT = "ml/data/raw"

QUERIES = {
    "flint": ["clear glass bottle", "empty glass bottle white background", "vodka bottle glass"],
    "green": ["green glass bottle", "green beer bottle", "green wine bottle"],
    "amber": ["brown beer bottle", "amber glass bottle", "whisky bottle brown glass"],
}

def api(params):
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())

def search(term, limit=40):
    return api({
        "action": "query", "generator": "search",
        "gsrsearch": f"filetype:bitmap {term}", "gsrnamespace": "6",
        "gsrlimit": str(limit), "prop": "imageinfo",
        "iiprop": "url|extmetadata|size", "iiurlwidth": "900", "format": "json",
    })

def clean(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()

def main():
    manifest = {}
    os.makedirs(OUT, exist_ok=True)
    for cls, terms in QUERIES.items():
        d = os.path.join(OUT, cls); os.makedirs(d, exist_ok=True)
        kept, seen = [], set()
        for term in terms:
            if len(kept) >= 16:
                break
            try:
                pages = search(term).get("query", {}).get("pages", {})
            except Exception as e:  # network hiccup: keep going
                print("search failed", term, e, file=sys.stderr); continue
            for pid, pg in pages.items():
                if len(kept) >= 16:
                    break
                try:
                    ii = pg["imageinfo"][0]
                except Exception:
                    continue
                w, h = ii.get("width", 0), ii.get("height", 0)
                if not (w and h) or h < 500:
                    continue
                ar = w / h
                if not (0.45 <= ar <= 1.35):      # portrait / near-square bottle shots
                    continue
                if ii["url"].lower().endswith((".svg", ".tif", ".tiff", ".png")):
                    continue
                title = pg["title"]
                if title in seen:
                    continue
                seen.add(title)
                md = ii.get("extmetadata", {})
                lic = clean(md.get("LicenseShortName", {}).get("value", "")).split(" ")[0]
                if lic not in ("CC0", "Public", "CC"):
                    continue
                slug = re.sub(r"[^a-z0-9]+", "-", title.replace("File:", "").lower()).strip("-")[:60]
                path = os.path.join(d, f"{slug}.jpg")
                try:
                    req = urllib.request.Request(ii["thumburl"], headers={"User-Agent": UA})
                    with urllib.request.urlopen(req, timeout=90) as r:
                        data = r.read()
                    with open(path, "wb") as f:
                        f.write(data)
                except Exception as e:
                    print("download failed", title, e, file=sys.stderr); continue
                kept.append(title)
                manifest[f"{cls}/{slug}"] = {
                    "title": title, "class": cls, "file": path,
                    "author": clean(md.get("Artist", {}).get("value", "unknown"))[:120],
                    "license": clean(md.get("LicenseShortName", {}).get("value", "")),
                    "license_url": clean(md.get("LicenseUrl", {}).get("value", "")),
                    "source": ii.get("descriptionurl", ""),
                    "width": w, "height": h, "query": term,
                }
                time.sleep(0.2)
        print(cls, len(kept))
    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)
    print("total", len(manifest))

if __name__ == "__main__":
    main()
