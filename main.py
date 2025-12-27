import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone
from dateutil import parser
import threading
import time
import os
from threading import Lock

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

def format_date(date_str):
    try:
        dt = parser.parse(date_str)
        return dt.strftime("%b %d, %H:%M")
    except:
        return date_str

@app.get("/api/events")
def get_events():
    # Prefer cached data (refreshed daily by systemd timer).
    raw_events = events_cache.get("events") or []
    if not raw_events:
        refresh_cache()
        raw_events = events_cache.get("events") or []
    current_time = datetime.now()
    
    upcoming_events = []
    
    for event in raw_events:
        try:
            end_time = parser.parse(event.get("end"))
            # Filter out events that have already ended
            # Note: The feed might contain local times without timezone, assuming local to user or UTC?
            # LeekDuck usually uses local time for events, but the ISO string might be interpreted.
            # We'll just compare naively if no tzinfo, or aware if present.
            
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=None)
                
            if end_time > current_time:
                upcoming_events.append(event)
        except:
            continue
            
    # Sort by start time
    upcoming_events.sort(key=lambda x: x.get("start"))
    
    slides = []
    for event in upcoming_events[:10]: # Limit to 10 events
        start_str = format_date(event.get("start"))
        end_str = format_date(event.get("end"))
        
        slides.append({
            "title": event.get("name"),
            "subtitle": event.get("heading"),
            "imageUrl": event.get("image"),
            "value": f"{start_str} - {end_str}",
            "url": event.get("link")
        })
        
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
