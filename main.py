import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone, timedelta
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
EXCLUDED_HEADINGS = {"go battle league"}
UPCOMING_WINDOW_DAYS = 30

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


def _is_excluded_heading(heading: str | None) -> bool:
    h = _norm_heading(heading).casefold()
    return h in EXCLUDED_HEADINGS


def _event_to_list_item(
    event: dict,
    subtitle_override: str | None = None,
    value_override: str | None = None,
) -> dict:
    if value_override is not None:
        value = value_override.strip()
    else:
        start_str = format_date(event.get("start"))
        end_str = format_date(event.get("end"))
        value = f"{start_str} - {end_str}".strip(" -")

    return {
        "title": (event.get("name") or "").strip(),
        "subtitle": (subtitle_override or "").strip(),
        "imageUrl": event.get("image"),
        "value": value,
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
    upcoming_cutoff = now + timedelta(days=UPCOMING_WINDOW_DAYS)

    # Filter to currently active events (started and not ended).
    current_events: list[dict] = []
    upcoming_events: list[dict] = []
    for event in raw_events:
        start_dt = _parse_dt_utc(event.get("start"))
        end_dt = _parse_dt_utc(event.get("end"))
        if not start_dt or not end_dt:
            continue
        if _is_excluded_heading(event.get("heading")):
            continue

        if start_dt <= now <= end_dt:
            current_events.append(event)
        elif start_dt > now and start_dt <= upcoming_cutoff:
            upcoming_events.append(event)

    # Group by heading/tag (e.g., Raid Battles, Events, Research, Timed Research, ...)
    grouped_current: dict[str, list[dict]] = {}
    for ev in current_events:
        heading = _norm_heading(ev.get("heading")) or "Other"
        grouped_current.setdefault(heading, []).append(ev)

    # Sunday-only: add a synthetic "Trade Day" into the existing current Event(s) group.
    # ScrapedDuck sometimes uses "Event" vs "Events"; prefer whichever is present so it lands
    # on the same slide as the other current event items.
    local_now = datetime.now().astimezone()
    if local_now.weekday() == 6:  # Sunday
        target_heading = "Event" if "Event" in grouped_current else "Events"
        grouped_current.setdefault(target_heading, []).append(
            {
                "name": "Trade Day",
                "heading": target_heading,
                "image": "https://cdn.leekduck.com/assets/img/events/events-default-img.jpg",
                "link": "https://leekduck.com/events/",
            }
        )

    grouped_upcoming: dict[str, list[dict]] = {}
    for ev in upcoming_events:
        heading = _norm_heading(ev.get("heading")) or "Other"
        grouped_upcoming.setdefault(heading, []).append(ev)

    # Sort within each group
    # - current: end time (soonest ending first)
    # - upcoming: start time (soonest starting first)
    for heading, evs in grouped_current.items():
        evs.sort(key=lambda e: (_parse_dt_utc(e.get("end")) or now))
    for heading, evs in grouped_upcoming.items():
        evs.sort(key=lambda e: (_parse_dt_utc(e.get("start")) or now))

    slides: list[dict] = []

    # Combine Season + GO Pass into a single split slide.
    season = grouped_current.pop("Season", [])
    go_pass = grouped_current.pop("GO Pass", [])
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

    # Combine Research + Timed Research into a single slide.
    research = grouped_current.pop("Research", [])
    timed_research = grouped_current.pop("Timed Research", [])
    if research or timed_research:
        combined = [("Research", e) for e in research] + [("Timed Research", e) for e in timed_research]
        combined.sort(key=lambda pair: (_parse_dt_utc(pair[1].get("end")) or now, pair[0], (pair[1].get("name") or "")))
        slides.append(
            {
                "title": "Current research",
                "subtitle": "Research + Timed Research",
                "maxItems": 50,
                "items": [_event_to_list_item(e, subtitle_override=label) for label, e in combined],
                "url": "https://leekduck.com/events/",
            }
        )

    # Prefer Raid Battles early since it's a frequently-checked category.
    raid = grouped_current.pop("Raid Battles", [])
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
    for heading in sorted(grouped_current.keys()):
        evs = grouped_current[heading]
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

    # Upcoming (next N days) — single slide, ordered by start time.
    if upcoming_events:
        upcoming_events.sort(
            key=lambda e: (
                _parse_dt_utc(e.get("start")) or now,
                (_norm_heading(e.get("heading")) or "Other"),
                (e.get("name") or ""),
            )
        )
        slides.append(
            {
                "title": "Upcoming events",
                "subtitle": f"Next {UPCOMING_WINDOW_DAYS} days",
                "maxItems": 200,
                "items": [
                    _event_to_list_item(
                        e,
                        subtitle_override=_norm_heading(e.get("heading")) or "Other",
                    )
                    for e in upcoming_events
                ],
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
