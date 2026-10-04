"""Small Tkinter window: enter PoGDesign login, pick folders, run the sync."""

from __future__ import annotations

import datetime as _dt
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import config
from .pogdesign import LoginError, PogError
from .sync import Options, load_aliases, run_sync


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("TV List - PoGDesign sync")
        self.geometry("820x640")
        self.minsize(640, 480)
        self.msgs: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.last_report = ""
        cfg = config.load()

        pad = {"padx": 6, "pady": 3}
        frm = ttk.Frame(self, padding=8)
        frm.pack(fill="both", expand=True)

        login = ttk.LabelFrame(frm, text="PoGDesign account", padding=6)
        login.pack(fill="x")
        ttk.Label(login, text="E-mail:").grid(row=0, column=0, sticky="e", **pad)
        self.email = tk.StringVar(value=cfg.get("email", ""))
        ttk.Entry(login, textvariable=self.email, width=40).grid(row=0, column=1, sticky="we", **pad)
        ttk.Label(login, text="Password:").grid(row=1, column=0, sticky="e", **pad)
        self.password = tk.StringVar()
        ttk.Entry(login, textvariable=self.password, show="•", width=40).grid(row=1, column=1, sticky="we", **pad)
        self.remember = tk.BooleanVar(value=bool(cfg.get("email")))
        ttk.Checkbutton(login, text="Remember e-mail (password is never saved)",
                        variable=self.remember).grid(row=2, column=1, sticky="w", **pad)
        login.columnconfigure(1, weight=1)

        fold = ttk.LabelFrame(frm, text="TV folders to scan", padding=6)
        fold.pack(fill="x", pady=6)
        self.folders = tk.Listbox(fold, height=4)
        self.folders.grid(row=0, column=0, rowspan=2, sticky="nsew", **pad)
        for f in cfg.get("folders", []):
            self.folders.insert("end", f)
        ttk.Button(fold, text="Add folder…", command=self.add_folder).grid(row=0, column=1, sticky="we", **pad)
        ttk.Button(fold, text="Remove", command=self.remove_folder).grid(row=1, column=1, sticky="we", **pad)
        fold.columnconfigure(0, weight=1)

        opts = ttk.LabelFrame(frm, text="Options", padding=6)
        opts.pack(fill="x")
        self.dry_run = tk.BooleanVar(value=cfg.get("dry_run", True))
        self.include_tracked = tk.BooleanVar(value=cfg.get("include_tracked", True))
        self.specials = tk.BooleanVar(value=cfg.get("include_specials", False))
        self.hide_watched = tk.BooleanVar(value=cfg.get("hide_watched_missing", False))
        ttk.Checkbutton(opts, text="Dry run (preview only - don't tick anything on the site)",
                        variable=self.dry_run).grid(row=0, column=0, sticky="w", **pad)
        ttk.Checkbutton(opts, text="Also check shows in my PoGDesign filter that aren't on my drive",
                        variable=self.include_tracked).grid(row=1, column=0, sticky="w", **pad)
        ttk.Checkbutton(opts, text="Include specials (season 0)",
                        variable=self.specials).grid(row=2, column=0, sticky="w", **pad)
        ttk.Checkbutton(opts, text="Don't list missing episodes already marked watched on the site",
                        variable=self.hide_watched).grid(row=3, column=0, sticky="w", **pad)
        self.debug = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Save debug files (pages from the site, for troubleshooting)",
                        variable=self.debug).grid(row=4, column=0, sticky="w", **pad)

        bar = ttk.Frame(frm)
        bar.pack(fill="x", pady=6)
        self.run_btn = ttk.Button(bar, text="Scan & Sync", command=self.start)
        self.run_btn.pack(side="left")
        self.save_btn = ttk.Button(bar, text="Save report…", command=self.save_report, state="disabled")
        self.save_btn.pack(side="left", padx=6)
        self.progress = ttk.Progressbar(bar, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=6)

        self.log = ScrolledText(frm, height=16, font=("Consolas", 10), state="disabled")
        self.log.pack(fill="both", expand=True)

        self.after(100, self.poll)

    # -- folder list -------------------------------------------------------
    def add_folder(self) -> None:
        d = filedialog.askdirectory(title="Choose a folder containing TV shows")
        if d and d not in self.folders.get(0, "end"):
            self.folders.insert("end", d)

    def remove_folder(self) -> None:
        for i in reversed(self.folders.curselection()):
            self.folders.delete(i)

    # -- running -----------------------------------------------------------
    def write(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        folders = list(self.folders.get(0, "end"))
        if not self.email.get().strip() or not self.password.get():
            messagebox.showwarning("Login needed", "Enter your PoGDesign e-mail and password.")
            return
        if not folders:
            messagebox.showwarning("No folders", "Add at least one folder to scan.")
            return
        if not self.dry_run.get() and not messagebox.askyesno(
            "Confirm", "Dry run is off: episodes found on your drive will be ticked as "
                       "watched on PoGDesign. Continue?"):
            return
        config.save({
            "email": self.email.get().strip() if self.remember.get() else "",
            "folders": folders,
            "dry_run": self.dry_run.get(),
            "include_tracked": self.include_tracked.get(),
            "include_specials": self.specials.get(),
            "hide_watched_missing": self.hide_watched.get(),
        })
        opts = Options(
            folders=folders,
            email=self.email.get().strip(),
            password=self.password.get(),
            dry_run=self.dry_run.get(),
            include_tracked=self.include_tracked.get(),
            include_specials=self.specials.get(),
            hide_watched_missing=self.hide_watched.get(),
            aliases=load_aliases(config.aliases_path()),
            debug_dir=str(config.debug_dir()) if self.debug.get() else None,
        )
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.run_btn.configure(state="disabled")
        self.save_btn.configure(state="disabled")
        self.progress.start(12)
        self.worker = threading.Thread(target=self._work, args=(opts,), daemon=True)
        self.worker.start()

    def _work(self, opts: Options) -> None:
        try:
            rep = run_sync(opts, log=lambda m: self.msgs.put(("log", m)))
            self.msgs.put(("done", rep.to_text()))
        except LoginError as e:
            self.msgs.put(("error", str(e)))
        except PogError as e:
            self.msgs.put(("error", f"PoGDesign error: {e}"))
        except Exception as e:  # show anything unexpected instead of dying silently
            self.msgs.put(("error", f"{type(e).__name__}: {e}"))

    def poll(self) -> None:
        try:
            while True:
                kind, payload = self.msgs.get_nowait()
                if kind == "log":
                    self.write(payload)
                    continue
                self.progress.stop()
                self.run_btn.configure(state="normal")
                if kind == "done":
                    self.last_report = payload
                    self.write("\n" + payload)
                    self.save_btn.configure(state="normal")
                else:
                    self.write("ERROR: " + payload)
                    messagebox.showerror("Error", payload)
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def save_report(self) -> None:
        name = f"tvlist-report-{_dt.datetime.now():%Y%m%d-%H%M}.txt"
        path = filedialog.asksaveasfilename(initialfile=name, defaultextension=".txt",
                                            filetypes=[("Text", "*.txt")])
        if path:
            Path(path).write_text(self.last_report, encoding="utf-8")


def main() -> None:
    App().mainloop()
