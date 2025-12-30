#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
動画マッピングスクリプト

PDFパンフレット情報、動画ファイル、Googleフォーム回答を統合し、
YouTubeアップロード用のメタデータを生成します。

マッピングロジック:
1. PDF情報 → 動画ファイル: プログラム順序と動画作成時刻の順番で紐付け
2. PDF+動画 → アンケート回答: 曲名+演奏者名で類似度判定（Gemini CLI使用）
3. アンケート回答がないものは除外
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_video_files_sorted(video_dir: Path) -> List[Dict]:
    """
    動画ファイルを作成時刻順にソート

    Args:
        video_dir: 動画ファイルのディレクトリ

    Returns:
        動画ファイル情報のリスト
    """
    video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv']
    video_files = [
        f for f in video_dir.iterdir()
        if f.is_file() and f.suffix.lower() in video_extensions
    ]

    # 作成時刻でソート（古い順）
    video_files.sort(key=lambda f: f.stat().st_ctime)

    video_info_list = []
    for i, video_file in enumerate(video_files, 1):
        ctime = datetime.fromtimestamp(video_file.stat().st_ctime)
        video_info_list.append({
            "file_order": i,
            "file_path": str(video_file),
            "file_name": video_file.name,
            "created_time": ctime.isoformat(),
            "created_timestamp": video_file.stat().st_ctime
        })

    logger.info(f"動画ファイル {len(video_info_list)}本を検出しました")
    for info in video_info_list:
        logger.info(f"  {info['file_order']}. {info['file_name']} ({info['created_time']})")

    return video_info_list


def map_program_to_videos(program_data: Dict, video_info_list: List[Dict]) -> List[Dict]:
    """
    PDFプログラム情報と動画ファイルを順序で紐付け

    Args:
        program_data: PDF解析結果
        video_info_list: 動画ファイル情報リスト

    Returns:
        紐付け結果のリスト
    """
    performances = program_data.get("performances", [])

    # program_orderでソート
    performances_sorted = sorted(performances, key=lambda p: p.get("program_order", 999))

    if len(performances_sorted) != len(video_info_list):
        logger.warning(
            f"プログラム数（{len(performances_sorted)}）と動画数（{len(video_info_list)}）が一致しません"
        )

    # 順序で紐付け
    mappings = []
    max_count = max(len(performances_sorted), len(video_info_list))

    for i in range(max_count):
        mapping = {
            "mapping_order": i + 1
        }

        if i < len(performances_sorted):
            perf = performances_sorted[i]
            mapping["program_data"] = perf
            mapping["performer_name"] = perf.get("performer_name", "")
            mapping["piece_title"] = perf.get("piece_title", "")
            mapping["piece_composer"] = perf.get("piece_composer", "")
        else:
            mapping["program_data"] = None

        if i < len(video_info_list):
            video = video_info_list[i]
            mapping["video_data"] = video
            mapping["video_file"] = video["file_path"]
            mapping["video_name"] = video["file_name"]
        else:
            mapping["video_data"] = None
            mapping["video_file"] = None

        mappings.append(mapping)

    logger.info(f"\nプログラム→動画の紐付け: {len(mappings)}件")
    for m in mappings:
        if m["program_data"] and m["video_data"]:
            logger.info(
                f"  {m['mapping_order']}. {m['performer_name']} / {m['piece_title']} "
                f"→ {m['video_name']}"
            )
        elif m["program_data"]:
            logger.warning(f"  {m['mapping_order']}. {m['performer_name']} → 動画なし")
        elif m["video_data"]:
            logger.warning(f"  {m['mapping_order']}. プログラム情報なし → {m['video_name']}")

    return mappings


def match_with_gemini_cli(
    program_performer: str,
    program_piece: str,
    form_performer: str,
    form_piece: str
) -> Tuple[bool, float, str]:
    """
    Gemini CLIを使って演奏者名と曲名の類似度を判定

    Args:
        program_performer: パンフレットの演奏者名
        program_piece: パンフレットの曲名
        form_performer: アンケートの演奏者名
        form_piece: アンケートの曲名

    Returns:
        (マッチするか, 信頼度スコア0-100, 理由)
    """
    prompt = f"""
以下の2つの演奏情報が同一の演奏を指しているか判定してください。

【パンフレット情報】
演奏者: {program_performer}
曲名: {program_piece}

【アンケート回答】
演奏者: {form_performer}
曲名: {form_piece}

判定基準:
1. 演奏者名が一致または類似しているか（姓のみ、名のみ、表記揺れを考慮）
2. 曲名が一致または類似しているか（略称、異なる表記、作品番号の有無を考慮）

以下のJSON形式で回答してください:
```json
{{
  "is_match": true または false,
  "confidence_score": 0-100の数値,
  "reason": "判定理由の説明"
}}
```

JSONのみを出力してください。
"""

    try:
        result = subprocess.run(
            ["gemini", prompt],
            capture_output=True,
            text=True,
            timeout=30,
            encoding='utf-8'
        )

        if result.returncode != 0:
            logger.error(f"Gemini CLIエラー: {result.stderr}")
            return False, 0.0, "エラー"

        output = result.stdout.strip()

        # JSON抽出
        if "```json" in output:
            start = output.find("```json") + 7
            end = output.find("```", start)
            json_str = output[start:end].strip()
        elif "```" in output:
            start = output.find("```") + 3
            end = output.find("```", start)
            json_str = output[start:end].strip()
        else:
            json_str = output

        result_data = json.loads(json_str)

        is_match = result_data.get("is_match", False)
        confidence = float(result_data.get("confidence_score", 0))
        reason = result_data.get("reason", "")

        return is_match, confidence, reason

    except subprocess.TimeoutExpired:
        logger.error("Gemini CLIがタイムアウトしました")
        return False, 0.0, "タイムアウト"

    except Exception as e:
        logger.error(f"マッチング判定エラー: {e}")
        return False, 0.0, f"エラー: {e}"


def map_with_form_responses(
    program_video_mappings: List[Dict],
    form_responses: List[Dict],
    use_gemini: bool = True
) -> List[Dict]:
    """
    プログラム+動画情報とアンケート回答を紐付け

    Args:
        program_video_mappings: プログラム→動画の紐付け結果
        form_responses: アンケート回答リスト
        use_gemini: Gemini CLIで類似度判定を行うか

    Returns:
        最終的なマッピング結果（アンケート回答があるもののみ）
    """
    final_mappings = []

    logger.info("\n" + "=" * 60)
    logger.info("アンケート回答との紐付けを開始します")
    logger.info("=" * 60)

    # アンケート回答でループ
    for form_resp in form_responses:
        form_name = form_resp.get("name", "")
        form_piece = form_resp.get("piece_title", "")

        logger.info(f"\n【アンケート回答 {form_resp['response_id']}】")
        logger.info(f"演奏者: {form_name}, 曲名: {form_piece}")

        best_match = None
        best_score = 0.0
        best_reason = ""

        # プログラム+動画情報と照合
        for mapping in program_video_mappings:
            if not mapping.get("program_data") or not mapping.get("video_data"):
                continue

            program_name = mapping.get("performer_name", "")
            program_piece = mapping.get("piece_title", "")

            if use_gemini:
                # Gemini CLIで類似度判定
                is_match, confidence, reason = match_with_gemini_cli(
                    program_name, program_piece,
                    form_name, form_piece
                )

                logger.info(
                    f"  vs プログラム {mapping['mapping_order']} "
                    f"({program_name} / {program_piece}): "
                    f"{'✓' if is_match else '✗'} 信頼度 {confidence:.0f}% - {reason}"
                )

                if is_match and confidence > best_score:
                    best_match = mapping
                    best_score = confidence
                    best_reason = reason

            else:
                # 簡易的な文字列マッチング（Gemini未使用時）
                name_match = form_name in program_name or program_name in form_name
                piece_match = form_piece in program_piece or program_piece in form_piece

                if name_match and piece_match:
                    confidence = 80.0
                    best_match = mapping
                    best_score = confidence
                    best_reason = "文字列部分一致"
                    break

        if best_match:
            # マッチング成功
            final_mapping = {
                **best_match,
                "form_response": form_resp,
                "confidence_score": best_score,
                "match_reason": best_reason,
                "matched": True
            }
            final_mappings.append(final_mapping)

            logger.info(f"  → ✓ マッチング成功 (信頼度: {best_score:.0f}%)")
        else:
            # マッチング失敗
            logger.warning(f"  → ✗ マッチするプログラムが見つかりませんでした")

    logger.info("\n" + "=" * 60)
    logger.info(f"マッチング完了: {len(final_mappings)}/{len(form_responses)}件")
    logger.info("=" * 60)

    return final_mappings


def generate_upload_metadata(mappings: List[Dict], concert_info: Optional[Dict] = None) -> Dict:
    """
    YouTubeアップロード用のメタデータを生成

    Args:
        mappings: 最終マッピング結果
        concert_info: コンサート情報（オプション）

    Returns:
        upload_metadata.json形式のデータ
    """
    videos = []

    for mapping in mappings:
        form_resp = mapping.get("form_response", {})
        program_data = mapping.get("program_data", {})

        # 演奏者名
        performer_name = program_data.get("performer_name", "")
        if not form_resp.get("display_name", True):
            performer_name = ""  # 氏名非表示の場合

        # タイトル
        piece_title = program_data.get("piece_title", form_resp.get("piece_title", ""))
        if performer_name:
            title = f"{piece_title} - {performer_name}"
        else:
            title = piece_title

        # 説明文
        description_parts = []

        if concert_info:
            description_parts.append(f"{concert_info.get('title', 'コンサート')}での演奏\n")

        if performer_name:
            description_parts.append(f"演奏者: {performer_name}")

        description_parts.append(f"曲名: {piece_title}")

        if program_data.get("piece_composer"):
            description_parts.append(f"作曲: {program_data['piece_composer']}")

        # アンケートの追加説明文
        extra_desc = form_resp.get("description_extra", "")
        if extra_desc:
            description_parts.append(f"\n{extra_desc}")

        description_parts.append("\n※この動画は自動編集ソフトウェアにより処理されています")

        description = "\n".join(description_parts)

        # タグ
        tags = ["ピアノ", "クラシック", "コンサート"]
        composer = program_data.get("piece_composer", "")
        if composer:
            tags.append(composer)

        # メタデータ
        video_metadata = {
            "title": title[:100],  # 最大100文字
            "description": description[:5000],  # 最大5000バイト
            "tags": tags,
            "privacy_status": form_resp.get("privacy", "unlisted"),
            "playlist_id": ""
        }

        videos.append(video_metadata)

        logger.info(f"メタデータ生成: {title}")

    upload_metadata = {
        "videos": videos
    }

    return upload_metadata


def main():
    """メイン関数（スタンドアロン実行用）"""
    import argparse

    parser = argparse.ArgumentParser(
        description="PDF、動画ファイル、アンケート回答を統合してマッピング"
    )
    parser.add_argument(
        "--program-json",
        type=Path,
        required=True,
        help="PDF解析結果のJSONファイル（pdf_parser.pyの出力）"
    )
    parser.add_argument(
        "--form-json",
        type=Path,
        required=True,
        help="アンケート回答のJSONファイル（google_form_connector.pyの出力）"
    )
    parser.add_argument(
        "--video-dir",
        type=Path,
        default=Path(__file__).parent / "output",
        help="動画ファイルのディレクトリ（デフォルト: output/）"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "upload_metadata.json",
        help="出力するメタデータファイル（デフォルト: upload_metadata.json）"
    )
    parser.add_argument(
        "--no-gemini",
        action="store_true",
        help="Gemini CLIを使用せず、簡易マッチングを使用"
    )
    parser.add_argument(
        "--mapping-output",
        type=Path,
        default=Path(__file__).parent / "video_mapping_result.json",
        help="マッピング結果の詳細を保存するファイル（デフォルト: video_mapping_result.json）"
    )

    args = parser.parse_args()

    try:
        # 1. データ読み込み
        logger.info("=" * 60)
        logger.info("データ読み込み")
        logger.info("=" * 60)

        with open(args.program_json, 'r', encoding='utf-8') as f:
            program_data = json.load(f)
        logger.info(f"✓ PDFプログラム情報: {args.program_json}")

        with open(args.form_json, 'r', encoding='utf-8') as f:
            form_data = json.load(f)
            form_responses = form_data.get("responses", [])
        logger.info(f"✓ アンケート回答: {args.form_json} ({len(form_responses)}件)")

        # 2. 動画ファイル取得
        video_info_list = get_video_files_sorted(args.video_dir)

        # 3. プログラム→動画のマッピング
        program_video_mappings = map_program_to_videos(program_data, video_info_list)

        # 4. アンケート回答との紐付け
        use_gemini = not args.no_gemini
        final_mappings = map_with_form_responses(
            program_video_mappings,
            form_responses,
            use_gemini=use_gemini
        )

        # 5. アップロードメタデータ生成
        logger.info("\n" + "=" * 60)
        logger.info("YouTubeアップロード用メタデータを生成")
        logger.info("=" * 60)

        concert_info = program_data.get("concert_info")
        upload_metadata = generate_upload_metadata(final_mappings, concert_info)

        # 6. 保存
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(upload_metadata, f, ensure_ascii=False, indent=2)
        logger.info(f"\n✓ アップロードメタデータを保存: {args.output}")

        # マッピング詳細も保存
        mapping_result = {
            "mapping_time": datetime.now().isoformat(),
            "total_mappings": len(final_mappings),
            "use_gemini": use_gemini,
            "mappings": final_mappings
        }
        with open(args.mapping_output, 'w', encoding='utf-8') as f:
            json.dump(mapping_result, f, ensure_ascii=False, indent=2)
        logger.info(f"✓ マッピング詳細を保存: {args.mapping_output}")

        # 結果サマリー
        print("\n" + "=" * 60)
        print("マッピング結果サマリー")
        print("=" * 60)
        print(f"アップロード対象動画: {len(upload_metadata['videos'])}本")
        print()

        for i, video_meta in enumerate(upload_metadata['videos'], 1):
            print(f"{i}. {video_meta['title']}")
            print(f"   公開設定: {video_meta['privacy_status']}")
            print()

    except KeyboardInterrupt:
        logger.info("\n中断されました")
        sys.exit(130)

    except Exception as e:
        logger.exception(f"エラーが発生しました: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
