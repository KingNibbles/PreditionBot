# Security notes

## What the app does on your PC
- **Network:** `id.twitch.tv` and `api.twitch.tv` over HTTPS (login and predictions), your browser opens Twitch's pages, and a
  WebSocket to OBS at `obs_host:obs_port` (default `localhost:4455`). Nothing else. There is no telemetry and no update check.
- **It never listens for connections.** It only makes outgoing ones.
- **It starts no other programs** (apart from opening your browser and the logs folder), changes no registry keys, adds
  nothing to startup, and deletes no files.
- **Files it writes:** `config.json`, `state.json`, `logs/`, and the template images you capture, all beside the app.
- **Secrets:** Twitch tokens and the OBS password are kept in Windows Credential Manager. They are not in `config.json`.
  Logs have tokens, passwords, device codes and your Windows user name scrubbed, including inside error tracebacks.
- A test (`tests/test_security.py`) fails if code is added that starts processes, uses the registry, loads code from
  strings, or contacts a host that isn't on the list above.

## Hardening in this version
| Area | What changed |
|---|---|
| `.env` | Only `TWITCH_CLIENT_ID` and `OBS_PASSWORD` are read. Anything else is ignored and named in the log. (Before, a line such as `BROWSER=...` could make the Connect button run a command) |
| Logs | Everything written to the log goes through one scrubber, so tracebacks, device codes and passwords with symbols are covered |
| OBS | Only an OBS on this PC is accepted unless `obs_allow_remote` is `true`. Oversized messages are rejected |
| Login | If Twitch ever grants more permissions than the one we ask for, the login is revoked and refused |
| Disconnect | The app now says so if a stored token could not be deleted |
| Files | `config.json` is saved atomically, and template paths can't point outside the app folder |
| Build | PyInstaller is a build-only dependency, UPX is off, the exe has version information, optional signing, and the zip's SHA-256 is printed |

## Building a version you can vouch for
1. Build on a clean Windows machine or in Windows Sandbox, with only Python installed.
2. Create a hash-locked list of packages once, then commit it:
   ```
   .venv\Scripts\python -m pip install pip-tools pip-audit
   .venv\Scripts\pip-compile --generate-hashes requirements-build.txt -o requirements-build.lock
   .venv\Scripts\pip-audit -r requirements.txt
   ```
   `build.bat` installs from `requirements-build.lock` with hash checking whenever the file exists, and warns when it doesn't.
3. Run `build.bat`. It prints the zip's SHA-256. Put that in the release notes.
4. Optional signing: set `VPB_SIGN_CERT` (a `.pfx` file) and `VPB_SIGN_PASSWORD`, then build. Never commit the certificate
   (`.gitignore` blocks `*.pfx`, `*.p12`, `*.pem` and `*.key`).
5. Check what went into the exe: `.venv\Scripts\pyi-archive_viewer dist\VPB\VPB.exe` should list only the app, Python,
   OpenCV, numpy, httpx, websocket-client, keyring and their own dependencies.

## Antivirus warnings
Unsigned PyInstaller programs are flagged by some scanners again and again, and a windowless program that keeps network
connections and uses the credential store looks like many real threats to a heuristic. Those verdicts are generic and
don't prove anything either way. What helps, roughly in order:
- Signing the exe, and building with UPX off (already done in `build.bat`).
- Rebuilding on a clean machine and rescanning on VirusTotal. Look at its Behavior tab: only `id.twitch.tv`,
  `api.twitch.tv` and `127.0.0.1:4455` should appear.
- Reporting the false positive to each flagging vendor, with a link to the source.
- Offering the source and `run.bat` for people who prefer not to run an unsigned exe.

## Where you install it
The app reads and writes beside itself, and an exe folder loads its own libraries, so keep it somewhere only you can
write to (your user profile, not `C:\` or a shared folder). It doesn't need administrator rights, so never run it as one.

## Things the code can't fix
- **The Client ID is public.** Anyone can use it in their own program and ask for different permissions under your app's
  name. On the Twitch approval page, check that the list only says "Manage Predictions".
- **`obs_allow_remote`** sends OBS's login handshake and screenshots unencrypted. Only use it on a network you trust.
- **OpenCV ships many native libraries** (including video codecs this app never uses). Keep it up to date and run
  `pip-audit` before each release.
- **Anyone who can edit your app folder can edit its settings.**
