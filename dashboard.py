"""Tk-based detail dashboard. Single window, refreshable, opened from the tray icon."""
from __future__ import annotations
import time
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from typing import Callable


def _fmt_reset(epoch: float) -> str:
    if not epoch:
        return "—"
    delta = epoch - time.time()
    when = datetime.fromtimestamp(epoch).strftime("%m/%d %H:%M")
    if delta <= 0:
        return f"{when}（過ぎてます）"
    h, rem = divmod(int(delta), 3600)
    m = rem // 60
    if h > 24:
        d = h // 24
        return f"{when}（あと {d}日{h % 24}時間）"
    return f"{when}（あと {h}h{m:02d}m）"


def _fmt_pct(pct: float) -> str:
    return f"{pct:5.1f}%"


class Dashboard:
    """Owns a single Toplevel that is shown/hidden rather than recreated.

    Pass a `refresh` callable that returns a fresh parsers.Snapshot. The window
    polls it on a timer while visible.
    """

    def __init__(self, refresh: Callable[[], "object"], cfg: dict):
        self.refresh = refresh
        self.cfg = cfg
        self.root: tk.Tk | None = None
        self.win: tk.Toplevel | None = None
        self._after_id = None

    # --- lifecycle -------------------------------------------------

    def ensure_root(self):
        if self.root is None:
            self.root = tk.Tk()
            self.root.withdraw()  # hidden parent
            try:
                style = ttk.Style(self.root)
                # Use a theme that works on Win 11
                if "vista" in style.theme_names():
                    style.theme_use("vista")
            except tk.TclError:
                pass

    def show(self):
        self.ensure_root()
        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            return
        self._build()
        self._populate()
        self._schedule_refresh()

    def hide(self):
        if self.win is not None and self.win.winfo_exists():
            self.win.withdraw()
        self._cancel_refresh()

    def destroy(self):
        """Destroy only the Toplevel; the root is owned by whoever created the
        Dashboard (tray.py), so we don't tear it down here."""
        self._cancel_refresh()
        if self.win is not None and self.win.winfo_exists():
            self.win.destroy()
        self.win = None

    # --- ui --------------------------------------------------------

    def _build(self):
        assert self.root is not None
        win = tk.Toplevel(self.root)
        win.title("Claude / Codex Usage Monitor")
        import taskbar_widget as _tw
        _s = getattr(_tw, "SCALE", 1.0)
        win.geometry(f"{int(520 * _s)}x{int(520 * _s)}")
        win.minsize(460, 460)
        win.protocol("WM_DELETE_WINDOW", self.hide)

        # Header
        header = ttk.Frame(win, padding=(14, 12, 14, 6))
        header.pack(fill="x")
        self.lbl_refreshed = ttk.Label(header, text="更新中…", foreground="#666")
        self.lbl_refreshed.pack(side="left")
        ttk.Button(header, text="今すぐ更新", command=self._populate).pack(side="right")

        body = ttk.Frame(win, padding=(14, 4, 14, 14))
        body.pack(fill="both", expand=True)

        # --- Codex section ---
        codex_frame = ttk.LabelFrame(body, text="  Codex CLI  ", padding=10)
        codex_frame.pack(fill="x", pady=(0, 10))
        self.codex_plan = ttk.Label(codex_frame, text="plan: ?")
        self.codex_plan.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        self._row_with_bar(codex_frame, 1, "5時間枠", "codex_5h")
        self._row_with_bar(codex_frame, 2, "週次枠", "codex_week")
        self.codex_event = ttk.Label(codex_frame, text="", foreground="#666")
        self.codex_event.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # --- Claude section ---
        claude_frame = ttk.LabelFrame(body, text="  Claude Code  ", padding=10)
        claude_frame.pack(fill="x", pady=(0, 10))
        self.claude_plan = ttk.Label(claude_frame, text="plan limit: ?")
        self.claude_plan.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        self._row_with_bar(claude_frame, 1, "5時間枠 (estimate)", "claude_5h")
        self._row_with_bar(claude_frame, 2, "週次枠 (estimate)", "claude_week")
        self.claude_models = ttk.Label(claude_frame, text="", foreground="#666",
                                       wraplength=460, justify="left")
        self.claude_models.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # Footer
        footer = ttk.Frame(win, padding=(14, 0, 14, 12))
        footer.pack(fill="x")
        ttk.Label(footer, text="config.json でプラン上限・更新間隔を調整可", foreground="#888").pack(side="left")

        self.win = win

    def _row_with_bar(self, parent, row: int, label: str, key: str):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8))
        bar = ttk.Progressbar(parent, length=320, mode="determinate", maximum=100)
        bar.grid(row=row, column=1, sticky="ew", pady=2)
        pct_lbl = ttk.Label(parent, text="—", width=8)
        pct_lbl.grid(row=row, column=2, sticky="e", padx=(8, 0))
        reset_lbl = ttk.Label(parent, text="", foreground="#666")
        reset_lbl.grid(row=row, column=3, sticky="w", padx=(8, 0))
        parent.columnconfigure(1, weight=1)
        setattr(self, f"{key}_bar", bar)
        setattr(self, f"{key}_pct", pct_lbl)
        setattr(self, f"{key}_reset", reset_lbl)

    # --- data ------------------------------------------------------

    def _populate(self):
        snap = self.refresh()
        if self.win is None or not self.win.winfo_exists():
            return

        self.lbl_refreshed.config(
            text=f"更新: {datetime.fromtimestamp(snap.refreshed_at).strftime('%H:%M:%S')}"
        )

        # Codex
        c = snap.codex
        if c.available:
            self.codex_plan.config(text=f"plan: {c.plan_type}    total_tokens: {c.total_tokens:,}")
            self._set_bar("codex_5h", c.primary_pct, c.primary_resets_at)
            self._set_bar("codex_week", c.secondary_pct, c.secondary_resets_at)
            self.codex_event.config(text=f"last event: {c.last_event_at}")
        else:
            self.codex_plan.config(text="plan: (Codex データなし)")
            self._set_bar("codex_5h", None, 0)
            self._set_bar("codex_week", None, 0)
            self.codex_event.config(text=c.note)

        # Claude
        cc = snap.claude
        from config import claude_plan_limit_usd
        limit = claude_plan_limit_usd(self.cfg)
        plan_name = self.cfg.get("claude_plan", "?")
        if cc.available:
            is_estimate = cc.note.startswith("estimate")
            model_lbl = cc.model or "Claude"
            if is_estimate:
                source = "calibrated est. (Anthropic 表示と±1%以内)"
            elif model_lbl.startswith("live:"):
                if getattr(cc, "fable_available", False):
                    source = f"LIVE API — {model_lbl}   Fable 監視: 有効"
                elif "setup-token" in model_lbl:
                    source = ("LIVE API (setup-token 経由) — Fable 監視には "
                              "PowerShell で `claude auth login` を実行して "
                              "Desktop 認証を更新してください")
                else:
                    source = f"LIVE API — {model_lbl}"
            else:
                source = f"LIVE — {model_lbl}"
            if is_estimate:
                self.claude_plan.config(
                    text=(f"[{source}]   plan: {plan_name}   "
                          f"limit: 5h ${limit['window_5h']:.0f} / 7d ${limit['window_7d']:.0f}   "
                          f"used: 5h ${cc.cost_5h_usd:,.2f} / 7d ${cc.cost_7d_usd:,.2f}")
                )
            else:
                age = ""
                if cc.snapshot_age_seconds > 60:
                    age = f"  (snapshot {int(cc.snapshot_age_seconds)//60} 分前)"
                self.claude_plan.config(
                    text=(f"[{source}]{age}   context: {cc.context_used_pct:.0f}%   "
                          f"local est. cost so far: 5h ${cc.cost_5h_usd:,.2f} / 7d ${cc.cost_7d_usd:,.2f}")
                )
            self._set_bar("claude_5h", cc.pct_5h, cc.block_resets_at)
            self._set_bar("claude_week", cc.pct_7d, cc.week_resets_at)
            extras = []
            if getattr(cc, "fable_available", False):
                extras.append(f"Fable 7d: {cc.fable_pct:.0f}%")
            if cc.models_5h:
                items = sorted(cc.models_5h.items(), key=lambda kv: -kv[1])
                joined = "  ".join(f"{m or '?'}: {n:,}" for m, n in items if n > 0)
                extras.append(f"5h models: {joined}")
            self.claude_models.config(text="   ".join(extras))
        else:
            self.claude_plan.config(text="plan: (Claude データなし)")
            self._set_bar("claude_5h", None, 0)
            self._set_bar("claude_week", None, 0)
            self.claude_models.config(text=cc.note)

    def _set_bar(self, key: str, pct: float | None, reset_epoch: float | int):
        bar = getattr(self, f"{key}_bar")
        pct_lbl = getattr(self, f"{key}_pct")
        reset_lbl = getattr(self, f"{key}_reset")
        if pct is None:
            bar["value"] = 0
            pct_lbl.config(text="—")
            reset_lbl.config(text="")
            return
        bar["value"] = min(100, max(0, pct))
        pct_lbl.config(text=_fmt_pct(pct))
        reset_lbl.config(text=f"reset: {_fmt_reset(float(reset_epoch))}")

    def _schedule_refresh(self):
        if self.win is None or not self.win.winfo_exists():
            return
        interval_ms = max(5, int(self.cfg.get("poll_seconds", 30))) * 1000
        self._after_id = self.win.after(interval_ms, self._tick)

    def _tick(self):
        if self.win is None or not self.win.winfo_exists():
            return
        self._populate()
        self._schedule_refresh()

    def _cancel_refresh(self):
        if self._after_id is not None and self.win is not None and self.win.winfo_exists():
            try:
                self.win.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
