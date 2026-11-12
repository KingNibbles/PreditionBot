"""Twitch Helix predictions client: create, get and end (lock, resolve, cancel). Needs channel:manage:predictions."""
from __future__ import annotations
import logging, time
from .lazy_http import LazyHttp
from datetime import datetime, timezone

log = logging.getLogger(__name__)
HELIX = "https://api.twitch.tv/helix"


class TwitchError(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg); self.status = status

class AuthError(TwitchError): pass
class RateLimited(TwitchError):
    def __init__(self, retry_after): super().__init__(f"rate limited, retry in {retry_after:.0f}s", 429); self.retry_after = retry_after
class TwitchTimeout(TwitchError):
    """Outcome unknown: the request may or may not have been applied. Verify before retrying."""
class BadRequest(TwitchError): pass


def parse_ts(s: str) -> datetime:
    # Twitch sends nanoseconds, datetime only takes microseconds
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        frac, tz = rest[:-6], rest[-6:]
        s = f"{head}.{frac[:6]}{tz}"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


class TwitchClient(LazyHttp):
    def __init__(self, client_id, broadcaster_id, auth, http=None, timeout=10.0):
        self.client_id, self.broadcaster_id, self.auth = client_id, broadcaster_id, auth
        self._http, self.timeout = http, timeout

    def _request(self, method, params=None, json=None, _retry_auth=True):
        headers = {"Authorization": f"Bearer {self.auth.get_access_token()}", "Client-Id": self.client_id}
        try:
            r = self.http.request(method, f"{HELIX}/predictions", headers=headers, params=params, json=json)
        except Exception as e:  # httpx.TimeoutException / network errors
            name = type(e).__name__
            if "Timeout" in name or "Connect" in name or "Read" in name or "Network" in name:
                raise TwitchTimeout(f"{method} {name}") from e
            raise
        if r.status_code == 401:
            if _retry_auth:
                log.warning("401 from Twitch; refreshing token once")
                self.auth.refresh(force=True)
                return self._request(method, params, json, _retry_auth=False)
            raise AuthError("Unauthorized after refresh; reconnect Twitch", 401)
        if r.status_code == 429:
            reset = r.headers.get("Ratelimit-Reset")
            wait = max(1.0, float(reset) - time.time()) if reset else 30.0
            raise RateLimited(min(wait, 120.0))
        if r.status_code in (400, 403, 404):
            raise BadRequest(f"{r.status_code}: {_msg(r)}", r.status_code)
        if r.status_code >= 500:
            raise TwitchError(f"Twitch server error {r.status_code}", r.status_code)
        if r.status_code >= 300:
            raise TwitchError(f"HTTP {r.status_code}: {_msg(r)}", r.status_code)
        return r.json()

    def create_prediction(self, title, outcome_titles, window_s):
        body = {"broadcaster_id": self.broadcaster_id, "title": title,
                "outcomes": [{"title": t} for t in outcome_titles], "prediction_window": int(window_s)}
        data = self._request("POST", json=body)["data"][0]
        log.info("Created prediction %s window=%s", data["id"], data["prediction_window"])
        return data

    def _first(self, **params):
        data = self._request("GET", params={"broadcaster_id": self.broadcaster_id, **params})["data"]
        return data[0] if data else None

    def get_prediction(self, pid):
        return self._first(id=pid)

    def latest_prediction(self):
        return self._first(first=1)

    def end_prediction(self, pid, status, winning_outcome_id=None):
        assert status in ("RESOLVED", "CANCELED", "LOCKED")
        body = {"broadcaster_id": self.broadcaster_id, "id": pid, "status": status}
        if status == "RESOLVED":
            if not winning_outcome_id:
                raise ValueError("RESOLVED requires winning_outcome_id")
            body["winning_outcome_id"] = winning_outcome_id
        data = self._request("PATCH", json=body)["data"][0]
        log.info("Prediction %s -> %s", pid, data["status"])
        return data


def _msg(r):
    try: return r.json().get("message", r.text)
    except Exception: return getattr(r, "text", "")
