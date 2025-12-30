import json
import os
from pathlib import Path

CONFIG_FILE = "app_config.json"

DEFAULT_CONFIG = {
    "paths": {
        "output_dir": "output",
        "temp_dir": "temp",
        "video_dir": "",
        "audio_dir": "",
        "pdf_path": "",
        "form_id": "",
        "form_csv_path": ""
    },
    "processing": {
        "video_audio_volume": 0.6,
        "mic_audio_volume": 1.5,
        "audio_sync_sample_rate": 22050,
        "mog2_threshold": 40,
        "min_contour_area": 3000,
        "min_duration_seconds": 30
    },
    "workflow": {
        "use_forms_api": True,
        "use_gemini": True,
        "skip_upload": False
    }
}

class ConfigManager:
    def __init__(self, config_path=CONFIG_FILE):
        self.config_path = config_path
        self.config = self.load_config()

    def load_config(self):
        if not os.path.exists(self.config_path):
            return DEFAULT_CONFIG.copy()

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                saved_config = json.load(f)
                # Merge with default to ensure all keys exist
                merged_config = DEFAULT_CONFIG.copy()
                for section, values in saved_config.items():
                    if section in merged_config:
                        merged_config[section].update(values)
                    else:
                        merged_config[section] = values
                return merged_config
        except Exception as e:
            print(f"Error loading config: {e}. Using defaults.")
            return DEFAULT_CONFIG.copy()

    def save_config(self):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
            print(f"Config saved to {self.config_path}")
        except Exception as e:
            print(f"Error saving config: {e}")

    def get(self, section, key):
        return self.config.get(section, {}).get(key)

    def set(self, section, key, value):
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = value
        self.save_config()

    def update_section(self, section, data_dict):
        if section not in self.config:
            self.config[section] = {}
        self.config[section].update(data_dict)
        self.save_config()
