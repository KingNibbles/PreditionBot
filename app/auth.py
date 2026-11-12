"""Twitch login for a desktop app: device code flow with a public client, so no client secret lives on this PC."""
from __future__ import annotations
import logging, threading, time
from .lazy_http import LazyHttp

log = logging.getLogger(__name__)
ID = "https://id.twitch.tv/oauth2"
SCOPES = ["channel:manage:predictions"]  # also covers reading predictions
SCOPE_PARAM = " ".join(SCOPES)
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SERVICE = "valorant-prediction-bot"   # name of the Credential Manager entries; renaming it would log everyone out


def is_twitch_url(url) -> bool:
    """True only for https pages on twitch.tv, so a bad response can never send the browser elsewhere."""
    from urllib.parse import urlparse
    try:
        u = urlparse(str(url))
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host == "twitch.tv" or host.endswith(".twitch.tv"))


class AuthRequired(Exception):
    """No usable token; user must click Connect Twitch."""


class KeyringStore:
    """Refresh/access tokens live in Windows Credential Manager, never in config or logs."""
    def __init__(self, service=SERVICE):
        import keyring
        self.kr, self.service = keyring, service
    def get(self, k): return self.kr.get_password(self.service, k)
    def set(self, k, v): self.kr.set_password(self.service, k, v)
    def delete(self, k):
        try:
            self.kr.delete_password(self.service, k)
        except Exception as e:
            try:
                still_there = self.kr.get_password(self.service, k) is not None
            except Exception:
                still_there = True
            if still_there:        # "not found" is fine; anything else means the secret is still stored
                log.warning("Could not delete stored %s (%s)", k, type(e).__name__)


class TwitchAuth(LazyHttp):
    def __init__(self, client_id, store, http=None, clock=time.time):
        self.client_id, self.store, self._http, self.clock = client_id, store, http, clock
        self._lock = threading.Lock()
        self.user_id = None
        self.login = None

    def start_device_flow(self):
        r = self.http.post(f"{ID}/device", data={"client_id": self.client_id, "scopes": SCOPE_PARAM})
        if r.status_code != 200:
            raise RuntimeError(f"device flow start failed: {r.status_code} {r.text}")
        return r.json()  # device_code, user_code, verification_uri, expires_in, interval

    def poll_device_flow(self, flow, sleep=time.sleep):
        deadline = self.clock() + flow["expires_in"]
        interval = max(5, int(flow.get("interval", 5)))
        while self.clock() < deadline:
            sleep(interval)
            r = self.http.post(f"{ID}/token", data={
                "client_id": self.client_id, "scopes": SCOPE_PARAM,
                "device_code": flow["device_code"], "grant_type": DEVICE_GRANT})
            body = r.json() if r.content else {}
            if r.status_code == 200:
                return self._accept(body)
            msg = str(body.get("message", ""))
            if "authorization_pending" in msg:
                continue
            if "slow_down" in msg:
                interval += 5; continue
            raise RuntimeError(f"device authorization failed: {msg or r.status_code}")
        raise TimeoutError("device authorization expired")

    def _accept(self, body):
        granted = set(body.get("scope") or [])
        missing = set(SCOPES) - granted
        if missing:
            raise RuntimeError(f"Twitch did not grant required scope(s): {sorted(missing)}")
        extra = granted - set(SCOPES)
        if extra:                  # we only ask for SCOPES, so anything more is unexpected: undo it
            self._revoke(body["access_token"])
            raise RuntimeError(f"Twitch granted unexpected permissions {sorted(extra)}; the login was revoked.")
        self._store_tokens(body)
        return self.validate()

    def _store_tokens(self, body):
        self.store.set("access_token", body["access_token"])
        self.store.set("refresh_token", body["refresh_token"])   # replaces the single-use old one
        self.store.set("expires_at", str(self.clock() + int(body.get("expires_in", 14000))))

    def validate(self, _retry=True):
        tok = self.store.get("access_token")
        if not tok:
            raise AuthRequired("not connected")
        r = self.http.get(f"{ID}/validate", headers={"Authorization": f"OAuth {tok}"})
        if r.status_code == 401 and _retry and self.store.get("refresh_token"):
            # access tokens expire after a few hours; refresh instead of asking to reconnect
            log.info("Saved access token expired; refreshing")
            self.refresh(force=True)
            return self.validate(_retry=False)
        if r.status_code != 200:
            raise AuthRequired(f"token invalid ({r.status_code})")
        info = r.json()
        if info.get("client_id") != self.client_id:
            raise AuthRequired("token belongs to a different client_id")
        if not set(SCOPES) <= set(info.get("scopes", [])):
            raise AuthRequired("token lacks channel:manage:predictions")
        self.user_id, self.login = info["user_id"], info.get("login")
        return info

    def refresh(self, force=False):
        with self._lock:   # one refresh at a time: refresh tokens are single use
            exp = float(self.store.get("expires_at") or 0)
            if not force and exp - self.clock() > 300:
                return
            rt = self.store.get("refresh_token")
            if not rt:
                raise AuthRequired("no refresh token")
            r = self.http.post(f"{ID}/token", data={"grant_type": "refresh_token",
                                                    "refresh_token": rt, "client_id": self.client_id})
            if r.status_code != 200:
                log.error("Token refresh failed: %s", r.status_code)
                if r.status_code in (400, 401):
                    self.disconnect_local()
                    raise AuthRequired("refresh token rejected/expired (30 days idle); reconnect Twitch")
                raise RuntimeError(f"refresh failed {r.status_code}")
            self._store_tokens(r.json())
            log.info("Twitch token refreshed")

    def get_access_token(self):
        if not self.store.get("access_token"):
            raise AuthRequired("not connected")
        self.refresh()      # does nothing unless the token is close to expiring
        return self.store.get("access_token")

    def is_connected(self):
        return bool(self.store.get("refresh_token"))

    def disconnect_local(self):
        """Delete the stored tokens. Returns the names of any that could not be deleted."""
        keys = ("access_token", "refresh_token", "expires_at")
        for k in keys:
            self.store.delete(k)
        return [k for k in keys if self.store.get(k)]

    def _revoke(self, token):
        try:
            self.http.post(f"{ID}/revoke", data={"client_id": self.client_id, "token": token})
        except Exception as e:
            log.warning("revoke failed: %s", type(e).__name__)

    def revoke_and_disconnect(self):
        tok = self.store.get("access_token")
        if tok:
            self._revoke(tok)
        return self.disconnect_local()
