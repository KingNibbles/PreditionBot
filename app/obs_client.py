"""Small OBS WebSocket v5 client that grabs source screenshots."""
from __future__ import annotations
import base64, hashlib, ipaddress, json, logging, uuid
import numpy as np
import cv2

log = logging.getLogger(__name__)


MAX_MESSAGE_BYTES = 4_000_000     # a 640x360 JPEG as base64 is far smaller than this


class ObsError(Exception): pass


def is_loopback(host):
    h = (host or "").strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _recv_json(ws):
    raw = ws.recv()
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ObsError("OBS sent an oversized message")
    return json.loads(raw)


def make_auth(password, salt, challenge):
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class Backoff:
    """Bounded exponential backoff: 1,2,4,... capped at `maximum`."""
    def __init__(self, maximum=30.0): self.maximum, self.n = maximum, 0
    def next(self):
        d = min(self.maximum, 2 ** self.n); self.n += 1; return d
    def reset(self): self.n = 0


class ObsClient:
    def __init__(self, host, port, password, source, width=640, height=360, ws_factory=None, timeout=5.0, allow_remote=False):
        self.host, self.port, self.password, self.source = host, port, password, source
        self.width, self.height, self.timeout = width, height, timeout
        self.ws = None
        self._factory = ws_factory
        self.allow_remote = allow_remote

    @property
    def connected(self): return self.ws is not None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def connect(self):
        try:
            if self._factory: ws = self._factory()
            else:
                if not self.allow_remote and not is_loopback(self.host):
                    raise ObsError(f"Refusing to connect to {self.host!r}: OBS must run on this PC. "
                                   "Set obs_allow_remote to true in config.json to allow another machine.")
                import websocket
                ws = websocket.create_connection(f"ws://{self.host}:{self.port}", timeout=self.timeout)
            hello = _recv_json(ws)
            ident = {"op": 1, "d": {"rpcVersion": 1}}
            auth = hello["d"].get("authentication")
            if auth:
                if not self.password: raise ObsError("OBS requires a password")
                ident["d"]["authentication"] = make_auth(self.password, auth["salt"], auth["challenge"])
            ws.send(json.dumps(ident))
            reply = _recv_json(ws)
            if reply.get("op") != 2: raise ObsError(f"identify failed: {reply}")
            self.ws = ws
            log.info("OBS connected")
        except ObsError:
            self.close(); raise
        except Exception as e:
            self.close(); raise ObsError(f"connect failed: {e}") from e

    def close(self):
        try:
            if self.ws: self.ws.close()
        except Exception: pass
        self.ws = None

    def _request(self, rtype, data):
        if not self.ws: raise ObsError("not connected")
        rid = uuid.uuid4().hex
        try:
            self.ws.send(json.dumps({"op": 6, "d": {"requestType": rtype, "requestId": rid, "requestData": data}}))
            while True:
                msg = _recv_json(self.ws)
                if msg.get("op") == 7 and msg["d"]["requestId"] == rid:
                    return msg["d"]
        except Exception as e:
            self.close(); raise ObsError(f"request failed: {e}") from e

    def screenshot(self):
        """Return a BGR numpy frame at the configured size (JPEG keeps the transfer small)."""
        d = self._request("GetSourceScreenshot", {
            "sourceName": self.source, "imageFormat": "jpg", "imageWidth": self.width,
            "imageHeight": self.height, "imageCompressionQuality": 70})
        if not d["requestStatus"]["result"]:
            raise ObsError(f"OBS: {d['requestStatus'].get('comment')}")
        b64 = d["responseData"]["imageData"].split(",", 1)[-1]
        frame = cv2.imdecode(np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_COLOR)
        if frame is None: raise ObsError("bad image data")
        return frame

    def list_sources(self):
        """Scene names plus input (source) names, for the setup dropdown."""
        names = [x["sceneName"] for x in self._request("GetSceneList", {})["responseData"]["scenes"]]
        inputs = self._request("GetInputList", {})["responseData"]["inputs"]
        return names + [i["inputName"] for i in inputs if i["inputName"] not in names]
