# Auto Predictions Bot

A Windows app for streamers. It watches your game picture in OBS and runs Twitch Channel Points Predictions for you:
it opens one when a match starts, lets viewers vote for 60 seconds, keeps it locked while you play, and resolves it
from the win or loss screen. Screen detection happens on your own PC with lightweight OpenCV template matching.
Nothing is uploaded anywhere except the requests to Twitch.

> **Early version.** It starts in **Dry-Run mode**, which simulates Twitch and never touches your channel. Live
> predictions have not been tested on a real channel yet, so run your first live match with the manual buttons ready.

## Supported games

| Game | Resolution | Status |
|---|---|---|
| VALORANT | 1920x1080, English client | Built-in templates. Tested in Dry-Run on real matches |

More games are planned. For any other game or resolution, set **game** to **Custom** in Settings and capture your own
templates with **Calibrate**. Details per game are in [docs/games.md](docs/games.md).

## Setup (Windows)
1. Install Python 3.11+ from python.org (tick "Add to PATH").
2. **Twitch:** live predictions need Affiliate or Partner status. In your Creator Dashboard, check that
   Channel Points > Predictions is available, and run one manual prediction first.
3. **Register an app** at https://dev.twitch.tv/console: redirect URL `http://localhost`, category "Application
   Integration", **Client Type: Public**. Copy the Client ID. (If the app has no Client ID built in, Connect Twitch
   walks you through this.)
4. Copy `.env.example` to `.env` and set `TWITCH_CLIENT_ID`.
5. Double-click `run.bat`. The first run installs the dependencies.

## OBS setup
1. OBS 28 or newer: Tools > WebSocket Server Settings > Enable, port 4455, turn on authentication, set a password.
2. In the app's Setup Guide, enter the password, click **Load sources**, and pick your game capture source. A source
   captures only the game, without your webcam or overlays, which matches better than a whole scene.
3. OBS has to be running. If it isn't, the app shows "disconnected" and keeps retrying.

## First run
1. Open the **Setup Guide** (it opens by itself the first time): OBS password and source, Connect Twitch.
2. Leave **Dry-Run** on and click **Start Monitoring**. Queue a match and watch the state change, with a 60-second
   countdown while voting is open. Every simulated action is logged as `[DRY-RUN]`.
3. When it behaves, go to Settings, untick Dry-run and confirm the warning. Connect Twitch first.

## Calibrating (only if the built-in templates miss your screens)
1. With the game on screen in OBS, click **Calibrate**, drag a box over something that looks the same every time,
   choose the screen type, give it a name (letters, numbers, `-` and `_`), and save.
2. Screen types: `MATCH_FOUND`, `AGENT_SELECT` or `MATCH_START` (any of these opens a prediction), `VICTORY`,
   `DEFEAT`, `DRAW`. Capture a few examples of each if you can.
3. Put screenshots of normal play and menus in a folder and press **Test Detection**. Everything should say `no hit`
   except the real screens. If something hits that shouldn't, raise that template's `threshold` (default 0.80).

## Troubleshooting
| Symptom | Fix |
|---|---|
| OBS "disconnected" forever | Is OBS running, WebSocket on, port and password right? Firewall? |
| Black or blank screenshots | Wrong source name, or the capture isn't hooked. Try Window Capture |
| 400 on create | Another prediction is open, or you aren't Affiliate/Partner |
| "Not connected" / reconnect needed | Unused for 30+ days or revoked: click Connect Twitch again |
| Prediction not created | Check the score against the threshold. Three scans in a row must match |
| MANUAL_REVIEW | Pick WIN, LOSE or Cancel, or fix it on Twitch and press Re-check Twitch |
| Antivirus flags the exe | Unsigned apps built with PyInstaller often get flagged. See [docs/security.md](docs/security.md), or run from source with `run.bat` |
| OBS "Refusing to connect" | OBS has to run on this PC. For another machine set `obs_allow_remote` to `true` in `config.json` (it isn't encrypted) |
| Stuck after a crash | Start Monitoring (it recovers from Twitch), or press Re-check Twitch |

Logs are in the `logs` folder. Tokens and passwords are never written to them.

## Known limitations
- **Live Twitch is untested.** Creating, locking, resolving and cancelling on a real channel has only run against a
  simulated Twitch.
- **Detection is proven on a small sample.** Other languages, resolutions, UI scales and game modes are untested.
- **One game for now.** The timing settings are shared by every game and are tuned for VALORANT.
- **Unsigned exe.** Windows and some antivirus tools will warn about it.

## More documentation
- [How it works](docs/how-it-works.md): the prediction lifecycle, safety rules, login and recovery
- [Games](docs/games.md): per-game notes, and how to add a game
- [Security](docs/security.md): what the app touches, the hardening, and building a version you can vouch for
- [Development](docs/development.md): tests, benchmark, building the exe, sharing

## Legal
This is an independent fan project. It is not affiliated with or endorsed by Riot Games, Twitch or the OBS Project.
VALORANT is a trademark of Riot Games, Inc. Use it at your own risk and check each service's current rules.
<!-- TODO before making this repo public: paste the exact notice required by section 6 of https://www.riotgames.com/en/legal -->
