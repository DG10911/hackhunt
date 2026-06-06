"""
HackHunt India — source adapters.

Each adapter returns a list of normalized dicts:
{
  "id": str, "title": str, "organizer": str, "platform": str,
  "url": str, "mode": "Online"|"Hybrid"|"Offline",
  "location": str, "starts": ISO str|None, "ends": ISO str|None,
  "deadline": ISO str|None, "prize": str, "tags": [str], "category": str,
  "image": str|None, "participants": int|None
}

Live adapters hit each platform's public/JSON endpoints. If a site changes,
blocks, or times out, the adapter raises/returns [] and the app falls back
to bundled sample data so the UI always has something to show.
"""

import datetime as dt
import hashlib
import json
import re

import requests

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}
TIMEOUT = 20

# Keywords that mark an event as one of the special categories the user cares about
CATEGORY_RULES = [
    ("Wildlife / Climate", ["wildlife", "climate", "conservation", "biodiversity",
                             "sustain", "environment", "green", "ocean", "forest",
                             "nature", "eco", "carbon"]),
    ("Ideathon", ["ideathon", "idea-thon", "pitch", "innovation challenge", "case study", "casethon"]),
    ("Hiring Challenge", ["hiring", "recruit", "job", "placement", "talent", "career"]),
    ("Government", ["government", "govt", "ministry", "smart india", "sih", "niti",
                    "isro", "drdo", "digital india", "startup india", "psu", "nic"]),
    ("Hackathon", ["hackathon", "hack-", "codefest", "buildathon", "datathon"]),
]


def classify(title, organizer, tags):
    blob = " ".join([title or "", organizer or "", " ".join(tags or [])]).lower()
    for cat, kws in CATEGORY_RULES:
        if any(k in blob for k in kws):
            return cat
    return "Hackathon"


# Big MNCs / tech companies — used to flag and tag "MNC" events
MNC_NAMES = [
    "google", "microsoft", "amazon", "meta", "facebook", "apple", "netflix",
    "ibm", "intel", "nvidia", "oracle", "sap", "salesforce", "adobe", "cisco",
    "qualcomm", "samsung", "sony", "dell", "hp ", "vmware", "uber", "paypal",
    "flipkart", "walmart", "swiggy", "zomato", "paytm", "phonepe", "razorpay",
    "tcs", "infosys", "wipro", "accenture", "cognizant", "capgemini", "deloitte",
    "jpmorgan", "j.p. morgan", "goldman", "morgan stanley", "barclays", "hsbc",
    "mercedes", "bmw", "bosch", "siemens", "ge ", "honeywell", "philips",
    "mastercard", "visa", "american express", "amex", "atlassian", "stripe",
    "linkedin", "twitter", "x corp", "spotify", "airbnb", "tesla", "boeing",
]

# Topic/theme keywords -> normalized theme label (for theme-wise filtering)
THEME_RULES = {
    "AI/ML": ["ai", "ml", "machine learning", "deep learning", "genai", "llm",
              "artificial intelligence", "data science", "nlp", "computer vision"],
    "Web3": ["web3", "blockchain", "crypto", "ethereum", "solana", "defi", "nft", "dao"],
    "Climate": ["climate", "sustain", "green", "carbon", "energy", "environment", "eco"],
    "Wildlife": ["wildlife", "conservation", "biodiversity", "forest", "ocean", "nature", "animal"],
    "FinTech": ["fintech", "finance", "payment", "banking", "trading", "insurance"],
    "HealthTech": ["health", "medical", "medtech", "biotech", "pharma", "wellness"],
    "Cybersecurity": ["cyber", "security", "infosec", "hacking", "privacy"],
    "IoT/Hardware": ["iot", "hardware", "embedded", "robotics", "drone", "electronics"],
    "Cloud/DevOps": ["cloud", "devops", "kubernetes", "aws", "azure", "gcp", "serverless"],
    "AR/VR": ["ar", "vr", "metaverse", "xr", "augmented", "virtual reality"],
    "Mobile": ["android", "ios", "flutter", "mobile app", "react native"],
    "Gaming": ["game", "gaming", "unity", "unreal"],
    "EdTech": ["edtech", "education", "learning", "student"],
    "Open Innovation": ["open innovation", "open theme", "any theme"],
}


def extract_themes(title, organizer, tags):
    blob = " ".join([title or "", organizer or "", " ".join(tags or [])]).lower()
    found = []
    for theme, kws in THEME_RULES.items():
        if any(re.search(r"\b" + re.escape(k) + r"\b", blob) for k in kws):
            found.append(theme)
    return found


def enrich(item):
    """Add 'mnc' flag, normalized 'themes', and a 'company' name to a record."""
    blob = " ".join([item.get("title", ""), item.get("organizer", ""),
                     " ".join(item.get("tags") or [])]).lower()
    item["mnc"] = any(n in blob for n in MNC_NAMES)
    item["govt"] = item.get("category") == "Government"
    item["themes"] = extract_themes(item.get("title"), item.get("organizer"), item.get("tags"))
    if item["mnc"] and "MNC" not in (item.get("tags") or []):
        item.setdefault("tags", []).insert(0, "MNC")
    return item


def mkid(*parts):
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _iso(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.utcfromtimestamp(value / 1000 if value > 1e11 else value).isoformat()
        except Exception:
            return None
    s = str(value)
    return s


# --------------------------------------------------------------------------
# Devpost — public JSON API. Reliable.
# --------------------------------------------------------------------------
def fetch_devpost():
    out = []
    url = "https://devpost.com/api/hackathons"
    for page in (1, 2, 3, 4, 5):
        params = {"page": page, "order_by": "deadline", "status[]": "open"}
        r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        for h in data.get("hackathons", []):
            loc = (h.get("displayed_location") or {}).get("location", "Online")
            is_online = "online" in loc.lower()
            tags = [t.get("name") for t in h.get("themes", []) if t.get("name")]
            title = h.get("title", "")
            prize = h.get("prize_amount", "") or ""
            prize = re.sub("<[^>]+>", "", prize)
            out.append({
                "id": mkid("devpost", h.get("id")),
                "title": title,
                "organizer": h.get("organization_name", "Devpost"),
                "platform": "Devpost",
                "url": h.get("url", ""),
                "mode": "Online" if is_online else "Offline",
                "location": loc,
                "starts": None,
                "ends": h.get("submission_period_dates", ""),
                "deadline": h.get("submission_period_dates", ""),
                "prize": prize,
                "tags": tags,
                "category": classify(title, h.get("organization_name"), tags),
                "image": (h.get("thumbnail_url") or "").replace("//", "https://", 1)
                if (h.get("thumbnail_url") or "").startswith("//") else h.get("thumbnail_url"),
                "participants": h.get("registrations_count"),
            })
    return out


# --------------------------------------------------------------------------
# Unstop — public search-result JSON API. India-focused.
# --------------------------------------------------------------------------
def fetch_unstop():
    out = []
    url = "https://unstop.com/api/public/opportunity/search-result"
    items = []
    for opp in ("hackathons", "competitions"):
        for page in (1, 2, 3):
            params = {
                "opportunity": opp,
                "page": page,
                "per_page": 50,
                "oppstatus": "open",
                "quickApply": "true",
            }
            try:
                r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
                r.raise_for_status()
                data = r.json()
                items += (((data or {}).get("data") or {}).get("data")) or []
            except Exception:
                break
    for h in items:
        title = h.get("title", "")
        org = (h.get("organisation") or {}).get("name", "Unstop")
        tags = [f.get("name") for f in (h.get("filters") or []) if f.get("name")]
        prize = ""
        if h.get("prizes"):
            try:
                prize = h["prizes"][0].get("cash") or ""
            except Exception:
                prize = ""
        seo = h.get("public_url") or h.get("seo_url") or ""
        link = seo if seo.startswith("http") else f"https://unstop.com/{seo.lstrip('/')}"
        out.append({
            "id": mkid("unstop", h.get("id")),
            "title": title,
            "organizer": org,
            "platform": "Unstop",
            "url": link,
            "mode": "Online",
            "location": h.get("region", "India"),
            "starts": None,
            "ends": (h.get("end_date") or None),
            "deadline": (h.get("regnRequirements") or {}).get("end_regn_dt")
            or h.get("end_date"),
            "prize": str(prize),
            "tags": tags,
            "category": classify(title, org, tags),
            "image": h.get("logoUrl2") or h.get("banner_mobile") or None,
            "participants": (h.get("registerCount") or None),
        })
    return out


# --------------------------------------------------------------------------
# Devfolio — public Algolia-backed search API.
# --------------------------------------------------------------------------
def fetch_devfolio():
    out = []
    url = "https://api.devfolio.co/api/search/hackathons"
    payload = {"type": "application_open", "from": 0, "size": 60}
    r = requests.post(url, json=payload, headers={**UA, "Content-Type": "application/json"},
                      timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    hits = (data.get("hits") or {}).get("hits") or data.get("result") or []
    for hit in hits:
        h = hit.get("_source", hit)
        title = h.get("name", "")
        slug = h.get("slug", "")
        loc = h.get("location") or ("Online" if h.get("is_online") else "India")
        tags = h.get("themes") or h.get("tags") or []
        if isinstance(tags, list):
            tags = [t if isinstance(t, str) else t.get("name", "") for t in tags]
        out.append({
            "id": mkid("devfolio", slug or title),
            "title": title,
            "organizer": h.get("organization", "Devfolio"),
            "platform": "Devfolio",
            "url": f"https://{slug}.devfolio.co" if slug else "https://devfolio.co",
            "mode": "Online" if h.get("is_online") else "Hybrid",
            "location": loc,
            "starts": _iso(h.get("starts_at")),
            "ends": _iso(h.get("ends_at")),
            "deadline": _iso(h.get("hackathon_setting", {}).get("reg_ends_at")
                             if isinstance(h.get("hackathon_setting"), dict) else None),
            "prize": str(h.get("prize") or ""),
            "tags": [t for t in tags if t],
            "category": classify(title, h.get("organization"), tags),
            "image": h.get("cover_img") or h.get("logo"),
            "participants": h.get("participants_count"),
        })
    return out


# --------------------------------------------------------------------------
# Hack2skill / Devnovate / Internshala — these are JS-rendered / no clean
# public JSON. We attempt a lightweight HTML grab; on failure return [].
# Sample data covers them so the dashboard always shows every platform.
# --------------------------------------------------------------------------
def fetch_generic_html(platform, listing_url):
    try:
        r = requests.get(listing_url, headers={**UA, "Accept": "text/html"}, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception:
        return []
    # These platforms are client-rendered; HTML rarely carries event data.
    # Return [] so sample data fills in. (Hook real parsing here if needed.)
    return []


def _walk_find_events(node, out, platform, base):
    """Recursively scan a JSON blob for hackathon-like objects."""
    if isinstance(node, dict):
        title = node.get("title") or node.get("name") or node.get("hackathonName")
        slug = node.get("slug") or node.get("seoUrl") or node.get("url")
        if title and (slug or node.get("startDate") or node.get("start_date")):
            link = slug or ""
            if link and not str(link).startswith("http"):
                link = base.rstrip("/") + "/" + str(link).lstrip("/")
            tags = node.get("tags") or node.get("themes") or node.get("technologies") or []
            if isinstance(tags, list):
                tags = [t if isinstance(t, str) else (t.get("name") if isinstance(t, dict) else "") for t in tags]
            title_s = str(title)
            out.append({
                "id": mkid(platform, node.get("id") or node.get("_id") or slug or title_s),
                "title": title_s,
                "organizer": (node.get("organiser") or node.get("organizer")
                              or node.get("company") or platform),
                "platform": platform,
                "url": link or base,
                "mode": node.get("mode") or ("Online" if node.get("isOnline") else "Online"),
                "location": node.get("location") or node.get("city") or "Online · India",
                "starts": _iso(node.get("startDate") or node.get("start_date")),
                "ends": _iso(node.get("endDate") or node.get("end_date")),
                "deadline": _iso(node.get("registrationDeadline") or node.get("regDeadline")
                                 or node.get("endDate")),
                "prize": str(node.get("prize") or node.get("prizeMoney") or node.get("totalPrize") or ""),
                "tags": [t for t in tags if t][:6],
                "category": classify(title_s, str(node.get("organiser") or ""), tags),
                "image": node.get("image") or node.get("banner") or node.get("coverImage") or node.get("logo"),
                "participants": node.get("registrations") or node.get("participants"),
            })
        for v in node.values():
            _walk_find_events(v, out, platform, base)
    elif isinstance(node, list):
        for v in node:
            _walk_find_events(v, out, platform, base)
    return out


def _fetch_next_data(platform, listing_url, base):
    """Many of these sites are Next.js apps; their initial data lives in
    a <script id="__NEXT_DATA__"> JSON blob. Parse it without a browser."""
    try:
        r = requests.get(listing_url, headers={**UA, "Accept": "text/html"}, timeout=TIMEOUT)
        r.raise_for_status()
        html = r.text
    except Exception:
        return []
    out = []
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            _walk_find_events(data, out, platform, base)
        except Exception:
            pass
    if not out:
        # try inline JSON arrays as a last resort
        for blob in re.findall(r'(\{"props".*?\})\s*</script>', html, re.S)[:1]:
            try:
                _walk_find_events(json.loads(blob), out, platform, base)
            except Exception:
                pass
    # de-dupe by id
    seen, dd = set(), []
    for e in out:
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        dd.append(e)
    return dd


def fetch_hack2skill():
    # public listing is a Next.js page; parse its embedded data
    for url in ("https://hack2skill.com/hackathons-listing",
                "https://vision.hack2skill.com/hackathons-listing"):
        evs = _fetch_next_data("Hack2skill", url, "https://hack2skill.com")
        if evs:
            return evs
    return []


def fetch_devnovate():
    return fetch_generic_html("Devnovate", "https://devnovate.co/")


def fetch_internshala():
    return fetch_generic_html("Internshala", "https://internshala.com/hackathons/")


ADAPTERS = {
    "Devpost": fetch_devpost,
    "Unstop": fetch_unstop,
    "Devfolio": fetch_devfolio,
    "Hack2skill": fetch_hack2skill,
    "Devnovate": fetch_devnovate,
    "Internshala": fetch_internshala,
}
