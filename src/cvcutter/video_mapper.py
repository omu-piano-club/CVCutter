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

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import json
from .gemini_utils import call_gemini_api, extract_json_from_text, configure_gemini
from .config_manager import ConfigManager

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_video_files_sorted(video_dir: Path) -> List[Dict]:
    """
    動画ファイルをファイル名順にソート

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

    # ファイル名でソート
    video_files.sort(key=lambda f: f.name)

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


def map_with_form_responses(
    program_video_mappings: List[Dict],
    form_responses: List[Dict],
    use_gemini: bool = True
) -> List[Dict]:
    """
    プログラム+動画情報とアンケート回答を紐付け（一括AIマッピング対応）
    """
    if not use_gemini:
        # Geminiを使わない場合の簡易マッチング（フォールバック）
        return _map_simple(program_video_mappings, form_responses)

    logger.info("\n" + "=" * 60)
    logger.info("アンケート回答との一括AI紐付けを開始します")
    logger.info("=" * 60)

    # 有効なプログラム情報のリストを作成
    program_list = []
    for m in program_video_mappings:
        if m.get("program_data") and m.get("video_data"):
            program_list.append({
                "mapping_order": m["mapping_order"],
                "performer_name": m.get("performer_name", ""),
                "piece_title": m.get("piece_title", ""),
                "video_name": m.get("video_name", "")
            })

    if not program_list or not form_responses:
        logger.warning("マッピング対象のデータが不足しています")
        return []

    prompt = f"""
あなたはピアノコンサートの運営スタッフです。
「プログラム情報」と「演奏者からのアンケート回答」を照合し、どのアンケート回答がどのプログラム（動画）に対応するかを紐付けてください。

【プログラム情報（動画紐付け済み）】
{json.dumps(program_list, ensure_ascii=False, indent=2)}

【アンケート回答】
{json.dumps(form_responses, ensure_ascii=False, indent=2)}

【紐付けルール】
1. 演奏者名 (performer_name vs name):
   - 姓名の順序、スペースの有無、常用漢字と旧字体の違いなどを考慮してください。
   - 名字のみ、あるいは名前のみの記載でも、他と重複がなければ同一人物とみなしてください。
2. 曲名 (piece_title):
   - 略称、通称、日本語/外国語表記の違いを考慮してください。
   - 作品番号 (Op., BWV等) や楽章番号が一部欠落していても、演奏者が一致すれば同一曲とみなしてください。
3. 全体最適化:
   - 1つのアンケート回答が複数のプログラムにマッチしそうな場合は、全体のバランスを見て最も自然な組み合わせを決定してください。
   - アンケート回答者がプログラムに存在しない場合は、mapping_order を null にしてください。

【出力形式】
必ず以下のJSON構造のみを返してください。

```json
{{
  "mappings": [
    {{
      "response_id": アンケートのID,
      "mapping_order": マッチしたプログラムの mapping_order (数値、見つからない場合は null),
      "confidence_score": 0-100の信頼度,
      "reason": "紐付けた理由（例：氏名が完全一致、曲名が「月光」で共通など）"
    }}
  ]
}}
```
"""

    try:
        config = ConfigManager().config
        api_key = config['workflow'].get('gemini_api_key')
        if not api_key:
            raise ValueError("Gemini APIキーが設定されていません。")
        
        configure_gemini(api_key)
        model_name = config['workflow'].get('gemini_model', 'gemini-2.5-flash')
        
        output = call_gemini_api(prompt, model_name=model_name)
        result_data = extract_json_from_text(output)
        
        mapping_dict = {m["response_id"]: m for m in result_data.get("mappings", [])}
        
        # 紐付け結果の整理
        # アンケート回答があったものだけを抽出する方針
        mapping_dict = {m["response_id"]: m for m in result_data.get("mappings", [])}
        
        final_mappings = []
        for form_resp in form_responses:
            res_id = form_resp["response_id"]
            m_info = mapping_dict.get(res_id)
            
            if m_info and m_info["mapping_order"] is not None:
                target_order = m_info["mapping_order"]
                # mapping_order から元のマッピング情報を探す
                best_match = next((m for m in program_video_mappings if m["mapping_order"] == target_order), None)
                
                if best_match:
                    final_mapping = {
                        **best_match,
                        "form_response": form_resp,
                        "confidence_score": m_info["confidence_score"],
                        "match_reason": m_info["reason"],
                        "matched": True
                    }
                    final_mappings.append(final_mapping)
                    logger.info(f"✓ アンケート {res_id} -> プログラム {target_order} (信頼度: {m_info['confidence_score']}%)")
                else:
                    logger.warning(f"？ アンケート {res_id} が指定したプログラム番号 {target_order} が見つかりません")
            else:
                logger.warning(f"✗ アンケート {res_id} ({form_resp.get('name')}) にマッチするプログラムが見つかりませんでした")

        return final_mappings

    except Exception as e:
        logger.error(f"一括マッピングエラー: {e}")
        return _map_simple(program_video_mappings, form_responses)

def _map_simple(program_video_mappings, form_responses):
    """
    簡易的な文字列一致によるマッピング（フォールバック用）
    """
    final_mappings = []
    for form_resp in form_responses:
        form_name = form_resp.get("name", "")
        form_piece = form_resp.get("piece_title", "")
        
        for mapping in program_video_mappings:
            if not mapping.get("program_data") or not mapping.get("video_data"):
                continue
            
            p_name = mapping.get("performer_name", "")
            p_piece = mapping.get("piece_title", "")
            
            if (form_name in p_name or p_name in form_name) and \
               (form_piece[:5] in p_piece or p_piece[:5] in form_piece):
                final_mappings.append({
                    **mapping,
                    "form_response": form_resp,
                    "confidence_score": 70.0,
                    "match_reason": "簡易一致",
                    "matched": True
                })
                break
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
