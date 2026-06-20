from flask import Flask, jsonify, request
from flask_cors import CORS

from scraper import (
    ScraperError,
    fetch_api,
    get_event,
    get_event_incidents,
    get_team_last_events,
    get_player_career,
    get_player_last_match,
    get_player_last_match_full,
    get_player_profile,
    get_player_recent_matches,
)

app = Flask(__name__)
CORS(
    app,
    resources={r"/*": {"origins": [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://kmbappe.fr",
        "https://www.kmbappe.fr",
        "https://kmbappe-site.web.app",
        "https://kmbappe-site.firebaseapp.com",
    ]}},
)

DEFAULT_PLAYER_ID = 826643
DEFAULT_PLAYER_SLUG = "kylian-mbappe"


def _player_slug() -> str | None:
    return request.args.get("slug", DEFAULT_PLAYER_SLUG)


def _match_context() -> str | None:
    context = request.args.get("context")
    if context in (None, "", "club", "national"):
        return context or None
    raise ScraperError("context must be club or national", status_code=400)


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.get("/player/<int:player_id>")
def player_profile(player_id: int):
    return jsonify(get_player_profile(player_id, _player_slug()))


@app.get("/player/<int:player_id>/last-match")
def player_last_match(player_id: int):
    match = get_player_last_match(
        player_id=player_id,
        slug=_player_slug(),
        context=_match_context(),
    )
    return jsonify({"playerId": player_id, **match, "source": "html"})


@app.get("/player/<int:player_id>/last-match/full")
def player_last_match_full(player_id: int):
    match = get_player_last_match_full(
        player_id=player_id,
        slug=_player_slug(),
        context=_match_context(),
    )
    return jsonify(match)


@app.get("/player/<int:player_id>/recent-matches")
def player_recent_matches(player_id: int):
    limit = min(request.args.get("limit", 20, type=int), 100)
    matches = get_player_recent_matches(
        player_id=player_id,
        slug=_player_slug(),
        limit=limit,
        context=_match_context(),
    )
    return jsonify({"playerId": player_id, "matches": matches, "source": "html"})


@app.get("/player/<int:player_id>/career")
def player_career(player_id: int):
    limit = min(request.args.get("limit", 100, type=int), 200)
    return jsonify(get_player_career(player_id, _player_slug(), limit=limit))


@app.get("/team/<int:team_id>/events/last/<int:page>")
def team_last_events(team_id: int, page: int):
    return jsonify(get_team_last_events(team_id, page))


@app.get("/event/<int:event_id>/incidents")
def event_incidents(event_id: int):
    return jsonify(get_event_incidents(event_id))


@app.get("/event/<int:event_id>")
def event(event_id: int):
    return jsonify(get_event(event_id))


@app.get("/proxy")
def proxy():
    path = request.args.get("path")
    if not path:
        raise ScraperError("Missing path query parameter", status_code=400)
    if not path.startswith("/"):
        path = f"/{path}"
    return jsonify(fetch_api(path))


@app.errorhandler(ScraperError)
def handle_scraper_error(error: ScraperError):
    return jsonify({"error": str(error)}), error.status_code


@app.errorhandler(Exception)
def handle_unexpected_error(error: Exception):
    return jsonify({"error": str(error)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
