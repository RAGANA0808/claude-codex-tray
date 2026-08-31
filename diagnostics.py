"""Self-diagnosis report — lets a user on any machine see exactly why numbers
are missing, without needing a console or a developer present."""
from __future__ import annotations
import json
import os
import platform
import time
from pathlib import Path

import paths


def _fmt_age(ts: float) -> str:
    if not ts:
        return "-"
    age = time.time() - float(ts)
    if age < 60:
        return f"{age:.0f}秒前"
    if age < 3600:
        return f"{age/60:.0f}分前"
    return f"{age/3600:.1f}時間前"


def collect() -> str:
    import parsers

    L: list[str] = []
    add = L.append

    if parsers.needs_login():
        add("=" * 52)
        add("★ 再ログインが必要です")
        add("   認証の有効期限が切れ、自動更新もできない状態です。")
        add("   PowerShell で次を実行してください:")
        add("       claude auth login")
        add("=" * 52)
        add("")

    add("=== 実行環境 ===")
    add(f"  OS         : {platform.system()} {platform.release()}")
    add(f"  実行形態   : {'exe (frozen)' if paths.is_frozen() else 'python script'}")
    add(f"  設定フォルダ: {paths.user_data_dir()}")
    add("")

    add("=== Claude CLI ===")
    cli = parsers._find_claude_cli()
    if cli:
        add(f"  検出       : {cli}")
    else:
        add("  検出       : ★見つかりません")
        add("               → Claude Code をインストールしてください")
    add("")

    add("=== 認証トークン ===")
    cred = Path.home() / ".claude" / ".credentials.json"
    if not cred.exists():
        add(f"  Desktop    : ★ファイルなし ({cred})")
        add("               → PowerShell で  claude auth login  を実行")
    else:
        try:
            o = json.loads(cred.read_text(encoding="utf-8")).get("claudeAiOauth") or {}
            ttl = (int(o.get("expiresAt", 0)) - int(time.time() * 1000)) // 60000
            scopes = o.get("scopes") or []
            state = f"有効 (あと{ttl}分)" if ttl > 0 else f"★期限切れ ({-ttl}分前)"
            add(f"  Desktop    : {state}")
            add(f"  scope      : {'user:profile あり' if 'user:profile' in scopes else '★user:profile なし → Fable取得不可'}")
        except Exception as e:
            add(f"  Desktop    : ★読み取り失敗 {e}")
    ll = parsers._find_long_lived_token()
    add(f"  setup-token: {'あり' if ll else 'なし (通常はこれで問題ありません)'}")
    try:
        st = json.loads(parsers._OAUTH_USAGE_STATE.read_text(encoding="utf-8"))
    except Exception:
        st = None
    if st and parsers._oauth_usage_forbidden_recently():
        left = (parsers._OAUTH_USAGE_FORBIDDEN_TTL
                - (time.time() - float(st.get("forbidden_at", 0)))) / 3600
        add(f"  Fable取得   : ★停止中 (403でロック / 解除まであと{left:.1f}時間)")
    else:
        add("  Fable取得   : ロックなし")
    add("")

    add("=== タスクバー埋め込み ===")
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        light = winreg.QueryValueEx(k, "SystemUsesLightTheme")[0]
        trans = winreg.QueryValueEx(k, "EnableTransparency")[0]
        add(f"  テーマ     : {'ライト' if light else 'ダーク'} / 透明効果 {'オン' if trans else 'オフ'}")
    except Exception:
        add("  テーマ     : (取得できず)")
    cal = Path.home() / ".claude" / "cache" / "tray-embed-calibration.json"
    if cal.exists():
        try:
            d = json.loads(cal.read_text(encoding="utf-8"))
            add(f"  色較正     : {d.get('verdict')} offset={d.get('offset')} "
                f"black={d.get('black')} gray={d.get('gray')} ({_fmt_age(d.get('at', 0))})")
        except Exception as e:
            add(f"  色較正     : ★読み取り失敗 {e}")
    else:
        add("  色較正     : まだ実行されていません")
    add("")

    add("=== データソース ===")
    for label, key, sub in [("Claude ログ", "claude_dir", ".claude/projects"),
                            ("Codex ログ", "codex_dir", ".codex/sessions")]:
        base = Path.home() / Path(sub)
        if base.exists():
            try:
                n = sum(1 for _ in base.rglob("*.jsonl"))
            except Exception:
                n = -1
            add(f"  {label} : あり ({n} ファイル)" if n >= 0 else f"  {label} : あり")
        else:
            add(f"  {label} : なし ({base})")
    add("")

    add("=== 直近のAPI取得 ===")
    lc = Path.home() / ".claude" / "cache" / "tray-live-cache.json"
    if lc.exists():
        try:
            d = json.loads(lc.read_text(encoding="utf-8"))
            add(f"  取得時刻   : {_fmt_age(d.get('fetched_at', 0))}")
            add(f"  取得経路   : {d.get('source', '-')}")
            add(f"  5h / 7d    : {d.get('five_hour_pct', '-')}% / {d.get('seven_day_pct', '-')}%")
            add(f"  Fable      : {d.get('fable_pct', '取得できていません')}")
        except Exception as e:
            add(f"  ★読み取り失敗 {e}")
    else:
        add("  ★まだ一度も取得できていません")
    add("")

    add("=== 今すぐ実測 ===")
    try:
        tok, src = parsers._resolve_live_token()
        add(f"  トークン解決: {src}")
        if not tok:
            add("  ★トークンが取得できません。claude auth login を実行してください")
        else:
            res = parsers._fetch_live_rate_limits()
            if res is None:
                add("  ★API取得に失敗しました（ネットワーク/認証を確認）")
            else:
                add(f"  5h={res.get('five_hour_pct')}%  7d={res.get('seven_day_pct')}%  "
                    f"Fable={res.get('fable_pct', 'なし')}")
    except Exception as e:
        add(f"  ★例外: {type(e).__name__}: {e}")
    add("")

    add("=== ログ ===")
    log = Path.home() / ".claude" / "cache" / "tray-cli-refresh.log"
    if log.exists():
        try:
            lines = log.read_text(encoding="utf-8", errors="ignore").splitlines()
            for ln in lines[-6:]:
                add(f"  {ln[:150]}")
        except Exception:
            pass
    else:
        add("  (なし)")

    return "\n".join(L)


def save_report() -> Path:
    out = paths.user_data_dir() / "診断結果.txt"
    try:
        out.write_text(collect(), encoding="utf-8")
    except Exception:
        out = Path(os.environ.get("TEMP", ".")) / "claude-codex-tray-diagnostics.txt"
        out.write_text(collect(), encoding="utf-8")
    return out
