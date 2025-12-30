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

# Logic imports
from .config_manager import ConfigManager
from . import video_processor
from .run_youtube_workflow import run_full_workflow
from .create_google_form import create_concert_form, authenticate_forms_api, save_form_config
from .video_mapper import get_video_files_sorted, map_program_to_videos, map_with_form_responses
from .google_form_connector import FormResponseParser
from .pdf_parser import parse_concert_pdf

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

        # Layout
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar
        self.sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(7, weight=1)

        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="CVCutter", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        self.btn_process = ctk.CTkButton(self.sidebar_frame, text="1. 動画処理", command=lambda: self.select_tab("process"))
        self.btn_process.grid(row=1, column=0, padx=20, pady=10)

        self.btn_preview = ctk.CTkButton(self.sidebar_frame, text="2. プレビュー & 紐付け", command=lambda: self.select_tab("preview"))
        self.btn_preview.grid(row=2, column=0, padx=20, pady=10)

        self.btn_upload = ctk.CTkButton(self.sidebar_frame, text="3. アップロード", command=lambda: self.select_tab("upload"))
        self.btn_upload.grid(row=3, column=0, padx=20, pady=10)

        self.btn_settings = ctk.CTkButton(self.sidebar_frame, text="設定", command=lambda: self.select_tab("settings"))
        self.btn_settings.grid(row=4, column=0, padx=20, pady=10)

        self.btn_tools = ctk.CTkButton(self.sidebar_frame, text="ツール", command=lambda: self.select_tab("tools"))
        self.btn_tools.grid(row=5, column=0, padx=20, pady=10)

        self.btn_help = ctk.CTkButton(self.sidebar_frame, text="ヘルプ", command=lambda: self.select_tab("help"))
        self.btn_help.grid(row=6, column=0, padx=20, pady=10)

        # Main Content
        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(0, weight=1)

        self.tabs = {}
        self._build_processing_tab()
        self._build_preview_tab()
        self._build_upload_tab()
        self._build_settings_tab()
        self._build_tools_tab()
        self._build_help_tab()

        self.select_tab("process")

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
        self.v_list = tk.Listbox(v_frame, bg="#2b2b2b", fg="white", borderwidth=0, highlightthickness=0, selectmode=tk.MULTIPLE)
        self.v_list.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        ctk.CTkButton(v_frame, text="ビデオを追加", command=self._add_videos).pack(pady=5)

        # Audio List
        a_frame = ctk.CTkFrame(sel_frame)
        a_frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        ctk.CTkLabel(a_frame, text="マイク音声 (任意)", font=ctk.CTkFont(weight="bold")).pack(pady=5)
        self.a_list = tk.Listbox(a_frame, bg="#2b2b2b", fg="white", borderwidth=0, highlightthickness=0)
        self.a_list.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
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

        self.upload_result_area = ctk.CTkScrollableFrame(tab, label_text="アップロード結果", height=300)
        self.upload_result_area.pack(pady=10, padx=20, fill=tk.BOTH, expand=True)

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
        
        ctk.CTkButton(auth_frame, text="Google ログイン (認証実行)", command=self._google_login).pack(pady=10)

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

    def _build_tools_tab(self):
        tab = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.tabs["tools"] = tab
        
        f_frame = ctk.CTkFrame(tab)
        f_frame.pack(fill=tk.X, padx=20, pady=20)
        ctk.CTkLabel(f_frame, text="Google フォーム自動生成", font=ctk.CTkFont(weight="bold")).pack(pady=10)
        
        self.tool_title_var = ctk.StringVar(value="コンサート出演者情報入力フォーム")
        ctk.CTkEntry(f_frame, textvariable=self.tool_title_var, placeholder_text="フォームのタイトル", width=400).pack(pady=5)
        ctk.CTkButton(f_frame, text="新しいフォームを作成", command=self._create_form).pack(pady=10)

    def _build_help_tab(self):
        tab = ctk.CTkScrollableFrame(self.main_frame, label_text="ヘルプ & 使い方")
        self.tabs["help"] = tab
        
        help_text = """
【初期設定ガイド】
1. Google Cloud Console でプロジェクトを作成し、Google Forms API と YouTube Data API v3 を有効にします。
2. 「認証情報」から OAuth 2.0 クライアント ID (デスクトップアプリ) を作成し、JSONファイルをダウンロードします。
3. 設定画面の「Client Secrets JSON」でダウンロードしたファイルを指定します。
4. 「Google ログイン」ボタンを押し、ブラウザで認証を完了させます。

【基本的な使い方】
1. 動画処理:
   - 編集したいビデオファイルを選択します。MTSファイルなどが分割されている場合は、複数選択して追加してください。
   - 外部マイク音声がある場合は追加します。ない場合はビデオの音声が使用されます。
   - 「処理を開始」を押すと、自動で演奏区間の切り出しと合成が行われます。

2. プレビュー & 紐付け:
   - 演奏会のプログラムPDFを選択します。
   - GoogleフォームのIDを入力します。
   - 「マッピングを生成」を押すと、AIが動画とプログラム情報を自動で紐付けます。

3. アップロード:
   - 内容を確認し、「ワークフローを開始」を押すとYouTubeへのアップロードが始まります。
   - YouTube APIの制限により、1日あたり約6本までアップロード可能です。
        """
        
        label = ctk.CTkLabel(tab, text=help_text, justify=tk.LEFT, font=ctk.CTkFont(size=13))
        label.pack(padx=20, pady=20, anchor="w")

    # --- Callbacks & Logic ---

    def _add_videos(self):
        files = filedialog.askopenfilenames(title="Select Video Files")
        for f in files: self.v_list.insert(tk.END, f)

    def _add_audios(self):
        files = filedialog.askopenfilenames(title="Select Audio Files")
        for f in files: self.a_list.insert(tk.END, f)

    def _match_and_queue(self):
        v_sel = self.v_list.curselection()
        a_sel = self.a_list.curselection()
        
        v_paths = [self.v_list.get(i) for i in v_sel]
        a_path = self.a_list.get(a_sel[0]) if a_sel else None

        if v_paths:
            self.queue_data.append((v_paths, a_path))
            v_names = ", ".join([os.path.basename(p) for p in v_paths])
            self.q_list.insert(tk.END, f"{v_names} + {'Mic Audio' if a_path else 'Video Audio Only'}")
        else:
            messagebox.showwarning("Selection", "Please select at least one video.")

    def _clear_queue(self):
        self.queue_data = []
        self.q_list.delete(0, tk.END)

    def _browse_file(self, var, key=None):
        f = filedialog.askopenfilename()
        if f: var.set(f)

    def _browse_dir(self, var):
        d = filedialog.askdirectory()
        if d: var.set(d)

    def _google_login(self):
        secrets = self.secrets_var.get()
        if not os.path.exists(secrets):
            messagebox.showerror("エラー", "Client Secrets JSONファイルが見つかりません。")
            return
        
        def task():
            try:
                print("Google認証を開始します。ブラウザを確認してください...")
                # Try Forms API auth
                authenticate_forms_api(client_secrets_path=Path(secrets))
                # Try YouTube API auth
                from .youtube_uploader import authenticate
                authenticate(client_secrets_path=Path(secrets))
                print("認証が完了しました！")
                messagebox.showinfo("成功", "Google認証に成功しました。")
            except Exception as e:
                print(f"認証エラー: {e}")
                messagebox.showerror("エラー", f"認証に失敗しました: {e}")
        
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
        messagebox.showinfo("Settings", "Settings saved successfully.")

    def _create_form(self):
        title = self.tool_title_var.get()
        def task():
            try:
                service = authenticate_forms_api()
                info = create_concert_form(service, form_title=title)
                save_form_config(info)
                print(f"Form created: {info['response_url']}")
            except Exception as e:
                print(f"Error: {e}")
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
                program_data = parse_concert_pdf(Path(pdf))
                # 2. Form
                parser = FormResponseParser()
                form_resps = parser.load_from_forms_api(form_id if form_id else None)
                # 3. Videos in output
                video_infos = get_video_files_sorted(Path(self.config['paths']['output_dir']))
                # 4. Map
                p_v_map = map_program_to_videos(program_data, video_infos)
                self.mapping_results = map_with_form_responses(p_v_map, form_resps, use_gemini=True)
                
                self.after(0, self._update_preview_ui)
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

    def _run_workflow(self):
        pdf = self.pdf_var.get()
        if not pdf:
            messagebox.showerror("Error", "PDF path is required.")
            return

        def task():
            try:
                # Need to capture results to display
                # For now, we'll look at upload_metadata.json which is generated during workflow
                run_full_workflow(
                    pdf_path=Path(pdf),
                    form_id=self.form_id_var.get(),
                    video_dir=Path(self.config['paths']['output_dir']),
                    skip_upload=self.skip_upload_var.get()
                )
                
                self.after(0, self._display_upload_results)
                self.after(0, lambda: messagebox.showinfo("完了", "ワークフローが完了しました！"))
            except Exception as e:
                print(f"ワークフローエラー: {e}")

        threading.Thread(target=task).start()

    def _display_upload_results(self):
        for widget in self.upload_result_area.winfo_children():
            widget.destroy()
            
        metadata_path = Path(self.config['paths']['output_dir']) / "upload_metadata.json"
        if metadata_path.exists():
            with open(metadata_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for i, v in enumerate(data.get('videos', [])):
                    frame = ctk.CTkFrame(self.upload_result_area)
                    frame.pack(fill=tk.X, padx=5, pady=2)
                    ctk.CTkLabel(frame, text=f"{i+1}. {v['title']}", font=ctk.CTkFont(weight="bold")).pack(side=tk.LEFT, padx=10)
                    # Video ID would be in upload_state.json if actually uploaded

        threading.Thread(target=task).start()

def main():
    app = ConcertVideoApp()
    app.mainloop()

if __name__ == "__main__":
    main()
