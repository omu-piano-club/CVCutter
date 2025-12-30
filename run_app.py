import sys
import os
from pathlib import Path

# exe内部または実行環境のパスを調整
if getattr(sys, 'frozen', False):
    # PyInstallerで固められたexeとして実行されている場合
    base_path = Path(sys._MEIPASS)
else:
    # 通常のPythonスクリプトとして実行されている場合
    base_path = Path(__file__).parent / "src"

sys.path.insert(0, str(base_path))

# cvcutterパッケージからメイン関数をインポート
from cvcutter.app import main

if __name__ == "__main__":
    main()