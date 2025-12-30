#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Googleフォーム作成スクリプト

コンサート動画アップロード用のGoogleフォームを自動作成します。
- OAuth 2.0認証
- 必要な質問項目を自動追加
- フォームURLを保存
"""

import os
import sys
import json
import pickle
import logging
from pathlib import Path
from typing import Dict, Optional

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

# API設定
SCOPES = [
    'https://www.googleapis.com/auth/forms.body',
    'https://www.googleapis.com/auth/forms.responses.readonly'
]
API_SERVICE_NAME = 'forms'
API_VERSION = 'v1'
CLIENT_SECRETS_FILE = Path(__file__).parent / "client_secrets.json"
TOKEN_PICKLE_FILE = Path(__file__).parent / "forms_token.pickle"
FORM_CONFIG_FILE = Path(__file__).parent / "form_config.json"


def authenticate_forms_api() -> object:
    """
    Google Forms API用のOAuth 2.0認証

    Returns:
        Forms APIサービスオブジェクト
    """
    credentials = None

    # トークンファイルが存在する場合は読み込み
    if TOKEN_PICKLE_FILE.exists():
        with open(TOKEN_PICKLE_FILE, 'rb') as token:
            credentials = pickle.load(token)

    # 認証情報が無効な場合は再認証
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            logger.info("アクセストークンを更新しています...")
            credentials.refresh(Request())
        else:
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
        with open(TOKEN_PICKLE_FILE, 'wb') as token:
            pickle.dump(credentials, token)
        logger.info("認証情報を保存しました")

    return build(API_SERVICE_NAME, API_VERSION, credentials=credentials)


def create_concert_form(
    service,
    form_title: str = "コンサート動画アップロード 演奏者情報フォーム",
    form_description: str = "ご自身の演奏動画をYouTubeにアップロードするための情報を入力してください。"
) -> Dict:
    """
    コンサート動画用のGoogleフォームを作成

    Args:
        service: Forms APIサービスオブジェクト
        form_title: フォームのタイトル
        form_description: フォームの説明

    Returns:
        作成されたフォーム情報
    """
    logger.info("=" * 60)
    logger.info("Googleフォームを作成しています...")
    logger.info("=" * 60)

    # 1. フォームの作成（タイトルと説明のみ）
    form_body = {
        "info": {
            "title": form_title,
            "documentTitle": form_title
        }
    }

    try:
        result = service.forms().create(body=form_body).execute()
        form_id = result['formId']
        form_url = f"https://docs.google.com/forms/d/{form_id}/edit"
        response_url = f"https://docs.google.com/forms/d/{form_id}/viewform"

        logger.info(f"✓ フォームを作成しました")
        logger.info(f"  フォームID: {form_id}")
        logger.info(f"  編集URL: {form_url}")
        logger.info(f"  回答URL: {response_url}")

    except HttpError as e:
        logger.error(f"フォーム作成エラー: {e}")
        raise

    # 2. フォームに説明を追加 + 質問を追加
    logger.info("\n質問項目を追加しています...")

    # batchUpdateリクエストを構築
    requests = []

    # 2-1. フォームの説明を更新
    requests.append({
        "updateFormInfo": {
            "info": {
                "description": form_description
            },
            "updateMask": "description"
        }
    })

    # 2-2. 質問1: お名前（必須、記述式）
    requests.append({
        "createItem": {
            "item": {
                "title": "お名前",
                "description": "フルネームを入力してください",
                "questionItem": {
                    "question": {
                        "required": True,
                        "textQuestion": {
                            "paragraph": False
                        }
                    }
                }
            },
            "location": {"index": 0}
        }
    })

    # 2-3. 質問2: 動画に氏名を表示しますか（必須、ラジオボタン）
    requests.append({
        "createItem": {
            "item": {
                "title": "動画に氏名を表示しますか？",
                "description": "YouTubeの動画タイトルにお名前を記載するか選択してください",
                "questionItem": {
                    "question": {
                        "required": True,
                        "choiceQuestion": {
                            "type": "RADIO",
                            "options": [
                                {"value": "表示する"},
                                {"value": "表示しない（匿名）"}
                            ]
                        }
                    }
                }
            },
            "location": {"index": 1}
        }
    })

    # 2-4. 質問3: 演奏された曲名（必須、記述式）
    requests.append({
        "createItem": {
            "item": {
                "title": "演奏された曲名を入力してください",
                "description": "例: ショパン ノクターン第2番、ベートーヴェン 月光ソナタ 第1楽章",
                "questionItem": {
                    "question": {
                        "required": True,
                        "textQuestion": {
                            "paragraph": False
                        }
                    }
                }
            },
            "location": {"index": 2}
        }
    })

    # 2-5. 質問4: 公開設定（必須、ラジオボタン）
    requests.append({
        "createItem": {
            "item": {
                "title": "公開設定",
                "description": "動画の公開範囲を選択してください",
                "questionItem": {
                    "question": {
                        "required": True,
                        "choiceQuestion": {
                            "type": "RADIO",
                            "options": [
                                {
                                    "value": "公開",
                                    "isOther": False
                                },
                                {
                                    "value": "限定公開（URLを知っている人のみ閲覧可能）",
                                    "isOther": False
                                },
                                {
                                    "value": "非公開（本人のみ閲覧可能）",
                                    "isOther": False
                                }
                            ]
                        }
                    }
                }
            },
            "location": {"index": 3}
        }
    })

    # 2-6. 質問5: 追加の説明文（任意、段落テキスト）
    requests.append({
        "createItem": {
            "item": {
                "title": "動画の説明文に追加したい内容があれば記入してください",
                "description": "動画の説明欄に表示される追加メッセージ（任意）",
                "questionItem": {
                    "question": {
                        "required": False,
                        "textQuestion": {
                            "paragraph": True
                        }
                    }
                }
            },
            "location": {"index": 4}
        }
    })

    # batchUpdateを実行
    update_body = {"requests": requests}

    try:
        update_result = service.forms().batchUpdate(
            formId=form_id,
            body=update_body
        ).execute()

        logger.info("✓ 質問項目を追加しました")
        logger.info(f"  追加した質問数: 5件")

    except HttpError as e:
        logger.error(f"質問追加エラー: {e}")
        raise

    # 3. フォーム情報を返す
    form_info = {
        "form_id": form_id,
        "form_title": form_title,
        "edit_url": form_url,
        "response_url": response_url,
        "created_at": result.get("responderUri", "")
    }

    return form_info


def save_form_config(form_info: Dict, config_file: Path = FORM_CONFIG_FILE):
    """
    フォーム情報を設定ファイルに保存

    Args:
        form_info: フォーム情報
        config_file: 設定ファイルのパス
    """
    try:
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(form_info, f, ensure_ascii=False, indent=2)

        logger.info(f"\n✓ フォーム情報を保存しました: {config_file}")

    except Exception as e:
        logger.error(f"設定ファイルの保存に失敗: {e}")
        raise


def load_form_config(config_file: Path = FORM_CONFIG_FILE) -> Optional[Dict]:
    """
    保存されたフォーム情報を読み込み

    Args:
        config_file: 設定ファイルのパス

    Returns:
        フォーム情報（存在しない場合はNone）
    """
    if not config_file.exists():
        return None

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"設定ファイルの読み込みに失敗: {e}")
        return None


def main():
    """メイン関数（スタンドアロン実行用）"""
    import argparse

    parser = argparse.ArgumentParser(
        description="コンサート動画用Googleフォームを作成"
    )
    parser.add_argument(
        "--title",
        type=str,
        default="コンサート動画アップロード 演奏者情報フォーム",
        help="フォームのタイトル"
    )
    parser.add_argument(
        "--description",
        type=str,
        default="ご自身の演奏動画をYouTubeにアップロードするための情報を入力してください。",
        help="フォームの説明文"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=FORM_CONFIG_FILE,
        help="フォーム情報の保存先（デフォルト: form_config.json）"
    )

    args = parser.parse_args()

    try:
        # 既存のフォーム情報を確認
        existing_form = load_form_config(args.output)
        if existing_form:
            logger.warning(f"\n既存のフォーム情報が見つかりました:")
            logger.warning(f"  フォームID: {existing_form.get('form_id')}")
            logger.warning(f"  タイトル: {existing_form.get('form_title')}")
            logger.warning(f"  回答URL: {existing_form.get('response_url')}")

            user_input = input("\n新しいフォームを作成しますか？ (yes/no): ").strip().lower()
            if user_input not in ['yes', 'y', 'はい']:
                logger.info("フォーム作成をキャンセルしました")
                print("\n【既存フォーム情報】")
                print(f"回答URL: {existing_form.get('response_url')}")
                print(f"編集URL: {existing_form.get('edit_url')}")
                return

        # Forms API認証
        logger.info("Google Forms APIに接続しています...")
        service = authenticate_forms_api()

        # フォーム作成
        form_info = create_concert_form(
            service,
            form_title=args.title,
            form_description=args.description
        )

        # 設定ファイルに保存
        save_form_config(form_info, args.output)

        # 結果表示
        print("\n" + "=" * 60)
        print("Googleフォーム作成完了")
        print("=" * 60)
        print(f"\nフォームID: {form_info['form_id']}")
        print(f"\n【演奏者に送るURL】")
        print(f"{form_info['response_url']}")
        print(f"\n【フォーム編集URL】")
        print(f"{form_info['edit_url']}")
        print("\n※ このURLを演奏者に送信してください")
        print(f"※ フォーム情報は {args.output} に保存されました")

    except KeyboardInterrupt:
        logger.info("\n中断されました")
        sys.exit(130)

    except Exception as e:
        logger.exception(f"エラーが発生しました: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
