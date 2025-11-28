import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import sys
import os
import queue
from pathlib import Path

# Logic imports
from config_manager import ConfigManager
import video_processor
from run_youtube_workflow import run_full_workflow
from create_google_form import create_concert_form, authenticate_forms_api, save_form_config

# --- Console Redirector ---
class ConsoleRedirector:
    """Redirects stdout/stderr to a text widget"""
    def __init__(self, text_widget, tag="stdout"):
        self.text_widget = text_widget
        self.tag = tag
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
                self.text_widget.insert(tk.END, text, (self.tag,))
                self.text_widget.see(tk.END)
                self.text_widget.configure(state='disabled')
        except queue.Empty:
            pass
        self.text_widget.after(self.update_interval, self._update_widget)

# --- Application ---
class ConcertVideoApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Concert Video Cutter & Uploader")
        self.geometry("900x700")

        # Initialize Config
        self.config_manager = ConfigManager()
        self.config = self.config_manager.config

        # Styles
        style = ttk.Style(self)
        style.theme_use('clam')  # 'clam', 'alt', 'default', 'classic'

        # Main Layout
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Tabs
        self.tab_process = ttk.Frame(self.notebook)
        self.tab_workflow = ttk.Frame(self.notebook)
        self.tab_full = ttk.Frame(self.notebook)
        self.tab_tools = ttk.Frame(self.notebook)
        self.tab_settings = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_process, text="1. Process Videos")
        self.notebook.add(self.tab_workflow, text="2. Workflow & Upload")
        self.notebook.add(self.tab_full, text="Full Automation")
        self.notebook.add(self.tab_tools, text="Tools")
        self.notebook.add(self.tab_settings, text="Settings")

        # Build UI
        self._build_processing_tab()
        self._build_workflow_tab()
        self._build_full_tab()
        self._build_tools_tab()
        self._build_settings_tab()
        self._build_console_output()

    def _build_processing_tab(self):
        frame = ttk.Frame(self.tab_process, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        # --- File Selection ---
        sel_frame = ttk.LabelFrame(frame, text="File Selection", padding=10)
        sel_frame.pack(fill=tk.X, pady=5)

        # Video List
        v_frame = ttk.Frame(sel_frame)
        v_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        ttk.Label(v_frame, text="Videos").pack(anchor=tk.W)
        self.proc_video_list = tk.Listbox(v_frame, height=8, selectmode=tk.SINGLE)
        self.proc_video_list.pack(fill=tk.BOTH, expand=True)
        ttk.Button(v_frame, text="Add Videos", command=self._add_videos).pack(fill=tk.X, pady=2)

        # Audio List
        a_frame = ttk.Frame(sel_frame)
        a_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        ttk.Label(a_frame, text="Audios (Mic)").pack(anchor=tk.W)
        self.proc_audio_list = tk.Listbox(a_frame, height=8, selectmode=tk.SINGLE)
        self.proc_audio_list.pack(fill=tk.BOTH, expand=True)
        ttk.Button(a_frame, text="Add Audios", command=self._add_audios).pack(fill=tk.X, pady=2)

        # Queue
        q_frame = ttk.LabelFrame(frame, text="Processing Queue", padding=10)
        q_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # Controls for Queue
        ctrl_frame = ttk.Frame(q_frame)
        ctrl_frame.pack(fill=tk.X)
        ttk.Button(ctrl_frame, text="Match & Add to Queue", command=self._match_and_queue).pack(side=tk.LEFT)
        ttk.Button(ctrl_frame, text="Clear Queue", command=self._clear_queue).pack(side=tk.LEFT, padx=5)

        self.proc_queue_list = tk.Listbox(q_frame, height=6)
        self.proc_queue_list.pack(fill=tk.BOTH, expand=True, pady=5)
        self.queue_data = [] # List of tuples (video, audio)

        # Run Button
        ttk.Button(frame, text="START PROCESSING", command=self._run_processing).pack(fill=tk.X, pady=10)

        # Progress Bar
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.progress_label = ttk.Label(frame, text="")
        self.progress_label.pack()

    def _build_workflow_tab(self):
        frame = ttk.Frame(self.tab_workflow, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        # --- Inputs ---
        input_frame = ttk.LabelFrame(frame, text="Workflow Inputs", padding=10)
        input_frame.pack(fill=tk.X, pady=5)

        # PDF
        pdf_row = ttk.Frame(input_frame)
        pdf_row.pack(fill=tk.X, pady=2)
        ttk.Label(pdf_row, text="PDF Program:").pack(side=tk.LEFT, width=15)
        self.wf_pdf_var = tk.StringVar(value=self.config['paths']['pdf_path'])
        ttk.Entry(pdf_row, textvariable=self.wf_pdf_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(pdf_row, text="Browse", command=lambda: self._browse_file(self.wf_pdf_var, "pdf_path")).pack(side=tk.LEFT, padx=5)

        # Form ID
        fid_row = ttk.Frame(input_frame)
        fid_row.pack(fill=tk.X, pady=2)
        ttk.Label(fid_row, text="Form ID:").pack(side=tk.LEFT, width=15)
        self.wf_form_id_var = tk.StringVar(value=self.config['paths']['form_id'])
        ttk.Entry(fid_row, textvariable=self.wf_form_id_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Form CSV (Optional)
        csv_row = ttk.Frame(input_frame)
        csv_row.pack(fill=tk.X, pady=2)
        ttk.Label(csv_row, text="Form CSV (Opt):").pack(side=tk.LEFT, width=15)
        self.wf_csv_var = tk.StringVar(value=self.config['paths']['form_csv_path'])
        ttk.Entry(csv_row, textvariable=self.wf_csv_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(csv_row, text="Browse", command=lambda: self._browse_file(self.wf_csv_var, "form_csv_path")).pack(side=tk.LEFT, padx=5)

        # --- Options ---
        opt_frame = ttk.LabelFrame(frame, text="Options", padding=10)
        opt_frame.pack(fill=tk.X, pady=5)

        self.wf_skip_upload = tk.BooleanVar(value=self.config['workflow']['skip_upload'])
        ttk.Checkbutton(opt_frame, text="Skip YouTube Upload (Generate Metadata only)", variable=self.wf_skip_upload).pack(anchor=tk.W)

        self.wf_use_gemini = tk.BooleanVar(value=self.config['workflow']['use_gemini'])
        ttk.Checkbutton(opt_frame, text="Use Gemini AI for Matching", variable=self.wf_use_gemini).pack(anchor=tk.W)

        # Run Button
        ttk.Button(frame, text="RUN WORKFLOW (Map & Upload)", command=self._run_workflow).pack(fill=tk.X, pady=10)
        ttk.Label(frame, text="Note: This uses videos currently in the output directory.").pack()

    def _build_full_tab(self):
        frame = ttk.Frame(self.tab_full, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Automated Full Flow", font=("Helvetica", 16, "bold")).pack(pady=10)
        ttk.Label(frame, text="1. Processes files in the Queue.").pack(anchor=tk.W)
        ttk.Label(frame, text="2. Takes the output and runs the YouTube Workflow.").pack(anchor=tk.W)
        ttk.Label(frame, text="Ensure all settings in previous tabs are correct!").pack(anchor=tk.W, pady=10)

        ttk.Button(frame, text="EXECUTE FULL FLOW", command=self._run_full_flow).pack(fill=tk.X, pady=20)

    def _build_tools_tab(self):
        frame = ttk.Frame(self.tab_tools, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        # Create Form
        f_frame = ttk.LabelFrame(frame, text="Google Form Generator", padding=10)
        f_frame.pack(fill=tk.X, pady=5)

        ttk.Label(f_frame, text="Title:").grid(row=0, column=0, sticky=tk.W)
        self.tool_form_title = tk.StringVar(value="Concert Registration Form")
        ttk.Entry(f_frame, textvariable=self.tool_form_title).grid(row=0, column=1, sticky="we")

        ttk.Button(f_frame, text="Create Form", command=self._create_form).grid(row=1, column=1, pady=5)

    def _build_settings_tab(self):
        frame = ttk.Frame(self.tab_settings, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        # --- Paths ---
        p_frame = ttk.LabelFrame(frame, text="Directories", padding=10)
        p_frame.pack(fill=tk.X, pady=5)

        self._add_setting_entry(p_frame, "Output Dir:", "paths", "output_dir")
        self._add_setting_entry(p_frame, "Temp Dir:", "paths", "temp_dir")

        # --- Processing ---
        proc_frame = ttk.LabelFrame(frame, text="Processing Parameters", padding=10)
        proc_frame.pack(fill=tk.X, pady=5)

        self._add_setting_entry(proc_frame, "Video Volume (0-1):", "processing", "video_audio_volume")
        self._add_setting_entry(proc_frame, "Mic Volume (>1):", "processing", "mic_audio_volume")
        self._add_setting_entry(proc_frame, "Sync Sample Rate:", "processing", "audio_sync_sample_rate")
        self._add_setting_entry(proc_frame, "Min Duration (s):", "processing", "min_duration_seconds")

        ttk.Button(frame, text="Save Settings", command=self._save_settings).pack(pady=10)

    def _add_setting_entry(self, parent, label, section, key):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text=label).pack(side=tk.LEFT, width=20)
        var = tk.StringVar(value=str(self.config[section][key]))
        entry = ttk.Entry(row, textvariable=var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        # Store reference to var to save later
        if not hasattr(self, 'setting_vars'): self.setting_vars = {}
        self.setting_vars[(section, key)] = var

    def _build_console_output(self):
        frame = ttk.LabelFrame(self, text="Console Output", padding=5)
        frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.console_text = tk.Text(frame, height=10, state='disabled', bg="#222", fg="#EEE")
        self.console_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(frame, command=self.console_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.console_text['yscrollcommand'] = scrollbar.set

        # Redirect stdout/stderr
        self.redirector = ConsoleRedirector(self.console_text)
        sys.stdout = self.redirector
        sys.stderr = self.redirector

    # --- Actions ---

    def _add_videos(self):
        files = filedialog.askopenfilenames(title="Select Video Files")
        for f in files: self.proc_video_list.insert(tk.END, f)

    def _add_audios(self):
        files = filedialog.askopenfilenames(title="Select Audio Files")
        for f in files: self.proc_audio_list.insert(tk.END, f)

    def _match_and_queue(self):
        """Simplistic matching by index. Better logic could be added."""
        v_sel = self.proc_video_list.curselection()
        a_sel = self.proc_audio_list.curselection()

        if v_sel and a_sel:
            v_path = self.proc_video_list.get(v_sel[0])
            a_path = self.proc_audio_list.get(a_sel[0])
            self.queue_data.append((v_path, a_path))
            self.proc_queue_list.insert(tk.END, f"{os.path.basename(v_path)} + {os.path.basename(a_path)}")
        else:
            messagebox.showwarning("Selection", "Please select one video and one audio file.")

    def _clear_queue(self):
        self.queue_data = []
        self.proc_queue_list.delete(0, tk.END)

    def _browse_file(self, var, config_key):
        f = filedialog.askopenfilename()
        if f:
            var.set(f)
            self.config['paths'][config_key] = f # Update config in memory

    def _save_settings(self):
        for (section, key), var in self.setting_vars.items():
            try:
                # Convert types if necessary
                val = var.get()
                if isinstance(self.config[section][key], int):
                    val = int(val)
                elif isinstance(self.config[section][key], float):
                    val = float(val)
                self.config_manager.set(section, key, val)
            except ValueError:
                print(f"Invalid value for {key}")
        messagebox.showinfo("Settings", "Settings saved successfully.")

    def _create_form(self):
        title = self.tool_form_title.get()
        def task():
            try:
                service = authenticate_forms_api()
                info = create_concert_form(service, form_title=title)
                save_form_config(info)
                print("Form created successfully!")
            except Exception as e:
                print(f"Error creating form: {e}")

        threading.Thread(target=task).start()

    # --- Runners ---

    def _run_processing(self):
        if not self.queue_data:
            messagebox.showwarning("Queue", "Queue is empty!")
            return

        self.tab_process.state(['disabled']) # Doesn't actually disable tab interaction easily, but intent is there

        # Get processing config
        proc_config = self.config['processing'].copy()
        proc_config['output_dir'] = self.config['paths']['output_dir']
        proc_config['temp_dir'] = self.config['paths']['temp_dir']

        def task():
            print("--- Starting Batch Processing ---")
            for v, a in self.queue_data:
                try:
                    video_processor.process_pair(v, a, proc_config, self._progress_callback)
                except Exception as e:
                    print(f"Error processing {os.path.basename(v)}: {e}")
            print("--- Processing Complete ---")
            messagebox.showinfo("Done", "Processing Complete")

        threading.Thread(target=task).start()

    def _run_workflow(self):
        # Gather inputs
        pdf = self.wf_pdf_var.get()
        form_id = self.wf_form_id_var.get()
        csv = self.wf_csv_var.get()

        if not pdf:
            messagebox.showerror("Error", "PDF path is required.")
            return

        def task():
            print("--- Starting Workflow ---")
            try:
                run_full_workflow(
                    pdf_path=Path(pdf),
                    form_csv_path=Path(csv) if csv else None,
                    form_id=form_id if form_id else None,
                    use_forms_api=not bool(csv),
                    video_dir=Path(self.config['paths']['output_dir']),
                    output_dir=Path(self.config['paths']['output_dir']),
                    skip_upload=self.wf_skip_upload.get(),
                    use_gemini_matching=self.wf_use_gemini.get()
                )
                print("--- Workflow Complete ---")
                messagebox.showinfo("Done", "Workflow Complete")
            except Exception as e:
                print(f"Workflow Error: {e}")

        threading.Thread(target=task).start()

    def _run_full_flow(self):
        if not self.queue_data:
            messagebox.showwarning("Queue", "Add files to queue first.")
            return

        pdf = self.wf_pdf_var.get()
        if not pdf:
            messagebox.showerror("Error", "PDF path is required for full flow.")
            return

        def task():
            # 1. Processing
            print("--- PHASE 1: VIDEO PROCESSING ---")
            proc_config = self.config['processing'].copy()
            proc_config['output_dir'] = self.config['paths']['output_dir']
            proc_config['temp_dir'] = self.config['paths']['temp_dir']

            for v, a in self.queue_data:
                try:
                    video_processor.process_pair(v, a, proc_config, self._progress_callback)
                except Exception as e:
                    print(f"Error processing {os.path.basename(v)}: {e}")

            # 2. Workflow
            print("--- PHASE 2: YOUTUBE WORKFLOW ---")
            try:
                run_full_workflow(
                    pdf_path=Path(pdf),
                    form_csv_path=Path(self.wf_csv_var.get()) if self.wf_csv_var.get() else None,
                    form_id=self.wf_form_id_var.get() if self.wf_form_id_var.get() else None,
                    use_forms_api=not bool(self.wf_csv_var.get()),
                    video_dir=Path(self.config['paths']['output_dir']),
                    output_dir=Path(self.config['paths']['output_dir']),
                    skip_upload=self.wf_skip_upload.get(),
                    use_gemini_matching=self.wf_use_gemini.get()
                )
                print("--- Full Flow Complete ---")
                messagebox.showinfo("Done", "Full Automation Complete")
            except Exception as e:
                print(f"Workflow Error: {e}")

        threading.Thread(target=task).start()

    def _progress_callback(self, current, total, message):
        # Update progress bar safely
        if total > 0:
            percentage = (current / total) * 100
            self.after(0, lambda: self.progress_var.set(percentage))

        # Update label
        if message:
            self.after(0, lambda: self.progress_label.config(text=message))

if __name__ == "__main__":
    app = ConcertVideoApp()
    app.mainloop()
