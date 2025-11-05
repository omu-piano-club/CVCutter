import os
import re
import subprocess
import numpy as np
from moviepy.editor import VideoFileClip, AudioFileClip
from detect_performances import detect_performances_by_motion
from sync_audio import find_audio_offset
import tkinter as tk
from tkinter import filedialog, Listbox, Button, Frame, Scrollbar, Toplevel, Label
from tqdm import tqdm
import threading

# --- Core Logic Functions (from previous version) ---

def get_consensus_offset(offsets, tolerance=1.0):
    if not offsets: return None
    sorted_offsets = sorted(offsets)
    best_cluster = []
    for i in range(len(sorted_offsets)):
        current_offset = sorted_offsets[i]
        current_cluster = [o for o in sorted_offsets if abs(o - current_offset) <= tolerance]
        if len(current_cluster) > len(best_cluster):
            best_cluster = current_cluster
    if not best_cluster: return np.median(sorted_offsets)
    return np.mean(best_cluster)

def run_ffmpeg_with_progress(command, duration, progress_label):
    process = subprocess.Popen(command, stderr=subprocess.PIPE, universal_newlines=True, encoding='utf-8')
    time_regex = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
    
    with tqdm(total=duration, unit='s', desc="    Encoding", ncols=80) as pbar:
        last_time = 0
        for line in process.stderr:
            match = time_regex.search(line)
            if match:
                hours, minutes, seconds, ms = map(int, match.groups())
                current_time = hours * 3600 + minutes * 60 + seconds + ms / 100
                update_amount = current_time - last_time
                pbar.update(update_amount)
                if progress_label:
                    progress_label.config(text=f"Encoding: {pbar.n:.2f} / {pbar.total:.2f} s")
                last_time = current_time
    
    process.wait()
    if process.returncode != 0:
        print(f"  ERROR: FFMPEG process failed with code {process.returncode}")
        return False
    return True

def process_pair(video_path, audio_path, config_overrides, status_label):
    """Main processing logic for a single video/audio pair."""
    
    status_label.config(text=f"Processing: {os.path.basename(video_path)}")
    print(f"\n=======================================================")
    print(f"Processing Video: {video_path}")
    print(f"Processing Audio: {audio_path}")
    print(f"=======================================================")

    config = {
        'video_path': video_path,
        'mic_audio_path': audio_path,
        'output_dir': 'output',
        'temp_dir': 'temp',
        'video_audio_volume': 0.6,
        'mic_audio_volume': 1.5,
        'audio_sync_sample_rate': 22050,
        'detection_config': { 'max_seconds_to_process': None, 'min_duration_seconds': 30, 'show_video': False, 
                              'mog2_threshold': 40, 'min_contour_area': 3000, 'left_zone_end_percent': 0.25, 
                              'center_zone_end_percent': 0.55 }
    }
    config.update(config_overrides)

    # --- Step 1: Detect Segments ---
    status_label.config(text=f"Detecting segments for {os.path.basename(video_path)}...")
    performance_segments = detect_performances_by_motion(config['video_path'], config['detection_config'])
    if not performance_segments:
        print("\nNo performance segments found. Skipping to next pair.")
        return
    print(f"\nDetected {len(performance_segments)} performance segments.")

    # --- Step 2: Sync ---
    status_label.config(text=f"Syncing audio for {os.path.basename(video_path)}...")
    all_offsets = []
    with VideoFileClip(config['video_path']) as video:
        for i, (start, end) in enumerate(performance_segments):
            needle_path = os.path.join(config['temp_dir'], f'needle_{i+1}.wav')
            video.audio.subclip(start, end).write_audiofile(needle_path, fps=config['audio_sync_sample_rate'], logger=None)
            sync_result = find_audio_offset(config['mic_audio_path'], needle_path, config['audio_sync_sample_rate'])
            if sync_result:
                all_offsets.append(sync_result['offset_seconds'] - start)
    if not all_offsets:
        print("Audio synchronization failed. Skipping to next pair.")
        return
    global_offset = get_consensus_offset(all_offsets)
    print(f"\nFinal consensus global time offset: {global_offset:.4f} seconds")

    # --- Step 3: Process with FFMPEG ---
    for i, (start_time, end_time) in enumerate(performance_segments):
        mic_start = start_time + global_offset
        duration = end_time - start_time
        if mic_start < 0: continue
            
        base_name = os.path.splitext(os.path.basename(video_path))[0]
        output_filename = os.path.join(config['output_dir'], f"{base_name}_performance_{i+1}.mp4")
        
        status_label.config(text=f"Encoding segment {i+1} of {os.path.basename(video_path)}...")
        
        command = [ 'ffmpeg', '-y', '-ss', str(start_time), '-i', config['video_path'], '-ss', str(mic_start),
                    '-i', config['mic_audio_path'], '-t', str(duration), '-filter_complex', 
                    f"[0:a]volume={config['video_audio_volume']}[a0];[1:a]volume={config['mic_audio_volume']}[a1];[a0][a1]amix=inputs=2[aout]",
                    '-map', '0:v', '-map', '[aout]', '-vf', 'yadif', '-c:v', 'libx264', '-preset', 'medium', 
                    '-c:a', 'aac', '-b:a', '192k', output_filename ]
        
        run_ffmpeg_with_progress(command, duration, None)


# --- GUI Application ---

class BatchProcessorApp:
    def __init__(self, master):
        self.master = master
        master.title("Batch Video Processor")
        
        self.videos = []
        self.audios = []
        self.queue = []

        # Frames
        self.top_frame = Frame(master, padx=10, pady=10)
        self.top_frame.pack()
        self.mid_frame = Frame(master, padx=10, pady=10)
        self.mid_frame.pack()
        self.bottom_frame = Frame(master, padx=10, pady=10)
        self.bottom_frame.pack()

        # Video List
        Button(self.top_frame, text="Select Video Files", command=self.select_videos).pack(side=tk.LEFT, padx=5)
        self.video_listbox = Listbox(self.top_frame, selectmode=tk.SINGLE, width=50, exportselection=False)
        self.video_listbox.pack(side=tk.LEFT, padx=5)
        
        # Audio List
        Button(self.top_frame, text="Select Audio Files", command=self.select_audios).pack(side=tk.LEFT, padx=5)
        self.audio_listbox = Listbox(self.top_frame, selectmode=tk.SINGLE, width=50, exportselection=False)
        self.audio_listbox.pack(side=tk.LEFT, padx=5)

        # Queue
        Button(self.mid_frame, text="Add Selected Pair to Queue", command=self.add_to_queue).pack()
        self.queue_listbox = Listbox(self.mid_frame, width=100)
        self.queue_listbox.pack(pady=10)

        # Process Button
        self.process_button = Button(self.bottom_frame, text="Start Processing Queue", command=self.start_processing)
        self.process_button.pack()

    def select_videos(self):
        self.videos = filedialog.askopenfilenames(title="Select Video Files")
        self.video_listbox.delete(0, tk.END)
        for v in self.videos: self.video_listbox.insert(tk.END, os.path.basename(v))

    def select_audios(self):
        self.audios = filedialog.askopenfilenames(title="Select Audio Files")
        self.audio_listbox.delete(0, tk.END)
        for a in self.audios: self.audio_listbox.insert(tk.END, os.path.basename(a))

    def add_to_queue(self):
        video_idx = self.video_listbox.curselection()
        audio_idx = self.audio_listbox.curselection()
        if video_idx and audio_idx:
            video_path = self.videos[video_idx[0]]
            audio_path = self.audios[audio_idx[0]]
            self.queue.append((video_path, audio_path))
            self.queue_listbox.insert(tk.END, f"{os.path.basename(video_path)}  ->  {os.path.basename(audio_path)}")
        else:
            print("Please select one video and one audio file to pair.")

    def start_processing(self):
        if not self.queue:
            print("Queue is empty. Add pairs before processing.")
            return
        
        self.master.destroy() # Close the setup window
        
        # Run processing in a separate thread to not freeze the (now closed) GUI
        # This is good practice but for this script, we just run sequentially
        
        # Create a simple status window
        status_window = tk.Tk()
        status_window.title("Processing...")
        status_label = Label(status_window, text="Starting...", font=("Helvetica", 14), padx=20, pady=20)
        status_label.pack()

        # Run the main loop
        for video_path, audio_path in self.queue:
            try:
                process_pair(video_path, audio_path, {}, status_label)
                status_window.update()
            except Exception as e:
                print(f"A critical error occurred processing {video_path}: {e}")

        status_label.config(text="All tasks complete!")
        print("\n★★★ All batch processing complete! ★★★")
        status_window.after(3000, status_window.destroy) # Close after 3 seconds
        status_window.mainloop()

if __name__ == '__main__':
    root = tk.Tk()
    app = BatchProcessorApp(root)
    root.mainloop()