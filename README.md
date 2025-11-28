# CVCutter
Concert Video Cutter ― Fully automated and simple editing software for piano concert

## 概要

このソフトウェアは、コンサートの長時間録画動画を演奏ごとに分割し、プログラムPDFやGoogleフォームの回答と連携して、YouTubeへの自動アップロードまでを一括で行うツールです。

## 主な機能

1.  **動画処理**:
    *   外部マイク音声との自動同期
    *   演奏時の動き（Motion Detection）による自動カット
    *   FFmpegによる高品質エンコード
2.  **ワークフロー自動化**:
    *   PDFパンフレットからの曲目・演奏者情報の抽出
    *   Googleフォーム回答との照合（Gemini AIを利用した類似度判定）
    *   YouTubeへの自動アップロード（メタデータ付与、公開設定反映）
3.  **統合GUI**:
    *   モダンなタブ切り替え式インターフェース
    *   設定の保存機能
    *   プログレスバーによる進捗表示

## セットアップ

### 1. 必須要件
*   Python 3.8以上
*   FFmpeg（パスが通っていること）
*   Google Cloud Platform (GCP) プロジェクト設定（YouTube Data API, Google Forms API）

### 2. インストール
```bash
pip install -r requirements.txt
```

### 3. API設定
ルートディレクトリに以下のファイルを配置してください。
*   `client_secrets.json` (YouTube Data API / Forms API用)

※ PDF解析とマッチングにGemini CLIを使用する場合は、別途インストールと設定が必要です。
```bash
npm install -g @google/generative-ai-cli
# または
pip install google-generativeai
```

## 使い方

### GUIの起動
```bash
python app.py
```

### GUIのタブ説明

1.  **Process Videos**
    *   動画ファイルと音声ファイル（マイク録音）を選択し、キューに追加します。
    *   「START PROCESSING」で同期とカット処理を実行します。
    *   処理結果は `Settings` タブで指定した `Output Dir` に保存されます。

2.  **Workflow & Upload**
    *   **PDF Program**: コンサートのプログラムPDFを選択します。
    *   **Form ID / CSV**: 演奏者情報の収集に使用したGoogleフォームのID、または回答CSVを選択します。
    *   「RUN WORKFLOW」ボタンで、以下の処理を一括で行います。
        1.  PDF解析
        2.  フォーム回答取得
        3.  動画とプログラムのマッチング
        4.  YouTubeへのアップロード（オプション）

3.  **Full Automation**
    *   動画処理からアップロードまでを連続して実行します。
    *   事前にファイルをキューに追加し、PDFなどの設定を完了させてから実行してください。

4.  **Tools**
    *   **Google Form Generator**: 演奏者用のGoogleフォームを自動作成します。

5.  **Settings**
    *   ディレクトリパス、音声ボリューム、同期感度などを設定・保存できます。

## ファイル構成
*   `app.py`: GUIアプリケーションのエントリーポイント
*   `video_processor.py`: 動画処理のコアロジック
*   `run_youtube_workflow.py`: アップロードワークフローの統合スクリプト
*   `config_manager.py`: 設定管理
