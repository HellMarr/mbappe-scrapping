import json
import os
import re
from datetime import datetime, timezone

from curl_cffi import requests as curl_requests

SESSION = curl_requests.Session(impersonate="chrome")
PROXY = os.getenv("PROXY_URL")
REFERER = "https://www.sofascore.com/"
NEXT_DATA_PATTERN = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)


class ScraperError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _request_kwargs() -> dict:
    kwargs = {
        "timeout": 20,
        "headers": {"Referer": REFERER},
    }
    if PROXY:
        kwargs["proxies"] = {"http": PROXY, "https": PROXY}
    return kwargs


def fetch_html(url: str) -> str:
    response = SESSION.get(url, **_request_kwargs())
    if response.status_code != 200:
        raise ScraperError(
            f"Sofascore returned {response.status_code} for {url}",
            status_code=response.status_code,
        )
    return response.text


def parse_next_data(html: str) -> dict:
    match = NEXT_DATA_PATTERN.search(html)
    if not match:
        raise ScraperError("__NEXT_DATA__ not found in Sofascore page")
    return json.loads(match.group(1))["props"]["pageProps"]


def fetch_player_page_props(player_id: int, slug: str | None = None) -> dict:
    slug_part = slug or "x"
    url = f"https://www.sofascore.com/football/player/{slug_part}/{player_id}"
    return parse_next_data(fetch_html(url))


def _is_national_tournament(tournament: dict | None) -> bool:
    if not tournament:
        return False
    category = tournament.get("category") or {}
    return category.get("flag") == "international"


def _format_match_event(event: dict, tournaments_map: dict) -> dict:
    tournament = tournaments_map.get(str(event["uniqueTournamentId"]))
    timestamp = int(event["timestamp"])
    return {
        "rating": float(event["value"]),
        "timestamp": timestamp,
        "date": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
        "tournamentId": event["uniqueTournamentId"],
        "tournament": tournament.get("name") if tournament else None,
        "tournamentSlug": tournament.get("slug") if tournament else None,
        "isNational": _is_national_tournament(tournament),
    }


def get_player_recent_matches(
    player_id: int,
    slug: str | None = None,
    limit: int = 20,
    context: str | None = None,
) -> list[dict]:
    page_props = fetch_player_page_props(player_id, slug)
    tournaments_map = page_props.get("uniqueTournamentsMap") or {}
    events = [
        event
        for event in page_props.get("lastYearSummary", [])
        if event.get("type") == "event"
    ]
    events.sort(key=lambda event: event["timestamp"], reverse=True)

    formatted = [_format_match_event(event, tournaments_map) for event in events]

    if context == "club":
        formatted = [event for event in formatted if not event["isNational"]]
    elif context == "national":
        formatted = [event for event in formatted if event["isNational"]]

    return formatted[:limit]


def get_player_last_match(
    player_id: int,
    slug: str | None = None,
    context: str | None = None,
) -> dict:
    matches = get_player_recent_matches(
        player_id=player_id,
        slug=slug,
        limit=1,
        context=context,
    )
    if not matches:
        raise ScraperError("No recent match found", status_code=404)
    return matches[0]


def get_player_profile(player_id: int, slug: str | None = None) -> dict:
    page_props = fetch_player_page_props(player_id, slug)
    player = page_props.get("player") or {}
    team = player.get("team") or {}

    return {
        "id": player.get("id", player_id),
        "name": player.get("name"),
        "slug": player.get("slug"),
        "position": player.get("position"),
        "jerseyNumber": player.get("jerseyNumber"),
        "dateOfBirthTimestamp": player.get("dateOfBirthTimestamp"),
        "team": {
            "id": team.get("id"),
            "name": team.get("name"),
            "slug": team.get("slug"),
        },
    }


def fetch_api(path: str) -> dict:
    if not path.startswith("/"):
        path = f"/{path}"

    kwargs = _request_kwargs()
    kwargs["headers"] = {
        "Referer": REFERER,
        "Origin": REFERER.rstrip("/"),
    }

    SESSION.get(REFERER, **kwargs)
    response = SESSION.get(
        f"https://api.sofascore.com/api/v1{path}",
        **kwargs,
    )

    if response.status_code != 200:
        raise ScraperError(
            f"Sofascore API returned {response.status_code}: {response.text[:300]}",
            status_code=response.status_code,
        )

    return response.json()
