#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Googleフォーム回答取得スクリプト

演奏者からの回答（曲名、公開設定など）を取得します。
- CSVファイルからの読み込み（手動エクスポート）
- Google Forms APIからの直接取得（推奨）
- Google Sheets APIからの自動取得（オプション）
"""

import csv
import json
import logging
import pickle
import sys
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Forms API設定
SCOPES = [
    'https://www.googleapis.com/auth/forms.body.readonly',
    'https://www.googleapis.com/auth/forms.responses.readonly',
    'https://www.googleapis.com/auth/spreadsheets.readonly'
]
CLIENT_SECRETS_FILE = Path(__file__).parent / "client_secrets.json"
FORMS_TOKEN_PICKLE = Path(__file__).parent / "forms_token.pickle"
FORM_CONFIG_FILE = Path(__file__).parent / "form_config.json"


class FormResponseParser:
    """Googleフォーム回答パーサー"""

    # フォームの質問項目（カラム名）のマッピング
    # 実際のCSVのカラム名に合わせて調整してください
    COLUMN_MAPPING = {
        "timestamp": ["タイムスタンプ", "Timestamp", "timestamp"],
        "name": ["お名前", "名前", "Name", "name"],
        "display_name": ["動画に氏名を表示しますか", "氏名表示", "Display name"],
        "piece_title": ["演奏された曲名", "曲名", "Piece title", "piece_title"],
        "privacy": ["公開設定", "Privacy", "privacy"],
        "description_extra": ["説明文に追加したい内容", "追加の説明", "Additional description"],
    }

    def __init__(self, csv_path: Optional[Path] = None):
        """
        初期化

        Args:
            csv_path: CSVファイルのパス
        """
        self.csv_path = csv_path
        self.responses = []

    def _find_column_index(self, headers: List[str], column_key: str) -> Optional[int]:
        """
        カラム名からインデックスを検索

        Args:
            headers: CSVヘッダーのリスト
            column_key: 検索するカラムキー

        Returns:
            カラムのインデックス（見つからない場合はNone）
        """
        possible_names = self.COLUMN_MAPPING.get(column_key, [])

        for i, header in enumerate(headers):
            # 完全一致
            if header in possible_names:
                return i
            # 部分一致（大文字小文字無視）
            for name in possible_names:
                if name.lower() in header.lower():
                    return i

        return None

    def _parse_privacy_value(self, value: str) -> str:
        """
        公開設定の値を正規化

        Args:
            value: フォームでの回答値

        Returns:
            正規化された値（'public', 'unlisted', 'private'）
        """
        value_lower = value.lower()

        if "公開" in value or "public" in value_lower:
            # 「限定公開」と「公開」を区別
            if "限定" in value or "unlisted" in value_lower:
                return "unlisted"
            return "public"
        elif "限定" in value or "unlisted" in value_lower:
            return "unlisted"
        elif "非公開" in value or "private" in value_lower:
            return "private"
        else:
            logger.warning(f"不明な公開設定値: {value} → デフォルト: unlisted")
            return "unlisted"

    def _parse_display_name_value(self, value: str) -> bool:
        """
        氏名表示の値を正規化

        Args:
            value: フォームでの回答値

        Returns:
            True: 表示する, False: 表示しない
        """
        value_lower = value.lower()

        if "表示する" in value or "yes" in value_lower or "はい" in value:
            return True
        elif "表示しない" in value or "no" in value_lower or "いいえ" in value or "匿名" in value:
            return False
        else:
            logger.warning(f"不明な氏名表示値: {value} → デフォルト: True")
            return True

    def load_from_csv(self, csv_path: Optional[Path] = None) -> List[Dict]:
        """
        CSVファイルから回答を読み込み

        Args:
            csv_path: CSVファイルのパス（指定しない場合はインスタンス変数を使用）

        Returns:
            回答データのリスト
        """
        if csv_path:
            self.csv_path = csv_path

        if not self.csv_path or not self.csv_path.exists():
            raise FileNotFoundError(f"CSVファイルが見つかりません: {self.csv_path}")

        logger.info(f"CSVファイルを読み込んでいます: {self.csv_path}")

        with open(self.csv_path, 'r', encoding='utf-8-sig') as f:  # BOM対応
            reader = csv.reader(f)
            rows = list(reader)

        return self._parse_rows(rows)

    def _parse_rows(self, rows: List[List[str]]) -> List[Dict]:
        """
        行データ（ヘッダー含む）を解析して回答リストを作成

        Args:
            rows: 行データのリスト（最初の行はヘッダー）

        Returns:
            回答データのリスト
        """
        if len(rows) < 2:
            raise ValueError("データが含まれていません")

        headers = rows[0]
        data_rows = rows[1:]

        logger.info(f"ヘッダー: {headers}")
        logger.info(f"データ行数: {len(data_rows)}")

        # カラムインデックスを取得
        col_indices = {}
        for key in self.COLUMN_MAPPING.keys():
            idx = self._find_column_index(headers, key)
            col_indices[key] = idx
            if idx is not None:
                logger.debug(f"カラム '{key}' → インデックス {idx} ({headers[idx]})")
            else:
                logger.warning(f"カラム '{key}' が見つかりません")

        # データをパース
        responses = []
        for i, row in enumerate(data_rows, 1):
            try:
                # 安全な値取得関数
                get_val = lambda idx: row[idx] if idx is not None and idx < len(row) else ""

                response = {}

                # タイムスタンプ
                response["timestamp"] = get_val(col_indices["timestamp"])

                # 名前（必須）
                name = get_val(col_indices["name"]).strip()
                if name:
                    response["name"] = name
                else:
                    logger.warning(f"行 {i}: 名前が見つかりません")
                    continue

                # 氏名表示
                response["display_name"] = self._parse_display_name_value(
                    get_val(col_indices["display_name"])
                )

                # 曲名（必須）
                piece = get_val(col_indices["piece_title"]).strip()
                if piece:
                    response["piece_title"] = piece
                else:
                    logger.warning(f"行 {i}: 曲名が見つかりません")
                    continue

                # 公開設定
                response["privacy"] = self._parse_privacy_value(
                    get_val(col_indices["privacy"])
                )

                # 追加の説明文
                response["description_extra"] = get_val(col_indices["description_extra"]).strip()

                # 回答IDを付与
                response["response_id"] = i

                responses.append(response)
                logger.info(
                    f"  回答 {i}: {response['name']} - {response['piece_title']} "
                    f"({response['privacy']})"
                )

            except Exception as e:
                logger.error(f"行 {i} の解析中にエラー: {e}")
                continue

        self.responses = responses
        logger.info(f"\n{len(responses)}件の有効な回答を読み込みました")

        return responses

    def load_from_google_sheets(self, spreadsheet_id: str, range_name: str = "A:Z") -> List[Dict]:
        """
        Google Sheets APIから回答を読み込み

        Args:
            spreadsheet_id: スプレッドシートID（またはURL）
            range_name: 読み込む範囲（デフォルト: "A:Z"）

        Returns:
            回答データのリスト
        """
        # URLからIDを抽出
        if 'docs.google.com/spreadsheets' in spreadsheet_id:
            try:
                # /d/ID/edit... の形式を想定
                parts = spreadsheet_id.split('/d/')
                if len(parts) > 1:
                    spreadsheet_id = parts[1].split('/')[0]
                logger.info(f"URLからスプレッドシートIDを抽出しました: {spreadsheet_id}")
            except Exception as e:
                logger.warning(f"URLからのID抽出に失敗しました: {e}. そのまま使用します。")

        logger.info("Google Sheets APIから回答を取得しています...")

        # 認証（Sheets API v4）
        service = self._authenticate_google_api('sheets', 'v4')

        try:
            sheet = service.spreadsheets()
            result = sheet.values().get(
                spreadsheetId=spreadsheet_id,
                range=range_name
            ).execute()

            values = result.get('values', [])

            if not values:
                raise ValueError("スプレッドシートにデータがありません")

            logger.info(f"{len(values)}行のデータを取得しました")

            # 取得したデータを解析
            return self._parse_rows(values)

        except HttpError as e:
            if e.resp.status == 403:
                logger.error("権限エラー: トークンが古いか、スプレッドシートの閲覧権限がありません。")
                logger.error(f"forms_token.pickleを削除して再認証を試みてください。\n{FORMS_TOKEN_PICKLE}")
            logger.error(f"Google Sheets APIエラー: {e}")
            raise

    def _authenticate_google_api(self, service_name: str, version: str):
        """
        Google API用のOAuth 2.0認証

        Args:
            service_name: サービス名（'forms', 'sheets' など）
            version: APIバージョン（'v1', 'v4' など）

        Returns:
            APIサービスオブジェクト
        """
        credentials = None

        # トークンファイルが存在する場合は読み込み
        if FORMS_TOKEN_PICKLE.exists():
            with open(FORMS_TOKEN_PICKLE, 'rb') as token:
                credentials = pickle.load(token)

        # 認証情報が無効な場合は再認証
        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                logger.info("アクセストークンを更新しています...")
                try:
                    credentials.refresh(Request())
                except Exception as e:
                    logger.warning(f"トークン更新に失敗しました: {e}。再認証を試みます。")
                    credentials = None

            if not credentials:
                if not CLIENT_SECRETS_FILE.exists():
                    raise FileNotFoundError(
                        f"client_secrets.jsonが見つかりません: {CLIENT_SECRETS_FILE}\n"
                        "Google Cloud Consoleから取得し、このファイルと同じディレクトリに配置してください。"
                    )

                logger.info("初回認証を実行します。ブラウザが開きます...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CLIENT_SECRETS_FILE), SCOPES
                )
                credentials = flow.run_local_server(port=0)

            # トークンを保存
            with open(FORMS_TOKEN_PICKLE, 'wb') as token:
                pickle.dump(credentials, token)
            logger.info("認証情報を保存しました")

        return build(service_name, version, credentials=credentials)

    def load_from_forms_api(self, form_id: Optional[str] = None) -> List[Dict]:
        """
        Google Forms APIから回答を直接取得

        Args:
            form_id: フォームID（指定しない場合はform_config.jsonから読み込み）

        Returns:
            回答データのリスト
        """
        # フォームIDの取得
        if not form_id:
            # form_config.jsonから読み込み
            if not FORM_CONFIG_FILE.exists():
                raise FileNotFoundError(
                    f"form_config.jsonが見つかりません: {FORM_CONFIG_FILE}\n"
                    "先にcreate_google_form.pyを実行してフォームを作成するか、\n"
                    "form_idを引数で指定してください。"
                )

            with open(FORM_CONFIG_FILE, 'r', encoding='utf-8') as f:
                form_config = json.load(f)
                form_id = form_config.get('form_id')

            if not form_id:
                raise ValueError("form_config.jsonにform_idが含まれていません")

        logger.info(f"Google Forms APIから回答を取得しています...")
        logger.info(f"フォームID: {form_id}")

        # Forms API認証
        service = self._authenticate_google_api('forms', 'v1')

        try:
            # フォーム構造を取得（質問IDの取得のため）
            form = service.forms().get(formId=form_id).execute()
            form_title = form.get('info', {}).get('title', '')
            logger.info(f"フォーム: {form_title}")

            # 質問IDのマッピングを作成
            question_mapping = self._build_question_mapping(form)

            # 回答を取得
            response_list = service.forms().responses().list(formId=form_id).execute()
            raw_responses = response_list.get('responses', [])

            logger.info(f"回答数: {len(raw_responses)}件")

            # 回答データを解析
            responses = []
            for i, raw_response in enumerate(raw_responses, 1):
                try:
                    response = self._parse_forms_api_response(
                        raw_response,
                        question_mapping,
                        i
                    )
                    if response:
                        responses.append(response)
                        logger.info(
                            f"  回答 {i}: {response['name']} - {response['piece_title']} "
                            f"({response['privacy']})"
                        )
                except Exception as e:
                    logger.error(f"回答 {i} の解析中にエラー: {e}")
                    continue

            self.responses = responses
            logger.info(f"\n{len(responses)}件の有効な回答を取得しました")

            return responses

        except HttpError as e:
            logger.error(f"Forms APIエラー: {e}")
            raise

    def _build_question_mapping(self, form: Dict) -> Dict:
        """
        フォームの質問IDをマッピング

        Args:
            form: フォーム情報

        Returns:
            質問タイトル -> 質問ID のマッピング
        """
        mapping = {}
        items = form.get('items', [])

        for item in items:
            question_item = item.get('questionItem')
            if question_item:
                question = question_item.get('question')
                if question:
                    question_id = question.get('questionId')
                    title = item.get('title', '')

                    # タイトルから質問の種類を判定
                    if 'お名前' in title or 'name' in title.lower():
                        mapping['name'] = question_id
                    elif '氏名を表示' in title or 'display' in title.lower():
                        mapping['display_name'] = question_id
                    elif '曲名' in title or 'piece' in title.lower():
                        mapping['piece_title'] = question_id
                    elif '公開設定' in title or 'privacy' in title.lower():
                        mapping['privacy'] = question_id
                    elif '説明文' in title or 'description' in title.lower():
                        mapping['description_extra'] = question_id

        logger.debug(f"質問マッピング: {mapping}")
        return mapping

    def _parse_forms_api_response(
        self,
        raw_response: Dict,
        question_mapping: Dict,
        response_id: int
    ) -> Optional[Dict]:
        """
        Forms APIの回答データを解析

        Args:
            raw_response: Forms APIから取得した生の回答データ
            question_mapping: 質問IDマッピング
            response_id: 回答ID

        Returns:
            解析された回答データ
        """
        answers = raw_response.get('answers', {})

        response = {
            'response_id': response_id,
            'timestamp': raw_response.get('createTime', ''),
            'name': '',
            'display_name': True,
            'piece_title': '',
            'privacy': 'unlisted',
            'description_extra': ''
        }

        # 名前
        name_q_id = question_mapping.get('name')
        if name_q_id and name_q_id in answers:
            text_answers = answers[name_q_id].get('textAnswers', {})
            answer_values = text_answers.get('answers', [])
            if answer_values:
                response['name'] = answer_values[0].get('value', '').strip()

        # 氏名表示
        display_q_id = question_mapping.get('display_name')
        if display_q_id and display_q_id in answers:
            text_answers = answers[display_q_id].get('textAnswers', {})
            answer_values = text_answers.get('answers', [])
            if answer_values:
                value = answer_values[0].get('value', '')
                response['display_name'] = self._parse_display_name_value(value)

        # 曲名
        piece_q_id = question_mapping.get('piece_title')
        if piece_q_id and piece_q_id in answers:
            text_answers = answers[piece_q_id].get('textAnswers', {})
            answer_values = text_answers.get('answers', [])
            if answer_values:
                response['piece_title'] = answer_values[0].get('value', '').strip()

        # 公開設定
        privacy_q_id = question_mapping.get('privacy')
        if privacy_q_id and privacy_q_id in answers:
            text_answers = answers[privacy_q_id].get('textAnswers', {})
            answer_values = text_answers.get('answers', [])
            if answer_values:
                value = answer_values[0].get('value', '')
                response['privacy'] = self._parse_privacy_value(value)

        # 追加の説明文
        desc_q_id = question_mapping.get('description_extra')
        if desc_q_id and desc_q_id in answers:
            text_answers = answers[desc_q_id].get('textAnswers', {})
            answer_values = text_answers.get('answers', [])
            if answer_values:
                response['description_extra'] = answer_values[0].get('value', '').strip()

        # 必須項目のチェック
        if not response['name'] or not response['piece_title']:
            logger.warning(f"回答 {response_id}: 必須項目が欠けています")
            return None

        return response

    def export_to_json(self, output_path: Path):
        """
        回答データをJSONファイルにエクスポート

        Args:
            output_path: 出力JSONファイルのパス
        """
        if not self.responses:
            logger.warning("エクスポートする回答データがありません")
            return

        output_data = {
            "export_time": datetime.now().isoformat(),
            "response_count": len(self.responses),
            "responses": self.responses
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        logger.info(f"回答データをエクスポートしました: {output_path}")

    def get_responses(self) -> List[Dict]:
        """回答データを取得"""
        return self.responses


def main():
    """メイン関数（スタンドアロン実行用）"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Googleフォーム回答を読み込み、JSON形式で保存"
    )
    parser.add_argument(
        "csv_file",
        nargs='?',
        type=Path,
        default=None,
        help="GoogleフォームからエクスポートしたCSVファイル"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="結果を保存するJSONファイルパス（デフォルト: form_responses.json）"
    )
    parser.add_argument(
        "--form-id",
        type=str,
        default=None,
        help="フォームID（Forms APIから直接取得する場合）"
    )
    parser.add_argument(
        "--use-api",
        action="store_true",
        help="Forms APIから直接回答を取得（推奨）"
    )
    parser.add_argument(
        "--sheet-id",
        type=str,
        default=None,
        help="スプレッドシートIDまたはURL（Google Sheets APIから取得）"
    )

    args = parser.parse_args()

    # 出力ファイル名のデフォルト設定
    if args.output is None:
        if args.csv_file:
            args.output = args.csv_file.parent / f"{args.csv_file.stem}_responses.json"
        else:
            args.output = Path(__file__).parent / "form_responses.json"

    try:
        response_parser = FormResponseParser()

        # Forms APIから取得
        if args.use_api or args.form_id:
            responses = response_parser.load_from_forms_api(args.form_id)

        # Sheets APIから取得
        elif args.sheet_id:
            responses = response_parser.load_from_google_sheets(args.sheet_id)

        # CSVから取得
        elif args.csv_file:
            responses = response_parser.load_from_csv(args.csv_file)

        else:
            # デフォルト: form_config.jsonがあればAPIから、なければエラー
            if FORM_CONFIG_FILE.exists():
                logger.info("form_config.jsonが見つかりました。Forms APIから回答を取得します。")
                responses = response_parser.load_from_forms_api()
            else:
                parser.error("CSVファイルまたは--use-apiまたは--sheet-idオプションを指定してください")

        # JSONにエクスポート
        response_parser.export_to_json(args.output)

        # 簡易的な結果表示
        print("\n" + "=" * 60)
        print("Googleフォーム回答")
        print("=" * 60)
        print(f"総回答数: {len(responses)}\n")

        for resp in responses:
            print(f"{resp['response_id']}. {resp['name']}")
            print(f"   曲名: {resp['piece_title']}")
            print(f"   公開設定: {resp['privacy']}")
            print(f"   氏名表示: {'する' if resp['display_name'] else 'しない'}")
            if resp.get('description_extra'):
                print(f"   追加説明: {resp['description_extra']}")
            print()

    except KeyboardInterrupt:
        logger.info("\n中断されました")
        sys.exit(130)

    except Exception as e:
        logger.exception(f"エラーが発生しました: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
