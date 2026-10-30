# HAM Voice Logger

アマチュア無線の音声をPCのマイクから取り込み、ローカル音声認識で文字起こしし、日本向けコールサイン候補を提示するデスクトップアプリです。オペレータが候補を確認してから保存し、QSOログを別操作で管理します。認識とQSO記録は独立しています。

Python 3.12以上（推奨は 3.12 系の最新パッチ）、Windows / macOS / Linux。音声認識はローカル処理（既定は `faster-whisper`。設定から `whisper.cpp` / Moonshine モデルも取得可能）で、有料APIには依存しません。

## できること

- 入力デバイスの常時監視と、音量（RMS）しきい値による発話区間の切り出し
- 日本語既定のローカル文字起こし
- 直接表記および英和フォネティックからのコールサイン候補抽出
- 候補ボタンをクリックしたときだけ確認済み候補として保存
- QSO開始・終了、開始日時の編集、レポート / QSL / Remarks / Code（JCC/JCG等）
- コールサイン入力時のデュープ表示（このアプリ内の過去交信）。前回値が複数あるときは一覧から選択して取り込み
- 周波数 / モード プリセット（コンボ選択・現在値の追加）。レポートプリセット（例: 59/59）。レポートは上下矢印 / ホイールで有効な RS(11–59) / RST(111–599) だけを ±1
- JCC/JCG/区番号（Code）から地名を表示し、空の QTH へ反映（`QTHへ` で上書きも可）
- DXフラグ（手動、または海外局Callbook取得時に自動ON）。履歴は JST / UTC / 両方表示を切替可
- 手動の公開DXCC照会、またはHamQTHアカウントがある場合の詳細Callbook照会
- QSO履歴の複合検索（文言＋モード／DX／QSL／開始日）、ADI／HAMLOG CSV の出力と取込（検索結果を出力、重複はスキップ）
- QSO履歴の削除モード（チェックボックスで複数選択 → 確認ダイアログ後に削除。関連候補の QSO 紐づけは解除）
- QSO履歴のロック（保護列クリック、または ⌘/Ctrl+クリックで飛び飛び複数選択→ロック／解除）。ロック中と記録中は削除モードで選択不可。削除モードでは行クリックで□を飛び飛び選択
- QSO履歴からの編集（行ダブルクリックまたは「選択を編集」→認識・QSOタブで修正→「編集を保存」）
- 最前面の候補ポップアップ（文字起こし＋コールサインを他ウィンドウの上に表示、クリックで確定）
- QSO開始時のQSL既定値（設定→QSOプリセットで送信／受信／経路を保存）
- QTHからCode検索（「地名→Code」）。履歴フィルタに送信待ち／請求中も追加
- ADIFは ASCII ADI / UTF-8 ADI（日本語保持）/ ADX（XML UTF-8）を出力可

ヘッダーの「候補ポップアップ」で、最前面の小さなウィンドウに最新の文字起こしとコールサイン候補を出せます（他ソフト作業中でもクリックで確定可）。音量しきい値、無音終了、認識モデル、ビーム、入力デバイス、ウィンドウ位置・サイズ、最終周波数 / モード、QSOプリセット、時刻表示モード、ポップアップのON/位置は終了時に `~/.ham_voice_logger/settings.json` へ保存し、次回起動で復元します。プリセット編集は「設定」→「QSOプリセット」。CLIの `--model` などは保存値を上書きします。

## セットアップ

Python 3.12以上と Tcl/Tk 対応の環境が必要です。セキュリティ修正のため、3.12 系は最新パッチ（例: 3.12.16 以上）を使ってください。

```bash
# macOS + Homebrew Python の例
brew install python-tk@3.12

# Ubuntu / Debian の例
sudo apt install python3-tk
```

WindowsではTcl/Tkを含む公式CPythonインストーラーを使ってください。

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -U "pip>=26.2.2" certifi
python -m pip install -r requirements.txt
python main.py
```

### 起動方法（OS別）

いずれもプロジェクト直下のランチャーです。先に `.venv` へ依存関係を入れておくと確実です。

| OS | ファイル | 使い方 |
|---|---|---|
| macOS | `start_mac.command` | Finder でダブルクリック（初回は確認ダイアログが出ることがあります）。Dock へドラッグ可 |
| Windows | `start_windows.bat` | Explorer でダブルクリック |
| Linux | `start_linux.sh` | 初回だけ `chmod +x start_linux.sh` のあと、ファイルマネージャで実行するか `./start_linux.sh` |

ターミナルから起動する場合は、これまでどおり `python main.py`（または `.venv` 内の Python）でも構いません。

GUIではヘッダーの「設定」→「認識モデル」でエンジン別モデルをダウンロードし、「このモデルを使う」で選択します。メイン画面の「認識モデル」には取得済みだけが表示されます。認識開始時の自動ダウンロードはしません。

- **faster-whisper**（既定・`requirements.txt` に含む）: `tiny` … `turbo`
- **whisper.cpp**（任意）: ggml `tiny` … `large-v3-turbo`。保存先は `~/.ham_voice_logger/models/whisper.cpp/`。初回取得時に `pywhispercpp` を自動インストール
- **Moonshine**（任意）: `useful-moonshine-onnx` 向け ONNX。日本語は `tiny-ja`（公式 ONNX に `base-ja` は無し）。英語 `tiny` / `base` も取得可。保存先は `~/.ham_voice_logger/models/moonshine/<label>/`。初回取得時にパッケージも自動インストール

CLI（`--cli`）では `--model` に `engine:label`（例: `whisper.cpp:small`）または従来どおりの short name（`small` → `faster-whisper:small`）を指定でき、未取得ならその場で取得できます。

ターミナル画面だけ使う場合:

```bash
python main.py --cli
```

テスト:

```bash
python -m unittest discover -s tests
```

## 使い方

1. 「設定」→「認識モデル」で必要なモデルをダウンロードし、「このモデルを使う」で選択する。メインのコンボでも取得済みモデルを切り替えられる。
2. 「認識開始」でマイク監視を始める。
3. 文字起こしとコールサイン候補が表示される。候補をクリックするとQSO欄のコールサインへ入り、確認済み履歴にも保存される。QSO記録中ならそのQSOにも関連付けられる。
4. 「QSO開始／終了」は認識とは独立。開始日時は開始ボタンを押した時刻で入り、QSO中はカレンダーと時計ダイヤルで編集できる。
5. 「局情報照会」（QSO中は `公開DXCC照会` または `HamQTH照会`）はQSO開始後に手動で実行する。HamQTH未設定なら登録不要の公開DXCC JSON、設定済みならXML Callbookを使う。取得内容を確認してからQSOへ付加する。HamQTH認証は「設定」→「HamQTH」。アプリ終了時にQSOが開いていれば保存を試み、失敗時は再試行／強制終了／キャンセルを選べる。
6. 「表示クリア」は画面上の文字起こしと候補ボタンだけを消す。保存済みデータは消えない。
7. 「QSO履歴」タブで文言検索＋モード／DX／QSL／開始日で絞り込み、「ADIF出力／取込」「HAMLOG CSV／取込」で検索結果の書き出しや外部ログの取り込みができる（同一コール＋開始時刻などは重複スキップ）。大切なログは保護列（🔒）や「ロック」で保護。誤登録の削除は「削除モード」→□で選択→「選択を削除」（ロック／記録中は選択不可）。既存ログの修正は行をダブルクリック（または「選択を編集」）。

コールサイン、周波数、レポート、Codeは全角入力でも保存時に半角へ正規化します。データベースは `~/.ham_voice_logger/logger.sqlite3`、GUI設定は `~/.ham_voice_logger/settings.json` です。JCC/JCG 地名辞書は同梱の `data/jcc_jcg.csv`（JARL 市郡区番号リスト由来のコミュニティ整理版）を参照します。

## Callbook照会

「設定」→「HamQTH」にユーザー名とパスワードを入れると、OSの資格情報ストア（`keyring`）へ保存します。リポジトリやSQLiteには置きません。アプリ実行中のセッションIDはメモリに最大45分保持し、期限切れなら再ログインします。

照会時にはコールサインがHamQTHへ送信されます。保存するのは名前、QTH、国、DXCC、グリッド、CQ/ITUゾーン、詳細、大陸、照会元、取得時刻です。住所、メール、生年、プロフィール本文は保存しません。

公開DXCCはアカウントなしで国、DXCC、CQ/ITU、大陸、公開詳細を返します。認証XMLは名前やQTHなどを返し、大陸はXMLから、DXCC詳細は公開DXCC側から補完します。

仕様: [HamQTH開発者向けドキュメント](https://www.hamqth.com/developers.php)

## ADIF出力

完了済みでコールサインがあるQSOのみ出力します。日時はUTCへ変換します。DXフラグ付きは `COMMENT` 先頭に `DX` を付けます。QSL状態/経路、Code（ADIF `CNTY`）、Remarks 1/2も対応項目へ出します。出力時に ASCII ADI（非ASCII省略）／UTF-8 ADI（日本語保持）／ADX（XML UTF-8）を選べます。取込は履歴タブの「ADIF取込」から。

## Turbo HAMLOG用CSV出力

「QSO履歴」の「HAMLOG CSV」は、Turbo HAMLOG/Win が取り込むクラシックな14列CSV（レコード番号なし・追加取り込み向け）を CP932 / CRLF で書き出します。画面の検索条件に一致する行だけが対象です。

並び: Call, Date(`yy/mm/dd` JST), Time(`HH:MMJ`), His, My, Freq, Mode, Code, G/L, QSL, Name, QTH, Remarks1, Remarks2。DXフラグ付きは Remarks1 先頭に `[DX]` を付けます。

取り込みは HAMLOG の「データのインポート」→「CSVファイルから」。**「データに上書き」は使わず追加**してください。版によって末尾列（hQSL 等）が増えている場合があるので、試し取り込みで確認してください。

### 出入・他ログ互換

| 方向 | 現状 |
|---|---|
| 本アプリ → HAMLOG | 14列 CSV 出力（検索結果、レコード番号なし・追加取込向け） |
| HAMLOG → 本アプリ | 複合検索CSV取込（14列、または先頭レコード番号付き）。CP932/UTF-8 |
| 本アプリ → 他ログ | ADI（ASCII / UTF-8）および ADX（UTC） |
| 他ログ → 本アプリ | ADIF 取込（WSJT-X / JTDX / LoTW 等の共通経路） |

取込はコールサイン＋開始時刻（＋周波数／モード）が一致すると重複スキップします。HAMLOG側でADIFを直接取り込む場合は、従来どおりCSV変換が無難です。

## コールサイン抽出

抽出は日本向け形式（先頭が `7-9` / `J` / `Q` / `M` / `S`、全体6〜7文字、数字を含む）に絞ります。`JA1YAA` や `7L1ABC` は対象、`JA1AA`（5文字）や海外局 `W1AW` は対象外です。候補の優先順は、文字起こし中の直接表記 → フォネティック完全解読 → 一部解読のうち正規表現に合うもの、です。フォネティック慣用語は[欧文・和文通話表](https://www.ne.jp/asahi/yokohama/cwl/phonetics.html)を参考にしています。

## 主要ファイル

| ファイル | 役割 |
|---|---|
| `main.py` | GUI既定。`--cli` でターミナル |
| `gui.py` | Tkinter UI、認識ワーカー、Callbook、QSO、ADIF |
| `app_settings.py` | GUI設定の JSON 永続化 |
| `audio_monitor.py` | マイクキャプチャとRMSによる区間切り出し |
| `decoder.py` | ローカル認識（faster-whisper / whisper.cpp / Moonshine）とモデル取得 |
| `callsign_extractor.py` | 直接表記とフォネティックからの抽出 |
| `callbook.py` | 公開DXCC JSON、認証XML、keyring |
| `log_store.py` | SQLiteとマイグレーション |
| `adif_export.py` / `adif_import.py` | ADI書き出し・取込 |
| `hamlog_csv_export.py` / `hamlog_csv_import.py` | Turbo HAMLOG用CSV書き出し・取込 |
| `tests/` | `unittest` |

`requirements.txt` の `numpy<2` は、NumPy 2.x と faster-whisper の組み合わせで出た警告への回避です。`pywhispercpp` / `useful-moonshine-onnx` はコア依存に含めず、設定画面からの初回取得（または認識開始）時に同じ Python へ自動インストールします。

## 既知の制限

- 発話検知はRMSしきい値のみで、音声/雑音の分類器ではない。実無線での切り出し精度は実機確認が先。
- OS通知は未実装（候補ポップアップはアプリ内の最前面ウィンドウで代替）。
- 自動のクラウド同期や、文字起こしからの自動QSO作成はしない。
