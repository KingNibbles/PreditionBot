"""Small Tkinter UI. Redraws on events + a 1 s tick ONLY while a voting countdown is running."""
from __future__ import annotations
import base64, queue, webbrowser, tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
import cv2
from . import config as C
from .controller import Controller
from .state_machine import State


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Auto Predictions Bot"); self.geometry("560x560"); self.minsize(520, 520)
        try: self.iconbitmap(default=str(C.APP_DIR / "assets" / "icon.ico"))   # all windows, incl. dialogs
        except Exception: pass                                                  # missing/unsupported icon is harmless
        self.q = queue.Queue()
        self.ctl = Controller(on_status=lambda **k: self.q.put(("status", k)), on_event=lambda m: self.q.put(("log", m)))
        self.vars = {k: tk.StringVar(value="-") for k in
                     ("obs", "twitch", "state", "last", "score", "prediction", "countdown", "ms", "mode")}
        self._build(); self.after(250, self._pump); self.after(1000, self._tick)
        self.refresh_mode(); self.vars["twitch"].set(self.ctl.twitch_status())
        if self.ctl.needs_setup(): self.after(400, self.setup_wizard)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self):
        f = ttk.Frame(self, padding=8); f.pack(fill="both", expand=True)
        rows = [("Mode", "mode"), ("OBS", "obs"), ("Twitch", "twitch"), ("State", "state"), ("Last screen", "last"),
                ("Confidence", "score"), ("Scan ms", "ms"), ("Prediction", "prediction"), ("Voting left", "countdown")]
        for i, (lab, key) in enumerate(rows):
            ttk.Label(f, text=lab + ":").grid(row=i, column=0, sticky="w")
            ttk.Label(f, textvariable=self.vars[key], font=("Segoe UI", 10, "bold")).grid(row=i, column=1, sticky="w")
        b = ttk.Frame(f); b.grid(row=len(rows), column=0, columnspan=2, pady=6, sticky="w")
        for i, (t, c) in enumerate([("Start Monitoring", self.start), ("Stop Monitoring", self.ctl.stop),
                                    ("Connect Twitch", self.connect), ("Disconnect Twitch", self.disconnect),
                                    ("Calibrate", self.calibrate), ("Test Detection", self.test),
                                    ("Settings", self.settings), ("Open Logs", self.open_logs), ("Setup Guide", self.setup_wizard)]):
            ttk.Button(b, text=t, command=c).grid(row=i // 4, column=i % 4, padx=2, pady=2)
        m = ttk.LabelFrame(f, text="Manual result / recovery"); m.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="we")
        for i, (t, c) in enumerate([("WIN", "WIN"), ("LOSE", "LOSE"), ("Cancel prediction", "CANCEL")]):
            ttk.Button(m, text=t, command=lambda c=c: self.manual(c)).grid(row=0, column=i, padx=2, pady=2)
        ttk.Button(m, text="Re-check Twitch", command=lambda: self._guard(lambda: self.ctl.manager.recheck_twitch())).grid(row=0, column=3, padx=2)
        ttk.Button(m, text="Acknowledge", command=lambda: self._guard(lambda: self.ctl.manager.acknowledge())).grid(row=0, column=4, padx=2)
        self.warn = ttk.Label(f, text="", foreground="#b00020", wraplength=520); self.warn.grid(row=len(rows) + 2, column=0, columnspan=2, sticky="w")
        self.log = tk.Text(f, height=10, state="disabled", wrap="word"); self.log.grid(row=len(rows) + 3, column=0, columnspan=2, sticky="nsew")
        f.rowconfigure(len(rows) + 3, weight=1); f.columnconfigure(1, weight=1)

    def _pump(self):
        try:
            while True:
                kind, v = self.q.get_nowait()
                if kind == "log": self._log(v)
                elif kind == "code": self._show_code(*v)
                else:
                    for k, val in v.items():
                        if k == "last": self.vars["last"].set(val)
                        elif k == "score": self.vars["score"].set(f"{val:.3f}")
                        elif k == "ms": self.vars["ms"].set(f"{val:.1f}")
                        elif k in ("obs", "twitch"): self.vars[k].set(val)
                        elif k == "state":
                            self.vars["state"].set(val)
                            self.warn.config(text="Manual review needed: choose WIN / LOSE / Cancel below." if val == "MANUAL_REVIEW"
                                             else ("Error: see log; it will return to IDLE automatically." if val == "ERROR" else ""))
        except queue.Empty: pass
        self.after(250, self._pump)

    def _tick(self):
        m = self.ctl.manager
        if m:
            s = m.snapshot(); self.vars["state"].set(s["state"]); self.vars["prediction"].set(s["prediction_id"] or "none")
            self.vars["countdown"].set(f"{s['voting_left']} s" if s["state"] == State.PREDICTION_ACTIVE.value else "-")
        self.after(1000, self._tick)

    def _log(self, msg):
        self.log.config(state="normal"); self.log.insert("end", msg + "\n"); self.log.see("end"); self.log.config(state="disabled")

    def refresh_mode(self):
        self.vars["mode"].set("DRY-RUN (simulated)" if self.ctl.cfg.dry_run else "LIVE (real predictions)")

    def _guard(self, fn):
        try: fn()
        except Exception as e: messagebox.showerror("Error", str(e))

    def start(self):
        self._guard(self.ctl.start); self.refresh_mode()

    def connect(self):
        c = self.ctl.cfg
        if not c.twitch_client_id:
            if not messagebox.askokcancel("One-time Twitch setup",
                "Twitch needs an app Client ID (free, takes about a minute).\n\n"
                "Your browser will open the Twitch developer page. Create an application with:\n"
                "  - Name: anything unique\n  - OAuth Redirect URL: http://localhost\n"
                "  - Category: Application Integration\n  - Client Type: Public\n\n"
                "Then copy the Client ID and paste it in the next box."):
                return
            webbrowser.open("https://dev.twitch.tv/console/apps/create")
            cid = simpledialog.askstring("Twitch Client ID", "Paste your Client ID:", parent=self)
            if not cid or not cid.strip(): return
            c.twitch_client_id = cid.strip(); C.save_config(c); self.ctl.auth = None   # not a secret
        self.ctl.connect_twitch(lambda url, code: self.q.put(("code", (url, code))))

    def _show_code(self, url, code):
        d = tk.Toplevel(self); d.title("Authorize on Twitch")
        ttk.Label(d, text="Your browser opened Twitch's authorization page.\nIf asked for a code, enter:", padding=8).pack()
        e = ttk.Entry(d, justify="center", font=("Consolas", 16), width=14); e.insert(0, code); e.pack(padx=10)
        e.config(state="readonly")
        ttk.Label(d, text="Click Authorize on that page. This window can stay open; status updates above.", padding=8).pack()
        ttk.Button(d, text="Reopen page", command=lambda: webbrowser.open(url)).pack(pady=(0, 8))

    def disconnect(self):
        if messagebox.askyesno("Disconnect", "Revoke Twitch access and delete stored tokens?"): self._guard(self.ctl.disconnect_twitch)

    def manual(self, choice):
        if not self.ctl.manager: return messagebox.showinfo("Manual", "Start monitoring first.")
        if messagebox.askyesno("Confirm", f"Apply '{choice}' to the active prediction?"):
            self._guard(lambda: self.ctl.manager.manual_resolve(choice))

    def test(self):
        d = filedialog.askdirectory(title="Folder of saved screenshots")
        if d: self._guard(lambda: [self._log(l) for l in self.ctl.test_detection(d)])

    def open_logs(self):
        import os; os.startfile(C.LOG_DIR) if hasattr(os, "startfile") else None

    def calibrate(self):
        try: frame = self.ctl.grab_frame()
        except Exception:
            p = filedialog.askopenfilename(title="Pick a saved screenshot", filetypes=[("Images", "*.png *.jpg *.jpeg")])
            if not p: return
            frame = cv2.imread(p)
        CalibrationDialog(self, self.ctl, frame)

    def settings(self):
        c = self.ctl.cfg
        fields = [("obs_host", str), ("obs_port", int), ("obs_source", str), ("width", int), ("height", int),
                  ("scan_interval_s", float), ("confirmations_required", int), ("default_threshold", float),
                  ("prediction_title", str), ("prediction_window_s", int), ("ambiguous_policy", str)]
        d = tk.Toplevel(self); d.title("Settings"); vs = {}
        for i, (k, t) in enumerate(fields):
            ttk.Label(d, text=k).grid(row=i, column=0, sticky="w"); vs[k] = tk.StringVar(value=str(getattr(c, k)))
            ttk.Entry(d, textvariable=vs[k], width=36).grid(row=i, column=1)
        n = len(fields)
        game, res = tk.StringVar(value=c.game), tk.StringVar(value=c.game_resolution)
        ttk.Label(d, text="game").grid(row=n, column=0, sticky="w")
        game_box = ttk.Combobox(d, textvariable=game, values=C.GAME_CHOICES, state="readonly", width=33)
        game_box.grid(row=n, column=1)
        ttk.Label(d, text="game_resolution").grid(row=n + 1, column=0, sticky="w")
        res_box = ttk.Combobox(d, textvariable=res, state="readonly", width=33)
        res_box.grid(row=n + 1, column=1)

        def show_resolutions(*_):
            options = C.resolutions_for(game.get())
            res_box["values"] = options
            if options:
                res_box.config(state="readonly")
                if res.get() not in options: res.set(options[0])
            else:                                   # Custom has no built-in sets
                res_box.config(state="disabled")
        game_box.bind("<<ComboboxSelected>>", show_resolutions); show_resolutions()
        ttk.Label(d, text="Built-in templates exist for the games listed. For anything else choose Custom and use Calibrate.",
                  foreground="#555", wraplength=330).grid(row=n + 2, columnspan=2, sticky="w")
        dry = tk.BooleanVar(value=c.dry_run); ttk.Checkbutton(d, text="Dry-run (simulate, never touch Twitch)", variable=dry).grid(row=n + 3, columnspan=2)
        def save():
            try:
                for k, t in fields: setattr(c, k, t(vs[k].get()))
                c.game = game.get()
                if res.get(): c.game_resolution = res.get()
                if not dry.get() and not c.live_confirmed:
                    if not messagebox.askyesno("Enable REAL predictions?",
                        "This will create, lock and resolve REAL Twitch predictions on your channel.\nYou should have tested in Dry-Run first.\n\nEnable live mode?"):
                        return
                    c.live_confirmed = True
                c.dry_run = dry.get(); C.save_config(c); self.refresh_mode(); d.destroy()
            except Exception as e: messagebox.showerror("Invalid", str(e))
        ttk.Button(d, text="Save", command=save).grid(row=n + 4, column=1, sticky="e")
        ttk.Button(d, text="OBS password…", command=lambda: self._guard(lambda: self.ctl.set_obs_password(
            simpledialog.askstring("OBS", "WebSocket password:", show="*") or ""))).grid(row=n + 4, column=0)

    def setup_wizard(self): SetupDialog(self, self.ctl)

    def _close(self):
        self.ctl.stop(); self.destroy()


class CalibrationDialog(tk.Toplevel):
    """Drag a rectangle over a distinctive UI element, choose the screen type, save as a template."""
    def __init__(self, parent, ctl, frame):
        super().__init__(parent); self.title("Calibrate template"); self.ctl, self.frame = ctl, cv2.resize(frame, (640, 360))
        ok, png = cv2.imencode(".png", self.frame); self.img = tk.PhotoImage(data=base64.b64encode(png.tobytes()))
        self.cv = tk.Canvas(self, width=640, height=360); self.cv.pack(); self.cv.create_image(0, 0, anchor="nw", image=self.img)
        self.rect = None; self.roi = (0.0, 0.0, 1.0, 1.0)
        self.cv.bind("<ButtonPress-1>", self._down); self.cv.bind("<B1-Motion>", self._drag)
        row = ttk.Frame(self); row.pack(pady=4)
        self.name = tk.StringVar(value="template1"); self.screen = tk.StringVar(value="MATCH_START"); self.th = tk.StringVar(value="0.80")
        ttk.Entry(row, textvariable=self.name, width=14).pack(side="left")
        ttk.Combobox(row, textvariable=self.screen, values=C.SCREEN_TYPES, width=12, state="readonly").pack(side="left")
        ttk.Entry(row, textvariable=self.th, width=5).pack(side="left")
        ttk.Button(row, text="Save template", command=self.save).pack(side="left")

    def _down(self, e): self.x0, self.y0 = e.x, e.y; self.rect = self.rect or self.cv.create_rectangle(0, 0, 0, 0, outline="red", width=2)
    def _drag(self, e):
        self.cv.coords(self.rect, self.x0, self.y0, e.x, e.y)
        x0, x1 = sorted((self.x0, e.x)); y0, y1 = sorted((self.y0, e.y))
        self.roi = (x0 / 640, y0 / 360, max(.02, (x1 - x0) / 640), max(.02, (y1 - y0) / 360))
    def save(self):
        try:
            self.ctl.save_template(self.frame, self.name.get().strip(), self.screen.get(), self.roi, float(self.th.get()))
            messagebox.showinfo("Saved", "Template saved. Capture several per screen for reliability."); self.destroy()
        except Exception as e: messagebox.showerror("Error", str(e))


class SetupDialog(tk.Toplevel):
    """First-run checklist: OBS source -> OBS password -> Twitch -> templates."""
    def __init__(self, parent, ctl):
        super().__init__(parent); self.title("Setup guide"); self.ctl, self.parent = ctl, parent
        f = ttk.Frame(self, padding=10); f.pack()
        ttk.Label(f, text="1. In OBS: Tools > WebSocket Server Settings > Enable, set a password.").grid(row=0, columnspan=3, sticky="w")
        ttk.Label(f, text="OBS password").grid(row=1, column=0, sticky="w")
        self.pw = tk.StringVar(value=ctl.obs_password()); ttk.Entry(f, textvariable=self.pw, show="*", width=28).grid(row=1, column=1)
        ttk.Button(f, text="Load sources", command=self.load).grid(row=1, column=2)
        ttk.Label(f, text="2. Game source").grid(row=2, column=0, sticky="w")
        self.src = tk.StringVar(value=ctl.cfg.obs_source)
        self.combo = ttk.Combobox(f, textvariable=self.src, width=26); self.combo.grid(row=2, column=1)
        self.combo.bind("<<ComboboxSelected>>", self.save_obs); self.combo.bind("<Return>", self.save_obs)
        self.combo.bind("<FocusOut>", self.save_obs)
        self.protocol("WM_DELETE_WINDOW", self.close)
        ttk.Button(f, text="Save", command=self.save_obs).grid(row=2, column=2)
        ttk.Label(f, text="3. Twitch (Affiliate/Partner needed for live predictions)").grid(row=3, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Button(f, text="Connect Twitch", command=parent.connect).grid(row=4, column=1)
        ttk.Label(f, text="4. Built-in templates are used for your game. Calibrate adds your own.").grid(row=5, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Button(f, text="Calibrate", command=parent.calibrate).grid(row=6, column=1)
        self.status = ttk.Label(f, text="", foreground="#555"); self.status.grid(row=7, columnspan=3, sticky="w", pady=6)
        ttk.Label(f, text="Then run in Dry-Run mode (default) before enabling live predictions.", foreground="#555").grid(row=8, columnspan=3, sticky="w")
        ttk.Button(f, text="Refresh status", command=self.refresh).grid(row=9, column=1); self.refresh(); self._autoload()

    def _autoload(self):
        """Fill the dropdown in the background when the window opens (OBS may be closed: ignore failures)."""
        import threading
        box = {}
        def work():
            try: box["v"] = self.ctl.list_obs_sources(self.pw.get())
            except Exception as e: box["e"] = e
        threading.Thread(target=work, daemon=True).start()
        def check():
            if not self.winfo_exists(): return
            if "v" in box:
                self.combo["values"] = box["v"]
            elif "e" not in box:
                self.after(300, check)
        self.after(300, check)

    def load(self):
        try:
            self.ctl.save_obs_password(self.pw.get()); self.combo["values"] = self.ctl.list_obs_sources(self.pw.get())
        except Exception as e: messagebox.showerror("OBS", f"Could not reach OBS: {e}")

    def save_obs(self, *_):
        """Called on selection, Enter, focus-out, the Save button and window close."""
        try:
            self.ctl.save_obs_password(self.pw.get())
            self.ctl.save_obs_source(self.src.get())
        except Exception as e:
            messagebox.showerror("Could not save", f"{e}\n\nIs the app in a folder you can write to (not Program Files)?")
        self.refresh()

    def close(self):
        self.save_obs(); self.destroy()

    def refresh(self):
        p = self.ctl.setup_progress(); ok = lambda b: "done" if b else "TODO"
        have = ", ".join(sorted(p["templates"])) or "none"
        screens = p["templates"]
        ready = bool(screens & set(C.START_SCREENS)) and {"VICTORY", "DEFEAT"} <= screens
        self.status.config(text=f"OBS source: {ok(p['obs'])}   Twitch: {ok(p['twitch'])}   Templates: {ok(ready)} ({have})")
