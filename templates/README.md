# Reference templates
Templates are full-frame screenshots (saved at scan resolution, default 640x360) plus a cropped ROI
and threshold stored in `config.json`. Image files here are git-ignored.
Capture 2-3 per screen (different maps/agents/times) with the **Calibrate** button or
`python -m scripts.capture_templates --name agent_select --screen MATCH_START --roi 0.3,0.05,0.4,0.15`.
Choose a ROI around something that is identical every time (a banner, a label, a logo), not
around things that change (agent portraits, player names, map art).
