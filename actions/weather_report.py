import webbrowser
from urllib.parse import quote_plus


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:
    city     = parameters.get("city")
    when     = parameters.get("time", "today")  

    if not city or not isinstance(city, str) or not city.strip():
        msg = "The city is missing for the weather report."
        _log(msg, player)
        return msg

    city = city.strip()
    when = (when or "today").strip()

    # RULE 1 and silent lookup: the forecast is fetched in the background and returned as
    # text. No browser or workspace is opened, so ARNOX answers without showing anything.
    from actions.silent_lookup import silent_web_lookup

    search_query = f"weather in {city} {when}"
    answer = silent_web_lookup(search_query)
    _log(f"Background weather lookup for {city}, {when}.", player)

    if session_memory:
        try:
            session_memory.set_last_search(query=search_query, response=answer)
        except Exception:
            pass

    return answer


def _log(message: str, player=None) -> None:
    print(f"[Weather] {message}")
    if player:
        try:
            player.write_log(f"ARNOX: {message}")
        except Exception:
            pass