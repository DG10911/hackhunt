"""Community layer — tech conferences, summits, meetups & student events in India
with ticketing links. Curated + easy to extend. Returns the same normalized
shape as hackathons plus: 'type' (Conference|Meetup|Summit|Workshop|Webinar),
'price', 'ticket_url', 'city'."""

COMMUNITY = [
    {"id": "c-cypher", "title": "Cypher 2026 — India's Biggest AI Conference", "organizer": "Analytics India Magazine",
     "type": "Conference", "category": "Conference", "platform": "Community",
     "city": "Bengaluru", "mode": "Offline", "location": "KTPO, Whitefield, Bengaluru",
     "starts": "2026-10-07", "ends": "2026-10-09", "deadline": "2026-10-01",
     "price": "From ₹7,000 (student passes available)", "ticket_url": "https://cypher.analyticsindiamag.com/tickets",
     "url": "https://cypher.analyticsindiamag.com/", "tags": ["AI", "ML", "Data"],
     "themes": ["AI/ML"], "mnc": True, "dates_confirmed": True},
    {"id": "c-gophercon", "title": "GopherCon India 2026", "organizer": "Emerging Technology Trust",
     "type": "Conference", "category": "Conference", "platform": "Community",
     "city": "Hyderabad", "mode": "Offline", "location": "Novotel Hyderabad Airport",
     "starts": "2026-11-22", "ends": "2026-11-22", "deadline": "2026-11-15",
     "price": "Paid (see site)", "ticket_url": "https://www.townscript.com/e/GopherCon-india-2026",
     "url": "https://gopherconindia.org/", "tags": ["Go", "Backend", "Cloud"],
     "themes": ["Cloud/DevOps"], "image": None, "dates_confirmed": True},
    {"id": "c-pyconf", "title": "PyConf Hyderabad 2026", "organizer": "HydPy · Python Community",
     "type": "Conference", "category": "Conference", "platform": "Community",
     "city": "Hyderabad", "mode": "Hybrid", "location": "Hyderabad / Online",
     "starts": "2026-03-14", "ends": "2026-03-15", "deadline": "2026-03-10",
     "price": "₹2,500 (student)", "ticket_url": "https://konfhub.com/pyconf-hyderabad-2026",
     "url": "https://konfhub.com/pyconf-hyderabad-2026", "tags": ["Python", "AI/ML", "OSS"],
     "themes": ["AI/ML"], "image": None, "dates_confirmed": True},
    {"id": "c-droidcon", "title": "droidcon India 2026", "organizer": "droidcon",
     "type": "Conference", "category": "Conference", "platform": "Community",
     "city": "Bengaluru", "mode": "Offline", "location": "Bengaluru, India",
     "starts": "2026-12-12", "ends": "2026-12-13", "deadline": "2026-12-01",
     "price": "₹6,000", "ticket_url": "https://india.droidcon.com/tickets",
     "url": "https://india.droidcon.com/", "tags": ["Android", "Mobile", "Kotlin"],
     "themes": ["Mobile"], "image": None, "dates_confirmed": False},
    {"id": "c-reactindia", "title": "React India 2026", "organizer": "React India",
     "type": "Conference", "category": "Conference", "platform": "Community",
     "city": "Goa", "mode": "Hybrid", "location": "Planet Hollywood, Utorda, South Goa",
     "starts": "2026-10-29", "ends": "2026-10-31", "deadline": "2026-10-15",
     "price": "₹11,800+ (incl. GST)", "ticket_url": "https://www.reactindia.io/conferences/tickets",
     "url": "https://www.reactindia.io/", "tags": ["React", "Frontend", "JS"],
     "themes": ["Mobile"], "image": None, "dates_confirmed": True},
    {"id": "c-gdg-blr", "title": "GDG DevFest Bengaluru 2026", "organizer": "Google Developer Groups",
     "type": "Meetup", "category": "Meetup", "platform": "Community",
     "city": "Bengaluru", "mode": "Offline", "location": "Bengaluru, India",
     "starts": "2026-11-07", "ends": "2026-11-07", "deadline": "2026-11-01",
     "price": "Free", "ticket_url": "https://gdg.community.dev/devfest/",
     "url": "https://gdg.community.dev/devfest/", "tags": ["Google", "Android", "AI"],
     "themes": ["AI/ML", "Cloud/DevOps"], "image": None, "mnc": True, "dates_confirmed": False},
    {"id": "c-awscd", "title": "AWS Community Day Bengaluru 2026", "organizer": "AWS User Group Bengaluru",
     "type": "Meetup", "category": "Meetup", "platform": "Community",
     "city": "Bengaluru", "mode": "Offline", "location": "NIMHANS Convention Centre, Bengaluru",
     "starts": "2026-07-11", "ends": "2026-07-11", "deadline": "2026-07-04",
     "price": "₹999", "ticket_url": "https://acd.awsugblr.in/",
     "url": "https://acd.awsugblr.in/", "tags": ["AWS", "Cloud", "Serverless"],
     "themes": ["Cloud/DevOps"], "image": None, "mnc": True, "dates_confirmed": True},
    {"id": "c-fossunited", "title": "FOSS United Hack Night", "organizer": "FOSS United",
     "type": "Meetup", "category": "Meetup", "platform": "Community",
     "city": "Online", "mode": "Online", "location": "Online · India",
     "starts": "2026-06-21", "ends": "2026-06-21", "deadline": "2026-06-20",
     "price": "Free", "ticket_url": "https://fossunited.org/events",
     "url": "https://fossunited.org/events", "tags": ["Open Source", "Community"],
     "themes": ["Open Innovation"], "image": None, "dates_confirmed": False},
    {"id": "c-cybersec", "title": "Nullcon Goa 2026 (Cybersecurity)", "organizer": "Nullcon",
     "type": "Conference", "category": "Conference", "platform": "Community",
     "city": "Goa", "mode": "Offline", "location": "BITS Pilani Goa Campus",
     "starts": "2026-02-28", "ends": "2026-03-01", "deadline": "2026-02-20",
     "price": "₹9,000 (student)", "ticket_url": "https://nullcon.net/",
     "url": "https://nullcon.net/", "tags": ["Security", "Hacking", "InfoSec"],
     "themes": ["Cybersecurity"], "image": None, "dates_confirmed": True},
    {"id": "c-rustmeet", "title": "Rust India Meetup", "organizer": "Rust India",
     "type": "Meetup", "category": "Meetup", "platform": "Community",
     "city": "Online", "mode": "Online", "location": "Online · India",
     "starts": "2026-07-12", "ends": "2026-07-12", "deadline": "2026-07-11",
     "price": "Free", "ticket_url": "https://www.meetup.com/rust-language-india/",
     "url": "https://www.meetup.com/rust-language-india/", "tags": ["Rust", "Systems"],
     "themes": ["Cloud/DevOps"], "image": None, "dates_confirmed": False},
    {"id": "c-web3conf", "title": "India Blockchain Week 2026", "organizer": "Hashed Emergent",
     "type": "Summit", "category": "Conference", "platform": "Community",
     "city": "Mumbai", "mode": "Offline", "location": "Fairmont, Mumbai",
     "starts": "2026-11-01", "ends": "2026-11-02", "deadline": "2026-10-20",
     "price": "Super Early Bird", "ticket_url": "https://www.indiablockchainweek.com/tickets",
     "url": "https://www.indiablockchainweek.com/", "tags": ["Web3", "Crypto", "DeFi"],
     "themes": ["Web3"], "image": None, "dates_confirmed": True},
    {"id": "c-designup", "title": "DesignUp Festival 2026", "organizer": "DesignUp",
     "type": "Conference", "category": "Conference", "platform": "Community",
     "city": "Bengaluru", "mode": "Hybrid", "location": "Sheraton Grand Whitefield, Bengaluru",
     "starts": "2026-10-01", "ends": "2026-10-04", "deadline": "2026-09-20",
     "price": "₹8,950 (student)", "ticket_url": "https://26.designup.io/",
     "url": "https://26.designup.io/", "tags": ["UX", "Design", "Product"],
     "themes": ["Open Innovation"], "image": None, "dates_confirmed": True},
]


# Stable, free Unsplash banner images by theme (load reliably; UI falls back
# to a gradient if any are blocked).
_U = "https://images.unsplash.com/"
_Q = "?auto=format&fit=crop&w=640&q=70"
THEME_IMG = {
    "AI/ML": _U + "photo-1677442136019-21780ecad995" + _Q,
    "Web3": _U + "photo-1639762681485-074b7f938ba0" + _Q,
    "Cybersecurity": _U + "photo-1550751827-4bd374c3f58b" + _Q,
    "Mobile": _U + "photo-1512941937669-90a1b58e7e9c" + _Q,
    "Cloud/DevOps": _U + "photo-1451187580459-43490279c0fa" + _Q,
    "Open Innovation": _U + "photo-1540575467063-178a50c2df87" + _Q,
}
DEFAULT_IMG = _U + "photo-1505373877841-8d25f7d46678" + _Q  # conference crowd


def get_community():
    out = []
    for c in COMMUNITY:
        c.setdefault("prize", "")
        c.setdefault("participants", None)
        c.setdefault("mnc", False)
        c.setdefault("govt", False)
        c.setdefault("sample", True)
        c.setdefault("verified", True)  # all curated community events are real, verified brands
        c.setdefault("dates_confirmed", False)
        if not c.get("image"):
            theme = (c.get("themes") or [None])[0]
            c["image"] = THEME_IMG.get(theme, DEFAULT_IMG)
        out.append(c)
    return out
