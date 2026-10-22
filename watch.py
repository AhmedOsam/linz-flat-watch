"""Watch rental listings near JKU Linz and send new ones by email and/or WhatsApp (CallMeBot)."""
import json
import math
import os
import re
import smtplib
import sys
import time
import urllib.parse
import urllib.request
from email.message import EmailMessage

import sources

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(HERE, "config.json")))
STATE_FILE = os.path.join(HERE, "state", "seen.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
KEEP_DAYS = 30
FAIL_ALERT_RUNS = 12  # about 2 hours of a source failing in a row


def km_to_jku(lat, lon):
    jlat, jlon = CONFIG["jku"]
    la1, lo1, la2, lo2 = map(math.radians, (jlat, jlon, lat, lon))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def matches(item):
    title = item.get("title") or ""
    if re.search(r"stellpl|parkpl", title, re.I) and not re.search(r"wohnung|zimmer", title, re.I):
        return False
    if item.get("price") is None or item["price"] > CONFIG["max_rent"]:
        return False
    if item.get("size") and item["size"] < CONFIG["min_size"]:
        return False
    plz = item.get("plz")
    if plz and str(plz) not in CONFIG["near_plz"]:
        return False
    if item.get("lat") is not None and item.get("lon") is not None:
        item["km"] = km_to_jku(item["lat"], item["lon"])
        return item["km"] <= CONFIG["max_km"]
    if plz:
        return True
    # No postcode or coordinates: fall back to place names near JKU.
    text = f"{title} {item.get('address') or ''}".lower()
    return any(p in text for p in CONFIG["near_places"])


def summary(item):
    parts = []
    if item.get("rooms"):
        parts.append(f"{item['rooms']} Zi")
    if item.get("size"):
        parts.append(f"{item['size']:g} m2")
    parts.append(f"EUR {item['price']:,.0f}".replace(",", "."))
    if item.get("km") is not None:
        parts.append(f"{item['km']:.1f} km to JKU")
    return " | ".join(parts)


def fmt(item):
    addr = item.get("address") or ""
    place = f"{item.get('plz') or ''} {item.get('city') or ''}".strip()
    where = addr if not place or (item.get("plz") or "-") in addr else ", ".join(x for x in (addr, place) if x)
    lines = [f"New flat ({item['source']}): {item['title']}", summary(item)]
    if where and where != item["title"]:
        lines.append(where)
    if item.get("note"):
        lines.append(item["note"])
    lines.append(item["url"])
    return "\n".join(lines)


def send_email(subject, text):
    host, user, pw, to = (os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "MAIL_TO"))
    if not (host and user and pw and to):
        return None
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ.get("MAIL_FROM") or user, to
    msg.set_content(text)
    try:
        with smtplib.SMTP_SSL(host, int(os.environ.get("SMTP_PORT") or 465), timeout=30) as s:
            s.login(user, pw)
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"email failed: {type(e).__name__}", file=sys.stderr)
        return False


def send_whatsapp(text):
    phone, key = os.environ.get("CALLMEBOT_PHONE"), os.environ.get("CALLMEBOT_APIKEY")
    if not phone or not key:
        return None
    url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
        {"phone": phone, "text": text, "apikey": key})
    for _ in range(2):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30) as r:
                body = r.read().decode("utf-8", "replace")
        except Exception as e:
            # Never print e itself: the URL carries the number and key.
            print(f"whatsapp failed: {type(e).__name__}", file=sys.stderr)
            time.sleep(5)
            continue
        # A response came back, so do not retry (a retry could deliver twice).
        ok = bool(re.search(r"queued|sent", body, re.I))
        if not ok:
            print("whatsapp rejected by CallMeBot", file=sys.stderr)
        return ok
    return False


def notify(subject, text):
    results = [send_email(subject, text), send_whatsapp(text)]
    if all(r is None for r in results):
        print(f"--- (dry run, no channel configured) ---\n{subject}\n{text}")
        return None
    return any(results)


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"meta": {"seeded": [], "fails": {}}, "seen": {}, "fps": {}}
    with open(STATE_FILE) as f:
        return json.load(f)  # a corrupt file fails loudly instead of being overwritten


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=0, sort_keys=True)
    os.replace(tmp, STATE_FILE)


def fingerprint(item):
    if item.get("plz") and item.get("size") and item.get("price"):
        return f"{item['plz']}:{round(item['price'])}:{round(item['size'])}"
    return None


def main():
    state = load_state()
    meta, seen, fps = state["meta"], state["seen"], state["fps"]
    now = int(time.time())
    alerts, broken = [], []

    for name, fn in sources.ALL.items():
        try:
            items = [i for i in fn(UA, CONFIG) if i.get("id") and i.get("url") and i.get("title")]
            print(f"{name}: {len(items)} listings")
            meta["fails"].pop(name, None)
        except Exception as e:
            meta["fails"][name] = meta["fails"].get(name, 0) + 1
            print(f"{name} FAILED ({meta['fails'][name]}x): {e}", file=sys.stderr)
            if meta["fails"][name] >= FAIL_ALERT_RUNS:
                broken.append(name)
            continue
        # A source's first successful run is recorded silently, so a source that comes back does not flood.
        silent = name not in meta["seeded"]
        for item in items:
            key = f"{item['src']}:{item['id']}"
            entry = seen.get(key)
            if entry is None:
                entry = seen[key] = {"sent": False}
                if item.get("lat") is None and item.get("geo_url"):
                    try:
                        loc = sources.geo(item["geo_url"], UA)
                        if loc:
                            entry["lat"], entry["lon"] = loc
                    except Exception as e:
                        print(f"geo lookup failed for {key}: {e}", file=sys.stderr)
            entry["t"] = now
            if entry["sent"]:
                continue
            if item.get("lat") is None and "lat" in entry:
                item["lat"], item["lon"] = entry["lat"], entry["lon"]
            if not matches(item):
                continue
            if silent:
                entry["sent"] = True
            else:
                alerts.append((key, item))
        if silent:
            meta["seeded"].append(name)
    save_state(state)

    if not meta.get("hello") and meta["seeded"]:
        meta["hello"] = notify("Flat watcher is live",
               f"Watching willhaben and the Linz Genossenschaften for rentals within {CONFIG['max_km']:g} km of JKU, "
               f"max EUR {CONFIG['max_rent']} (Miete + BK). Each new flat arrives as its own message.") is True
        save_state(state)

    sent = 0
    for key, item in sorted(alerts, key=lambda kv: 99 if kv[1].get("km") is None else kv[1]["km"]):
        # Same flat listed by the builder and an aggregator: alert once.
        fp = fingerprint(item)
        if fp and fps.get(fp, {}).get("src") not in (None, item["src"]):
            seen[key]["sent"] = True
            fps[fp]["t"] = now
            save_state(state)
            continue
        try:
            ok = notify(f"New flat near JKU: {summary(item)}", fmt(item)) is not False
        except Exception as e:
            print(f"notify crashed for {key}: {e}", file=sys.stderr)
            ok = False
        if ok:
            seen[key]["sent"] = True
            if fp:
                fps[fp] = {"src": item["src"], "t": now}
            sent += 1
            save_state(state)
        time.sleep(3)

    cutoff = now - KEEP_DAYS * 86400
    state["seen"] = {k: v for k, v in seen.items() if v.get("t", 0) >= cutoff}
    state["fps"] = {k: v for k, v in fps.items() if v.get("t", 0) >= cutoff}
    save_state(state)
    print(f"alerts: {len(alerts)}, sent: {sent}, tracked: {len(state['seen'])}")
    if broken:
        # A red run makes GitHub email the repo owner.
        print(f"sources failing for {FAIL_ALERT_RUNS}+ runs: {', '.join(broken)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if "--test" in sys.argv:
        sys.exit(0 if notify("Flat watcher test", "Test message: delivery works.") else 1)
    main()
