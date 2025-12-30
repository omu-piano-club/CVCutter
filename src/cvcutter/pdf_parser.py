#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PDF解析スクリプト（Gemini CLI使用）

コンサートパンフレットPDFから演奏プログラム情報を抽出します。
- Gemini CLIを使用してPDFを解析
- 演奏順序、演奏者名、曲名を構造化データとして出力
"""

import os
import sys
import json
import subprocess
import logging
from pathlib import Path
from typing import Dict, List, Optional

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Gemini CLIのプロンプト
GEMINI_PROMPT = """
このPDFはピアノコンサートのパンフレットです。
演奏プログラムの情報を抽出し、JSON形式で出力してください。

以下の形式で出力してください：

```json
{
  "concert_info": {
    "title": "コンサートのタイトル",
    "date": "開催日",
    "venue": "会場名"
  },
  "performances": [
    {
      "program_order": 1,
      "performer_name": "演奏者のフルネーム",
      "piece_title": "曲名（作曲家名を含む完全な形式）",
      "piece_composer": "作曲家名",
      "notes": "備考があれば"
    }
  ]
}
```

注意事項：
- program_orderは演奏順序を表す連番（1から開始）
- performer_nameは必ず抽出してください
- piece_titleは正確に、作品番号なども含めて記載してください
- 複数の演奏がある場合は、プログラムに記載されている順序通りに並べてください
- JSONのみを出力し、余計な説明文は不要です
"""


def check_gemini_cli() -> bool:
    """
    Gemini CLIがインストールされているか確認

    Returns:
        True: インストール済み, False: 未インストール
    """
    try:
        result = subprocess.run(
            ["gemini", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False
    except Exception as e:
        logger.warning(f"Gemini CLIの確認中にエラー: {e}")
        return False


def parse_pdf_with_gemini(pdf_path: Path, prompt: str = GEMINI_PROMPT) -> Optional[str]:
    """
    Gemini CLIを使用してPDFを解析

    Args:
        pdf_path: 解析するPDFファイルのパス
        prompt: Gemini CLIに送るプロンプト

    Returns:
        Gemini CLIの出力（JSON文字列を含む）
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDFファイルが見つかりません: {pdf_path}")

    # Gemini CLIが利用可能か確認
    if not check_gemini_cli():
        raise RuntimeError(
            "Gemini CLIが見つかりません。\n"
            "以下のコマンドでインストールしてください：\n"
            "npm install -g @google/generative-ai-cli\n"
            "または\n"
            "pip install google-generativeai"
        )

    logger.info(f"PDFを解析しています: {pdf_path}")

    try:
        # Gemini CLIを実行
        # 形式: gemini "プロンプト" --file path/to/file.pdf
        cmd = [
            "gemini",
            prompt,
            "--file", str(pdf_path)
        ]

        logger.debug(f"実行コマンド: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,  # タイムアウト60秒
            encoding='utf-8'
        )

        if result.returncode != 0:
            logger.error(f"Gemini CLIがエラーを返しました: {result.stderr}")
            raise RuntimeError(f"Gemini CLI実行エラー: {result.stderr}")

        output = result.stdout.strip()
        logger.debug(f"Gemini CLI出力: {output[:200]}...")  # 最初の200文字のみログ

        return output

    except subprocess.TimeoutExpired:
        logger.error("Gemini CLIがタイムアウトしました")
        raise

    except Exception as e:
        logger.error(f"PDF解析中にエラーが発生: {e}")
        raise


def extract_json_from_output(output: str) -> Dict:
    """
    Gemini CLIの出力からJSON部分を抽出

    Args:
        output: Gemini CLIの出力

    Returns:
        解析されたJSON辞書
    """
    # JSONコードブロックを探す
    if "```json" in output:
        # ```json ... ``` の形式
        start = output.find("```json") + 7
        end = output.find("```", start)
        json_str = output[start:end].strip()
    elif "```" in output:
        # ``` ... ``` の形式
        start = output.find("```") + 3
        end = output.find("```", start)
        json_str = output[start:end].strip()
    else:
        # コードブロックなし、全体をJSONとして扱う
        json_str = output.strip()

    try:
        data = json.loads(json_str)
        return data
    except json.JSONDecodeError as e:
        logger.error(f"JSON解析エラー: {e}")
        logger.error(f"解析しようとした文字列:\n{json_str[:500]}")
        raise ValueError(f"Gemini CLIの出力がJSON形式ではありません: {e}")


def validate_program_data(data: Dict) -> bool:
    """
    抽出されたプログラムデータを検証

    Args:
        data: 解析されたJSON辞書

    Returns:
        True: 有効, False: 無効
    """
    if "performances" not in data:
        logger.error("'performances'キーが見つかりません")
        return False

    performances = data["performances"]

    if not isinstance(performances, list) or len(performances) == 0:
        logger.error("'performances'が空またはリストではありません")
        return False

    # 各演奏データの検証
    for i, perf in enumerate(performances, 1):
        required_keys = ["program_order", "performer_name", "piece_title"]
        for key in required_keys:
            if key not in perf or not perf[key]:
                logger.warning(f"演奏 {i}: '{key}'が欠落しています")

    return True


def parse_concert_pdf(pdf_path: Path, output_json: Optional[Path] = None) -> Dict:
    """
    コンサートパンフレットPDFを解析してプログラム情報を抽出

    Args:
        pdf_path: PDFファイルのパス
        output_json: 結果を保存するJSONファイルパス（オプション）

    Returns:
        抽出されたプログラム情報（辞書）
    """
    logger.info("=" * 60)
    logger.info("PDF解析開始")
    logger.info("=" * 60)

    # Gemini CLIでPDFを解析
    gemini_output = parse_pdf_with_gemini(pdf_path)

    # JSON部分を抽出
    logger.info("Gemini CLIの出力からJSONを抽出しています...")
    program_data = extract_json_from_output(gemini_output)

    # データ検証
    if not validate_program_data(program_data):
        raise ValueError("抽出されたプログラムデータが無効です")

    # 結果をログ出力
    logger.info(f"\n抽出された演奏プログラム: {len(program_data['performances'])}件")
    for perf in program_data["performances"]:
        logger.info(
            f"  {perf.get('program_order')}. "
            f"{perf.get('performer_name')} - "
            f"{perf.get('piece_title')}"
        )

    # JSONファイルに保存（指定されている場合）
    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump(program_data, f, ensure_ascii=False, indent=2)
        logger.info(f"\n結果を保存しました: {output_json}")

    logger.info("=" * 60)
    logger.info("PDF解析完了")
    logger.info("=" * 60)

    return program_data


def main():
    """メイン関数（スタンドアロン実行用）"""
    import argparse

    parser = argparse.ArgumentParser(
        description="コンサートパンフレットPDFから演奏プログラム情報を抽出"
    )
    parser.add_argument(
        "pdf_file",
        type=Path,
        help="解析するPDFファイルのパス"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="結果を保存するJSONファイルパス（デフォルト: <pdf名>_program.json）"
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default=GEMINI_PROMPT,
        help="カスタムプロンプト（オプション）"
    )

    args = parser.parse_args()

    # 出力ファイル名のデフォルト設定
    if args.output is None:
        args.output = args.pdf_file.parent / f"{args.pdf_file.stem}_program.json"

    try:
        program_data = parse_concert_pdf(args.pdf_file, args.output)

        # 簡易的な結果表示
        print("\n" + "=" * 60)
        print("抽出された演奏プログラム")
        print("=" * 60)

        if "concert_info" in program_data:
            info = program_data["concert_info"]
            print(f"コンサート: {info.get('title', 'N/A')}")
            print(f"日時: {info.get('date', 'N/A')}")
            print(f"会場: {info.get('venue', 'N/A')}")
            print()

        for perf in program_data["performances"]:
            print(f"{perf.get('program_order')}. {perf.get('performer_name')}")
            print(f"   {perf.get('piece_title')}")
            if perf.get('piece_composer'):
                print(f"   作曲: {perf.get('piece_composer')}")
            print()

    except KeyboardInterrupt:
        logger.info("\n中断されました")
        sys.exit(130)

    except Exception as e:
        logger.exception(f"エラーが発生しました: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
