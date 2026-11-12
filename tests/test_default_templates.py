import os, tempfile, unittest
from pathlib import Path
import cv2
from app import config as C
from app.detector import Detector

# Optional regression check against your own full-size screenshots: put them in a folder, named as below, and run
#   set VPB_SAMPLES=C:\path\to\folder      (PowerShell: $env:VPB_SAMPLES="C:\path\to\folder")
# The folder is never part of the repo (screenshots may contain chat, names or other private data).
U = os.environ.get("VPB_SAMPLES", "")
U = (U.rstrip("/\\") + os.sep) if U else ""
FULL = {
    "match_found": ("match_found.png", "MATCH_FOUND"),
    "agent_select": ("agent_select.png", "AGENT_SELECT"),
    "victory": ("victory.png", "VICTORY"),
    "defeat": ("defeat.png", "DEFEAT"),
    "draw": ("draw.png", "DRAW"),
}

class DefaultTemplates(unittest.TestCase):
    def setUp(self):
        self.cfg = C.Config()          # built-ins come from the default game and resolution
        self.det = Detector(C.template_specs(self.cfg))

    def test_files_exist_and_paths_are_absolute(self):
        for s in C.template_specs(self.cfg):
            self.assertTrue(Path(s.path).is_absolute()); self.assertTrue(os.path.exists(s.path), s.path)

    def test_defaults_used_when_config_has_none(self):
        d = tempfile.mkdtemp(); p = Path(d) / "config.json"; p.write_text("{}")
        cfg = C.load_config(p)
        self.assertEqual(len(C.effective_templates(cfg)), 6); self.assertEqual(cfg.templates, [])
        self.assertEqual(C.load_config(p).scan_interval_s, 0.5)

    def test_masked_defaults_identify_themselves(self):
        for name, scr in (("match_found", "MATCH_FOUND"), ("agent_select", "AGENT_SELECT"),
                          ("victory", "VICTORY"), ("defeat", "DEFEAT"), ("draw", "DRAW")):
            m, _, _ = self.det.classify(cv2.imread(str(C.APP_DIR / "templates/defaults/valorant/1920x1080" / f"{name}.png")))
            self.assertIsNotNone(m, name); self.assertEqual(m.screen, scr)

    @unittest.skipUnless(U and os.path.exists(U + FULL["victory"][0]), "set VPB_SAMPLES to a folder of full screenshots to run this")
    def test_defaults_vs_full_screenshots(self):
        for name, (f, scr) in FULL.items():
            m, top, _ = self.det.classify(cv2.imread(U + f))
            self.assertIsNotNone(m, f"{name}: best {top.screen} {top.score:.3f}"); self.assertEqual(m.screen, scr, name)
            self.assertGreater(m.score, 0.90, name)

    def test_unrelated_frames_never_hit(self):
        import numpy as np
        rng = np.random.RandomState(0)
        for _ in range(5):
            m, _, _ = self.det.classify((rng.rand(360, 640, 3) * 255).astype("uint8")); self.assertIsNone(m)
        self.assertIsNone(self.det.classify(np.zeros((360, 640, 3), "uint8"))[0])


class GameTests(unittest.TestCase):
    MINE = {"path": "templates/mine.png", "screen": "VICTORY", "roi": [0, 0, 1, 1], "threshold": 0.8}

    def _cfg(self, text):
        import json
        d = tempfile.mkdtemp(); p = Path(d) / "config.json"; p.write_text(json.dumps(text)); return C.load_config(p)

    def test_default_game_has_all_screens(self):
        cfg = C.Config(); self.assertEqual((cfg.game, cfg.game_resolution), ("VALORANT", "1920x1080"))
        self.assertEqual({t["screen"] for t in C.effective_templates(cfg)},
                         {"MATCH_FOUND", "AGENT_SELECT", "VICTORY", "DEFEAT", "DRAW"})

    def test_choices(self):
        self.assertEqual(C.GAME_CHOICES, ("VALORANT", "Custom"))
        self.assertEqual(C.resolutions_for("VALORANT"), ("1920x1080",))
        self.assertEqual(C.resolutions_for("Custom"), ())

    def test_custom_game_keeps_user_templates_only(self):
        cfg = self._cfg({"game": "Custom", "templates": [self.MINE]})
        self.assertEqual(C.effective_templates(cfg), [self.MINE])
        self.assertEqual(C.effective_templates(self._cfg({"game": "Custom"})), [])

    def test_user_templates_are_added_to_builtins(self):
        self.assertEqual(len(C.effective_templates(self._cfg({"templates": [self.MINE]}))), 7)

    def test_old_config_with_custom_resolution_becomes_custom_game(self):
        cfg = self._cfg({"game_resolution": "Custom", "templates": [self.MINE]})
        self.assertEqual((cfg.game, cfg.game_resolution), ("Custom", "1920x1080"))
        self.assertEqual(C.effective_templates(cfg), [self.MINE])

    def test_old_builtin_copies_dropped(self):
        old = [{"path": "templates/defaults/victory.png", "screen": "VICTORY"},
               {"path": "templates/defaults/1920x1080/draw.png", "screen": "DRAW"},
               {"path": "templates/mine.png", "screen": "DEFEAT"}]
        cfg = self._cfg({"templates": old})
        self.assertEqual([t["path"] for t in cfg.templates], ["templates/mine.png"])
        self.assertEqual(len(C.effective_templates(cfg)), 7)

    def test_unknown_game_or_resolution_falls_back(self):
        self.assertEqual(self._cfg({"game": "Nope"}).game, "VALORANT")
        self.assertEqual(self._cfg({"game_resolution": "800x600"}).game_resolution, "1920x1080")

    def test_validate_rejects_unknown_values(self):
        for field, value in (("game", "nope"), ("game_resolution", "nope")):
            cfg = C.Config(); setattr(cfg, field, value)
            with self.assertRaises(ValueError): cfg.validate()

    def test_choice_persists_through_save_and_load(self):
        d = tempfile.mkdtemp(); p = Path(d) / "config.json"
        cfg = C.Config(); cfg.game = "Custom"; C.save_config(cfg, p)
        self.assertEqual(C.load_config(p).game, "Custom")

    def test_every_builtin_template_file_exists(self):
        for game, sets in C.GAMES.items():
            for resolution, entries in sets.items():
                for t in entries: self.assertTrue((C.APP_DIR / t["path"]).exists(), t["path"])


if __name__ == "__main__": unittest.main()
