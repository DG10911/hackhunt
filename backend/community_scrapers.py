"""Live community-event scrapers — tech conferences, summits & meetups in India
with ticketing links. Same defensive pattern as scrapers.py: each adapter is
best-effort. If a source is down, blocks, or changes shape, it returns [] and
the app falls back to the curated + admin-managed lists. Never raises out.

Returns dicts in the community shape:
  id, title, organizer, type, category, platform, city, mode, location,
  starts, ends, deadline, price, ticket_url, url, tags, themes, image
"""

import hashlib
import json
import re

import requests

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
}
TIMEOUT = 18

# Only surface India-relevant events.
_INDIA = re.compile(r"\b(india|bangalore|bengaluru|mumbai|delhi|hyderabad|pune|"
                    r"chennai|kolkata|gurgaon|gurugram|noida|goa|jaipur|ahmedabad|"
                    r"kochi|indore|nagpur|online|virtual)\b", re.I)

_THEME_RULES = [
    ("AI/ML", ["ai", "ml", "machine learning", "data", "llm", "genai", "deep learning"]),
    ("Cloud/DevOps", ["cloud", "devops", "aws", "kubernetes", "k8s", "sre", "platform", "azure", "gcp"]),
    ("Cybersecurity", ["security", "infosec", "hacking", "cyber", "appsec", "pentest"]),
    ("Mobile", ["android", "ios", "flutter", "mobile", "kotlin", "react native"]),
    ("Web3", ["web3", "blockchain", "crypto", "ethereum", "solidity"]),
    ("Open Innovation", ["open source", "oss", "foss", "community"]),
]


def _themes(text):
    t = (text or "").lower()
    found = [name for name, kws in _THEME_RULES if any(k in t for k in kws)]
    return found[:3]


def _mkid(prefix, *parts):
    return prefix + "-" + hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:10]


def _india_ok(*texts):
    return _INDIA.search(" ".join(str(t or "") for t in texts)) is not None


# ---------------------------------------------------------------- KonfHub
def fetch_konfhub():
    """KonfHub exposes a public events API."""
    out = []
    try:
        r = requests.get("https://api.konfhub.com/event/all",
                         headers=UA, params={"limit": 60}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        rows = data if isinstance(data, list) else (data.get("data") or data.get("events") or [])
    except Exception:
        return []
    for e in rows:
        try:
            title = e.get("name") or e.get("event_name") or e.get("title")
            if not title:
                continue
            city = e.get("city") or e.get("location") or ""
            mode = "Online" if (e.get("is_online") or "online" in str(city).lower()) else "Offline"
            if not _india_ok(title, city, e.get("country", "India")):
                continue
            slug = e.get("event_url") or e.get("slug") or ""
            url = slug if str(slug).startswith("http") else f"https://konfhub.com/{str(slug).lstrip('/')}"
            out.append({
                "id": _mkid("konfhub", title, e.get("start_date", "")),
                "title": str(title)[:140], "organizer": e.get("organiser_name") or "KonfHub host",
                "type": "Conference", "category": "Conference", "platform": "Community",
                "city": str(city)[:60], "mode": mode, "location": str(city)[:120],
                "starts": (e.get("start_date") or "")[:10], "ends": (e.get("end_date") or "")[:10],
                "deadline": "", "price": "See site", "ticket_url": url, "url": url,
                "tags": [], "themes": _themes(title + " " + str(e.get("description", ""))),
                "image": e.get("banner_image") if str(e.get("banner_image", "")).startswith("http") else None,
            })
        except Exception:
            continue
    return out


# ---------------------------------------------------------------- GDG (Bevy)
def fetch_gdg():
    """GDG / DevFest chapters run on the Bevy platform, which has a JSON API."""
    out = []
    try:
        r = requests.get("https://gdg.community.dev/api/event/",
                         headers=UA, params={"country_code": "IN", "status": "Upcoming", "limit": 60},
                         timeout=TIMEOUT)
        r.raise_for_status()
        rows = (r.json() or {}).get("results") or []
    except Exception:
        return []
    for e in rows:
        try:
            title = e.get("title")
            if not title:
                continue
            chap = (e.get("chapter") or {})
            city = chap.get("city") or e.get("city") or ""
            url = e.get("url") or e.get("event_url") or "https://gdg.community.dev/"
            out.append({
                "id": _mkid("gdg", title, e.get("start_date", "")),
                "title": str(title)[:140], "organizer": chap.get("title") or "Google Developer Group",
                "type": "Meetup", "category": "Meetup", "platform": "Community",
                "city": str(city)[:60], "mode": "Offline" if city else "Online",
                "location": str(city)[:120],
                "starts": (e.get("start_date") or "")[:10], "ends": (e.get("end_date") or "")[:10],
                "deadline": "", "price": "Free", "ticket_url": url, "url": url,
                "tags": ["Google"], "themes": _themes(title + " Google Android AI cloud"),
                "image": e.get("picture") if str(e.get("picture", "")).startswith("http") else None,
                "mnc": True,
            })
        except Exception:
            continue
    return out


# ---------------------------------------------------------------- Townscript
def fetch_townscript():
    """Townscript tech category — best-effort listing scrape."""
    out = []
    try:
        r = requests.get("https://www.townscript.com/discover/technology",
                         headers={**UA, "Accept": "text/html"}, timeout=TIMEOUT)
        r.raise_for_status()
        html = r.text
    except Exception:
        return []
    # Townscript embeds event JSON-LD blocks
    for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)[:40]:
        try:
            d = json.loads(m)
        except Exception:
            continue
        for ev in (d if isinstance(d, list) else [d]):
            if not isinstance(ev, dict) or ev.get("@type") != "Event":
                continue
            title = ev.get("name")
            loc = ev.get("location", {})
            city = loc.get("name", "") if isinstance(loc, dict) else str(loc)
            if not title or not _india_ok(title, city, "India"):
                continue
            url = ev.get("url") or "https://www.townscript.com/"
            out.append({
                "id": _mkid("townscript", title, ev.get("startDate", "")),
                "title": str(title)[:140], "organizer": "Townscript host",
                "type": "Conference", "category": "Conference", "platform": "Community",
                "city": str(city)[:60], "mode": "Offline", "location": str(city)[:120],
                "starts": (ev.get("startDate") or "")[:10], "ends": (ev.get("endDate") or "")[:10],
                "deadline": "", "price": "See site", "ticket_url": url, "url": url,
                "tags": [], "themes": _themes(title),
                "image": ev.get("image") if str(ev.get("image", "")).startswith("http") else None,
            })
    return out


# ---------------------------------------------------------------- Meetup
def fetch_meetup():
    """Meetup tech events in major Indian cities via its public GraphQL gateway."""
    out = []
    query = {
        "operationName": "categorySearch",
        "variables": {"lat": 12.97, "lon": 77.59, "radius": 100, "topicCategoryId": 546,
                      "startDateRange": ""},
        "query": ("query categorySearch($lat:Float!,$lon:Float!,$radius:Int,$topicCategoryId:Int){"
                  "keywordSearch(input:{first:40},filter:{lat:$lat,lon:$lon,radius:$radius,"
                  "source:EVENTS,categoryId:$topicCategoryId}){edges{node{id title dateTime "
                  "eventUrl venue{name city} group{name}}}}}"),
    }
    try:
        r = requests.post("https://www.meetup.com/gql", headers={**UA, "Content-Type": "application/json"},
                          data=json.dumps(query), timeout=TIMEOUT)
        r.raise_for_status()
        edges = (((r.json() or {}).get("data") or {}).get("keywordSearch") or {}).get("edges") or []
    except Exception:
        return []
    for ed in edges:
        try:
            n = ed.get("node") or {}
            title = n.get("title")
            venue = n.get("venue") or {}
            city = venue.get("city") or ""
            if not title or not _india_ok(title, city, "India"):
                continue
            url = n.get("eventUrl") or "https://www.meetup.com/"
            out.append({
                "id": _mkid("meetup", n.get("id") or title),
                "title": str(title)[:140], "organizer": (n.get("group") or {}).get("name") or "Meetup group",
                "type": "Meetup", "category": "Meetup", "platform": "Community",
                "city": str(city)[:60], "mode": "Offline" if city else "Online", "location": str(city)[:120],
                "starts": (n.get("dateTime") or "")[:10], "ends": (n.get("dateTime") or "")[:10],
                "deadline": "", "price": "See site", "ticket_url": url, "url": url,
                "tags": [], "themes": _themes(title),
                "image": None,
            })
        except Exception:
            continue
    return out


ADAPTERS = {
    "KonfHub": fetch_konfhub,
    "GDG": fetch_gdg,
    "Townscript": fetch_townscript,
    "Meetup": fetch_meetup,
}


def fetch_all():
    """Run every community adapter; return (events, meta). Never raises."""
    import concurrent.futures as cf
    import time
    results, meta = [], []

    def run(name, fn):
        t0 = time.time()
        try:
            items = fn() or []
            return name, items, ("live" if items else "empty"), round(time.time() - t0, 1), None
        except Exception as e:
            return name, [], "error", round(time.time() - t0, 1), str(e)[:120]

    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(run, n, f) for n, f in ADAPTERS.items()]
        for fut in cf.as_completed(futs):
            name, items, status, took, err = fut.result()
            results.extend(items)
            meta.append({"platform": name, "status": status, "count": len(items),
                         "seconds": took, "error": err})
    return results, meta
