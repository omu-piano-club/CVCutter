import os
import re
import subprocess
import shutil
import imageio_ffmpeg
import numpy as np
from pathlib import Path
try:
    from moviepy.editor import VideoFileClip, AudioFileClip
except ImportError:
    from moviepy import VideoFileClip, AudioFileClip
from .detect_performances import detect_performances_by_motion
from .sync_audio import find_audio_offset
from tqdm import tqdm
import time
from .video_utils import concatenate_videos, get_gpu_args

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

def run_ffmpeg_with_progress(command, duration, progress_callback=None):
    """
    Executes FFMPEG with progress monitoring.
    progress_callback: function(current_time, total_duration, message)
    """
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    if command[0] == 'ffmpeg':
        command[0] = ffmpeg_path
        
    process = subprocess.Popen(command, stderr=subprocess.PIPE, universal_newlines=True, encoding='utf-8')
    time_regex = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")

    # We still use tqdm for CLI output but also call the callback for GUI
    with tqdm(total=duration, unit='s', desc="    Encoding", ncols=80) as pbar:
        last_time = 0
        for line in process.stderr:
            match = time_regex.search(line)
            if match:
                hours, minutes, seconds, ms = map(int, match.groups())
                current_time = hours * 3600 + minutes * 60 + seconds + ms / 100
                update_amount = current_time - last_time
                pbar.update(update_amount)

                if progress_callback:
                    progress_callback(current_time, duration, f"Encoding: {pbar.n:.2f} / {pbar.total:.2f} s")

                last_time = current_time

    process.wait()
    if process.returncode != 0:
        print(f"  ERROR: FFMPEG process failed with code {process.returncode}")
        return False
    return True

def process_pair(video_paths, audio_path, config_overrides, progress_callback=None):
    """
    Main processing logic for a single video/audio pair.
    video_paths can be a single string or a list of strings (for concatenated segments).

    progress_callback: function(current_value, max_value, message) or similar
                       Here we adapt it to update status text.
    """

    def update_status(text):
        if progress_callback:
            # Pass 0,0 to indicate just a status update, not progress bar
            progress_callback(0, 0, text)
            print(text)

    # Ensure paths are strings
    if isinstance(video_paths, (str, Path)):
        video_paths = [str(video_paths)]
    else:
        video_paths = [str(p) for p in video_paths]
    
    audio_path = str(audio_path) if audio_path else None

    # Handle concatenation if multiple videos provided
    if len(video_paths) > 1:
        update_status("Concatenating video segments...")
        concat_video_path = os.path.join(config_overrides.get('temp_dir', 'temp'), "concatenated_input.mp4")
        if not concatenate_videos(video_paths, concat_video_path):
            print("Failed to concatenate videos. Using only the first one.")
            video_path = video_paths[0]
        else:
            video_path = concat_video_path
    else:
        video_path = video_paths[0]

    update_status(f"Processing: {os.path.basename(video_path)}")
    print(f"\n=======================================================")
    print(f"Processing Video: {video_path}")
    print(f"Processing Audio: {audio_path if audio_path else 'None (using video audio only)'}")
    print(f"=======================================================")

    config = {
        'video_path': video_path,
        'mic_audio_path': audio_path,
        'output_dir': 'output',
        'temp_dir': 'temp',
        'video_audio_volume': 0.6,
        'mic_audio_volume': 1.5,
        'audio_sync_sample_rate': 22050,
        'use_gpu': True,
        'detection_config': { 'max_seconds_to_process': None, 'min_duration_seconds': 30, 'show_video': False,
                              'mog2_threshold': 40, 'min_contour_area': 3000, 'left_zone_end_percent': 0.25,
                              'center_zone_end_percent': 0.55 }
    }
    config.update(config_overrides)

    os.makedirs(config['output_dir'], exist_ok=True)
    os.makedirs(config['temp_dir'], exist_ok=True)

    # --- Step 1: Detect Segments ---
    update_status(f"Detecting segments for {os.path.basename(video_path)}...")
    performance_segments = detect_performances_by_motion(config['video_path'], config['detection_config'])
    if not performance_segments:
        print("\nNo performance segments found. Skipping to next pair.")
        return
    print(f"\nDetected {len(performance_segments)} performance segments.")

    # --- Step 2: Sync ---
    global_offset = 0
    if config['mic_audio_path']:
        update_status(f"Syncing audio for {os.path.basename(video_path)}...")
        all_offsets = []

        # We need to handle MoviePy not blocking the UI if possible, but here it runs in the thread
        with VideoFileClip(config['video_path']) as video:
            for i, (start, end) in enumerate(performance_segments):
                needle_path = os.path.join(config['temp_dir'], f'needle_{i+1}.wav')
                # Extract audio for sync
                video.audio.subclip(start, end).write_audiofile(needle_path, fps=config['audio_sync_sample_rate'], logger=None)

                sync_result = find_audio_offset(config['mic_audio_path'], needle_path, config['audio_sync_sample_rate'])
                if sync_result:
                    all_offsets.append(sync_result['offset_seconds'] - start)

                # Simple progress update
                if progress_callback:
                    progress_callback(i+1, len(performance_segments), f"Syncing segment {i+1}/{len(performance_segments)}")

        if not all_offsets:
            print("Audio synchronization failed. Falling back to video audio only.")
            config['mic_audio_path'] = None
        else:
            global_offset = get_consensus_offset(all_offsets)
            print(f"\nFinal consensus global time offset: {global_offset:.4f} seconds")

    # --- Step 3: Process with FFMPEG ---
    for i, (start_time, end_time) in enumerate(performance_segments):
        duration = end_time - start_time
        base_name = os.path.splitext(os.path.basename(video_path))[0]
        output_filename = os.path.join(config['output_dir'], f"{base_name}_performance_{i+1}.mp4")

        update_status(f"Encoding segment {i+1} of {os.path.basename(video_path)}...")

        # Base command with input video
        command = ['ffmpeg', '-y']
        
        # Check for GPU acceleration
        gpu_args = get_gpu_args() if config.get('use_gpu') else ['-c:v', 'libx264', '-preset', 'medium']
        vcodec_idx = gpu_args.index('-c:v') + 1
        vcodec = gpu_args[vcodec_idx]
        extra_args = gpu_args[vcodec_idx+1:]

        if config['mic_audio_path']:
            mic_start = start_time + global_offset
            if mic_start < 0:
                print(f"Warning: Mic start time {mic_start} is negative for segment {i+1}. Skipping sync for this segment.")
                # Fallback for this segment
                command += ['-ss', str(start_time), '-i', config['video_path'], '-t', str(duration),
                            '-map', '0:v', '-map', '0:a', '-vf', 'yadif', '-c:v', vcodec] + extra_args + \
                           ['-c:a', 'aac', '-b:a', '192k', output_filename]
            else:
                command += ['-ss', str(start_time), '-i', config['video_path'],
                            '-ss', str(mic_start), '-i', config['mic_audio_path'],
                            '-t', str(duration), '-filter_complex',
                            f"[0:a]volume={config['video_audio_volume']}[a0];[1:a]volume={config['mic_audio_volume']}[a1];[a0][a1]amix=inputs=2[aout]",
                            '-map', '0:v', '-map', '[aout]', '-vf', 'yadif', '-c:v', vcodec] + extra_args + \
                           ['-c:a', 'aac', '-b:a', '192k', output_filename]
        else:
            # Video audio only
            command += ['-ss', str(start_time), '-i', config['video_path'], '-t', str(duration),
                        '-map', '0:v', '-map', '0:a', '-vf', 'yadif', '-c:v', vcodec] + extra_args + \
                       ['-c:a', 'aac', '-b:a', '192k', output_filename]

        run_ffmpeg_with_progress(command, duration, progress_callback)
