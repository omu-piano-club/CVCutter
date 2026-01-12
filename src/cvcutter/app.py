import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
from PIL import Image
import threading
import sys
import os
import queue
import time
from pathlib import Path
import json
from datetime import datetime

# Logic imports
from .config_manager import ConfigManager
from . import video_processor
from .create_google_form import create_concert_form, authenticate_forms_api, save_form_config, load_form_history
from .video_mapper import get_video_files_sorted, map_program_to_videos, map_with_form_responses, generate_upload_metadata
from .google_form_connector import FormResponseParser
from .pdf_parser import parse_concert_pdf
from .gemini_utils import configure_gemini
from . import youtube_uploader

# --- Console Redirector ---
class ConsoleRedirector:
    def __init__(self, text_widget):
        self.text_widget = text_widget
        self.queue = queue.Queue()
        self.update_interval = 50
        self._update_widget()

    def write(self, string):
        self.queue.put(string)

    def flush(self):
        pass

    def _update_widget(self):
        try:
            while True:
                text = self.queue.get_nowait()
                self.text_widget.configure(state='normal')
                self.text_widget.insert(tk.END, text)
                self.text_widget.see(tk.END)
                self.text_widget.configure(state='disabled')
        except queue.Empty:
            pass
        self.text_widget.after(self.update_interval, self._update_widget)

class ConcertVideoApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("CVCutter - コンサート動画編集・アップロード")
        self.geometry("1100x800")
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        # Initialize Config
        self.config_manager = ConfigManager()
        self.config = self.config_manager.config

        # UI State
        self.queue_data = []
        self.mapping_results = []
        self.v_checkboxes = []
        self.a_checkboxes = []
        self.program_data = None # To store parsed PDF data

        # Layout
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar
        self.sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(7, weight=1)

        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="CVCutter", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        self.btn_form = ctk.CTkButton(self.sidebar_frame, text="0. フォーム生成", command=lambda: self.select_tab("form"))
        self.btn_form.grid(row=1, column=0, padx=20, pady=10)

        self.btn_process = ctk.CTkButton(self.sidebar_frame, text="1. 動画処理", command=lambda: self.select_tab("process"))
        self.btn_process.grid(row=2, column=0, padx=20, pady=10)

        self.btn_preview = ctk.CTkButton(self.sidebar_frame, text="2. プレビュー & 紐付け", command=lambda: self.select_tab("preview"))
        self.btn_preview.grid(row=3, column=0, padx=20, pady=10)

        self.btn_upload = ctk.CTkButton(self.sidebar_frame, text="3. アップロード", command=lambda: self.select_tab("upload"))
        self.btn_upload.grid(row=4, column=0, padx=20, pady=10)

        self.btn_settings = ctk.CTkButton(self.sidebar_frame, text="設定", command=lambda: self.select_tab("settings"))
        self.btn_settings.grid(row=5, column=0, padx=20, pady=10)

        self.btn_help = ctk.CTkButton(self.sidebar_frame, text="ヘルプ", command=lambda: self.select_tab("help"))
        self.btn_help.grid(row=6, column=0, padx=20, pady=10)

        # Main Content
        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(0, weight=1)

        self.tabs = {}
        self._build_form_tab()
        self._build_processing_tab()
        self._build_preview_tab()
        self._build_upload_tab()
        self._build_settings_tab()
        self._build_help_tab()

        self.select_tab("form")

        # Console (Bottom)
        self.console_frame = ctk.CTkFrame(self, height=150)
        self.console_frame.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=20, pady=(0, 20))
        self.console_frame.grid_columnconfigure(0, weight=1)
        self.console_frame.grid_rowconfigure(0, weight=1)

        self.console_text = ctk.CTkTextbox(self.console_frame, font=("Consolas", 12))
        self.console_text.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        self.console_text.configure(state="disabled")

        sys.stdout = ConsoleRedirector(self.console_text)
        sys.stderr = sys.stdout
        
        # Configure logging to use the redirected stdout
        import logging
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            stream=sys.stdout
        )

    def select_tab(self, name):
        for tab in self.tabs.values():
            tab.grid_remove()
        self.tabs[name].grid(row=0, column=0, sticky="nsew")

    def _build_processing_tab(self):
        tab = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.tabs["process"] = tab
        tab.grid_columnconfigure((0, 1), weight=1)

        # File Selection
        sel_frame = ctk.CTkFrame(tab)
        sel_frame.grid(row=0, column=0, columnspan=2, sticky="nsew", pady=(0, 10), padx=5)
        sel_frame.grid_columnconfigure((0, 1), weight=1)

        # Video List
        v_frame = ctk.CTkFrame(sel_frame)
        v_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        ctk.CTkLabel(v_frame, text="ビデオファイル", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        self.v_scroll = ctk.CTkScrollableFrame(v_frame, fg_color="#2b2b2b", height=200)
        self.v_scroll.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        ctk.CTkButton(v_frame, text="ビデオを追加", command=self._add_videos).pack(pady=5)

        # Audio List
        a_frame = ctk.CTkFrame(sel_frame)
        a_frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        ctk.CTkLabel(a_frame, text="マイク音声 (任意)", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        self.a_scroll = ctk.CTkScrollableFrame(a_frame, fg_color="#2b2b2b", height=200)
        self.a_scroll.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        ctk.CTkButton(a_frame, text="音声を追加", command=self._add_audios).pack(pady=5)

        # Queue
        q_frame = ctk.CTkFrame(tab)
        q_frame.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=10, padx=5)
        ctk.CTkLabel(q_frame, text="処理キュー", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        self.q_list = tk.Listbox(q_frame, bg="#2b2b2b", fg="white", height=5, borderwidth=0, highlightthickness=0)
        self.q_list.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        btn_row = ctk.CTkFrame(q_frame, fg_color="transparent")
        btn_row.pack(fill=tk.X, padx=10, pady=5)
        ctk.CTkButton(btn_row, text="選択項目をキューに追加", command=self._match_and_queue).pack(side=tk.LEFT, padx=5)
        ctk.CTkButton(btn_row, text="キューをクリア", command=self._clear_queue).pack(side=tk.LEFT, padx=5)

        # Run
        self.proc_btn = ctk.CTkButton(tab, text="動画処理を開始", height=50, font=ctk.CTkFont(size=16, weight="bold"), command=self._run_processing)
        self.proc_btn.grid(row=2, column=0, columnspan=2, sticky="ew", pady=20, padx=5)

        self.progress_bar = ctk.CTkProgressBar(tab)
        self.progress_bar.grid(row=3, column=0, columnspan=2, sticky="ew", padx=5)
        self.progress_bar.set(0)
        self.progress_label = ctk.CTkLabel(tab, text="待機中")
        self.progress_label.grid(row=4, column=0, columnspan=2)

    def _build_preview_tab(self):
        tab = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.tabs["preview"] = tab
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        # Inputs
        in_frame = ctk.CTkFrame(tab)
        in_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        
        ctk.CTkLabel(in_frame, text="プログラムPDF:").grid(row=0, column=0, padx=10, pady=5, sticky="w")
        self.pdf_var = ctk.StringVar(value=self.config['paths']['pdf_path'])
        ctk.CTkEntry(in_frame, textvariable=self.pdf_var, width=400).grid(row=0, column=1, padx=10, pady=5)
        ctk.CTkButton(in_frame, text="参照", width=80, command=lambda: self._browse_file(self.pdf_var, "pdf_path")).grid(row=0, column=2, padx=10, pady=5)

        ctk.CTkLabel(in_frame, text="フォームID:").grid(row=1, column=0, padx=10, pady=5, sticky="w")
        self.form_id_var = ctk.StringVar(value=self.config['paths']['form_id'])
        ctk.CTkEntry(in_frame, textvariable=self.form_id_var, width=400).grid(row=1, column=1, padx=10, pady=5)

        ctk.CTkButton(in_frame, text="マッピングを生成", command=self._run_mapping).grid(row=2, column=1, pady=10)

        # Preview Scrollable
        self.preview_area = ctk.CTkScrollableFrame(tab, label_text="マッピング プレビュー")
        self.preview_area.grid(row=1, column=0, sticky="nsew")

    def _build_upload_tab(self):
        tab = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.tabs["upload"] = tab
        
        ctk.CTkLabel(tab, text="YouTube アップロード", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=20)
        
        self.skip_upload_var = ctk.BooleanVar(value=self.config['workflow']['skip_upload'])
        ctk.CTkCheckBox(tab, text="実際のアップロードをスキップ（メタデータ生成のみ）", variable=self.skip_upload_var).pack(pady=10)

        self.upload_btn = ctk.CTkButton(tab, text="アップロード ワークフローを開始", height=60, command=self._run_workflow)
        self.upload_btn.pack(pady=20, padx=50, fill=tk.X)

        result_frame = ctk.CTkFrame(tab)
        result_frame.pack(pady=10, padx=20, fill=tk.BOTH, expand=True)
        result_frame.grid_columnconfigure(0, weight=1)
        result_frame.grid_rowconfigure(1, weight=1)
        
        btn_frame = ctk.CTkFrame(result_frame, fg_color="transparent")
        btn_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=5)
        
        ctk.CTkButton(btn_frame, text="結果をクリップボードにコピー", command=self._copy_results_to_clipboard).pack(side=tk.LEFT, padx=5)
        ctk.CTkButton(btn_frame, text="結果をファイルに保存", command=self._export_results_to_file).pack(side=tk.LEFT, padx=5)

        self.upload_result_area = ctk.CTkScrollableFrame(result_frame, label_text="アップロード結果")
        self.upload_result_area.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)

    def _build_settings_tab(self):
        tab = ctk.CTkScrollableFrame(self.main_frame, label_text="システム設定")
        self.tabs["settings"] = tab
        
        self.setting_vars = {}
        
        # Google Auth
        auth_frame = ctk.CTkFrame(tab)
        auth_frame.pack(fill=tk.X, padx=10, pady=10)
        ctk.CTkLabel(auth_frame, text="Google API 認証", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        
        row = ctk.CTkFrame(auth_frame, fg_color="transparent")
        row.pack(fill=tk.X, padx=5, pady=2)
        ctk.CTkLabel(row, text="Client Secrets JSON:", width=150, anchor="w").pack(side=tk.LEFT)
        self.secrets_var = ctk.StringVar(value=str(Path(sys.executable).parent / "client_secrets.json" if getattr(sys, 'frozen', False) else Path("client_secrets.json").absolute()))
        ctk.CTkEntry(row, textvariable=self.secrets_var, width=300).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ctk.CTkButton(row, text="参照", width=60, command=lambda: self._browse_file(self.secrets_var)).pack(side=tk.LEFT, padx=5)
        
        auth_btn_frame = ctk.CTkFrame(auth_frame, fg_color="transparent")
        auth_btn_frame.pack(pady=10)
        
        self.btn_auth_forms = ctk.CTkButton(auth_btn_frame, text="Google フォーム認証", command=lambda: self._google_login("forms"))
        self.btn_auth_forms.pack(side=tk.LEFT, padx=5)
        
        self.btn_auth_youtube = ctk.CTkButton(auth_btn_frame, text="YouTube アップロード認証", command=lambda: self._google_login("youtube"))
        self.btn_auth_youtube.pack(side=tk.LEFT, padx=5)

        # Paths
        self._add_setting_group(tab, "ディレクトリ設定", [
            ("出力ディレクトリ", "paths", "output_dir", "dir"),
            ("一時ディレクトリ", "paths", "temp_dir", "dir")
        ])
        
        # Processing
        self._add_setting_group(tab, "処理パラメータ", [
            ("ビデオ音量 (0-1)", "processing", "video_audio_volume"),
            ("マイク音量 (>1)", "processing", "mic_audio_volume"),
            ("最小演奏時間 (秒)", "processing", "min_duration_seconds"),
            ("GPUアクセラレーション", "processing", "use_gpu", "bool")
        ])

        # Gemini AI Auth
        gemini_frame = ctk.CTkFrame(tab)
        gemini_frame.pack(fill=tk.X, padx=10, pady=10)
        ctk.CTkLabel(gemini_frame, text="Gemini AI 設定", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        
        row1 = ctk.CTkFrame(gemini_frame, fg_color="transparent")
        row1.pack(fill=tk.X, padx=5, pady=2)
        ctk.CTkLabel(row1, text="AI紐付けを使用:", width=150, anchor="w").pack(side=tk.LEFT)
        self.use_gemini_var = ctk.BooleanVar(value=self.config['workflow']['use_gemini'])
        ctk.CTkCheckBox(row1, text="", variable=self.use_gemini_var).pack(side=tk.LEFT)

        row2 = ctk.CTkFrame(gemini_frame, fg_color="transparent")
        row2.pack(fill=tk.X, padx=5, pady=2)
        ctk.CTkLabel(row2, text="Gemini API Key:", width=150, anchor="w").pack(side=tk.LEFT)
        self.gemini_key_var = ctk.StringVar(value=self.config['workflow'].get('gemini_api_key', ''))
        ctk.CTkEntry(row2, textvariable=self.gemini_key_var, width=300, show="*").pack(side=tk.LEFT, fill=tk.X, expand=True)

        row3 = ctk.CTkFrame(gemini_frame, fg_color="transparent")
        row3.pack(fill=tk.X, padx=5, pady=2)
        ctk.CTkLabel(row3, text="Gemini モデル:", width=150, anchor="w").pack(side=tk.LEFT)
        self.gemini_model_var = ctk.StringVar(value=self.config['workflow'].get('gemini_model', 'gemini-2.5-flash'))
        models = [
            "gemini-2.5-flash-lite", "gemini-2.5-flash-tts", "gemini-2.5-flash",
            "gemini-3-flash", "gemini-robotics-er-1.5-preview", "gemma-3-12b",
            "gemma-3-1b", "gemma-3-27b", "gemma-3-2b", "gemma-3-4b"
        ]
        ctk.CTkOptionMenu(row3, variable=self.gemini_model_var, values=models, width=300).pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        ctk.CTkButton(gemini_frame, text="APIキーを検証",
                      command=self._verify_gemini).pack(pady=10)

        ctk.CTkButton(tab, text="設定をすべて保存", command=self._save_settings).pack(pady=20)

    def _add_setting_group(self, parent, title, items):
        frame = ctk.CTkFrame(parent)
        frame.pack(fill=tk.X, padx=10, pady=10)
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(weight="bold")).pack(pady=5)
        
        for label, section, key, *opts in items:
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill=tk.X, padx=5, pady=2)
            ctk.CTkLabel(row, text=label, width=150, anchor="w").pack(side=tk.LEFT)
            
            val = self.config[section].get(key, "")
            if opts and opts[0] == "bool":
                var = ctk.BooleanVar(value=bool(val))
                ctk.CTkCheckBox(row, text="", variable=var).pack(side=tk.LEFT)
            else:
                var = ctk.StringVar(value=str(val))
                ctk.CTkEntry(row, textvariable=var, width=300).pack(side=tk.LEFT, fill=tk.X, expand=True)
                if opts and opts[0] == "dir":
                    ctk.CTkButton(row, text="参照", width=60, command=lambda v=var: self._browse_dir(v)).pack(side=tk.LEFT, padx=5)
            
            self.setting_vars[(section, key)] = var

    def _build_form_tab(self):
        tab = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.tabs["form"] = tab
        tab.grid_columnconfigure(0, weight=1)

        # フォーム作成フレーム
        create_frame = ctk.CTkFrame(tab)
        create_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=20)
        create_frame.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(create_frame, text="Google フォーム自動生成", font=ctk.CTkFont(weight="bold")).pack(pady=10)
        
        self.tool_title_var = ctk.StringVar(value="コンサート出演者情報入力フォーム")
        ctk.CTkEntry(create_frame, textvariable=self.tool_title_var, placeholder_text="フォームのタイトル", width=400).pack(pady=5, padx=20, fill="x")
        ctk.CTkButton(create_frame, text="新しいフォームを作成", command=self._create_form).pack(pady=10)

        # フォーム履歴フレーム
        history_frame = ctk.CTkScrollableFrame(tab, label_text="作成済みフォーム履歴")
        history_frame.grid(row=1, column=0, sticky="nsew", padx=20, pady=10)
        tab.grid_rowconfigure(1, weight=1)

        self.form_history_frame = history_frame
        self._update_form_history()

    def _build_help_tab(self):
        tab = ctk.CTkScrollableFrame(self.main_frame, label_text="ヘルプ & 使い方")
        self.tabs["help"] = tab
        
        help_text = """
【CVCutter：コンサート動画編集・アップロード自動化ツール】

本アプリは、コンサートの長時間録画から各演奏を自動で切り出し、外部マイク音声との合成、
　　　　　　PDFプログラムとアンケート回答に基づくタイトル付け、そしてYouTubeへの自動投稿までを一気通貫で行うツールです。

---
【1. 事前準備：Google Cloud Platform (GCP) の設定】
YouTube への自動投稿やアンケート回答の取得（Forms API）には、Google Cloud での「認証情報」作成が必要です。

A. プロジェクトの作成と API の有効化
1. [Google Cloud Console](https://console.cloud.google.com/) にアクセスし、新しいプロジェクトを作成します。
2. 左メニューの「API とサービス」 > 「ライブラリ」を開き、以下の 2 つを検索して「有効化」してください：
   - 「YouTube Data API v3」
   - 「Google Forms API」

B. OAuth 同意画面の設定
1. 「API とサービス」 > 「OAuth 同意画面」を開きます。
2. ユーザータイプで「外部」を選択し「作成」を押します。
3. アプリ名（例: CVCutter）やユーザーサポートメール等の必須項目を入力して保存します。
4. スコープの設定：
   - 「スコープを追加または削除」から、以下の 3 つを検索して追加してください。
   - `https://www.googleapis.com/auth/forms.body` (フォーム作成用)
   - `https://www.googleapis.com/auth/forms.responses.readonly` (回答取得用)
   - `https://www.googleapis.com/auth/youtube.upload` (YouTube アップロード用)
5. 【最重要】「テストユーザー」セクションで、ご自身の Google アカウント（Gmail アドレス）を必ず追加してください。

C. 認証情報の作成
1. 「API とサービス」 > 「認証情報」を開きます。
2. 「認証情報を作成」 > 「OAuth クライアント ID」を選択します。
3. アプリケーションの種類で「デスクトップ アプリ」を選択。
4. 名前は任意（例: CVCutter Client）で作成します。
5. 作成後、リストの右側にある「JSON をダウンロード」ボタン（↓）を押し、ファイルを保存します。
6. 本アプリの「設定」タブを開き、「Client Secrets JSON」の参照ボタンから、ダウンロードしたファイルを選択してください。

---
【2. 事前準備：Gemini AI (自動解析) の設定】
PDF プログラムの読み取りや、演奏情報とアンケートの高度な紐付けに AI を使用します。

1. [Google AI Studio](https://aistudio.google.com/app/apikey) にアクセスします。
2. 「Create API key」をクリックして API キーを発行し、コピーします。
3. 本アプリの「設定」タブにある「Gemini API Key」欄に貼り付けます。
4. 「APIキーを検証」ボタンを押し、「成功」と表示されることを確認してください。
   - モデル：通常は高速な「gemini-2.5-flash」を推奨します。

---
【3. 基本的な操作フロー】

ステップ 1：動画処理（演奏区間の自動切り出し）
- 「動画処理」タブで、カメラで撮影したビデオファイルを選択します。
- 録画が途中で分割されている場合は、それらをすべて選択して追加してください（自動で 1 本に結合して処理します）。
- 高音質な外部マイク音声がある場合は「音声を追加」から選択してください。
- 「処理を開始」すると、AI が映像内の「人の出入り（入場・退場）」を検知し、演奏中のみを切り出して出力ディレクトリに保存します。

ステップ 2：プレビュー & 紐付け（情報の統合）
- 「プレビュー & 紐付け」タブで、演奏会の「プログラム PDF」と「Google フォーム ID」を入力します。
- 【フォーム ID の確認方法】
  - フォーム編集画面の URL `https://docs.google.com/forms/d/【ここがフォームID】/edit` の部分をコピーしてください。
- 「マッピングを生成」を押すと、AIがPDFの内容を構造化データとして読み取り、アンケート回答と照らし合わせてどの動画が誰の演奏かを判別します。
- 【重要】本アプリは「アンケートに回答した人のみ」をアップロード対象とします。動画公開を希望しないの演奏者の動画は、この段階で除外されます。

ステップ 3：アップロード（YouTube 投稿）
- マッピング結果を確認し、タイトルや公開設定に間違いがなければ「アップロード ワークフローを開始」を押します。
- 動画タイトル、説明文、公開設定（公開/限定公開）がアンケート回答に基づいて自動適用されます。

---
【4. YouTube のアップロード制限（クォータ）について】
Google API の無料枠には、1日あたりのアップロード数に制限があります。
- 通常、1日あたり最大 6 本程度のアップロードが可能です（1600クォータ/本）。
- 制限に達した場合、アプリは「クォータリセット（日本時間の午後 4 時〜 5 時頃）」まで自動的に待機（スリープ）状態に入ります。
- 大量の動画をアップロードする場合は、アプリを起動したままにしておくことで、リセット後に自動で続きから再開されます。

---
【5. 便利なツール：Google フォームの自動生成】
- 「ツール」タブの「新しいフォームを作成」を使うと、本アプリと完全に連携する Google フォームを 1 クリックで自動作成できます。
- 作成される質問項目：お名前、氏名表示の可否、演奏曲名、公開設定、追加の説明文。
- 作成後に表示される「回答用 URL」を演奏者に配布し、本番当日までに回答してもらってください。
        """
        
        label = ctk.CTkLabel(tab, text=help_text, justify=tk.LEFT, font=ctk.CTkFont(size=13))
        label.pack(padx=20, pady=20, anchor="w")

    # --- Callbacks & Logic ---

    def _add_videos(self):
        files = filedialog.askopenfilenames(title="Select Video Files")
        for f in files:
            var = ctk.BooleanVar(value=False)
            cb = ctk.CTkCheckBox(self.v_scroll, text=os.path.basename(f), variable=var)
            cb.pack(anchor="w", padx=5, pady=2)
            self.v_checkboxes.append({'path': f, 'var': var, 'widget': cb})

    def _add_audios(self):
        files = filedialog.askopenfilenames(title="Select Audio Files")
        for f in files:
            var = ctk.BooleanVar(value=False)
            cb = ctk.CTkCheckBox(self.a_scroll, text=os.path.basename(f), variable=var)
            cb.pack(anchor="w", padx=5, pady=2)
            self.a_checkboxes.append({'path': f, 'var': var, 'widget': cb})

    def _match_and_queue(self):
        v_paths = [item['path'] for item in self.v_checkboxes if item['var'].get()]
        
        # Audio selection: logic might need to handle only one audio, but let's take the first checked one
        a_paths = [item['path'] for item in self.a_checkboxes if item['var'].get()]
        a_path = a_paths[0] if a_paths else None

        if v_paths:
            self.queue_data.append((v_paths, a_path))
            v_names = ", ".join([os.path.basename(p) for p in v_paths])
            self.q_list.insert(tk.END, f"{v_names} + {'Mic Audio' if a_path else 'Video Audio Only'}")
            
            # Reset checkboxes after adding to queue
            for item in self.v_checkboxes:
                item['var'].set(False)
            for item in self.a_checkboxes:
                item['var'].set(False)
        else:
            messagebox.showwarning("Selection", "Please select at least one video.")

    def _clear_queue(self):
        self.queue_data = []
        self.q_list.delete(0, tk.END)
        
        # Clear checkboxes list and widgets
        for item in self.v_checkboxes:
            item['widget'].destroy()
        self.v_checkboxes = []
        
        for item in self.a_checkboxes:
            item['widget'].destroy()
        self.a_checkboxes = []

    def _browse_file(self, var, key=None):
        f = filedialog.askopenfilename()
        if f: var.set(f)

    def _browse_dir(self, var):
        d = filedialog.askdirectory()
        if d: var.set(d)

    def _google_login(self, target):
        secrets = self.secrets_var.get()
        if not secrets or not os.path.exists(secrets):
            # Fallback to current directory
            alt_secrets = Path(sys.executable).parent / "client_secrets.json" if getattr(sys, 'frozen', False) else Path("client_secrets.json")
            if alt_secrets.exists():
                secrets = str(alt_secrets)
                self.secrets_var.set(secrets)
            else:
                messagebox.showerror("エラー", "Client Secrets JSONファイルが見つかりません。設定画面で正しいファイルを指定してください。")
                return
        
        def task():
            try:
                if target == "forms":
                    print("Google フォーム認証を開始します。ブラウザを確認してください...")
                    authenticate_forms_api(client_secrets_path=Path(secrets))
                    print("Google フォームの認証が完了しました！")
                    self.after(0, lambda: messagebox.showinfo("成功", "Google フォームの認証に成功しました。"))
                else:
                    print("YouTube アップロード認証を開始します。ブラウザを確認してください...")
                    from .youtube_uploader import authenticate
                    authenticate(client_secrets_path=Path(secrets))
                    print("YouTube アップロードの認証が完了しました！")
                    self.after(0, lambda: messagebox.showinfo("成功", "YouTube アップロードの認証に成功しました。"))
            except Exception as e:
                print(f"認証エラー ({target}): {e}")
                self.after(0, lambda err=e: messagebox.showerror("エラー", f"認証に失敗しました: {err}"))
        
        threading.Thread(target=task).start()

    def _verify_gemini(self):
        key = self.gemini_key_var.get()
        if not key:
            messagebox.showwarning("入力エラー", "APIキーを入力してください。")
            return
            
        def task():
            try:
                print("Gemini APIキーを検証中...")
                configure_gemini(key)
                from .gemini_utils import call_gemini_api
                call_gemini_api("Hello, this is a test message to verify the API key.")
                print("Gemini APIキーの検証に成功しました！")
                self.after(0, lambda: messagebox.showinfo("成功", "Gemini APIキーは有効です。"))
            except Exception as e:
                print(f"Gemini 検証エラー: {e}")
                self.after(0, lambda err=e: messagebox.showerror("エラー", f"APIキーの検証に失敗しました。\n{err}"))
        threading.Thread(target=task).start()

    def _save_settings(self):
        for (section, key), var in self.setting_vars.items():
            val = var.get()
            # Type conversion
            orig = self.config[section].get(key)
            if isinstance(orig, bool): val = bool(val)
            elif isinstance(orig, int): val = int(val)
            elif isinstance(orig, float): val = float(val)
            self.config_manager.set(section, key, val)
        
        # Save explicit vars
        self.config_manager.set('workflow', 'use_gemini', bool(self.use_gemini_var.get()))
        self.config_manager.set('workflow', 'gemini_api_key', self.gemini_key_var.get())
        self.config_manager.set('workflow', 'gemini_model', self.gemini_model_var.get())
        
        messagebox.showinfo("Settings", "Settings saved successfully.")

    def _create_form(self):
        title = self.tool_title_var.get()
        secrets = self.secrets_var.get()
        if not secrets or not os.path.exists(secrets):
            messagebox.showerror("エラー", "Client Secrets JSONファイルが見つかりません。設定画面で正しいファイルを指定してください。")
            return

        def task():
            try:
                service = authenticate_forms_api(client_secrets_path=Path(secrets))
                info = create_concert_form(service, form_title=title)
                save_form_config(info)
                print(f"フォームを作成しました: {info['response_url']}")
                self.after(0, lambda: messagebox.showinfo("成功", f"フォームを作成しました！\n{info['response_url']}"))
                self.after(0, self._update_form_history)
            except Exception as e:
                print(f"フォーム作成エラー: {e}")
                self.after(0, lambda err=e: messagebox.showerror("エラー", f"フォーム作成に失敗しました: {err}"))
        threading.Thread(target=task).start()

    def _run_processing(self):
        if not self.queue_data: return
        self.proc_btn.configure(state="disabled")
        
        proc_config = self.config['processing'].copy()
        proc_config.update(self.config['paths'])

        def task():
            print("--- バッチ処理を開始します ---")
            total_items = len(self.queue_data)
            start_time = time.time()
            
            for i, (v, a) in enumerate(self.queue_data):
                try:
                    # Overall progress calculation
                    def sub_callback(curr, tot, msg):
                        overall = (i / total_items) + (curr / tot / total_items) if tot > 0 else (i / total_items)
                        self._progress_callback(overall, 1.0, msg)
                        
                        # Estimate remaining time
                        elapsed = time.time() - start_time
                        if overall > 0:
                            total_est = elapsed / overall
                            remaining = total_est - elapsed
                            hours = int(remaining // 3600)
                            mins = int((remaining % 3600) // 60)
                            time_str = f"残り時間目安: {hours}時間{mins}分"
                            self.after(0, lambda: self.progress_label.configure(text=f"{msg} ({time_str})"))

                    video_processor.process_pair(v, a, proc_config, sub_callback)
                except Exception as e:
                    print(f"処理エラー {v}: {e}")
            
            print("--- すべての処理が完了しました ---")
            self.after(0, lambda: self.proc_btn.configure(state="normal"))
            self.after(0, lambda: messagebox.showinfo("完了", "動画処理が完了しました！"))
        
        threading.Thread(target=task).start()

    def _progress_callback(self, current, total, message):
        if total > 0:
            self.after(0, lambda: self.progress_bar.set(current / total))
        if message:
            self.after(0, lambda: self.progress_label.configure(text=message))

    def _run_mapping(self):
        pdf = self.pdf_var.get()
        form_id = self.form_id_var.get()
        if not pdf:
            messagebox.showerror("Error", "PDF path is required.")
            return

        def task():
            print("--- マッピング解析を実行中 ---")
            try:
                secrets = self.secrets_var.get()
                # 1. PDF
                self.program_data = parse_concert_pdf(Path(pdf))
                # 2. Form
                parser = FormResponseParser()
                form_resps = parser.load_from_forms_api(form_id if form_id else None)
                # 3. Videos in output
                video_infos = get_video_files_sorted(Path(self.config['paths']['output_dir']))
                # 4. Map
                p_v_map = map_program_to_videos(self.program_data, video_infos)
                self.mapping_results = map_with_form_responses(p_v_map, form_resps, use_gemini=True)
                
                self.after(0, self._update_preview_ui)
                self.after(0, self._generate_and_save_metadata)
                print("--- マッピング解析完了 ---")
            except Exception as e:
                print(f"マッピングエラー: {e}")
        
        threading.Thread(target=task).start()

    def _update_preview_ui(self):
        for widget in self.preview_area.winfo_children():
            widget.destroy()
        
        for i, m in enumerate(self.mapping_results):
            frame = ctk.CTkFrame(self.preview_area)
            frame.pack(fill=tk.X, padx=5, pady=5)
            
            title = m['form_response'].get('piece_title', 'Unknown')
            name = m['form_response'].get('name', 'Unknown')
            video = os.path.basename(m['video_file']) if m['video_file'] else "N/A"
            
            ctk.CTkLabel(frame, text=f"#{i+1}: {title} - {name}", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=10, sticky="w")
            ctk.CTkLabel(frame, text=f"動画ファイル: {video}").grid(row=1, column=0, padx=10, sticky="w")
            ctk.CTkLabel(frame, text=f"公開設定: {m['form_response'].get('privacy', 'unlisted')}").grid(row=1, column=1, padx=10, sticky="w")

            edit_btn = ctk.CTkButton(frame, text="編集", width=60, command=lambda m=m: self._edit_mapping(m))
            edit_btn.grid(row=0, column=2, rowspan=2, padx=10)
    
    def _generate_and_save_metadata(self):
        """mapping_resultsからメタデータを生成し、ファイルに保存する"""
        if not self.mapping_results:
            print("マッピング結果がありません。メタデータを生成できません。")
            return

        print("--- アップロード用メタデータを生成・保存します ---")
        try:
            # _run_mappingで保存したprogram_dataを使用
            concert_info = self.program_data.get("concert_info") if self.program_data else None
            metadata = generate_upload_metadata(self.mapping_results, concert_info)

            metadata_path = Path(self.config['paths']['output_dir']) / "upload_metadata.json"
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
            print(f"✓ メタデータを保存しました: {metadata_path}")
            
            # アップロードタブの表示も更新
            self.after(0, self._display_upload_results)

        except Exception as e:
            print(f"メタデータの生成・保存中にエラーが発生しました: {e}")
            self.after(0, lambda: messagebox.showerror("エラー", f"メタデータの生成に失敗しました:\n{e}"))

    def _run_workflow(self):
        """保存されたメタデータを使ってアップロード処理を実行する"""
        output_dir = Path(self.config['paths']['output_dir'])
        metadata_path = output_dir / "upload_metadata.json"

        if not metadata_path.exists():
            messagebox.showerror("エラー", "アップロードメタデータファイル (upload_metadata.json) が見つかりません。\n先に「2. プレビュー & 紐付け」タブでマッピングを生成してください。")
            return

        if self.skip_upload_var.get():
            messagebox.showinfo("スキップ", "アップロードはスキップされました。メタデータは準備完了です。")
            self._display_upload_results() # Just display the existing file
            return

        secrets_path_str = self.secrets_var.get()
        if not secrets_path_str or not os.path.exists(secrets_path_str):
             messagebox.showerror("エラー", "Client Secrets JSONファイルが見つかりません。「設定」タブで正しいファイルを指定してください。")
             return
        secrets_path = Path(secrets_path_str)

        def task():
            try:
                print("--- YouTubeアップロード処理を開始します ---")
                updated_metadata, summary = youtube_uploader.batch_upload(
                    video_dir=output_dir,
                    metadata_file=metadata_path,
                    client_secrets_path=secrets_path
                )

                # アップローダーが返したURL情報などを含む最新のメタデータを保存
                with open(metadata_path, 'w', encoding='utf-8') as f:
                    json.dump(updated_metadata, f, ensure_ascii=False, indent=2)

                print(f"--- アップロード処理完了: {summary.get('success', 0)}件成功 ---")
                self.after(0, self._display_upload_results)
                self.after(0, lambda: messagebox.showinfo("完了", "アップロード処理が完了しました！"))

            except Exception as e:
                print(f"アップロードワークフローエラー: {e}")
                self.after(0, lambda err=e: messagebox.showerror("エラー", f"アップロード処理に失敗しました:\n{err}"))

        threading.Thread(target=task).start()

    def _display_upload_results(self):
        for widget in self.upload_result_area.winfo_children():
            widget.destroy()
            
        metadata_path = Path(self.config['paths']['output_dir']) / "upload_metadata.json"
        if metadata_path.exists():
            try:
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for i, v in enumerate(data.get('videos', [])):
                        frame = ctk.CTkFrame(self.upload_result_area)
                        frame.pack(fill=tk.X, padx=5, pady=5)
                        frame.grid_columnconfigure(0, weight=1)
                        
                        title_text = f"{i+1}. {v['title']}"
                        status_text = f"状態: {v.get('privacy_status', '不明')}"
                        url_text = f"URL: {v.get('video_url', 'アップロードされていません')}"

                        ctk.CTkLabel(frame, text=title_text, font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w", padx=10)
                        ctk.CTkLabel(frame, text=status_text, text_color="gray").grid(row=0, column=1, sticky="e", padx=10)
                        ctk.CTkLabel(frame, text=url_text, justify=tk.LEFT).grid(row=1, column=0, columnspan=2, sticky="w", padx=10)

            except Exception as e:
                print(f"結果表示エラー: {e}")

    def _get_results_as_text(self) -> str:
        """Helper to format upload results as a string."""
        results_text = []
        metadata_path = Path(self.config['paths']['output_dir']) / "upload_metadata.json"
        if metadata_path.exists():
            try:
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for i, v in enumerate(data.get('videos', [])):
                        results_text.append(f"[{i+1}] {v['title']}")
                        results_text.append(f"  状態: {v.get('privacy_status', '不明')}")
                        results_text.append(f"  URL: {v.get('video_url', 'N/A')}")
                        results_text.append("-" * 20)
                return "\n".join(results_text)
            except Exception as e:
                return f"結果の取得中にエラーが発生しました: {e}"
        return "結果ファイルが見つかりません。"

    def _copy_results_to_clipboard(self):
        """Copy upload results to the clipboard."""
        results_str = self._get_results_as_text()
        self.clipboard_clear()
        self.clipboard_append(results_str)
        messagebox.showinfo("コピー完了", "アップロード結果をクリップボードにコピーしました。")

    def _export_results_to_file(self):
        """Export upload results to a text file."""
        results_str = self._get_results_as_text()
        
        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="結果を保存"
        )
        
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(results_str)
                messagebox.showinfo("保存完了", f"結果を {os.path.basename(file_path)} に保存しました。")
            except Exception as e:
                messagebox.showerror("保存エラー", f"ファイルの保存中にエラーが発生しました:\n{e}")

    def _edit_mapping(self, mapping_item):
        """Open a toplevel window to edit a single mapping."""
        
        edit_window = ctk.CTkToplevel(self)
        edit_window.title("マッピング編集")
        edit_window.geometry("600x300")
        edit_window.transient(self) # Keep on top
        edit_window.grab_set() # Modal

        # Get all available video files from output dir
        output_dir = Path(self.config['paths']['output_dir'])
        available_videos = get_video_files_sorted(output_dir)
        video_filenames = [os.path.basename(v['file_path']) for v in available_videos]

        # Current info
        title = mapping_item['form_response'].get('piece_title', 'Unknown')
        name = mapping_item['form_response'].get('name', 'Unknown')
        current_video = os.path.basename(mapping_item['video_file']) if mapping_item['video_file'] else "N/A"

        ctk.CTkLabel(edit_window, text=f"プログラム: {title} - {name}", font=ctk.CTkFont(weight="bold")).pack(pady=10)
        
        ctk.CTkLabel(edit_window, text="紐付ける動画ファイルを選択してください:").pack(pady=(10, 0))
        
        new_video_var = ctk.StringVar(value=current_video)
        video_menu = ctk.CTkOptionMenu(edit_window, variable=new_video_var, values=video_filenames, width=400)
        video_menu.pack(pady=10)

        def save_and_close():
            selected_filename = new_video_var.get()
            selected_video_info = next((v for v in available_videos if os.path.basename(v['file_path']) == selected_filename), None)
            
            if selected_video_info:
                self._save_manual_mapping(mapping_item, selected_video_info)
                edit_window.destroy()
            else:
                messagebox.showerror("エラー", "選択された動画情報が見つかりません。", parent=edit_window)


        save_btn = ctk.CTkButton(edit_window, text="保存して閉じる", command=save_and_close)
        save_btn.pack(pady=20)

    def _save_manual_mapping(self, mapping_item, new_video_info):
        """Update the mapping_results with the user's manual selection."""
        
        # Find the item in the main list
        for i, item in enumerate(self.mapping_results):
            # Compare by a unique identifier, e.g., form_response response_id
            if item['form_response']['response_id'] == mapping_item['form_response']['response_id']:
                print(f"Updating mapping for '{item['form_response']['piece_title']}'")
                print(f"  Old video: {item['video_file']}")
                print(f"  New video: {new_video_info['file_path']}")
                
                # Update the video-related fields
                self.mapping_results[i]['video_file'] = new_video_info['file_path']
                self.mapping_results[i]['video_name'] = new_video_info['file_name']
                self.mapping_results[i]['video_data'] = new_video_info
                break
        
        # Refresh the UI to show the change
        self._update_preview_ui()
        # 変更をメタデータファイルに即時反映
        self._generate_and_save_metadata()

    def _update_form_history(self):
        # 既存のウィジェットをクリア
        for widget in self.form_history_frame.winfo_children():
            widget.destroy()

        try:
            history = load_form_history() # 最新3件が返される想定

            if not history:
                ctk.CTkLabel(self.form_history_frame, text="作成履歴はありません。").pack(pady=10)
                return

            for item in history:
                item_frame = ctk.CTkFrame(self.form_history_frame)
                item_frame.pack(fill="x", padx=10, pady=5)
                
                title = item.get("form_title", "無題のフォーム")
                form_id = item.get("form_id", "ID不明")
                created_at = item.get("created_at", "")
                
                created_str = "日時不明"
                if created_at:
                    try:
                        # ISO 8601形式の文字列をdatetimeオブジェクトに変換
                        dt_obj = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        # ローカルタイムゾーンに変換して表示
                        created_str = dt_obj.astimezone().strftime('%Y-%m-%d %H:%M')
                    except (ValueError, TypeError):
                        created_str = created_at

                label_text = f"タイトル: {title}\nID: {form_id}\n作成日時: {created_str}"
                ctk.CTkLabel(item_frame, text=label_text, justify=tk.LEFT).pack(anchor="w", padx=10, pady=5)
        except Exception as e:
            print(f"フォーム履歴の読み込みに失敗しました: {e}")
            ctk.CTkLabel(self.form_history_frame, text=f"履歴の読み込みに失敗しました:\n{e}", text_color="red").pack(pady=10)

def main():
    app = ConcertVideoApp()
    app.mainloop()

if __name__ == "__main__":
    main()
