# Live2D素材のローカル監査

Python 3.10以上（Pillow 12対応版）。Windows PowerShellでリポジトリのルートから実行します。

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r tools/live2d_asset_audit/requirements.txt
.\.venv\Scripts\python.exe tools/live2d_asset_audit/audit.py --input "C:\parts" --output "C:\audit-reports\run-001"
$LASTEXITCODE
```

任意の設定は `--config tools/live2d_asset_audit/config.example.json`。命名正規表現は拡張子を含むファイル名全体に適用します。`min_pixels` は非透明画素の下限。左右は末尾 `_L` / `_R` を大文字小文字を区別せず照合します。設定の `pairs` は入力ルートからの相対パス（`/`区切り）です。

`pairs` ではWindowsドライブ形式、UNC、先頭 `/`、バックスラッシュ、コロン、`.` / `..` 成分、空成分、制御文字を拒否します。不正設定は検査・出力作成前に終了コード2で停止し、入力値をログやレポートへ出しません。`parts/eye_L.png` のような相対パスを使用してください。

出力は `report.json`、HTMLエスケープした `report.html`、`sha256.json`。終了コードは0=警告なし、1=要確認、2=読み取り失敗/設定・出力エラーです。失敗ファイルがあっても他のファイルを検査します。対象ファイルなしは警告です。

入力は読み取り専用です。出力は入力フォルダ外の新しいディレクトリを指定してください。既存レポートは上書きしません。ネットワーク通信・画像埋め込み・絶対パス出力はありません。レポートには相対ファイル名・ハッシュ・PSDレイヤー名が含まれるため、実素材のレポートは公開前に内容確認が必要です。ツールはレポートをGitHubへ送信しません。

## 検査範囲と制限

- PNG: 寸法、モード、透明度、非透明領域のbbox（右・下端は含まない）、画素数、SHA-256。
- 重複: ファイル全体とRGBA画素を別々に比較。完全透明画素のRGBは無視します。近似画像・鏡像は重複と判定しません。
- 全キャンバス非透明は背景矩形の候補として警告します。正しい背景にも警告が出ます。局所的な矩形混入・白フチ・不要物の意味・隠れ代不足・絵柄の左右差は自動判定しません。目視確認を併用してください。
- 大きすぎる画像はPillowの安全制限で失敗扱いになります。画素解析は画像サイズに応じたメモリを使います。
- PSDは任意対応。`pip install "psd-tools>=1.10,<2"` が必要です。未導入時はハッシュのみ取得し、未検査警告を出します。
- PSDはキャンバスと全レイヤーの名前・種別・表示フラグ・bboxを一覧化。画素レイヤーは保存画素の透明度から空を判定します。グループ、調整、描画不能レイヤーは `empty: null`（不明）。合成結果・マスク・クリッピングの見え方を保証しません。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

テスト画像は一時フォルダで生成し削除します。実素材は不要です。
