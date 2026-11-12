# Development

## Layout
`app/` the program (see [how it works](how-it-works.md)), `scripts/` small tools, `tests/` the test suite, `templates/` reference images, `assets/` the icon,
`docs/` these notes, `vpb.py` the launcher the exe is built from.

## Tests and measurements
```
python -m unittest discover -s tests -t .     # 133 tests, no network, OBS or Twitch needed (1 optional test skipped)
python -m scripts.benchmark --seconds 30      # per-scan ms, CPU %, RSS
```
The skipped test checks the built-in templates against your own full-size screenshots. To run it, put them in a folder
named `match_found.png`, `agent_select.png`, `victory.png`, `defeat.png`, `draw.png` and set `VPB_SAMPLES` to that folder.
Keep that folder out of the repo, because screenshots can contain chat and names.

In a Linux sandbox (640x360, 7 templates, scanning only what the current state needs): mean 6.2 ms per scan, p95 7.3 ms,
about 0.6% of one core at 1 scan per second for the detector alone, 67 MB peak RSS. That excludes OBS's screenshot
encoding, Tkinter and Windows overhead, and it is not a Windows measurement. On Windows, check Task Manager > Details >
`pythonw.exe`. Detection latency is about `confirmations_required x scan_interval_s` after a screen appears.

## Capturing a template from the command line
```
python -m scripts.capture_templates --name my_victory --screen VICTORY --roi 0.34,0.42,0.31,0.17
```
It grabs a frame from OBS (or use `--from-file`), saves it in `templates/`, and adds it to `config.json`.

## Building and sharing (zip + exe, no Python needed)
1. Put your Client ID in `BUNDLED_CLIENT_ID` in `app/config.py` on your own PC, so users don't need the developer
   console. Each person still logs in with their own account. The app is registered under your Twitch account, so Twitch
   issues come to you. Don't commit that change.
2. Run `build.bat`. It installs from `requirements-build.lock` with hash checking when that file exists (see
   [security.md](security.md) to create it), builds with UPX off and a version resource, signs the exe if `VPB_SIGN_CERT`
   is set, and prints the zip's SHA-256. It makes `dist\VPB-windows.zip` (the executable is still named `VPB`).
3. Unzip it in a fresh folder and try it in Dry-Run, then upload it to a GitHub release.
4. Users unzip anywhere that isn't Program Files (settings are saved beside the exe) and run `VPB.exe`. Unsigned exes
   show Windows SmartScreen ("More info" > "Run anyway"). Live predictions need Affiliate or Partner status.

By hand: `pyinstaller --noconsole --onedir --noupx --name VPB --version-file version_info.txt --paths . --hidden-import keyring.backends.Windows vpb.py`.
PyInstaller is only in `requirements-build.txt`. The app itself needs just `requirements.txt`.
Put `.env` next to `VPB.exe`. `config.json`, `state.json`, `templates/` and `logs/` are created beside it (set `VPB_HOME`
to change that). No secrets are embedded. `--onedir` triggers fewer antivirus warnings than `--onefile`.

## Changing the icon
The exe and the windows use `assets/icon.ico`, made by `python -m scripts.make_icon` (needs Pillow). To use your own,
replace it with a multi-size .ico (256, 128, 64, 48, 32, 16 px) and run `build.bat` again. Windows caches icons, so if
Explorer shows the old one, rename the exe or restart Explorer.
