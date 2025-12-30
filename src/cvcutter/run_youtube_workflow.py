#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
YouTube動画アップロード統合ワークフロー

PDF解析、フォーム回答取得、マッピング、YouTubeアップロードを
一括で実行するスクリプト
"""

import sys
import logging
from pathlib import Path
from typing import Optional

# 各モジュールをインポート
from .pdf_parser import parse_concert_pdf
from .google_form_connector import FormResponseParser
from .video_mapper import (
    get_video_files_sorted,
    map_program_to_videos,
    map_with_form_responses,
    generate_upload_metadata
)
from .youtube_uploader import batch_upload

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_full_workflow(
    pdf_path: Path,
    form_csv_path: Optional[Path] = None,
    form_id: Optional[str] = None,
    use_forms_api: bool = True,
    video_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    skip_upload: bool = False,
    use_gemini_matching: bool = True
):
    """
    完全なワークフローを実行

    Args:
        pdf_path: PDFパンフレットのパス
        form_csv_path: Googleフォーム回答CSVのパス（CSVから取得する場合）
        form_id: フォームID（Forms APIから取得する場合）
        use_forms_api: Forms APIから直接回答を取得するか（デフォルト: True）
        video_dir: 動画ファイルのディレクトリ
        output_dir: 中間ファイルと最終メタデータの出力ディレクトリ
        skip_upload: Trueの場合、YouTubeアップロードをスキップ
        use_gemini_matching: Gemini CLIで類似度判定を行うか
    """
    if video_dir is None:
        video_dir = Path(__file__).parent / "output"
    if output_dir is None:
        output_dir = Path(__file__).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 80)
    logger.info("YouTube動画アップロード統合ワークフロー")
    logger.info("=" * 80)

    # ========================================
    # ステップ1: PDF解析
    # ========================================
    logger.info("\n【ステップ1】PDFパンフレットの解析")
    logger.info("-" * 80)

    program_json = output_dir / "program_data.json"

    try:
        program_data = parse_concert_pdf(pdf_path, program_json)
        logger.info(f"✓ PDF解析完了: {len(program_data['performances'])}件の演奏プログラムを抽出")
    except Exception as e:
        logger.error(f"✗ PDF解析に失敗しました: {e}")
        raise

    # ========================================
    # ステップ2: Googleフォーム回答取得
    # ========================================
    logger.info("\n【ステップ2】Googleフォーム回答の取得")
    logger.info("-" * 80)

    form_json = output_dir / "form_data.json"

    try:
        form_parser = FormResponseParser()

        # Forms APIから取得
        if use_forms_api or form_id:
            form_responses = form_parser.load_from_forms_api(form_id)
            logger.info("✓ Forms APIから回答を取得しました")

        # CSVから取得
        elif form_csv_path:
            form_responses = form_parser.load_from_csv(form_csv_path)
            logger.info("✓ CSVから回答を読み込みました")

        else:
            # デフォルト: form_config.jsonから自動取得
            logger.info("form_config.jsonからフォーム情報を読み込みます...")
            form_responses = form_parser.load_from_forms_api()
            logger.info("✓ Forms APIから回答を取得しました")

        form_parser.export_to_json(form_json)
        logger.info(f"✓ フォーム回答取得完了: {len(form_responses)}件")

    except Exception as e:
        logger.error(f"✗ フォーム回答の取得に失敗しました: {e}")
        raise

    # ========================================
    # ステップ3: 動画ファイル取得
    # ========================================
    logger.info("\n【ステップ3】動画ファイルの取得")
    logger.info("-" * 80)

    try:
        video_info_list = get_video_files_sorted(video_dir)
        logger.info(f"✓ 動画ファイル取得完了: {len(video_info_list)}本")
    except Exception as e:
        logger.error(f"✗ 動画ファイルの取得に失敗しました: {e}")
        raise

    # ========================================
    # ステップ4: データマッピング
    # ========================================
    logger.info("\n【ステップ4】データのマッピング")
    logger.info("-" * 80)

    try:
        # プログラム→動画
        program_video_mappings = map_program_to_videos(program_data, video_info_list)

        # アンケート回答との照合
        final_mappings = map_with_form_responses(
            program_video_mappings,
            form_responses,
            use_gemini=use_gemini_matching
        )

        logger.info(f"✓ マッピング完了: {len(final_mappings)}件のマッチング成功")

        # マッピング詳細を保存
        import json
        from datetime import datetime

        mapping_result_json = output_dir / "video_mapping_result.json"
        mapping_result = {
            "mapping_time": datetime.now().isoformat(),
            "total_mappings": len(final_mappings),
            "use_gemini": use_gemini_matching,
            "mappings": final_mappings
        }

        with open(mapping_result_json, 'w', encoding='utf-8') as f:
            json.dump(mapping_result, f, ensure_ascii=False, indent=2)

        logger.info(f"✓ マッピング詳細を保存: {mapping_result_json}")

    except Exception as e:
        logger.error(f"✗ データマッピングに失敗しました: {e}")
        raise

    # ========================================
    # ステップ5: YouTubeアップロードメタデータ生成
    # ========================================
    logger.info("\n【ステップ5】YouTubeアップロードメタデータの生成")
    logger.info("-" * 80)

    upload_metadata_json = output_dir / "upload_metadata.json"

    try:
        concert_info = program_data.get("concert_info")
        upload_metadata = generate_upload_metadata(final_mappings, concert_info)

        import json
        with open(upload_metadata_json, 'w', encoding='utf-8') as f:
            json.dump(upload_metadata, f, ensure_ascii=False, indent=2)

        logger.info(f"✓ メタデータ生成完了: {len(upload_metadata['videos'])}本の動画")
        logger.info(f"✓ メタデータを保存: {upload_metadata_json}")

    except Exception as e:
        logger.error(f"✗ メタデータ生成に失敗しました: {e}")
        raise

    # ========================================
    # 結果サマリー表示
    # ========================================
    logger.info("\n" + "=" * 80)
    logger.info("ワークフロー完了")
    logger.info("=" * 80)

    print("\n【アップロード予定の動画】")
    for i, video_meta in enumerate(upload_metadata['videos'], 1):
        print(f"{i}. {video_meta['title']}")
        print(f"   公開設定: {video_meta['privacy_status']}")

    print(f"\n総数: {len(upload_metadata['videos'])}本")

    # ========================================
    # ステップ6: YouTubeアップロード（オプション）
    # ========================================
    if not skip_upload:
        logger.info("\n【ステップ6】YouTubeへのアップロード")
        logger.info("-" * 80)

        # In GUI mode, we assume yes if skip_upload is False,
        # or we could add another argument 'auto_upload'.
        # For now, let's just proceed or assume affirmative if running from function call.
        # But to be safe for CLI usage, we check if sys.stdin.isatty()

        should_upload = True
        if sys.stdin and sys.stdin.isatty():
             user_input = input("\nYouTubeへのアップロードを開始しますか？ (yes/no): ").strip().lower()
             if user_input not in ['yes', 'y', 'はい']:
                 should_upload = False

        if should_upload:
            try:
                # video_dirではなく、実際の動画ファイルパスを使う
                # final_mappingsから動画ファイルパスを取得して、一時的なメタデータを作成

                # アップロード用に動画パスを含むメタデータを作成
                # youtube_uploader.pyはvideo_dirから動画を取得するので、
                # 動画ファイルが正しくソートされていることを前提とする

                summary = batch_upload(
                    video_dir=video_dir,
                    metadata_file=upload_metadata_json
                )

                logger.info("\n" + "=" * 80)
                logger.info("YouTubeアップロード完了")
                logger.info("=" * 80)
                logger.info(f"成功: {summary['success']}本")
                logger.info(f"失敗: {summary['failed']}本")

            except Exception as e:
                logger.error(f"✗ YouTubeアップロードに失敗しました: {e}")
                raise
        else:
            logger.info("YouTubeアップロードをスキップしました")
            logger.info(f"後でアップロードする場合: python youtube_uploader.py --video-dir {video_dir} --metadata {upload_metadata_json}")
    else:
        logger.info("\n--skip-uploadが指定されたため、YouTubeアップロードをスキップします")
        logger.info(f"後でアップロードする場合: python youtube_uploader.py --video-dir {video_dir} --metadata {upload_metadata_json}")


def main():
    """メイン関数"""
    import argparse

    parser = argparse.ArgumentParser(
        description="YouTube動画アップロード統合ワークフロー"
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        required=True,
        help="PDFパンフレットのパス"
    )
    parser.add_argument(
        "--form-csv",
        type=Path,
        default=None,
        help="Googleフォーム回答のCSVファイル（CSVから取得する場合）"
    )
    parser.add_argument(
        "--form-id",
        type=str,
        default=None,
        help="フォームID（Forms APIから取得する場合）"
    )
    parser.add_argument(
        "--use-csv",
        action="store_true",
        help="CSVファイルから回答を取得（デフォルト: Forms APIから取得）"
    )
    parser.add_argument(
        "--video-dir",
        type=Path,
        default=Path(__file__).parent / "output",
        help="動画ファイルのディレクトリ（デフォルト: output/）"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent,
        help="中間ファイルの出力ディレクトリ（デフォルト: カレントディレクトリ）"
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="YouTubeアップロードをスキップ（メタデータ生成まで）"
    )
    parser.add_argument(
        "--no-gemini",
        action="store_true",
        help="Gemini CLIを使用せず、簡易マッチングを使用"
    )

    args = parser.parse_args()

    try:
        run_full_workflow(
            pdf_path=args.pdf,
            form_csv_path=args.form_csv,
            form_id=args.form_id,
            use_forms_api=not args.use_csv,
            video_dir=args.video_dir,
            output_dir=args.output_dir,
            skip_upload=args.skip_upload,
            use_gemini_matching=not args.no_gemini
        )

    except KeyboardInterrupt:
        logger.info("\n中断されました")
        sys.exit(130)

    except Exception as e:
        logger.exception(f"致命的なエラーが発生しました: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
