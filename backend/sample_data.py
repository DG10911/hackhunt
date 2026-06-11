"""Curated FALLBACK data — shown only when a live source is blocked/offline.

Every entry here is a REAL, recurring flagship Indian hackathon/challenge with
its official link, verified via web search. Dates are approximate for the next
edition (organisers confirm exact dates closer to the event) — the UI flags
these as "Verified · confirm dates on site". No fictional events.
"""

SAMPLE = [
    # ---- FEATURED (June 2026) — biggest live event, pinned to top of feed ----
    {"id": "s-india-runs", "title": "India Runs by RedRob AI", "organizer": "RedRob AI · Hack2skill",
     "platform": "Hack2skill",
     "url": "https://hack2skill.com/event/india_runs?utm_source=hack2skill&utm_medium=teamdashboard&utm_campaign=india_runs&utm_term=referral-1&utm_content=6976ef22c297ec950b361a36",
     "ticket_url": "https://hack2skill.com/event/india_runs/registration",
     "mode": "Online", "location": "India (Nationwide · Online)", "starts": "2026-06-10", "ends": "2026-07-22",
     "deadline": "2026-07-15", "prize": "₹50 Lakh+ Prize Pool",
     "tags": ["AI", "Data", "Ideathon", "Open Innovation", "Student"],
     "category": "Hackathon", "image": None, "participants": 50000,
     "verified": True, "dates_confirmed": False, "featured": True},

    # Recurring govt/flagship events whose NEXT edition is upcoming (dates approx,
    # flagged "confirm on site"). Verified still active annually as of June 2026.
    {"id": "s-sih", "title": "Smart India Hackathon 2026", "organizer": "Govt of India · Ministry of Education",
     "platform": "Internshala", "url": "https://internshala.com/competitions/smart-india-hackathon-sih-2026/",
     "mode": "Hybrid", "location": "India (Nationwide)", "starts": "2026-09-01", "ends": "2026-12-15",
     "deadline": "2026-08-25", "prize": "₹1,00,000 per problem", "tags": ["Govt", "Open Innovation"],
     "category": "Government", "image": None, "participants": 200000, "verified": True, "dates_confirmed": False},
    {"id": "s-ethindia", "title": "ETHIndia 2026", "organizer": "Devfolio",
     "platform": "Devfolio", "url": "https://ethindia.co/", "mode": "Offline",
     "location": "Bengaluru, India", "starts": "2026-12-05", "ends": "2026-12-07",
     "deadline": "2026-11-20", "prize": "$200,000+", "tags": ["Web3", "Blockchain"],
     "category": "Hackathon", "image": None, "participants": 10000, "verified": True, "dates_confirmed": False},

    # ---- Verified LIVE (June 2026) — confirmed open, real apply links ----
    {"id": "s-samsung-sft", "title": "Samsung Solve for Tomorrow 2026", "organizer": "Samsung India",
     "platform": "Internshala", "url": "https://internshala.com/competitions/samsung-solve-for-tomorrow-2026/",
     "mode": "Hybrid", "location": "India (ages 14–22)", "starts": "2026-05-07", "ends": "2026-07-03",
     "deadline": "2026-07-03", "prize": "Grants up to ₹2,00,00,000", "tags": ["Social Impact", "AI", "Innovation", "Student"],
     "category": "Ideathon", "image": None, "participants": 70000, "verified": True, "dates_confirmed": True},
    {"id": "s-odoo", "title": "Odoo Hackathon 2026", "organizer": "Odoo",
     "platform": "Internshala", "url": "https://internshala.com/competitions/odoo-hackathon-2026/",
     "mode": "Hybrid", "location": "India", "starts": "2026-07-12", "ends": "2026-09-06",
     "deadline": "2026-07-11", "prize": "Cash + Goodies", "tags": ["Coding", "Open Innovation"],
     "category": "Hackathon", "image": None, "participants": 30000, "verified": True, "dates_confirmed": True},
]
