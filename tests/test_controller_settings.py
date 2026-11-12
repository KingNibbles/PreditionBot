import json, tempfile, unittest
from pathlib import Path
from app import config as C
from app.controller import Controller

class ObsSettingsPersist(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.old = C.CONFIG_PATH
        C.CONFIG_PATH = self.tmp / "config.json"
    def tearDown(self): C.CONFIG_PATH = self.old

    def test_source_choice_survives_restart(self):
        ctl = Controller(); self.assertTrue(ctl.save_obs_source("  Game Capture "))
        self.assertEqual(json.loads(C.CONFIG_PATH.read_text())["obs_source"], "Game Capture")
        self.assertEqual(C.load_config(C.CONFIG_PATH).obs_source, "Game Capture")   # what the next launch reads

    def test_unchanged_choice_does_not_rewrite(self):
        ctl = Controller(); ctl.save_obs_source("Game Capture"); C.CONFIG_PATH.unlink()
        self.assertFalse(ctl.save_obs_source("Game Capture")); self.assertFalse(C.CONFIG_PATH.exists())

    def test_password_not_in_config(self):
        ctl = Controller(); saved = []
        ctl.set_obs_password = lambda pw: saved.append(pw); ctl.obs_password = lambda: ""
        ctl.save_obs_password("s3cret-pass"); ctl.save_obs_source("Game Capture")
        self.assertEqual(saved, ["s3cret-pass"]); self.assertNotIn("s3cret", C.CONFIG_PATH.read_text())

    def test_setup_wizard_not_needed_after_source_saved(self):
        ctl = Controller(); self.assertTrue(ctl.needs_setup())
        ctl.save_obs_source("Game Capture"); self.assertFalse(ctl.needs_setup())


class ClientIdHandling(unittest.TestCase):
    def setUp(self):
        import os
        self.tmp = Path(tempfile.mkdtemp()); self.old = (C.CONFIG_PATH, C.BUNDLED_CLIENT_ID)
        C.CONFIG_PATH = self.tmp / "config.json"; os.environ.pop("TWITCH_CLIENT_ID", None)
    def tearDown(self): C.CONFIG_PATH, C.BUNDLED_CLIENT_ID = self.old

    def test_bundled_id_is_not_frozen_into_config(self):
        C.BUNDLED_CLIENT_ID = "bundled-id"
        cfg = C.load_config(C.CONFIG_PATH); C.save_config(cfg, C.CONFIG_PATH)
        self.assertNotIn("twitch_client_id", json.loads(C.CONFIG_PATH.read_text()))
        C.BUNDLED_CLIENT_ID = "new-bundled-id"                       # a newer build
        self.assertEqual(C.load_config(C.CONFIG_PATH).twitch_client_id, "new-bundled-id")

    def test_user_typed_id_is_kept(self):
        C.BUNDLED_CLIENT_ID = "bundled-id"
        cfg = C.load_config(C.CONFIG_PATH); cfg.twitch_client_id = "my-own-id"; C.save_config(cfg, C.CONFIG_PATH)
        self.assertEqual(C.load_config(C.CONFIG_PATH).twitch_client_id, "my-own-id")

    def test_other_client_token_cleared(self):
        from app.auth import TwitchAuth, SCOPES
        from tests.fakes import MemoryStore
        from tests.test_twitch_client import FakeHttp, Resp
        store = MemoryStore(); [store.set(k, "x") for k in ("access_token", "refresh_token")]
        http = FakeHttp([Resp(200, {"client_id": "someone-else", "user_id": "1", "scopes": SCOPES})])
        ctl = Controller(); ctl.auth = TwitchAuth("my-client", store, http=http)
        msg = ctl.twitch_status()
        self.assertIn("Connect Twitch", msg); self.assertFalse(ctl.auth.is_connected())


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.old = (C.CONFIG_PATH, C.TEMPLATE_DIR)
        C.CONFIG_PATH = self.tmp / "config.json"; C.TEMPLATE_DIR = self.tmp / "templates"
    def tearDown(self): C.CONFIG_PATH, C.TEMPLATE_DIR = self.old

    def test_template_name_stays_in_folder(self):
        import numpy as np
        ctl = Controller(); frame = np.zeros((360, 640, 3), "uint8")
        for bad in ("../evil", "..\\evil", "a/b", "name.png", "", "x" * 41, "C:\\boot", "con ole"):
            with self.assertRaises(ValueError, msg=bad): ctl.save_template(frame, bad, "VICTORY", (0, 0, 1, 1), 0.8)
        self.assertFalse(any(self.tmp.rglob("*evil*")))

    def test_good_template_name_saved(self):
        import numpy as np
        ctl = Controller(); ctl.save_template(np.zeros((360, 640, 3), "uint8"), "my_victory-2", "VICTORY", (0, 0, 1, 1), 0.8)
        self.assertTrue((C.TEMPLATE_DIR / "my_victory-2.png").exists())
        self.assertTrue(C.CONFIG_PATH.exists())           # saved to the redirected config, not the project's

    def test_only_real_twitch_pages_are_opened(self):
        from app.auth import is_twitch_url
        for ok in ("https://www.twitch.tv/activate?device-code=ABC", "https://id.twitch.tv/x", "https://twitch.tv/"):
            self.assertTrue(is_twitch_url(ok), ok)
        for bad in ("http://www.twitch.tv/activate", "https://twitch.tv.evil.com/", "https://evil.com/twitch.tv",
                    "https://eviltwitch.tv/", "file:///C:/Windows/System32/calc.exe", "javascript:alert(1)", "", None,
                    "https://user@evil.com/@twitch.tv"):
            self.assertFalse(is_twitch_url(bad), bad)

if __name__ == "__main__": unittest.main()
