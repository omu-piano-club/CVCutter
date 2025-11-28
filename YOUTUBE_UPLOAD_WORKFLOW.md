# YouTube動画アップロードワークフロー

このドキュメントでは、コンサート動画を処理してYouTubeにアップロードするまでの全体のワークフローを説明します。
**GUI (`app.py`) を使用する場合は、これらの手順は自動化されます。** 手動でコマンドラインから実行する場合の参考にしてください。

## 概要

```
1. 動画録画・音声録音
   ↓
2. 動画分割・音声合成（app.py または video_processor.py）
   ↓
3. PDFパンフレット解析（pdf_parser.py）
   ↓
4. Googleフォーム回答収集（google_form_connector.py）
   ↓
5. データマッピング（video_mapper.py）
   ↓
6. YouTubeアップロード（youtube_uploader.py）
```

---

## GUIを使用した実行（推奨）

`app.py` を起動することで、以下のすべてのステップをグラフィカルに操作できます。

```bash
python app.py
```

*   **Process Videosタブ**: ステップ1を実行します。
*   **Workflow & Uploadタブ**: ステップ2〜6を実行します。
*   **Full Automationタブ**: 全ステップを連続実行します。

---

## コマンドラインでの手動実行

### ステップ1: 動画の分割と音声合成

GUIを使用しない場合、スクリプトとして呼び出す必要がありますが、現在は `app.py` 経由または `video_processor.py` をライブラリとして使用することが推奨されます。
旧 `main.py` の機能は `video_processor.py` に移行されましたが、直接実行するためのCLIラッパーは現在 `app.py` に統合されています。

### ステップ2: PDFパンフレットの解析

```bash
python pdf_parser.py path/to/concert_program.pdf --output program_data.json
```

### ステップ3: Googleフォーム回答の取得

```bash
python google_form_connector.py form_responses.csv --output form_data.json
```

### ステップ4: データマッピング

```bash
python video_mapper.py \
  --program-json program_data.json \
  --form-json form_data.json \
  --video-dir output \
  --output upload_metadata.json
```

### ステップ5: YouTubeへのアップロード

```bash
python youtube_uploader.py --video-dir output --metadata upload_metadata.json
```

---

## トラブルシューティング

### 動画数とプログラム数が一致しない
GUIのコンソールログ、またはログファイルを確認してください。

### GUIがフリーズする
処理は別スレッドで実行されていますが、非常に重い処理（動画エンコードなど）中は応答が遅くなることがあります。コンソールの出力が動いているか確認してください。

### "ImportError: No module named..."
必要なライブラリがインストールされているか確認してください。
```bash
pip install -r requirements.txt
```
