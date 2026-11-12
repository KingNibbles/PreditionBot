import unittest, time, json
from app.twitch_client import (TwitchClient, RateLimited, TwitchTimeout, BadRequest, TwitchError, parse_ts)
from app.auth import TwitchAuth, AuthRequired, SCOPES, DEVICE_GRANT
from tests.fakes import MemoryStore
from app.obs_client import make_auth, Backoff, ObsClient, ObsError

class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._b, self.headers = status, body if body is not None else {}, headers or {}
        self.text = json.dumps(self._b); self.content = self.text.encode()
    def json(self): return self._b

class FakeHttp:
    def __init__(self, responses): self.responses, self.calls = list(responses), []
    def request(self, method, url, **kw):
        self.calls.append((method, url, kw)); r = self.responses.pop(0)
        if isinstance(r, Exception): raise r
        return r
    def post(self, url, **kw): return self.request("POST", url, **kw)
    def get(self, url, **kw): return self.request("GET", url, **kw)

class FakeAuth:
    def __init__(self): self.refreshed = 0
    def get_access_token(self): return "tok"
    def refresh(self, force=False): self.refreshed += 1

PRED = {"data": [{"id": "p1", "status": "ACTIVE", "created_at": "2026-01-01T12:00:00.123456789Z", "prediction_window": 60}]}

class TwitchClientTests(unittest.TestCase):
    def client(self, *resp):
        self.http = FakeHttp(resp); self.auth = FakeAuth()
        return TwitchClient("cid", "123", self.auth, http=self.http)

    def test_create_payload(self):
        c = self.client(Resp(200, PRED)); c.create_prediction("T", ["WIN", "LOSE"], 60)
        m, url, kw = self.http.calls[0]
        self.assertEqual((m, url), ("POST", "https://api.twitch.tv/helix/predictions"))
        self.assertEqual(kw["json"], {"broadcaster_id": "123", "title": "T",
                         "outcomes": [{"title": "WIN"}, {"title": "LOSE"}], "prediction_window": 60})
        self.assertEqual(kw["headers"]["Client-Id"], "cid")

    def test_resolve_payload_and_requires_winner(self):
        c = self.client(Resp(200, PRED))
        with self.assertRaises(ValueError): c.end_prediction("p1", "RESOLVED")
        c.end_prediction("p1", "RESOLVED", "o1")
        self.assertEqual(self.http.calls[0][2]["json"]["winning_outcome_id"], "o1")
        self.assertEqual(self.http.calls[0][0], "PATCH")

    def test_401_refreshes_once_then_succeeds(self):
        c = self.client(Resp(401), Resp(200, PRED)); c.get_prediction("p1")
        self.assertEqual(self.auth.refreshed, 1)

    def test_401_twice_raises(self):
        c = self.client(Resp(401), Resp(401))
        with self.assertRaises(TwitchError): c.get_prediction("p1")

    def test_429_rate_limit(self):
        c = self.client(Resp(429, {}, {"Ratelimit-Reset": str(time.time() + 7)}))
        with self.assertRaises(RateLimited) as cm: c.get_prediction("p1")
        self.assertGreater(cm.exception.retry_after, 4)

    def test_timeout_is_distinct(self):
        class ReadTimeout(Exception): pass
        c = self.client(ReadTimeout("x"))
        with self.assertRaises(TwitchTimeout): c.create_prediction("T", ["a", "b"], 60)

    def test_400_and_500(self):
        c = self.client(Resp(400, {"message": "active prediction"}), Resp(503))
        with self.assertRaises(BadRequest): c.create_prediction("T", ["a", "b"], 60)
        with self.assertRaises(TwitchError): c.get_prediction("p")

    def test_parse_nanosecond_timestamp(self):
        d = parse_ts("2022-06-27T16:22:15.478726636Z")
        self.assertEqual((d.year, d.second, d.microsecond), (2022, 15, 478726))

class AuthTests(unittest.TestCase):
    def mk(self, *resp, now=None):
        self.http = FakeHttp(resp); self.store = MemoryStore()
        self.t = [1000.0]
        return TwitchAuth("cid", self.store, http=self.http, clock=lambda: self.t[0])
    VALID = Resp(200, {"client_id": "cid", "user_id": "42", "login": "me", "scopes": SCOPES})
    TOK = {"access_token": "a1", "refresh_token": "r1", "expires_in": 14000, "scope": SCOPES}

    def test_device_flow_pending_then_success(self):
        a = self.mk(Resp(400, {"status": 400, "message": "authorization_pending"}), Resp(200, self.TOK), self.VALID)
        info = a.poll_device_flow({"device_code": "dc", "expires_in": 1800, "interval": 5}, sleep=lambda s: None)
        self.assertEqual(a.user_id, "42"); self.assertEqual(self.store.get("refresh_token"), "r1")
        body = self.http.calls[1][2]["data"]
        self.assertEqual(body["grant_type"], DEVICE_GRANT); self.assertNotIn("client_secret", body)

    def test_missing_scope_rejected(self):
        a = self.mk(Resp(200, {**self.TOK, "scope": []}))
        with self.assertRaises(RuntimeError): a.poll_device_flow({"device_code": "d", "expires_in": 99, "interval": 5}, sleep=lambda s: None)
        self.assertIsNone(self.store.get("access_token"))

    def test_validate_rejects_foreign_client(self):
        a = self.mk(Resp(200, {"client_id": "other", "user_id": "1", "scopes": SCOPES}))
        self.store.set("access_token", "x")
        with self.assertRaises(AuthRequired): a.validate()

    def test_expiry_triggers_one_refresh(self):
        a = self.mk(Resp(200, {**self.TOK, "access_token": "a2", "refresh_token": "r2"}))
        self.store.set("access_token", "a1"); self.store.set("refresh_token", "r1"); self.store.set("expires_at", "1100")
        self.assertEqual(a.get_access_token(), "a2")
        self.assertEqual(self.store.get("refresh_token"), "r2")
        self.assertNotIn("client_secret", self.http.calls[0][2]["data"])
        self.assertEqual(len(self.http.calls), 1)

    def test_rejected_refresh_disconnects(self):
        a = self.mk(Resp(400, {"message": "Invalid refresh token"}))
        self.store.set("access_token", "a1"); self.store.set("refresh_token", "r1"); self.store.set("expires_at", "0")
        with self.assertRaises(AuthRequired): a.get_access_token()
        self.assertFalse(a.is_connected())

    def test_expired_token_refreshes_on_startup(self):
        new = {**self.TOK, "access_token": "a2", "refresh_token": "r2"}
        a = self.mk(Resp(401, {"message": "invalid access token"}), Resp(200, new), self.VALID)
        self.store.set("access_token", "old"); self.store.set("refresh_token", "r1"); self.store.set("expires_at", "0")
        info = a.validate()
        self.assertEqual(info["user_id"], "42"); self.assertEqual(self.store.get("access_token"), "a2")
        self.assertEqual(self.store.get("refresh_token"), "r2")
        self.assertEqual([c[0] for c in self.http.calls], ["GET", "POST", "GET"])   # validate, refresh, validate again

    def test_401_without_refresh_token(self):
        a = self.mk(Resp(401)); self.store.set("access_token", "old")
        with self.assertRaises(AuthRequired): a.validate()

    def test_rejected_refresh_clears_tokens(self):
        a = self.mk(Resp(401), Resp(400, {"message": "Invalid refresh token"}))
        self.store.set("access_token", "old"); self.store.set("refresh_token", "r1")
        with self.assertRaises(AuthRequired): a.validate()
        self.assertFalse(a.is_connected())

    def test_only_one_refresh_attempt_per_validate(self):
        a = self.mk(Resp(401), Resp(200, self.TOK), Resp(401))      # still 401 after refreshing
        self.store.set("access_token", "old"); self.store.set("refresh_token", "r1")
        with self.assertRaises(AuthRequired): a.validate()
        self.assertEqual(len(self.http.calls), 3)

    def test_revoke_clears_tokens(self):
        a = self.mk(Resp(200)); self.store.set("access_token", "a"); self.store.set("refresh_token", "r")
        a.revoke_and_disconnect(); self.assertFalse(a.is_connected())
        self.assertIn("/revoke", self.http.calls[0][1])

class FakeWs:
    def __init__(self, msgs): self.msgs, self.sent, self.closed = list(msgs), [], False
    def recv(self):
        if not self.msgs: raise ConnectionError("closed")
        return json.dumps(self.msgs.pop(0))
    def send(self, s): self.sent.append(json.loads(s))
    def close(self): self.closed = True

class ObsTests(unittest.TestCase):
    def test_auth_string_matches_spec_algorithm(self):
        import base64, hashlib
        s = base64.b64encode(hashlib.sha256(b"pwsalt").digest()).decode()
        self.assertEqual(make_auth("pw", "salt", "chal"), base64.b64encode(hashlib.sha256((s + "chal").encode()).digest()).decode())
    def test_backoff_bounded(self):
        b = Backoff(10); seq = [b.next() for _ in range(6)]
        self.assertEqual(seq, [1, 2, 4, 8, 10, 10]); b.reset(); self.assertEqual(b.next(), 1)
    def test_connect_identify_and_no_crash_on_failure(self):
        ws = FakeWs([{"op": 0, "d": {"authentication": {"salt": "s", "challenge": "c"}}}, {"op": 2, "d": {}}])
        o = ObsClient("h", 1, "pw", "src", ws_factory=lambda: ws); o.connect()
        self.assertTrue(o.connected); self.assertIn("authentication", ws.sent[0]["d"])
        o2 = ObsClient("h", 1, "pw", "src", ws_factory=lambda: (_ for _ in ()).throw(OSError("refused")))
        with self.assertRaises(ObsError): o2.connect()
        self.assertFalse(o2.connected)
    def test_request_failure_marks_disconnected(self):
        ws = FakeWs([{"op": 0, "d": {}}, {"op": 2, "d": {}}])
        o = ObsClient("h", 1, "", "src", ws_factory=lambda: ws); o.connect()
        with self.assertRaises(ObsError): o.screenshot()
        self.assertFalse(o.connected)

if __name__ == "__main__": unittest.main()

class ObsListTests(unittest.TestCase):
    def test_list_sources_scenes_and_inputs(self):
        import re
        class WS(FakeWs):
            def send(self, s):
                m = json.loads(s); super().send(s)
                if m["op"] != 6: return
                rt, rid = m["d"]["requestType"], m["d"]["requestId"]
                data = {"GetSceneList": {"scenes": [{"sceneName": "Game"}]},
                        "GetInputList": {"inputs": [{"inputName": "Game Capture"}, {"inputName": "Game"}]}}[rt]
                self.msgs.append({"op": 7, "d": {"requestId": rid, "requestStatus": {"result": True}, "responseData": data}})
        ws = WS([{"op": 0, "d": {}}, {"op": 2, "d": {}}])
        o = ObsClient("h", 1, "", "", ws_factory=lambda: ws); o.connect()
        self.assertEqual(o.list_sources(), ["Game", "Game Capture"])
