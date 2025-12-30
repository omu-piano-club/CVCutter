import PyInstaller.__main__
import os
import shutil
from pathlib import Path
import customtkinter

def build():
    # Clean previous builds
    for d in ['build', 'dist']:
        if os.path.exists(d):
            shutil.rmtree(d)

    ctk_path = os.path.dirname(customtkinter.__file__)
    
    PyInstaller.__main__.run([
        'run_app.py',
        '--name=CVCutter',
        '--windowed',
        '--onefile',
        f'--icon=src/favicon.ico',
        f'--add-data={ctk_path};customtkinter/',
        '--add-data=src/cvcutter;cvcutter/',
        '--copy-metadata=imageio',
        '--collect-all=cv2',
        '--collect-all=moviepy',
        '--collect-all=librosa',
        '--collect-all=scipy',
        '--collect-all=imageio_ffmpeg',
        '--hidden-import=cvcutter.config_manager',
        '--hidden-import=cvcutter.video_processor',
        '--hidden-import=cvcutter.run_youtube_workflow',
        '--hidden-import=cvcutter.create_google_form',
        '--hidden-import=cvcutter.video_mapper',
        '--hidden-import=cvcutter.google_form_connector',
        '--hidden-import=cvcutter.pdf_parser',
        '--hidden-import=cvcutter.video_utils',
        '--hidden-import=cvcutter.detect_performances',
        '--hidden-import=cvcutter.sync_audio',
        '--hidden-import=cvcutter.youtube_uploader',
    ])

if __name__ == "__main__":
    build()