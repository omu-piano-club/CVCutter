import librosa
import librosa.display
import numpy as np
import os
import matplotlib.pyplot as plt
import argparse
from scipy.signal import correlate

def find_anchor(audio, sr, duration_s=15):
    """音声内で最も音量が大きい部分をアンカーとして切り出す"""
    frame_size = int(sr * 0.1) # 0.1秒ごとのエネルギーを計算
    hop_size = int(sr * 0.05)
    
    # 音声エネルギーの移動平均を計算
    energy = np.array([
        np.sum(np.abs(audio[i:i+frame_size])**2)
        for i in range(0, len(audio) - frame_size, hop_size)
    ])
    
    # 最もエネルギーが高いフレームの中心を見つける
    energetic_frame_index = np.argmax(energy)
    center_sample = energetic_frame_index * hop_size + frame_size // 2
    
    # アンカーの開始・終了サンプルを決定
    anchor_duration_samples = int(duration_s * sr)
    start_sample = max(0, center_sample - anchor_duration_samples // 2)
    end_sample = min(len(audio), start_sample + anchor_duration_samples)
    
    anchor_audio = audio[start_sample:end_sample]
    
    print(f"最も特徴的な部分（アンカー）を {start_sample/sr:.2f}秒地点から {duration_s}秒間 切り出しました。")
    return anchor_audio, start_sample

def find_audio_offset(haystack_path, needle_path, target_sr):
    """
    アンカー検索を用いて、2つの音声ファイルのオフセットを高精度に計算する。
    """
    print(f"\n--- 音声同期を開始します (アンカー検索モード) ---")
    print(f"基準音声 (haystack): {os.path.basename(haystack_path)}")
    print(f"対象音声 (needle): {os.path.basename(needle_path)}")
    print(f"処理レート: {target_sr} Hz")

    try:
        # 1. 音声ファイルを読み込み
        print("Haystackファイルを読み込み中...")
        haystack_audio, _ = librosa.load(haystack_path, sr=target_sr)
        
        print("Needleファイルを読み込み中...")
        needle_audio, _ = librosa.load(needle_path, sr=target_sr)
        
        # 2. Needleからアンカー（最も特徴的な部分）を切り出す
        anchor_audio, anchor_start_in_needle = find_anchor(needle_audio, target_sr)

        # 3. 音量を正規化
        print("波形を正規化中...")
        haystack_norm = (haystack_audio - np.mean(haystack_audio)) / np.std(haystack_audio)
        anchor_norm = (anchor_audio - np.mean(anchor_audio)) / np.std(anchor_audio)

        # 4. クロス相関でアンカーをHaystackから探す
        print("アンカーをHaystack内で検索中...")
        correlation = correlate(haystack_norm, anchor_norm, mode='valid')
        
        # 5. 最も相関が高かった位置（ラグ）を見つける
        lag_in_haystack = np.argmax(correlation)
        
        # 6. 最終的なオフセットを計算
        #    Needleの開始位置 = (Haystackで見つかったアンカーの位置) - (Needle内でのアンカーの開始位置)
        final_offset_samples = lag_in_haystack - anchor_start_in_needle
        final_offset_seconds = float(final_offset_samples) / target_sr

        print(f"\n計算完了: NeedleはHaystackの {final_offset_seconds:.4f} 秒地点から始まります。")
        print(f"（正の値はNeedleが遅れて始まることを、負の値はNeedleが先行して始まることを意味します）")
        
        return {
            'offset_seconds': final_offset_seconds,
            'offset_samples': final_offset_samples,
        }

    except Exception as e:
        import traceback
        print(f"音声同期中にエラーが発生しました: {e}")
        traceback.print_exc()
        return None

def plot_verification(haystack_path, needle_path, sr, offset_seconds):

    """
    検出されたオフセットに基づき、2つの波形をプロットして視覚的に一致を確認する。
    """
    print("一致検証のため、波形プロットを生成しています...")
    try:
        needle_audio, _ = librosa.load(needle_path, sr=sr)
        duration_seconds = librosa.get_duration(y=needle_audio, sr=sr)
        
        # オフセットとデュレーションを使って、haystackの該当部分を読み込む
        haystack_segment, _ = librosa.load(
            haystack_path, 
            sr=sr, 
            offset=offset_seconds, 
            duration=duration_seconds
        )
        
        fig, ax = plt.subplots(figsize=(18, 7))
        ax.set_title(f"Audio Alignment Verification (SR: {sr} Hz)")
        
        librosa.display.waveshow(haystack_segment, sr=sr, ax=ax, label="Haystack (Matched Section)", color='blue', alpha=0.75)
        librosa.display.waveshow(needle_audio, sr=sr, ax=ax, label="Needle (Reference)", color='red', alpha=0.6)

        ax.legend()
        plt.tight_layout()
        print("プロットを表示します。ウィンドウを閉じるとプログラムが終了します。")
        plt.show()

    except Exception as e:
        print(f"プロットの生成に失敗しました: {e}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="2つの音声ファイルをクロス相関で同期させます。")
    parser.add_argument('haystack', help="基準となる音声ファイルのパス (例: mic_audio.wav)")
    parser.add_argument('needle', help="同期させたい音声ファイルのパス (例: video_audio.wav)")
    args = parser.parse_args()

    if os.path.exists(args.haystack) and os.path.exists(args.needle):
        # --- 処理に使うサンプリングレートをここで定義 ---
        TARGET_SAMPLE_RATE = 22050
        
        # メインの同期処理を実行
        sync_result = find_audio_offset(args.haystack, args.needle, TARGET_SAMPLE_RATE)
        
        # 結果が得られた場合、グラフで視覚的に確認
        if sync_result:
            plot_verification(
                args.haystack, 
                args.needle, 
                TARGET_SAMPLE_RATE, 
                sync_result['offset_seconds']
            )
    else:
        print(f"エラー: ファイルが見つかりません '{args.haystack}' または '{args.needle}'")