"""Listing sources. Each returns a list of dicts with id, source, title, price, url and location fields."""
import html as htmllib
import json
import re
import urllib.parse
import urllib.request

WILLHABEN_AREAS = ["oberoesterreich/linz", "oberoesterreich/urfahr-umgebung"]


def get(url, ua):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Language": "de-AT"}),
                                timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def willhaben(ua, cfg):
    out = []
    for area in WILLHABEN_AREAS:
        html = get(f"https://www.willhaben.at/iad/immobilien/mietwohnungen/{area}?PRICE_TO={cfg['max_rent']}&rows=100&sort=1", ua)
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if not m:
            raise RuntimeError(f"willhaben {area}: no __NEXT_DATA__ (blocked or layout change)")
        ads = json.loads(m.group(1))["props"]["pageProps"]["searchResult"]["advertSummaryList"]["advertSummary"]
        for ad in ads:
            at = {a["name"]: a["values"][0] for a in ad["attributes"]["attribute"] if a.get("values")}
            lat = lon = None
            if at.get("COORDINATES"):
                lat, lon = map(float, at["COORDINATES"].split(","))
            out.append({
                "src": "willhaben",
                "source": "willhaben",
                "id": ad["id"],
                "title": at.get("HEADING", ad.get("description", "")),
                "price": _num(at.get("PRICE")),
                "size": _num(at.get("ESTATE_SIZE/LIVING_AREA") or at.get("ESTATE_SIZE")),
                "rooms": at.get("NUMBER_OF_ROOMS") if at.get("NUMBER_OF_ROOMS") not in (None, "0") else None,
                "address": at.get("ADDRESS"),
                "plz": at.get("POSTCODE"),
                "city": at.get("LOCATION"),
                "lat": lat,
                "lon": lon,
                "url": "https://www.willhaben.at/iad/" + at.get("SEO_URL", f"object?adId={ad['id']}"),
            })
    return out


def _eur(text):
    """'1.234,56' style amount -> float."""
    m = re.search(r"(\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+(?:,\d+)?)", text or "")
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def _plz(text):
    m = re.search(r"\b(4\d{3})\b", text or "")
    return m.group(1) if m else None


def _clean(text):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def genossenschaften_immo(ua, cfg):
    """Aggregator: carries GWG, Lebensraeume, EBS, WAG, LAWOG, Familie, VLW and more."""
    out = []
    for region in ("stadt-linz", "urfahr-umgebung"):
        ids = set()
        for page in range(1, 11):
            h = get(f"https://genossenschaften.immo/immobilien/regionen/oberoesterreich/{region}/{page}/", ua)
            cards = h.split("residence-teaser mb-3")[1:]
            page_ids = set(re.findall(r'/(\d+)/" class="stretched-link', h))
            if not cards or page_ids <= ids:
                break  # past the last page the site repeats it
            ids |= page_ids
            for c in cards:
                url = re.search(r'<a href="([^"]+)" class="stretched-link', c)
                if not url:
                    continue
                badges = [_clean(b) for b in re.findall(r'<span class="mt-1 badge[^>]*>(.*?)</span>', c, re.S)]
                if "Verfügbar" not in badges or "Miete" not in badges:
                    continue
                vals = {b.strip(): _clean(a) for a, b in
                        re.findall(r'<div class="fs-5">(.*?)</div>\s*<small class="text-muted">(.*?)</small>', c, re.S)}
                name = _clean((re.search(r'itemprop="name">([^<]+)<', c) or [None, ""])[1])
                builder = next((b for b in badges if b.isupper() or "-" in b), "")
                note = f"Eigenmittel {vals['Eigenkapital']}" if vals.get("Eigenkapital") else ""
                out.append({
                    "src": "gimmo",
                    "source": f"Genossenschaft {builder}".strip(),
                    "id": url.group(1).rstrip("/").split("/")[-1],
                    "title": name,
                    "price": _eur(vals.get("Miete")),
                    "size": _eur(vals.get("Fläche")),
                    "rooms": vals.get("Zimmer"),
                    "address": name,
                    "plz": _plz(name),
                    "city": "",
                    "note": note,
                    "url": "https://genossenschaften.immo" + url.group(1),
                    "geo_url": "https://genossenschaften.immo" + url.group(1),
                })
    return out


def geo(url, ua):
    """Coordinates from a detail page's JSON-LD, or None."""
    h = get(url, ua)
    lat = re.search(r'"latitude":\s*"?([\d.]+)', h)
    lon = re.search(r'"longitude":\s*"?([\d.]+)', h)
    return (float(lat.group(1)), float(lon.group(1))) if lat and lon else None


def wsg(ua, cfg):
    out = []
    for bezirk in ("Linz (L)", "Urfahr-Umgebung (UU)"):
        h = get("https://www.wsg.at/wohnungssuche/?wpv-wpcf-bezirke=" + urllib.parse.quote(bezirk), ua)
        for c in h.split("wohnung-item result-item")[1:]:
            g = lambda p: (re.search(p, c, re.S) or [None, None])[1]
            head = _clean(g(r'post-wg-header">(.*?)</div>'))
            spans = [_clean(x) for x in re.findall(r'<span class="text">(.*?)</span>', g(r'post-wg-details">(.*?)</div>') or "")]
            url = g(r'<a href="([^"?]+)')
            out.append({
                "src": "wsg",
                "source": "Genossenschaft WSG",
                "id": url.rstrip("/").split("/")[-1],
                "title": head,
                "price": _eur(next((s for s in spans if "€" in s), None)),
                "size": _eur(next((s for s in spans if "m²" in s), None)),
                "rooms": next((s.split()[0] for s in spans if "Z." in s), None),
                "address": head,
                "plz": None,
                "city": "",
                "note": "Miete inkl. BK",
                "url": url,
            })
    return out


def neue_heimat(ua, cfg):
    base = "https://www.neue-heimat-ooe.at"
    h = get(base + "/immobilien/aktuelle-wohnungsangebote", ua)
    out = []
    for blk in h.split("var arTrans = new Array();")[1:]:
        blk = blk.split("arObjekte.push")[0]
        d = dict(re.findall(r'arTrans\["(\w+)"\] = "([^"]*)"', blk))
        uid = re.search(r'arTrans\["uid"\] = (\d+)', blk)
        if not uid:
            continue
        note = f"Eigenmittel {_clean(d['eigenmittel'])}" if d.get("eigenmittel") else ""
        out.append({
            "src": "nh",
            "source": "Genossenschaft Neue Heimat",
            "id": uid.group(1),
            "title": f"{d.get('ort', '')}, {d.get('adresse', '')}",
            "price": _eur(_clean(d.get("monatlich"))),
            "size": _eur(d.get("mm2nw")),
            "rooms": d.get("topzim"),
            "address": d.get("adresse"),
            "plz": d.get("plz"),
            "city": d.get("ort"),
            "note": note,
            "url": base + htmllib.unescape(d.get("url", "")),
        })
    return out


ALL = {
    "willhaben": willhaben,
    "genossenschaften.immo": genossenschaften_immo,
    "wsg": wsg,
    "neue-heimat": neue_heimat,
}
