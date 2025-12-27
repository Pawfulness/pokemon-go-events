# Pokemon Go Events Scraper

This service fetches upcoming Pokemon Go events from LeekDuck (via ScrapedDuck) and provides an API for the Home Page Dashboard.

It supports a small in-memory cache that can be refreshed on a schedule (recommended: once per day).

## Setup

1.  Install dependencies:
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

2.  Run the server:
    ```bash
    python3 main.py
    ```

The service runs on port 8002.

## API

- `GET /api/events` - returns dashboard slides
- `POST /api/refresh` - refreshes the cached upstream JSON (intended for a systemd timer)

## Raspberry Pi (systemd)

This repo includes example systemd unit files in `systemd/`:

- `pokemon-go-events.service`: keeps the API running
- `pokemon-go-events-refresh.timer`: triggers a daily cache refresh (default `00:02`)

### Install

```bash
sudo cp systemd/pokemon-go-events.service /etc/systemd/system/
sudo cp systemd/pokemon-go-events-refresh.service /etc/systemd/system/
sudo cp systemd/pokemon-go-events-refresh.timer /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable --now pokemon-go-events.service
sudo systemctl enable --now pokemon-go-events-refresh.timer
```

### Logs

```bash
journalctl -u pokemon-go-events.service -f
journalctl -u pokemon-go-events-refresh.service -n 200 --no-pager
```
