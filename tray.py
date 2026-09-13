"""Claude / Codex usage monitor — taskbar widget + system-tray icon.

Architecture:
  - Main thread runs the Tk mainloop (Tk demands the main thread).
  - pystray runs detached on its own thread (run_detached on Windows).
  - A background poller thread refreshes the snapshot, pushes a new icon image
    onto the tray, and asks the widget to redraw (via root.after for safety).

Both UIs share one Tk root.
"""
from __future__ import annotations
import json
import os
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path

import pystray
from pystray import MenuItem, Menu

import config
import parsers
import autostart
import icon as icon_mod
import paths
from dashboard import Dashboard
import taskbar_widget
from taskbar_widget import TaskbarWidget

taskbar_widget.set_dpi_awareness()

HERE = paths.user_data_dir()
APP_NAME = "Claude / Codex Usage Monitor"


class App:
    def __init__(self):
        self.cfg = config.load()
        parsers.set_live_refresh(self.cfg.get("live_refresh_seconds", 60))
        config.write_default_if_missing()

        self.root: tk.Tk | None = None
        self.snapshot: parsers.Snapshot | None = None
        self._snap_lock = threading.Lock()
        self._stop = threading.Event()

        self.dashboard: Dashboard | None = None
        self.widget: TaskbarWidget | None = None
        self._widget_visible = True  # default-on
        self._notified_needs_login = False

        initial_img = icon_mod.make_icon(None, None)
        self.tray = pystray.Icon(
            "claude-codex-tray",
            initial_img,
            APP_NAME,
            menu=self._build_menu(),
        )

    # ---------- snapshot ----------

    def _latest_snapshot(self) -> parsers.Snapshot:
        with self._snap_lock:
            if self.snapshot is None:
                self.snapshot = parsers.collect_all(self.cfg)
            return self.snapshot

    def _refresh_once(self):
        snap = parsers.collect_all(self.cfg)
        with self._snap_lock:
            self.snapshot = snap
        try:
            new_img = icon_mod.make_icon_from_snapshot(snap, self.cfg)
            self.tray.icon = new_img
            self.tray.title = self._tooltip(snap)
        except Exception as e:
            print(f"[tray] icon update failed: {e}")

        self._maybe_notify_needs_login(snap)

        # Redraw widget on the Tk thread.
        if self.root is not None and self.widget is not None:
            self.root.after(0, self._redraw_widget)

    def _redraw_widget(self):
        if self.widget is None:
            return
        # Windows can switch light/dark while we run; pick the palette up again
        # and rebuild only when it actually changed.
        before = taskbar_widget.THEME
        if taskbar_widget.apply_theme(self.cfg) != before:
            print(f"[widget] taskbar theme -> {taskbar_widget.THEME}")
            self.widget.retheme()
        snap = self._latest_snapshot()
        self.widget.render(snap, self.cfg)

    def _maybe_notify_needs_login(self, snap: parsers.Snapshot) -> None:
        need = bool(getattr(snap.claude, "needs_login", False))
        if need and not self._notified_needs_login:
            self._notified_needs_login = True
            try:
                self.tray.notify(
                    "Claude の認証が切れました。通知領域アイコンを右クリック → "
                    "「Claude にログイン…」からサインインしてください。",
                    "Claude / Codex Usage",
                )
            except Exception:
                pass
        elif not need:
            self._notified_needs_login = False

    def _tooltip(self, snap: parsers.Snapshot) -> str:
        parts = [APP_NAME]
        if getattr(snap.claude, "needs_login", False):
            parts.append("★ 要再ログイン: claude auth login")
        if snap.codex.available:
            parts.append(f"Codex  5h {snap.codex.primary_pct:.0f}%  "
                         f"week {snap.codex.secondary_pct:.0f}%  ({snap.codex.plan_type})")
        else:
            parts.append("Codex  (no data)")
        if snap.claude.available:
            parts.append(f"Claude 5h {snap.claude.pct_5h:.0f}%  "
                         f"week {snap.claude.pct_7d:.0f}%  "
                         f"(${snap.claude.cost_5h_usd:.1f} / ${snap.claude.cost_7d_usd:.1f})")
        else:
            parts.append("Claude (no data)")
        return "\n".join(parts)

    def _poller(self):
        interval = max(3, int(self.cfg.get("poll_seconds", 10)))
        while not self._stop.is_set():
            try:
                self._refresh_once()
            except Exception as e:
                print(f"[poller] refresh failed: {e}")
            if self._stop.wait(interval):
                return

    # ---------- menu / actions ----------

    def _build_menu(self) -> Menu:
        return Menu(
            MenuItem("ダッシュボードを開く", self._on_open_dashboard, default=True),
            MenuItem("ウィジェットを表示", self._on_toggle_widget,
                     checked=lambda _i: self._widget_visible),
            MenuItem("Claude にログイン…", self._on_claude_login),
            MenuItem("表示設定…", self._on_open_settings),
            MenuItem("タスクバーに埋め込む", self._on_toggle_embed,
                     checked=lambda _i: self._embed_active()),
            MenuItem("ウィジェットの位置を既定に戻す", self._on_recenter_widget),
            MenuItem("今すぐ更新", self._on_force_refresh),
            Menu.SEPARATOR,
            MenuItem("Windows起動時に自動起動", self._on_toggle_autostart,
                     checked=lambda _i: autostart.is_enabled()),
            MenuItem("診断情報を表示（数値が出ないとき）", self._on_show_diagnostics),
            MenuItem("config.json を開く", self._on_open_config),
            MenuItem("config.json があるフォルダを開く", self._on_open_folder),
            Menu.SEPARATOR,
            MenuItem("終了", self._on_quit),
        )

    def _on_toggle_autostart(self, *_):
        threading.Thread(target=autostart.toggle, daemon=True).start()

    def _on_show_diagnostics(self, *_):
        if self.root is None:
            return

        def build():
            import diagnostics
            win = tk.Toplevel(self.root)
            win.title("診断情報 — Claude / Codex Usage")
            win.geometry("760x600")
            frame = tk.Frame(win)
            frame.pack(fill="both", expand=True, padx=10, pady=(10, 4))
            txt = tk.Text(frame, wrap="none", font=("Consolas", 10))
            ysb = tk.Scrollbar(frame, orient="vertical", command=txt.yview)
            txt.configure(yscrollcommand=ysb.set)
            ysb.pack(side="right", fill="y")
            txt.pack(side="left", fill="both", expand=True)
            txt.insert("1.0", "収集中…")

            btns = tk.Frame(win)
            btns.pack(fill="x", padx=10, pady=(0, 10))

            def do_copy():
                win.clipboard_clear()
                win.clipboard_append(txt.get("1.0", "end-1c"))

            def do_save():
                p = diagnostics.save_report()
                try:
                    os.startfile(str(p))  # type: ignore[attr-defined]
                except Exception:
                    pass

            tk.Button(btns, text="クリップボードにコピー", command=do_copy).pack(side="left")
            tk.Button(btns, text="ファイルに保存して開く", command=do_save).pack(side="left", padx=6)
            tk.Button(btns, text="閉じる", command=win.destroy).pack(side="right")

            def fill():
                try:
                    report = diagnostics.collect()
                except Exception as e:
                    report = f"診断の収集に失敗しました:\n{type(e).__name__}: {e}"
                def apply():
                    if win.winfo_exists():
                        txt.delete("1.0", "end")
                        txt.insert("1.0", report)
                self.root.after(0, apply)

            threading.Thread(target=fill, daemon=True).start()

        self.root.after(0, build)

    def _on_open_dashboard(self, *_):
        if self.root is None or self.dashboard is None:
            return
        self.root.after(0, self.dashboard.show)

    def _on_toggle_widget(self, *_):
        if self.root is None:
            return
        def toggle():
            if self.widget is None:
                return
            if self._widget_visible:
                self.widget.hide()
                self._widget_visible = False
            else:
                self.widget.show()
                self._widget_visible = True
                self._redraw_widget()
        self.root.after(0, toggle)

    def _on_claude_login(self, *_):
        """Hand over to the Claude CLI's sign-in, in its own window."""
        def go():
            if parsers.start_interactive_login():
                try:
                    self.tray.notify(
                        "サインイン画面を開きました。画面の案内に従って"
                        "完了してください。数値は自動で戻ります。",
                        "Claude / Codex Usage")
                except Exception:
                    pass
                self._notified_needs_login = False
            else:
                try:
                    self.tray.notify(
                        "Claude Code が見つかりません。先に Claude Code を"
                        "インストールしてください。",
                        "Claude / Codex Usage")
                except Exception:
                    pass
        threading.Thread(target=go, daemon=True).start()

    def _on_open_settings(self, *_):
        if self.root is None:
            return

        def build():
            win = tk.Toplevel(self.root)
            win.title("表示設定 — Claude / Codex Usage")
            win.resizable(False, False)
            frame = tk.Frame(win, padx=16, pady=14)
            frame.pack(fill="both", expand=True)

            tk.Label(frame, text="タスクバーに表示する項目",
                     font=("Segoe UI", 10, "bold")).pack(anchor="w")
            tk.Label(frame, foreground="#666", justify="left",
                     text="外した側は帯から消え、幅もその分だけ縮みます。\n"
                          "両方を外すことはできません。").pack(anchor="w", pady=(2, 10))

            v_claude = tk.BooleanVar(value=bool(self.cfg.get("show_claude", True)))
            v_codex = tk.BooleanVar(value=bool(self.cfg.get("show_codex", True)))
            warn = tk.Label(frame, foreground="#c04040", text="")

            def guard(changed: tk.BooleanVar, other: tk.BooleanVar):
                if not changed.get() and not other.get():
                    changed.set(True)
                    warn.config(text="少なくとも片方は表示する必要があります")
                else:
                    warn.config(text="")

            tk.Checkbutton(frame, text="Claude Code（5h / F / 7d）",
                           variable=v_claude,
                           command=lambda: guard(v_claude, v_codex)).pack(anchor="w")
            tk.Checkbutton(frame, text="Codex CLI（5h / 7d）",
                           variable=v_codex,
                           command=lambda: guard(v_codex, v_claude)).pack(anchor="w")
            warn.pack(anchor="w", pady=(6, 0))

            btns = tk.Frame(frame)
            btns.pack(fill="x", pady=(14, 0))

            def apply_and_close():
                self._set_sides(v_claude.get(), v_codex.get())
                win.destroy()

            tk.Button(btns, text="OK", width=10,
                      command=apply_and_close).pack(side="right")
            tk.Button(btns, text="キャンセル", width=10,
                      command=win.destroy).pack(side="right", padx=(0, 6))
            # No transient(): the root is withdrawn, and a transient of a
            # withdrawn master never gets mapped.
            win.lift()
            win.focus_force()

        self.root.after(0, build)

    def _set_sides(self, show_claude: bool, show_codex: bool):
        if not (show_claude or show_codex):
            return
        self.cfg["show_claude"] = bool(show_claude)
        self.cfg["show_codex"] = bool(show_codex)
        self._save_cfg_field("show_claude", bool(show_claude))
        self._save_cfg_field("show_codex", bool(show_codex))
        if self.widget is not None:
            self.widget.relayout()

    def _embed_active(self) -> bool:
        return bool(self.widget is not None and self.widget.embedded)

    def _on_toggle_embed(self, *_):
        if self.root is None or self.widget is None:
            return
        def toggle():
            want = not self._embed_active()
            self.widget.set_embedded(want)
            mode = "taskbar" if self.widget.embedded else "float"
            self.cfg["widget_mode"] = mode
            self._save_cfg_field("widget_mode", mode)
            self._redraw_widget()
        self.root.after(0, toggle)

    def _on_recenter_widget(self, *_):
        if self.root is None or self.widget is None:
            return
        def recenter():
            # Wipe the saved spot so the default for the active mode applies.
            self.cfg.pop("widget_position", None)
            self._save_cfg_field("widget_position", None)
            self.cfg.pop("widget_taskbar_offset", None)
            self._save_cfg_field("widget_taskbar_offset", None)
            self.widget.reset_position()
        self.root.after(0, recenter)

    def _on_force_refresh(self, *_):
        threading.Thread(target=self._refresh_once, daemon=True).start()

    def _on_open_config(self, *_):
        path = HERE / "config.json"
        if not path.exists():
            config.write_default_if_missing()
        try:
            import os
            os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception:
            webbrowser.open(path.as_uri())

    def _on_open_folder(self, *_):
        try:
            import os
            os.startfile(str(HERE))  # type: ignore[attr-defined]
        except Exception:
            pass

    def _on_quit(self, *_):
        self._stop.set()
        try:
            self.tray.stop()
        except Exception:
            pass
        if self.root is not None:
            self.root.after(0, self._teardown_tk)

    def _teardown_tk(self):
        try:
            if self.widget is not None:
                self.widget.destroy()
        except Exception:
            pass
        try:
            if self.dashboard is not None:
                self.dashboard.destroy()
        except Exception:
            pass
        if self.root is not None:
            try:
                self.root.quit()
                self.root.destroy()
            except Exception:
                pass

    # ---------- widget callbacks ----------

    def _on_widget_double_click(self):
        if self.dashboard is None or self.root is None:
            return
        self.root.after(0, self.dashboard.show)

    def _on_widget_right_click(self, x_root: int, y_root: int):
        # Build a transient Tk menu at the cursor; the tray menu is pystray-side
        # and not directly reusable here.
        if self.root is None:
            return
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="ダッシュボードを開く", command=self.dashboard.show if self.dashboard else (lambda: None))
        m.add_command(label="今すぐ更新", command=lambda: threading.Thread(target=self._refresh_once, daemon=True).start())
        m.add_separator()
        m.add_command(label="ウィジェットを隠す", command=self._on_toggle_widget)
        m.add_command(label="Claude にログイン…", command=self._on_claude_login)
        m.add_command(label="表示設定…", command=self._on_open_settings)
        m.add_command(label="タスクバーに埋め込む / 解除", command=self._on_toggle_embed)
        m.add_command(label="位置を既定に戻す", command=self._on_recenter_widget)
        m.add_separator()
        m.add_command(label="config.json を開く", command=self._on_open_config)
        m.add_separator()
        m.add_command(label="終了", command=self._on_quit)
        try:
            m.tk_popup(x_root, y_root)
        finally:
            m.grab_release()

    def _make_widget(self) -> TaskbarWidget:
        return TaskbarWidget(
            self.root, self.cfg,
            on_double_click=self._on_widget_double_click,
            on_right_click_menu=self._on_widget_right_click,
            save_position=self._save_widget_position,
            save_taskbar_offset=self._save_widget_offset,
            on_taskbar_lost=self._on_taskbar_lost,
            on_embed_refused=self._on_embed_refused,
        )

    def _on_embed_refused(self, verdict: str):
        """Embedding was measured to be impossible here — the toggle would
        otherwise just flip back with no explanation."""
        why = {
            "invisible": "タスクバーがウィジェットの描画を隠しています",
            "too-bright": "タスクバーの色が明るく、埋め込むと文字が読めません",
        }.get(verdict, verdict)
        try:
            self.tray.notify(
                f"タスクバーに埋め込めないため、帯の上に重ねて表示します（{why}）",
                "Claude / Codex Usage")
        except Exception:
            pass

    def _on_taskbar_lost(self):
        """explorer.exe restarted: the taskbar destroyed our child window.
        Rebuild the widget once the new taskbar exists."""
        if self.root is None:
            return

        def rebuild(attempt: int = 0):
            import taskbar_widget as tw
            if not tw._find_taskbar():
                if attempt < 30:
                    self.root.after(1000, lambda: rebuild(attempt + 1))
                return
            try:
                if self.widget is not None:
                    self.widget.destroy()
            except Exception:
                pass
            self.widget = self._make_widget()
            if not self._widget_visible:
                self.widget.hide()
            self._redraw_widget()

        self.root.after(1000, rebuild)

    def _save_widget_offset(self, right: int):
        self._save_cfg_field("widget_taskbar_offset", {"right": int(right)})
        self.cfg["widget_taskbar_offset"] = {"right": int(right)}

    def _save_widget_position(self, x: int, y: int):
        self._save_cfg_field("widget_position", {"x": x, "y": y})
        # Also keep in-memory cfg in sync
        self.cfg["widget_position"] = {"x": x, "y": y}

    # ---------- config writeback ----------

    def _save_cfg_field(self, key: str, value):
        path = HERE / "config.json"
        try:
            data = {}
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[config] writeback failed: {e}")

    # ---------- entrypoint ----------

    def run(self):
        # Tk root and widgets must live on main thread.
        self.root = tk.Tk()
        self.root.withdraw()
        try:
            from tkinter import ttk
            style = ttk.Style(self.root)
            if "vista" in style.theme_names():
                style.theme_use("vista")
        except Exception:
            pass

        # Pixel geometry has to know the real DPI before any window is built.
        tw_scale = taskbar_widget.init_scale(self.root, self.cfg)
        print(f"[widget] dpi scale = {tw_scale:.2f}")

        self.dashboard = Dashboard(refresh=self._latest_snapshot, cfg=self.cfg)
        self.dashboard.root = self.root  # share the same root

        self.widget = self._make_widget()
        # Initial draw so the bar isn't empty before the first poll completes.
        self._redraw_widget()

        # Tray on its own thread (Windows-only API).
        if hasattr(self.tray, "run_detached"):
            self.tray.run_detached()
        else:
            threading.Thread(target=self.tray.run, daemon=True).start()

        # Background poller.
        threading.Thread(target=self._poller, daemon=True).start()

        # Block on Tk mainloop.
        self.root.mainloop()


def main():
    app = App()
    app.run()


def _log_crash(exc: BaseException) -> Path:
    """A windowed exe has no console, so a crash would otherwise be invisible."""
    import traceback
    log = paths.user_data_dir() / "crash.log"
    try:
        with log.open("a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            f.write("".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__)))
    except Exception:
        pass
    return log


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        path = _log_crash(e)
        try:
            import tkinter.messagebox as mb
            r = tk.Tk()
            r.withdraw()
            mb.showerror(
                "Claude / Codex Usage — 起動エラー",
                f"起動に失敗しました。\n\n{type(e).__name__}: {e}\n\n"
                f"詳細を書き出しました:\n{path}",
            )
            r.destroy()
        except Exception:
            pass
        raise
