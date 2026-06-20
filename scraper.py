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


JSON_SCRIPT_PATTERN = re.compile(
    r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
    re.DOTALL,
)
MATCH_LINK_PATTERN = re.compile(
    r"/football/match/([a-z0-9-]+)/([A-Za-z0-9]+)#id:(\d+)"
)

MBAPPE_TEAM_IDS = {2829, 4481, 1644, 1653}
TEAM_DISPLAY_NAMES = {
    2829: "Real Madrid",
    4481: "France",
    1644: "PSG",
    1653: "Monaco",
}
TEAM_PAGES = [
    ("real-madrid", 2829),
    ("france", 4481),
    ("paris-saint-germain", 1644),
    ("as-monaco", 1653),
]


def parse_page_props_json(html: str) -> dict:
    scripts = JSON_SCRIPT_PATTERN.findall(html)
    if not scripts:
        raise ScraperError("application/json page data not found")
    return json.loads(scripts[0])["props"]["pageProps"]


def fetch_match_page_props(slug: str, custom_id: str) -> dict:
    url = f"https://www.sofascore.com/football/match/{slug}/{custom_id}"
    return parse_page_props_json(fetch_html(url))


def _score_value(score: dict | None, key: str) -> int | None:
    if not score:
        return None
    value = score.get(key)
    if isinstance(value, dict):
        value = value.get("current")
    return value if isinstance(value, int) else None


def _format_score(home_score: dict | None, away_score: dict | None) -> str:
    home = _score_value(home_score, "current")
    away = _score_value(away_score, "current")
    if home is None or away is None:
        return ""
    return f"{home}-{away}"


def _result_label(player_team_id: int, home_team_id: int, home_score: int, away_score: int) -> str:
    player_is_home = player_team_id == home_team_id
    player_goals = home_score if player_is_home else away_score
    opponent_goals = away_score if player_is_home else home_score
    if player_goals > opponent_goals:
        return "Victoire"
    if player_goals < opponent_goals:
        return "Défaite"
    return "Match nul"


def _field_label(player_team_id: int, home_team_id: int, is_neutral: bool) -> str:
    if is_neutral:
        return "Neutre"
    return "Domicile" if player_team_id == home_team_id else "Extérieur"


def _player_team_id(event: dict, player_id: int) -> int | None:
    home = event.get("homeTeam") or {}
    away = event.get("awayTeam") or {}
    if home.get("id") in MBAPPE_TEAM_IDS:
        return home["id"]
    if away.get("id") in MBAPPE_TEAM_IDS:
        return away["id"]
    return None


def _player_in_lineups(lineups: dict | None, player_id: int) -> dict | None:
    if not lineups:
        return None
    for side in ("home", "away"):
        for entry in (lineups.get(side) or {}).get("players") or []:
            player = entry.get("player") or {}
            if player.get("id") == player_id:
                return {
                    "side": side,
                    "statistics": entry.get("statistics") or {},
                    "substitute": entry.get("substitute"),
                }
    return None


def _extract_mbappe_events_from_json(data: dict) -> dict[int, dict]:
    events: dict[int, dict] = {}

    def walk(obj):
        if isinstance(obj, dict):
            if {"homeTeam", "awayTeam", "startTimestamp", "id"} <= set(obj.keys()):
                home_id = (obj.get("homeTeam") or {}).get("id")
                away_id = (obj.get("awayTeam") or {}).get("id")
                if home_id in MBAPPE_TEAM_IDS or away_id in MBAPPE_TEAM_IDS:
                    events[int(obj["id"])] = obj
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(data)
    return events


def _collect_mbappe_events(player_id: int, slug: str | None) -> list[dict]:
    player_html = fetch_html(
        f"https://www.sofascore.com/football/player/{slug or 'x'}/{player_id}"
    )
    player_json = json.loads(JSON_SCRIPT_PATTERN.findall(player_html)[0])
    events = _extract_mbappe_events_from_json(player_json)

    for team_slug, _team_id in TEAM_PAGES:
        team_html = fetch_html(
            f"https://www.sofascore.com/football/team/{team_slug}/{_team_id}"
        )
        team_json = json.loads(JSON_SCRIPT_PATTERN.findall(team_html)[0])
        events.update(_extract_mbappe_events_from_json(team_json))

    return sorted(events.values(), key=lambda event: event["startTimestamp"], reverse=True)


def _played_timestamps(page_props: dict) -> set[int]:
    return {
        int(event["timestamp"])
        for event in page_props.get("lastYearSummary", [])
        if event.get("type") == "event"
    }


def _fetch_match_details(event: dict) -> dict:
    slug = event.get("slug")
    custom_id = event.get("customId")
    if slug and custom_id:
        return fetch_match_page_props(slug, custom_id)

    event_id = event.get("id")
    if event_id:
        event_html = fetch_html(f"https://www.sofascore.com/event/{event_id}")
        links = MATCH_LINK_PATTERN.findall(event_html)
        if links:
            slug_part, custom_id_part, _ = links[0]
            return fetch_match_page_props(slug_part, custom_id_part)

    return {"event": event, "incidents": [], "lineups": None}


def _map_match(
    event: dict,
    lineups: dict | None,
    incidents: list[dict],
    player_id: int,
    played_timestamps: set[int] | None = None,
) -> dict | None:
    player_team_id = _player_team_id(event, player_id)
    if not player_team_id:
        return None

    home_team = event.get("homeTeam") or {}
    away_team = event.get("awayTeam") or {}
    opponent_team = away_team if player_team_id == home_team.get("id") else home_team
    lineup = _player_in_lineups(lineups, player_id)

    goals = 0
    assists = 0
    goal_incidents = []
    assist_incidents = []

    for incident in incidents or []:
        if incident.get("incidentType") != "goal":
            continue
        scorer = (incident.get("player") or {}).get("id")
        assister = (incident.get("assist1") or {}).get("id")
        if scorer == player_id:
            goals += 1
            goal_incidents.append(incident)
        if assister == player_id:
            assists += 1
            assist_incidents.append(incident)

    if not lineup and goals == 0 and assists == 0:
        timestamp = int(event.get("startTimestamp") or 0)
        if not played_timestamps or timestamp not in played_timestamps:
            return None

    home_score = _score_value(event.get("homeScore"), "current") or 0
    away_score = _score_value(event.get("awayScore"), "current") or 0
    timestamp = int(event.get("startTimestamp") or 0)
    tournament = event.get("uniqueTournament") or event.get("tournament") or {}
    season = event.get("season") or {}
    round_info = event.get("roundInfo") or {}
    is_final = "final" in (round_info.get("name") or "").lower()

    minutes = ""
    if lineup and lineup["statistics"].get("minutesPlayed") is not None:
        minutes = str(lineup["statistics"]["minutesPlayed"])

    return {
        "eventId": event.get("id"),
        "opponent": opponent_team.get("name") or "",
        "goals": str(goals),
        "competition": tournament.get("name") or "",
        "date": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
        "team": TEAM_DISPLAY_NAMES.get(player_team_id, (event.get("homeTeam") or {}).get("name") or ""),
        "minutesPlayed": minutes,
        "assists": str(assists),
        "result": _result_label(player_team_id, home_team.get("id"), home_score, away_score),
        "season": str(season.get("year") or season.get("name") or ""),
        "field": _field_label(player_team_id, home_team.get("id"), bool(event.get("neutralGround"))),
        "score": _format_score(event.get("homeScore"), event.get("awayScore")),
        "videoId": "",
        "isFinal": "true" if is_final else "false",
        "rating": lineup["statistics"].get("rating") if lineup else None,
        "goalIncidents": goal_incidents,
        "assistIncidents": assist_incidents,
        "timestamp": timestamp,
    }


def get_player_career(
    player_id: int,
    slug: str | None = None,
    limit: int = 100,
) -> dict:
    page_props = fetch_player_page_props(player_id, slug)
    player = page_props.get("player") or {}
    country = player.get("country") or {}
    team = player.get("team") or {}
    birth_ts = player.get("dateOfBirthTimestamp")
    played_ts = _played_timestamps(page_props)
    mbappe_events = _collect_mbappe_events(player_id, slug)[:limit]

    matches = []
    goals = []
    assists = []

    for event in mbappe_events:
        match_props = _fetch_match_details(event)
        event = match_props.get("event") or event
        mapped = _map_match(
            event,
            match_props.get("lineups"),
            match_props.get("incidents"),
            player_id,
            played_ts,
        )
        if not mapped:
            continue

        matches.append(mapped)

        for index, incident in enumerate(mapped.pop("goalIncidents"), start=1):
            assist_name = (incident.get("assist1") or {}).get("name") or ""
            goals.append(
                {
                    "assistPlayer": assist_name,
                    "competition": mapped["competition"],
                    "date": mapped["date"],
                    "distance": "",
                    "field": mapped["field"],
                    "minute": str(incident.get("time") or ""),
                    "number": str(len(goals) + 1),
                    "opponent": mapped["opponent"],
                    "shootPart": "",
                    "team": mapped["team"],
                    "xG": "",
                    "xGa": "",
                }
            )

        for incident in mapped.pop("assistIncidents"):
            scorer_name = (incident.get("player") or {}).get("name") or ""
            assists.append(
                {
                    "competition": mapped["competition"],
                    "date": mapped["date"],
                    "distance": "",
                    "field": mapped["field"],
                    "minute": str(incident.get("time") or ""),
                    "number": str(len(assists) + 1),
                    "opponent": mapped["opponent"],
                    "shootPart": "",
                    "scorer": scorer_name,
                    "team": mapped["team"],
                    "xG": "",
                    "xGa": "",
                }
            )

    matches.sort(key=lambda match: match["timestamp"])
    for index, match in enumerate(matches, start=1):
        match["matchNumber"] = str(index)
        if birth_ts:
            age_seconds = match["timestamp"] - int(birth_ts)
            match["age"] = str(max(0, age_seconds // (365 * 24 * 3600)))
        else:
            match["age"] = ""
        match.pop("timestamp", None)
        match.pop("rating", None)

    total_goals = sum(int(match["goals"]) for match in matches if match["goals"].isdigit())
    total_assists = sum(int(match["assists"]) for match in matches if match["assists"].isdigit())

    name_parts = (player.get("name") or "").split(" ", 1)
    first_name = name_parts[0] if name_parts else ""
    last_name = name_parts[1] if len(name_parts) > 1 else ""

    return {
        "profile": {
            "firstName": first_name,
            "lastName": last_name,
            "nationalities": country.get("alpha3") or country.get("name") or "FRA",
            "birthDateTimestamp": birth_ts,
            "size": f"{player.get('height')} cm" if player.get("height") else "",
            "team": team.get("name") or "",
            "formerTeamList": ["Monaco", "PSG", "Real Madrid"],
            "totalAssists": str(total_assists),
            "totalGoals": str(total_goals),
            "totalTrophies": "",
            "totalMatches": str(len(matches)),
        },
        "matches": matches,
        "goals": goals,
        "assists": assists,
        "source": "sofascore-html",
    }


def get_player_profile(player_id: int, slug: str | None = None) -> dict:
    page_props = fetch_player_page_props(player_id, slug)
    player = page_props.get("player") or {}
    team = player.get("team") or {}
    country = player.get("country") or {}

    return {
        "id": player.get("id", player_id),
        "name": player.get("name"),
        "slug": player.get("slug"),
        "position": player.get("position"),
        "jerseyNumber": player.get("jerseyNumber"),
        "dateOfBirthTimestamp": player.get("dateOfBirthTimestamp"),
        "height": player.get("height"),
        "country": country,
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
