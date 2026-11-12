import ast, io, json, logging, os, re, sys, tempfile, types, unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

from app import config as C
from app.auth import KeyringStore, TwitchAuth, SCOPES
from app.controller import Controller, ENV_KEYS, load_dotenv
from app.logging_config import RedactingFormatter, redact, setup_logging
from app.obs_client import ObsClient, ObsError, MAX_MESSAGE_BYTES, is_loopback
from tests.fakes import MemoryStore
from tests.test_twitch_client import FakeHttp, FakeWs, Resp

TOK = {"access_token": "a1", "refresh_token": "r1", "expires_in": 14000, "scope": SCOPES}


def render(message="", raise_error=None):
    """Run a message (or an exception) through a logger that uses the app's real formatter."""
    stream = io.StringIO(); handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("redaction-test"); logger.propagate = False; logger.handlers = [handler]
    if raise_error:
        try: raise raise_error
        except Exception: logger.exception("failed")
    else:
        logger.warning("%s", message)
    return stream.getvalue()


class RedactionTests(unittest.TestCase):
    SECRETS = {
        "key=value token": ("access_token=abcdef1234567890", ["abcdef1234567890"]),
        "json tokens": (json.dumps({"access_token": "abcdef1234567890", "refresh_token": "zyxwvu9876543210"}),
                        ["abcdef1234567890", "zyxwvu9876543210"]),
        "python dict": ("{'refresh_token': 'LEAKYTOKEN1234567890'}", ["LEAKYTOKEN1234567890"]),
        "bearer header": ("Authorization: Bearer abcdef1234567890", ["abcdef1234567890"]),
        "oauth header": ("Authorization: OAuth abcdef1234567890", ["abcdef1234567890"]),
        "device code": ("device_code=DEVCODE1234567890abcdef", ["DEVCODE1234567890abcdef"]),
        "client secret": ("client_secret: s3cr3tvalue99", ["s3cr3tvalue99"]),
        "password with symbols": ("password=P@ssw0rd!Secret#2026", ["P@ssw0rd!Secret#2026", "Secret#2026"]),
        "short password": ("password=abc1234", ["abc1234"]),
    }

    def test_secrets_never_reach_the_log(self):
        for name, (text, secrets) in self.SECRETS.items():
            out = render(text)
            for s in secrets: self.assertNotIn(s, out, name)

    def test_exception_tracebacks_are_redacted_too(self):
        out = render(raise_error=RuntimeError("device flow start failed: 400 {'refresh_token': 'LEAKYTOKEN1234567890'}"))
        self.assertIn("RuntimeError", out); self.assertNotIn("LEAKYTOKEN1234567890", out)

    def test_windows_user_name_is_scrubbed(self):
        for path in ("C:\\Users\\andre\\AppData\\Local\\x.py", "C:/Users/andre/Downloads/app/main.py"):
            out = render(f'File "{path}", line 3'); self.assertNotIn("andre", out); self.assertIn("<user>", out)

    def test_ordinary_messages_are_unchanged(self):
        for msg in ("Twitch token refreshed", "STATE IDLE -> CONFIRMING_START ", "Created prediction abc123 window=60",
                    "Saved access token expired; refreshing", "token invalid (401)"):
            self.assertEqual(redact(msg), msg)

    def test_setup_logging_uses_the_redacting_formatter(self):
        root = logging.getLogger(); saved = root.handlers[:]
        try:
            root.handlers = []
            setup_logging(Path(tempfile.mkdtemp()))
            self.assertTrue(root.handlers and all(isinstance(h.formatter, RedactingFormatter) for h in root.handlers))
        finally:
            for h in root.handlers: h.close()
            root.handlers = saved


class DotenvTests(unittest.TestCase):
    def _load(self, text, **env):
        path = Path(tempfile.mkdtemp()) / ".env"; path.write_text(text, encoding="utf-8")
        with mock.patch.dict(os.environ, env):
            for k in ("TWITCH_CLIENT_ID", "OBS_PASSWORD", "BROWSER", "PYTHONPATH"):
                if k not in env: os.environ.pop(k, None)
            before_path = os.environ.get("PATH")
            with self.assertLogs("app.controller", "WARNING") as logs:
                logging.getLogger("app.controller").warning("start")      # keeps assertLogs quiet when nothing is ignored
                load_dotenv(path)
            return dict(os.environ), before_path, "\n".join(logs.output)

    def test_only_the_two_allowed_variables_are_read(self):
        env, path_before, logs = self._load(
            "# comment\nTWITCH_CLIENT_ID=abc\nOBS_PASSWORD=pw\nBROWSER=touch /tmp/x %s\nPYTHONPATH=/tmp/evil\nPATH=C:\\evil\nnonsense line\n")
        self.assertEqual(ENV_KEYS, ("TWITCH_CLIENT_ID", "OBS_PASSWORD"))
        self.assertEqual((env["TWITCH_CLIENT_ID"], env["OBS_PASSWORD"]), ("abc", "pw"))
        self.assertNotIn("BROWSER", env); self.assertNotIn("PYTHONPATH", env)
        self.assertEqual(env.get("PATH"), path_before)
        self.assertIn("BROWSER", logs); self.assertNotIn("touch", logs)         # the key is named, the value never logged

    def test_real_environment_wins_over_the_file(self):
        env, _, _ = self._load("TWITCH_CLIENT_ID=from-file\n", TWITCH_CLIENT_ID="from-environment")
        self.assertEqual(env["TWITCH_CLIENT_ID"], "from-environment")

    def test_missing_file_is_fine(self):
        load_dotenv(Path(tempfile.mkdtemp()) / "nope.env")


class ObsHardeningTests(unittest.TestCase):
    HELLO = [{"op": 0, "d": {}}, {"op": 2, "d": {}}]

    def _fake_websocket(self):
        mod = types.ModuleType("websocket"); mod.calls = []
        def create_connection(url, timeout=None):
            mod.calls.append(url); return FakeWs(list(self.HELLO))
        mod.create_connection = create_connection
        return mod

    def test_loopback_detection(self):
        for ok in ("localhost", "LOCALHOST", "127.0.0.1", "127.5.5.5", "::1", "[::1]"):
            self.assertTrue(is_loopback(ok), ok)
        for bad in ("192.168.1.5", "8.8.8.8", "evil.example.com", "localhost.evil.com", "127.0.0.1.evil.com", "", None):
            self.assertFalse(is_loopback(bad), bad)

    def test_remote_host_is_refused_before_any_connection(self):
        fake = self._fake_websocket()
        with mock.patch.dict(sys.modules, {"websocket": fake}):
            o = ObsClient("203.0.113.9", 4455, "pw", "src")
            with self.assertRaises(ObsError) as cm: o.connect()
        self.assertIn("Refusing", str(cm.exception)); self.assertEqual(fake.calls, []); self.assertFalse(o.connected)

    def test_local_host_connects_without_any_flag(self):
        fake = self._fake_websocket()
        with mock.patch.dict(sys.modules, {"websocket": fake}):
            o = ObsClient("localhost", 4455, "", "src"); o.connect()
        self.assertTrue(o.connected); self.assertEqual(fake.calls, ["ws://localhost:4455"])

    def test_remote_host_needs_the_explicit_flag(self):
        fake = self._fake_websocket()
        with mock.patch.dict(sys.modules, {"websocket": fake}):
            o = ObsClient("203.0.113.9", 4455, "", "src", allow_remote=True); o.connect()
        self.assertTrue(o.connected)

    def test_oversized_message_is_rejected(self):
        class BigWs(FakeWs):
            def recv(self): return "x" * (MAX_MESSAGE_BYTES + 1)
        o = ObsClient("h", 1, "", "src", ws_factory=lambda: BigWs([]))
        with self.assertRaises(ObsError) as cm: o.connect()
        self.assertIn("oversized", str(cm.exception)); self.assertFalse(o.connected)

    def test_flag_is_passed_from_the_settings(self):
        ctl = Controller(); self.assertFalse(ctl._obs().allow_remote)
        ctl.cfg.obs_allow_remote = True; self.assertTrue(ctl._obs().allow_remote)

    def test_port_must_be_a_real_port(self):
        for bad in (0, -1, 65536, "4455", None):
            cfg = C.Config(); cfg.obs_port = bad
            with self.assertRaises(ValueError, msg=repr(bad)): cfg.validate()
        cfg = C.Config(); cfg.obs_port = 4455; cfg.validate()


class ConfigHardeningTests(unittest.TestCase):
    def test_templates_outside_the_app_folder_are_ignored(self):
        outside = str(Path(tempfile.gettempdir()) / "other.png")
        bad = [{"path": outside, "screen": "VICTORY"}, {"path": "../outside.png", "screen": "VICTORY"},
               {"path": "templates/../../outside.png", "screen": "DEFEAT"}]
        good = {"path": "templates/mine.png", "screen": "VICTORY", "roi": [0, 0, 1, 1], "threshold": 0.8}
        cfg = C.Config(); cfg.templates = bad + [good]
        with self.assertLogs("app.config", "WARNING") as logs:
            specs = C.template_specs(cfg)
        self.assertEqual(len(logs.records), 3)
        paths = [Path(s.path).name for s in specs]
        self.assertIn("mine.png", paths); self.assertNotIn("other.png", paths); self.assertNotIn("outside.png", paths)
        self.assertEqual(len(specs), len(C.GAMES["VALORANT"]["1920x1080"]) + 1)

    def test_save_is_atomic(self):
        path = Path(tempfile.mkdtemp()) / "config.json"
        a = C.Config(); a.scan_interval_s = 0.5; C.save_config(a, path)
        self.assertFalse(path.with_suffix(".tmp").exists()); self.assertEqual(C.load_config(path).scan_interval_s, 0.5)
        b = C.Config(); b.scan_interval_s = 2.0
        with mock.patch("app.config.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError): C.save_config(b, path)
        self.assertEqual(C.load_config(path).scan_interval_s, 0.5)          # the old file is untouched


class FakeKeyring:
    def __init__(self, delete_fails, stored): self.delete_fails, self.stored = delete_fails, stored
    def delete_password(self, service, key):
        if self.delete_fails: raise RuntimeError("boom")
    def get_password(self, service, key): return self.stored


class StubbornStore(MemoryStore):
    def delete(self, k):
        if k != "refresh_token": super().delete(k)


class AuthHardeningTests(unittest.TestCase):
    def _store(self, delete_fails, stored):
        s = KeyringStore.__new__(KeyringStore); s.kr, s.service = FakeKeyring(delete_fails, stored), "svc"; return s

    def test_failed_deletion_is_logged_without_the_secret(self):
        with self.assertLogs("app.auth", "WARNING") as logs:
            self._store(True, "SECRETVALUE123").delete("access_token")
        text = "\n".join(logs.output)
        self.assertIn("Could not delete stored access_token", text); self.assertNotIn("SECRETVALUE123", text)

    def test_deleting_something_already_gone_is_not_a_warning(self):
        with self.assertNoLogs("app.auth", "WARNING"):
            self._store(True, None).delete("access_token")
            self._store(False, "x").delete("access_token")

    def test_leftover_tokens_are_reported(self):
        store = StubbornStore(); [store.set(k, "v") for k in ("access_token", "refresh_token", "expires_at")]
        auth = TwitchAuth("cid", store, http=FakeHttp([Resp(200)]))
        self.assertEqual(auth.revoke_and_disconnect(), ["refresh_token"])
        clean = MemoryStore(); clean.set("access_token", "v")
        self.assertEqual(TwitchAuth("cid", clean, http=FakeHttp([Resp(200)])).revoke_and_disconnect(), [])

    def test_disconnect_status_tells_the_truth(self):
        statuses = []
        ctl = Controller(on_status=lambda **k: statuses.append(k))
        store = StubbornStore(); store.set("refresh_token", "v")
        ctl.auth = TwitchAuth("cid", store, http=FakeHttp([]))
        ctl.disconnect_twitch()
        self.assertIn("incomplete", statuses[-1]["twitch"])
        ctl.auth = TwitchAuth("cid", MemoryStore(), http=FakeHttp([]))
        ctl.disconnect_twitch(); self.assertEqual(statuses[-1]["twitch"], "disconnected")

    def test_unexpected_permissions_are_rejected_and_revoked(self):
        http = FakeHttp([Resp(200, {**TOK, "scope": SCOPES + ["chat:edit"]}), Resp(200)])
        store = MemoryStore(); auth = TwitchAuth("cid", store, http=http)
        with self.assertRaises(RuntimeError) as cm:
            auth.poll_device_flow({"device_code": "d", "expires_in": 99, "interval": 5}, sleep=lambda s: None)
        self.assertIn("unexpected permissions", str(cm.exception))
        method, url, kw = http.calls[1]
        self.assertTrue(url.endswith("/revoke")); self.assertEqual(kw["data"]["token"], "a1")
        self.assertIsNone(store.get("access_token")); self.assertIsNone(store.get("refresh_token"))


class NoDangerousCodeTests(unittest.TestCase):
    """Keeps the audit's findings true: nothing here may start programs, touch the registry or add network hosts."""
    FILES = sorted(Path(C.APP_DIR, "app").glob("*.py"))
    BAD_IMPORTS = {"subprocess", "winreg", "_winreg", "ctypes", "cffi", "pickle", "marshal", "socket", "socketserver",
                   "http", "ftplib", "smtplib", "telnetlib", "pyautogui", "pynput", "keyboard", "mss", "requests", "urllib3"}
    BAD_CALLS = {"eval", "exec", "compile", "__import__"}
    BAD_ATTR_CALLS = {("os", "system"), ("os", "popen"), ("os", "spawnl"), ("os", "execv"), ("os", "remove"),
                      ("os", "unlink"), ("shutil", "rmtree")}
    ALLOWED_HOSTS = {"id.twitch.tv", "api.twitch.tv", "dev.twitch.tv", "www.twitch.tv", "localhost"}

    def test_found_the_app_files(self):
        self.assertGreater(len(self.FILES), 10)

    def test_no_dangerous_imports_or_calls(self):
        for f in self.FILES:
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    for a in node.names: self.assertNotIn(a.name.split(".")[0], self.BAD_IMPORTS, f"{f.name}: import {a.name}")
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    self.assertNotIn(node.module.split(".")[0], self.BAD_IMPORTS, f"{f.name}: from {node.module}")
                elif isinstance(node, ast.Call):
                    fn = node.func
                    if isinstance(fn, ast.Name): self.assertNotIn(fn.id, self.BAD_CALLS, f"{f.name}:{node.lineno}")
                    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
                        self.assertNotIn((fn.value.id, fn.attr), self.BAD_ATTR_CALLS, f"{f.name}:{node.lineno}")
                    for kw in node.keywords:
                        self.assertFalse(kw.arg == "shell" and getattr(kw.value, "value", None) is True, f"{f.name}:{node.lineno}")
                        self.assertFalse(kw.arg == "verify" and getattr(kw.value, "value", None) is False, f"{f.name}:{node.lineno}")

    # the OBS address comes from the settings; obs_client.is_loopback() blocks non-local hosts unless allowed
    DYNAMIC_OK = {("obs_client.py", "ws://{self.host}:{self.port}")}

    def test_only_known_hosts_appear(self):
        for f in self.FILES:
            for url in re.findall(r"(?:https?|wss?)://[^\s\"'`)>\\]+", f.read_text(encoding="utf-8")):
                if (f.name, url) in self.DYNAMIC_OK:
                    continue
                host = urlparse(url).hostname
                self.assertIn(host, self.ALLOWED_HOSTS, f"{f.name}: {url}")

    def test_http_clients_keep_certificate_checking_on(self):
        text = Path(C.APP_DIR, "app", "lazy_http.py").read_text(encoding="utf-8")
        self.assertIn("httpx.Client(timeout=self.timeout)", text); self.assertNotIn("verify", text)


class BuildHygieneTests(unittest.TestCase):
    def _read(self, name): return Path(C.APP_DIR, name).read_text(encoding="utf-8")

    def test_runtime_requirements_do_not_include_the_build_tool(self):
        self.assertNotIn("pyinstaller", self._read("requirements.txt").lower())
        build = self._read("requirements-build.txt").lower()
        self.assertIn("pyinstaller", build); self.assertIn("-r requirements.txt", build)

    def test_build_script_uses_the_safer_options(self):
        text = self._read("build.bat")
        for needle in ("--noupx", "--version-file version_info.txt", "--require-hashes", "requirements-build.lock",
                       "--hidden-import keyring.backends.Windows", "VPB_SIGN_CERT", "SHA256"):
            self.assertIn(needle, text, needle)
        self.assertNotIn("--upgrade pip", text)

    def test_run_script_does_not_install_the_build_tool(self):
        text = self._read("run.bat"); self.assertIn("requirements.txt", text); self.assertNotIn("requirements-build", text)

    def test_version_resource_is_valid(self):
        stub = {n: (lambda *a, _n=n, **k: (_n, a, k)) for n in
                ("VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable", "StringStruct", "VarFileInfo", "VarStruct")}
        info = eval(self._read("version_info.txt"), stub)
        flat = json.dumps(info, default=str)
        for needle in ("FileDescription", "Auto Predictions Bot", "OriginalFilename", "VPB.exe", "FileVersion"):
            self.assertIn(needle, flat)

    def test_certificates_and_keys_cannot_be_committed(self):
        text = self._read(".gitignore")
        for pattern in ("*.pfx", "*.p12", "*.pem", "*.key"): self.assertIn(pattern, text)


if __name__ == "__main__": unittest.main()
