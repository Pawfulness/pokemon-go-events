import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone
from dateutil import parser
import threading
import time
import os
from threading import Lock
import re

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_URL = "https://raw.githubusercontent.com/bigfoott/ScrapedDuck/data/events.json"

events_cache = {
    "events": [],
    "last_updated": None,
}
_cache_lock = Lock()
_refresh_in_progress = False

def fetch_events():
    try:
        response = requests.get(DATA_URL)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching events: {e}")
        return []


def refresh_cache() -> bool:
    global _refresh_in_progress
    with _cache_lock:
        if _refresh_in_progress:
            return False
        _refresh_in_progress = True

    try:
        data = fetch_events() or []
        with _cache_lock:
            events_cache["events"] = data
            events_cache["last_updated"] = datetime.now(timezone.utc).isoformat()
        return True
    finally:
        with _cache_lock:
            _refresh_in_progress = False

def _parse_dt_utc(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        dt = parser.parse(date_str)
    except Exception:
        return None
    if dt.tzinfo is None:
        # LeekDuck / ScrapedDuck timestamps are typically local-ish strings;
        # treat naive times as UTC for consistent comparisons/display.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_date(date_str: str | None) -> str:
    dt = _parse_dt_utc(date_str)
    if not dt:
        return (date_str or "").strip()
    return dt.strftime("%b %d, %H:%M")


def _norm_heading(s: str | None) -> str:
    t = (s or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t


def _event_to_list_item(event: dict) -> dict:
    start_str = format_date(event.get("start"))
    end_str = format_date(event.get("end"))
    return {
        "title": (event.get("name") or "").strip(),
        "subtitle": "",
        "imageUrl": event.get("image"),
        "value": f"{start_str} - {end_str}".strip(" -"),
        "url": event.get("link"),
    }

@app.get("/api/events")
def get_events():
    # Prefer cached data (refreshed daily by systemd timer).
    raw_events = events_cache.get("events") or []
    if not raw_events:
        refresh_cache()
        raw_events = events_cache.get("events") or []
    now = datetime.now(timezone.utc)

    # Filter to currently active events (started and not ended).
    current_events: list[dict] = []
    for event in raw_events:
        start_dt = _parse_dt_utc(event.get("start"))
        end_dt = _parse_dt_utc(event.get("end"))
        if not start_dt or not end_dt:
            continue
        if start_dt <= now <= end_dt:
            current_events.append(event)

    # Group by heading/tag (e.g., Raid Battles, Events, Research, Timed Research, ...)
    grouped: dict[str, list[dict]] = {}
    for ev in current_events:
        heading = _norm_heading(ev.get("heading")) or "Other"
        grouped.setdefault(heading, []).append(ev)

    # Sort within each group by end time (soonest ending first)
    for heading, evs in grouped.items():
        evs.sort(key=lambda e: (_parse_dt_utc(e.get("end")) or now))

    slides: list[dict] = []

    # Combine Season + GO Pass into a single split slide.
    season = grouped.pop("Season", [])
    go_pass = grouped.pop("GO Pass", [])
    if season or go_pass:
        slides.append(
            {
                "type": "split-slide",
                "title": "GO Pass",
                "subtitle": "Current",
                "items": [_event_to_list_item(e) for e in go_pass][:5],
                "rightTitle": "Season",
                "rightSubtitle": "Current",
                "rightItems": [_event_to_list_item(e) for e in season][:5],
                "url": "https://leekduck.com/events/",
            }
        )

    # Prefer Raid Battles early since it's a frequently-checked category.
    raid = grouped.pop("Raid Battles", [])
    if raid:
        slides.append(
            {
                "title": "Current Raid Battles",
                "subtitle": "Raid Battles",
                "items": [_event_to_list_item(e) for e in raid][:10],
                "url": "https://leekduck.com/events/",
            }
        )

    # Remaining groups in stable alphabetical order.
    for heading in sorted(grouped.keys()):
        evs = grouped[heading]
        if not evs:
            continue
        slides.append(
            {
                "title": f"Current {heading}",
                "subtitle": heading,
                "items": [_event_to_list_item(e) for e in evs][:10],
                "url": "https://leekduck.com/events/",
            }
        )

    if not slides:
        return {
            "slides": [
                {
                    "title": "No current events",
                    "subtitle": "Pokemon GO",
                    "value": "No active events right now",
                    "imageUrl": "https://cdn.leekduck.com/assets/img/events/events-default-img.jpg",
                    "url": "https://leekduck.com/events/",
                }
            ]
        }

    return {"slides": slides}


@app.post("/api/refresh")
def refresh_now():
    did_start = refresh_cache()
    return {
        "status": "ok" if did_start else "already_running",
        "in_progress": _refresh_in_progress,
        "last_updated": events_cache.get("last_updated"),
    }

@app.get("/")
def root():
    return {"status": "ok", "service": "pokemon-go-events"}

def register_service():
    # Wait for server to start
    time.sleep(5)
    try:
        payload = {
            "id": "pokemon-go-events",
            "name": "Pokemon Go Events",
            "url": "http://raspberrypi.local:8002",
            "apiUrl": "http://raspberrypi.local:8002/api/events",
            "type": "slideshow",
            "size": "1x1"
        }
        requests.post("http://raspberrypi.local:3005/api/services", json=payload)
        print("Registered service with home-page")
    except Exception as e:
        print(f"Failed to register service: {e}")

# Start registration in background
threading.Thread(target=register_service, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
