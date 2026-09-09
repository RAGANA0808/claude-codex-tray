# Claude / Codex Usage Tray + Widget

**Windows のタスクバーに埋め込んで**、Claude Code と Codex CLI の使用率
（5時間枠・週次枠・Fable 週次枠）をリアルタイム表示する常駐ウィジェット。

*A Windows taskbar-embedded widget that shows your Claude Code / Codex CLI
rate-limit usage (5-hour, weekly, and Fable weekly meters) in real time.
Download the single-file exe from
[Releases](../../releases) — no Python required. Japanese documentation below;
the config keys and code comments are in English.*

![widget preview](preview-widget-on-taskbar.png)

- 左が Claude Code（`5h` / `F`=Fable 週次 / `7d`）、右が Codex（`5h` / `7d`）
- 送信先は公式 API の2つ（`api.anthropic.com` と `chatgpt.com`）だけ。
  いずれも各ツールが保存済みの資格情報で自分の使用量を読むだけで、
  テレメトリや第三者サーバーへの送信は一切なし
- Python 不要の単一 exe（[Releases](../../releases) からダウンロード）

## インストール

### exe を使う（推奨）

1. [Releases](../../releases) から `ClaudeCodexUsage.exe` をダウンロード
2. 好きなフォルダに置いてダブルクリック

前提条件は **Claude Code がログイン済みであること** だけです（`claude` CLI が
PATH にあること）。Codex は使っていれば自動で表示、無ければ「—」になります。

> **「Windows によって PC が保護されました」と出る場合**:
> コード署名をしていないため、ダウンロードした exe の初回起動時に
> Windows SmartScreen のこの青い警告が出ます。異常ではありません。
>
> 1. 警告画面の **「詳細情報」** をクリック（リンクは左上の小さい文字です）
> 2. 現れる **「実行」** ボタンをクリック
>
> 次回からは出ません。別の方法として、exe を右クリック → プロパティ →
> 下部の「許可する」にチェック → OK でも解除できます。
>
> exe は GitHub Actions がこのリポジトリの公開ソースからビルドしたもので、
> 改ざんされていないことは Release の `SHA256SUMS.txt` で検証できます:
> `Get-FileHash ClaudeCodexUsage.exe -Algorithm SHA256`
> 心配な場合は下記「ソースから動かす」の手順で自分でビルドしてください。

### ソースから動かす

PowerShell を開いて:

```powershell
cd <クローンしたフォルダ>
.\setup.ps1
```

これで依存（`pystray` / `Pillow`）のインストール、スタートアップ登録（確認あり）、
起動まで行われます。個別操作は下表のとおり。

| やりたいこと | コマンド |
|---|---|
| 今すぐ起動 | `.\start.bat`（ダブルクリック可）|
| スタートアップ登録 / 解除 | `.\install-startup.bat` / `.\uninstall-startup.bat` |
| statusLine 連携 / 解除 | `.\install-statusline.ps1` / `.\uninstall-statusline.ps1` |
| exe をビルド | `.\build.ps1` → `dist\ClaudeCodexUsage.exe` |

## セキュリティとプライバシー

公開にあたり第三者視点でレビューした要点:

- **トークンの扱い**: 各ツールが保存済みの資格情報を読み取り、**そのツール自身の
  公式 API で自分の使用量を問い合わせるためだけ**に使います。
  - Claude: `~/.claude/.credentials.json`（あれば setup-token）→ `api.anthropic.com`
  - Codex: `~/.codex/auth.json` → `chatgpt.com`（Codex アプリと同じ使用量 API）

  トークンがログ・キャッシュ・診断ファイルに書き出されることはありません
  （キャッシュに残るのは使用率の数値と、Claude トークンの SHA-256 指紋16桁だけ）。
- **ネットワーク**: 接続先は上記2ホストのハードコードされたエンドポイントのみで、
  いずれも読み取り（GET / 1トークンの POST）です。設定やファイルから接続先を
  変えられる経路はありません。Codex への問い合わせは `config.json` の
  `codex_live` を `false` にすれば止められます（その場合はローカルの
  rollout ファイルのみを使います）。
- **診断レポート**（`診断結果.txt`）: 不具合報告用に共有しても安全な内容
  （TTL・スコープ名・使用率）だけを含みます。
- **exe の出所**: Release の exe は GitHub Actions が公開ソースからビルドし、
  SHA-256 チェックサム付きで添付されます。リポジトリにバイナリはコミットしません。

## 2 つの UI

### タスクバー帯ウィジェット

**Windows のタスクバーの中に埋め込まれる**枠なしの帯（既定 500×48 px、DPI に応じて拡大）。

- 色は `緑 < warn% < 黄 < danger% < 赤`（`config.json` で閾値変更可）
- **ドラッグで移動**（埋め込み時はタスクバー内で左右に移動、位置は自動保存）
- **ダブルクリック**で詳細ダッシュボード
- **右クリック**で隠す／埋め込み切替／位置リセット／更新／設定／終了

#### 表示モード（`config.json` の `widget_mode`）

| 値 | 動作 |
|---|---|
| `taskbar`（既定） | ウィンドウを `Shell_TrayWnd` の子にしてタスクバーに埋め込む。タスクバーと一緒に隠れ、全画面アプリの上に残らない |
| `float` | 最前面ウィンドウとしてタスクバーの上に重ねて表示 |

Windows 11 は Deskband（公式のタスクバー拡張 API）を廃止したため、埋め込みは
`SetParent` でタスクバーの子ウィンドウになる方式で実現しています。タスクバーが
見つからない場合は自動的に `float` にフォールバックします。explorer.exe が
再起動するとタスクバーごと子ウィンドウが破棄されるので、検知してウィジェットを
作り直します。

> 埋め込みモードではタスクバー（DPI 対応）の子になるため DWM の自動拡大が効きません。
> そのためアプリ自身を per-monitor DPI aware にし、実 DPI に合わせて描画を拡大しています。

#### 色をタスクバーに合わせる仕組み

Windows 11 のタスクバーは、埋め込んだ子ウィンドウの描画内容を自分の背景
（テーマ・透明効果・壁紙で機体ごとに異なる）に**加算合成**します。そのため
起動時に**自動較正**を行います: 黒と50%グレーを一瞬描画して画面上の実際の
ピクセルを測定し、差し引くべき背景色を導出します。描画が画面に届いていない、
または背景が明るすぎて加算合成では文字が読めないと判定した場合は、自動で
`float` モード（最前面表示）へフォールバックします。

`config.json` の `taskbar_bg` は既定 `"auto"`。固定したい場合は `"#rrggbb"`、
補正を無効にするには `"#000000"` を指定します。

またタスクバーは、子ウィンドウが去った領域を再描画しません。そのため移動・終了の
直前に帯を背景色で塗りつぶしてから動かすことで、残像が残らないようにしています。
（タスクマネージャー等で強制終了するとこの後片付けが走らず残像が出ます。
その場合は explorer.exe を再起動すると消えます。）

#### 上限に達したとき

Codex は枠を使い切ると、時間ウィンドウを持たない別枠（クレジット）へ切り替えた
イベントを最後に1件書きます。この最新イベントだけを読むと数値が消えてしまうため、
**直前の計測値まで遡って表示**し、アイコンに `!` を付けます。復帰時刻の
カウントダウンもそのまま表示されるので、いつ使えるようになるかが分かります。

#### Codex の数値の取得元

**live**（既定）: Codex アプリと同じ使用量 API（`chatgpt.com/backend-api/codex/usage`）を
`~/.codex/auth.json` の資格情報で読みます。アカウント単位の実際の値なので、
Codex アプリの表示と一致します。

**local**（フォールバック）: live が使えないときは `~/.codex/sessions/` の rollout
ファイルを読みます。ただし Codex は**動いている間しか**記録せず、枠はデスクトップ
アプリや Codex Cloud でも消費されるため、実際の使用量より**低く**出ます。
最後の計測から一定時間が過ぎた値にはアイコンへ `~` を付け、ダッシュボードに計測時刻と
経過時間を出します（閾値は `codex_stale_minutes`、既定 10 分）。

local の計測値の選び方も、ファイル更新時刻ではなく「現在も続いている枠の計測値のうち
最大のもの」にしています（更新時刻は書き込みのたびに動くので最新の計測値を指さない。
枠の中で使用量は増える一方なので、最大値が現在値に最も近い）。

### 通知領域アイコン

小さい 64×64 アイコン（同じく二段バー）が通知領域にも出ます。
ウィジェットを誤って閉じたときや、右クリックメニューを出したいとき用。

## アイコンのカスタマイズ

リポジトリに入っているのは中立デザイン（C / X の文字タイル）です。
アプリ（または exe）と同じフォルダに `app-claude-custom.png` /
`app-codex-custom.png` を置くと、そちらが優先して表示されます
（git 管理外なので、好きな画像を使えます）。

## Claude バーの数値について

Anthropic の使用量 API（`/api/oauth/usage`）と、`/v1/messages` 応答の
rate-limit ヘッダーから **live 値** を取得します。`F`（Fable 週次枠）は
`user:profile` スコープ付きトークンがあるときだけ表示されます。

live 取得ができない環境では **キャリブレーション済みローカル集計** に
フォールバックします:

- `~/.claude/projects/**/*.jsonl` の usage を集計
- `cache_read_input_tokens` は rate limit に算入されないので除外
- `input + output + cache_creation` を USD 換算し、
  `config.json` の `plan_limits_usd` と比較

推定モードのときはウィジェットに `~` マークが付きます。ズレを感じたら
Claude Code 内で `/usage` の公式パネルと見比べ、`window_5h` / `window_7d` を
調整してください。

## 設定

`config.json` を編集（無ければ初回起動時に exe と同じフォルダへ自動生成。
書込不可の場所では `%LOCALAPPDATA%\claude-codex-tray\` を使います）。

| キー | 役割 |
|---|---|
| `poll_seconds` | 画面の更新間隔（秒）。既定 10。Codex はローカルファイル読みなので、この間隔でそのまま更新される |
| `live_refresh_seconds` | Claude のメーターを API から取り直す間隔（秒）。既定 60、最短 15。1回につき極小の API リクエストを1件使う |
| `claude_plan` | `pro` / `max5` / `max20` / `custom` |
| `plan_limits_usd.<plan>.window_5h` / `window_7d` | 推定モード用のプラン上限 USD |
| `pricing.<model>` | モデル別の per-Mtok 料金 |
| `thresholds.warn` / `danger` | 色が黄／赤に切り替わる % |
| `widget_mode` | `taskbar`（埋め込み）/ `float`（最前面表示） |
| `taskbar_bg` | `auto`（起動時に実測・既定）/ `#rrggbb`（固定）/ `#000000`（補正なし） |
| `codex_stale_minutes` | local 取得時、計測値が何分古くなったら `~` を付けるか。既定 10 |
| `codex_live` | Codex の使用量 API を使うか。`false` でローカルファイルのみ。既定 `true` |

## 仕組み（参考）

- **Codex**: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` の最新ファイルから
  最後の `token_count` イベントを読み、`rate_limits.primary/secondary.used_percent`
  を表示。
- **Claude Code**: 上記の live API を優先し、フォールバックとして
  `~/.claude/projects/**/*.jsonl` を走査（`requestId` で dedupe）。
  5時間ブロックは ccusage と同じ「直前メッセージから 5h ギャップで新ブロック」
  ルール。

## 開発

```powershell
pip install pystray Pillow pytest ruff
ruff check .
pytest tests/
python make_icon.py   # アイコン一式を再生成
.\build.ps1           # exe をビルド
```

CI（GitHub Actions）が push / PR ごとに lint・テスト・exe ビルドを検証します。
`v*` タグを push すると Release が自動作成され、exe と SHA256SUMS が添付されます。

## ファイル

| ファイル | 役割 |
|---|---|
| `tray.py`         | エントリ。pystray + Tk mainloop + ウィジェット起動 |
| `parsers.py`      | Codex / Claude の使用量取得（API + ローカル集計）|
| `paths.py`        | スクリプト実行と exe 実行でパスを出し分ける |
| `autostart.py`    | スタートアップ登録のトグル |
| `icon.py`         | 通知領域アイコン PNG 生成 |
| `taskbar_widget.py` | タスクバー埋め込みウィジェット本体 |
| `dashboard.py`    | ダブルクリックで開く詳細ウィンドウ |
| `diagnostics.py`  | 診断レポート生成（数値が出ないときの自己診断）|
| `make_icon.py`    | アイコン一式（app-*.png / app-icon.ico）を生成 |
| `ClaudeCodexUsage.spec` / `build.ps1` | 配布用 exe のビルド |
| `config.py`       | 設定の読み込みと既定値 |
| `tests/`          | ユニットテスト（CI で実行）|
| `smoke_check.py`  | 実データに対する手動スモークチェック |

## License

MIT
