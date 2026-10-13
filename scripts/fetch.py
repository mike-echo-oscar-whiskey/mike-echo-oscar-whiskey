#!/usr/bin/env python3
"""Fetch what the profile is built from: the account's pull requests in other people's repositories
(data/patches.json) and the current weather at the place profile.toml names (data/weather.json).

Usage: scripts/fetch.py            (needs gh, signed in; network access to api.open-meteo.com)
"""
from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=60).stdout


def fetch_pull_requests() -> dict[str, object]:
    login = gh("api", "user", "--jq", ".login").strip()
    pages = gh(
        "api", "-X", "GET", "search/issues", "--paginate", "--slurp",
        "-f", f"q=is:pr author:{login} -user:{login}", "-f", "per_page=100",
    )
    items = [item for page in json.loads(pages) for item in page["items"]]
    pull_requests = []
    for item in items:
        merged = item["pull_request"].get("merged_at") is not None
        pull_requests.append({
            "repository": item["repository_url"].split("/repos/", 1)[1],
            "number": item["number"],
            "title": item["title"],
            "url": item["html_url"],
            "state": "merged" if merged else item["state"],
            "created_at": item["created_at"],
        })
    pull_requests.sort(key=lambda p: str(p["created_at"]), reverse=True)
    return {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "author": login, "pull_requests": pull_requests}


def fetch_weather(settings: dict[str, object]) -> dict[str, object]:
    query = urlencode({
        "latitude": settings["latitude"], "longitude": settings["longitude"], "timezone": settings["timezone"],
        "current": "temperature_2m,weather_code,wind_speed_10m",
    })
    with urlopen(f"https://api.open-meteo.com/v1/forecast?{query}", timeout=30) as response:
        weather: dict[str, object] = json.load(response)
    if "current" not in weather:
        raise SystemExit(f"open-meteo answered without a current block: {weather}")
    return weather


def main() -> int:
    profile = tomllib.loads((ROOT / "profile.toml").read_text())
    DATA.mkdir(exist_ok=True)
    print("=== Step 1: pull requests outside the account's own repositories ===")
    patches = fetch_pull_requests()
    (DATA / "patches.json").write_text(json.dumps(patches, indent=2, ensure_ascii=False) + "\n")
    print(f"{len(patches['pull_requests'])} pull requests by {patches['author']}")
    print("=== Step 2: current weather ===")
    weather = fetch_weather(profile["weather"])
    (DATA / "weather.json").write_text(json.dumps(weather, indent=2) + "\n")
    current = weather["current"]
    assert isinstance(current, dict)
    print(f"{profile['weather']['place']} at {current['time']}: code {current['weather_code']}, {current['temperature_2m']} °C")
    return 0


if __name__ == "__main__":
    sys.exit(main())
