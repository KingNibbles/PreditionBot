# How it works

The app is a loop with a safety net. Every half second it asks OBS for a small picture of your game, checks it against
a few reference images, and tells a manager what it saw. The manager is the only part that talks to Twitch.

## The pieces

| File | Job |
|---|---|
| `app/obs_client.py` | Asks OBS (over its WebSocket) for a 640x360 screenshot of your source |
| `app/detector.py` | Matches the screenshot against the templates. A screen only counts after 3 scans in a row, then it can't fire again until it disappears |
| `app/monitor.py` | The scan loop. Sleeps between scans, reconnects to OBS with a 1, 2, 4 ... 30 s backoff, and only looks for screens that matter in the current state |
| `app/prediction_manager.py` | Turns confirmed screens into Twitch actions: create, lock, resolve, cancel |
| `app/state_machine.py` | The list of allowed states, saved to `state.json` after every change so a restart can pick up where it stopped |
| `app/twitch_client.py` | The Twitch calls (create, get, end), with one token refresh on a 401, and clear errors for timeouts and rate limits |
| `app/dryrun.py` | A pretend Twitch that lives in memory, used by Dry-Run and the tests |
| `app/auth.py` | Twitch login and token refresh |
| `app/lazy_http.py` | Creates the web client on first use, shared by the Twitch calls and the login |
| `app/controller.py` | Wires it all together for the window |
| `app/ui.py` | The Tkinter window |
| `app/config.py` | Settings, the list of games and their built-in templates |

## One match, step by step

| Step | What happens |
|---|---|
| 1. Start screen | A start screen (for example a "match found" banner) is confirmed on 3 scans in a row |
| 2. Check Twitch | The manager asks Twitch for the latest prediction. If one is already open it never creates a second |
| 3. Create | `POST /helix/predictions` with `prediction_window: 60` and two outcomes, `WIN` and `LOSE` |
| 4. Voting | The countdown starts from Twitch's own `created_at`, not from when we first saw the screen |
| 5. Lock | Twitch locks the prediction itself when the window ends. One timer fires 1.5 seconds later, asks Twitch once, and only sends a lock request if Twitch still says ACTIVE |
| 6. Waiting | The prediction stays locked while the match runs. It is never resolved at this point |
| 7. Result | A Victory screen resolves WIN, a Defeat screen resolves LOSE, a Draw screen cancels so everyone is refunded |
| 8. Cooldown | 20 seconds, then back to idle for the next match |

Anything the app can't be sure about goes to **MANUAL_REVIEW** instead of a guess: a result screen that shows up
too early (a remake or a false hit), no result after 90 minutes, a failed cancel, a Twitch error it can't verify.
You then pick WIN, LOSE or Cancel yourself.

## Why it is built this way

- **Twitch owns the 60 seconds.** Our own timer can drift or be lost in a crash, but Twitch's window can't. So we only
  store `created_at`, and everything else is worked out from it. After a restart the app asks Twitch what is really open
  and rebuilds the deadline, so the 60 seconds never starts over. A failed request can't stretch the window either.
- **Unknown is not the same as failed.** If a create request times out, the prediction may still exist. The app checks
  Twitch before it does anything else, so it can't open two predictions for one match.
- **One timer per purpose.** Timers are one-shot and keyed (lock, retry, result timeout, cooldown), so a second copy of
  the same timer can't exist, and there is no polling loop.
- **Dry-Run can't touch your channel.** The manager refuses to start with a real Twitch client while Dry-Run is on,
  and refuses live mode until you've confirmed it once.

## States

`IDLE` waiting for a start screen, `CONFIRMING_START` a candidate is being checked, `CREATING_PREDICTION` talking to
Twitch, `PREDICTION_ACTIVE` voting is open, `WAITING_FOR_RESULT` locked and the match is on, `RESOLVING` ending it,
`COOLDOWN` short pause, `MANUAL_REVIEW` waiting for you, `ERROR` something failed (it clears itself after a minute
when nothing is open on Twitch).

## Login

Twitch's authorization-code flow needs a client secret, and Twitch recommends a **Public** client with the **Device
Code** flow for desktop apps. So no secret exists on your PC. The app asks Twitch for a short code, you approve it in
your browser, and the tokens go into Windows Credential Manager. Refresh tokens are single use, so refreshes are
serialized and the new token is stored straight away. They expire after about 30 days unused; then you just connect
again. Disconnect revokes the token and deletes it locally.

Because the Client ID is public, anyone could put it in their own program. On the Twitch approval page, check that the
permission list only says "Manage Predictions".

## Where things are stored

| What | Where |
|---|---|
| Settings (no secrets) | `config.json` |
| The open prediction's id and state | `state.json` (`state_dryrun.json` in Dry-Run) |
| Twitch tokens and the OBS password | Windows Credential Manager |
| Logs | `logs/app.log` (tokens and passwords are redacted) |
| Templates you capture | `templates/` |

## Checked against Twitch's docs

https://dev.twitch.tv/docs/api/predictions/ (Oct 2026): the scope `channel:manage:predictions` covers create, end and
get. Only one prediction can be open at a time, and unresolved ones are refunded after 24 hours. The docs say a locked
prediction needs a winner but not whether RESOLVED works straight from ACTIVE, so the app always locks first.
