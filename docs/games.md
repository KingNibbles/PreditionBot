# Games

## VALORANT
Built-in templates for 1920x1080 on the English client, in `templates/defaults/valorant/1920x1080/`.

- **Screens:** the MATCH FOUND banner, Agent Select (two boxes: the LOCK IN button and the role icons), Victory,
  Defeat and Draw. Everything outside the match boxes is blanked, so the images contain no player names or chat.
  On real screenshots the right boxes scored 0.94 or higher and the wrong ones 0.47 or lower.
- **Dodges and re-queues:** if MATCH FOUND appears again while a prediction is open and it is between 20 s and 600 s
  old (`requeue_min_age_s`, `requeue_max_age_s`; agent select lasts about 85 s, then queue time), the app assumes the
  match never started. It cancels the prediction (points refunded) and opens a fresh one with a new 60 s window.
  Under 20 s it is the same banner still on screen. Over 600 s it is never auto-cancelled and goes to manual review.
  If the cancel fails no new prediction is created. Turn it off with `restart_on_requeue: false`.
- **Dodge without re-queuing:** nothing is seen, so the prediction stays open until you cancel it or the 90-minute
  result timeout sends it to manual review.
- **Draws:** the Draw screen cancels the prediction (`draw_policy`: `cancel` or `manual`). A draw seen during voting or
  before `min_match_seconds` (300) goes to manual review. Surrenders probably show Victory or Defeat (unverified).
  Remakes and abandoned matches have no known screen and end in manual review.
- **Timing settings:** they are shared by every game for now, and tuned for this one.

## Any other game (Custom)
Set **game** to **Custom** in Settings, then use **Calibrate** to capture a start screen, a Victory and a Defeat screen
(and a Draw screen if the game has one). Your own templates are always added to the built-in ones.

## Adding a built-in game (developers)
1. Take full-size screenshots of the start, win, loss and (if any) draw screens at one resolution.
2. For each, pick a box around something fixed (a banner, a label), and measure it as fractions of the frame:
   `x, y, width, height`. Avoid anything that changes from game to game, like scores, timers and names.
3. Blank everything outside the boxes so the image carries no private data, and save it as
   `templates/defaults/<game>/<WxH>/<name>.png` (1280x720 works well).
4. Add an entry under `GAMES` in `app/config.py`: game name, then resolution, then a list of `_entry(...)` lines
   (folder, file, screen type, box). It then shows up in the Settings dropdowns.
5. Test with `python -m scripts.benchmark` and the **Test Detection** button on screens that must *not* match.
   Every wrong screen should score well under the threshold (default 0.80).

What is not per-game yet: the timing settings (`min_match_seconds`, the re-queue window, the draw policy). A game with
a different shape, like a battle royale with placements instead of win or loss, would also need new logic.
