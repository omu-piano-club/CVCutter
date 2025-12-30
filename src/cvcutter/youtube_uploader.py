#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
YouTube Data API v3を使用した動画アップロードスクリプト

このスクリプトは、処理済みの動画ファイルをYouTubeに自動アップロードします。
- OAuth 2.0認証
- 1日6本のアップロード制限管理
- 指数バックオフによるリトライ
- エラーハンドリングとログ記録
"""

import os
import sys
import json
import time
import random
import logging
import pickle
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import google.auth.transport.requests
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from http.client import HTTPException

# ログ設定
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "youtube_upload.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# API設定
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']
API_SERVICE_NAME = 'youtube'
API_VERSION = 'v3'
CLIENT_SECRETS_FILE = Path(__file__).parent / "client_secrets.json"
TOKEN_PICKLE_FILE = Path(__file__).parent / "token.pickle"

# クォータ設定
DAILY_QUOTA_LIMIT = 10000  # 1日のクォータ上限
VIDEO_INSERT_COST = 1600   # 1回のアップロードコスト
MAX_UPLOADS_PER_DAY = DAILY_QUOTA_LIMIT // VIDEO_INSERT_COST  # 6本/日

# 状態管理ファイル
STATE_FILE = Path(__file__).parent / "upload_state.json"

# リトライ設定
MAX_RETRIES = 5
RETRIABLE_EXCEPTIONS = (
    HTTPException,
    IOError,
)
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]


class QuotaManager:
    """YouTube API クォータ管理クラス"""

    def __init__(self, state_file: Path = STATE_FILE):
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> Dict:
        """状態ファイルを読み込み"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"状態ファイルの読み込みに失敗: {e}")

        # デフォルト状態
        return {
            "quota_reset_time": self._get_next_quota_reset().isoformat(),
            "uploads_today": 0,
            "upload_history": [],
            "pending_uploads": []
        }

    def _save_state(self):
        """状態ファイルに保存"""
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"状態ファイルの保存に失敗: {e}")

    def _get_next_quota_reset(self) -> datetime:
        """次のクォータリセット時刻を取得（太平洋時間の午前0時）"""
        # 太平洋時間のタイムゾーン（UTCから-8時間または-7時間、夏時間考慮）
        # 簡易的にUTC-8として計算
        now_utc = datetime.now(timezone.utc)
        pacific_offset = timedelta(hours=-8)
        now_pacific = now_utc + pacific_offset

        # 太平洋時間の次の午前0時
        next_midnight_pacific = (now_pacific + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # UTCに戻す
        next_midnight_utc = next_midnight_pacific - pacific_offset
        return next_midnight_utc

    def check_and_reset_quota(self):
        """クォータをチェックし、必要に応じてリセット"""
        reset_time = datetime.fromisoformat(self.state["quota_reset_time"])
        now = datetime.now(timezone.utc)

        if now >= reset_time:
            logger.info("クォータをリセットしました")
            self.state["uploads_today"] = 0
            self.state["quota_reset_time"] = self._get_next_quota_reset().isoformat()
            self._save_state()

    def can_upload(self) -> bool:
        """アップロード可能かチェック"""
        self.check_and_reset_quota()
        return self.state["uploads_today"] < MAX_UPLOADS_PER_DAY

    def wait_for_quota_reset(self):
        """クォータリセットまで待機"""
        reset_time = datetime.fromisoformat(self.state["quota_reset_time"])
        now = datetime.now(timezone.utc)

        if now < reset_time:
            wait_seconds = (reset_time - now).total_seconds()
            wait_hours = wait_seconds / 3600

            logger.info(f"1日のアップロード上限（{MAX_UPLOADS_PER_DAY}本）に達しました")
            logger.info(f"クォータリセットまで {wait_hours:.1f} 時間待機します...")
            logger.info(f"再開予定時刻: {reset_time.astimezone()}")

            time.sleep(wait_seconds)
            self.check_and_reset_quota()

    def increment_upload_count(self):
        """アップロードカウントをインクリメント"""
        self.state["uploads_today"] += 1
        self._save_state()

    def add_upload_history(self, file_path: str, video_id: Optional[str],
                          status: str, error: Optional[str] = None):
        """アップロード履歴を追加"""
        history_entry = {
            "file_path": file_path,
            "video_id": video_id,
            "upload_time": datetime.now(timezone.utc).isoformat(),
            "status": status
        }
        if error:
            history_entry["error"] = error

        self.state["upload_history"].append(history_entry)
        self._save_state()

    def get_upload_summary(self) -> Dict:
        """アップロード結果のサマリーを取得"""
        history = self.state["upload_history"]
        return {
            "total": len(history),
            "success": sum(1 for h in history if h["status"] == "success"),
            "failed": sum(1 for h in history if h["status"] == "failed"),
            "uploads_today": self.state["uploads_today"],
            "quota_reset_time": self.state["quota_reset_time"]
        }


def authenticate() -> object:
    """
    OAuth 2.0認証を実行し、YouTube APIサービスオブジェクトを返す

    Returns:
        YouTubeサービスオブジェクト
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


def get_video_files_sorted_by_time(directory: Path) -> List[Path]:
    """
    指定ディレクトリ内の動画ファイルを作成時刻順にソート

    Args:
        directory: 動画ファイルのディレクトリ

    Returns:
        作成時刻順にソートされた動画ファイルパスのリスト
    """
    video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv']
    video_files = [
        f for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in video_extensions
    ]

    # 作成時刻でソート（古い順）
    video_files.sort(key=lambda f: f.stat().st_ctime)

    logger.info(f"{len(video_files)}個の動画ファイルを検出しました")
    for i, video_file in enumerate(video_files, 1):
        ctime = datetime.fromtimestamp(video_file.stat().st_ctime)
        logger.info(f"  {i}. {video_file.name} (作成: {ctime})")

    return video_files


def load_upload_metadata(metadata_file: Path) -> Dict:
    """
    アップロードメタデータファイルを読み込み

    Args:
        metadata_file: メタデータJSONファイルのパス

    Returns:
        メタデータ辞書
    """
    if not metadata_file.exists():
        raise FileNotFoundError(f"メタデータファイルが見つかりません: {metadata_file}")

    with open(metadata_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def validate_metadata_mapping(video_files: List[Path], metadata: Dict) -> Tuple[bool, str]:
    """
    動画ファイルとメタデータのマッピングを検証

    Args:
        video_files: 動画ファイルリスト
        metadata: メタデータ辞書

    Returns:
        (検証成功フラグ, エラーメッセージ)
    """
    video_count = len(video_files)
    metadata_count = len(metadata.get("videos", []))

    if video_count != metadata_count:
        error_msg = (
            f"動画ファイル数（{video_count}本）とメタデータ数（{metadata_count}件）が一致しません。\n"
            f"動画ファイル: {[f.name for f in video_files]}\n"
            f"メタデータ: {[m.get('title', '無題') for m in metadata.get('videos', [])]}"
        )
        return False, error_msg

    return True, ""


def upload_video(youtube, video_file: Path, metadata: Dict,
                retry_count: int = 0) -> Optional[str]:
    """
    1本の動画をYouTubeにアップロード

    Args:
        youtube: YouTubeサービスオブジェクト
        video_file: アップロードする動画ファイル
        metadata: 動画のメタデータ
        retry_count: リトライ回数

    Returns:
        アップロードされた動画のvideo_id（失敗時はNone）
    """
    # リクエストボディの構築
    body = {
        "snippet": {
            "title": metadata.get("title", video_file.stem),
            "description": metadata.get("description", ""),
            "tags": metadata.get("tags", []),
            "categoryId": "10",  # 音楽カテゴリ
            "defaultLanguage": "ja",
            "defaultAudioLanguage": "ja"
        },
        "status": {
            "privacyStatus": metadata.get("privacy_status", "unlisted"),
            "selfDeclaredMadeForKids": False
        }
    }

    # MediaFileUploadオブジェクトの作成
    media = MediaFileUpload(
        str(video_file),
        chunksize=-1,  # 全ファイルを一度にアップロード
        resumable=True
    )

    try:
        # アップロードリクエストの作成
        insert_request = youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media
        )

        # 再開可能アップロードの実行
        logger.info(f"アップロード開始: {video_file.name}")
        response = None

        while response is None:
            try:
                status, response = insert_request.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    logger.info(f"  進捗: {progress}%")
            except HttpError as e:
                if e.resp.status in RETRIABLE_STATUS_CODES:
                    # リトライ可能なエラー
                    if retry_count < MAX_RETRIES:
                        sleep_seconds = random.random() * (2 ** retry_count)
                        logger.warning(
                            f"HTTPエラー {e.resp.status} が発生。"
                            f"{sleep_seconds:.1f}秒後にリトライします... "
                            f"(試行 {retry_count + 1}/{MAX_RETRIES})"
                        )
                        time.sleep(sleep_seconds)
                        return upload_video(youtube, video_file, metadata, retry_count + 1)
                    else:
                        raise
                else:
                    raise

        video_id = response.get("id")
        logger.info(f"✓ アップロード成功: {video_file.name} (ID: {video_id})")

        # 再生リストに追加（指定されている場合）
        playlist_id = metadata.get("playlist_id")
        if playlist_id:
            try:
                add_video_to_playlist(youtube, video_id, playlist_id)
                logger.info(f"  再生リストに追加しました: {playlist_id}")
            except Exception as e:
                logger.warning(f"  再生リストへの追加に失敗: {e}")

        return video_id

    except RETRIABLE_EXCEPTIONS as e:
        if retry_count < MAX_RETRIES:
            sleep_seconds = random.random() * (2 ** retry_count)
            logger.warning(
                f"一時的なエラーが発生: {e}\n"
                f"{sleep_seconds:.1f}秒後にリトライします... "
                f"(試行 {retry_count + 1}/{MAX_RETRIES})"
            )
            time.sleep(sleep_seconds)
            return upload_video(youtube, video_file, metadata, retry_count + 1)
        else:
            logger.error(f"最大リトライ回数に達しました: {video_file.name}")
            raise

    except HttpError as e:
        logger.error(f"HTTPエラーが発生: {e}")
        raise

    except Exception as e:
        logger.error(f"予期しないエラーが発生: {e}")
        raise


def add_video_to_playlist(youtube, video_id: str, playlist_id: str):
    """
    動画を再生リストに追加

    Args:
        youtube: YouTubeサービスオブジェクト
        video_id: 動画ID
        playlist_id: 再生リストID
    """
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id
                }
            }
        }
    ).execute()


def batch_upload(video_dir: Path, metadata_file: Path,
                 confirm_callback=None) -> Dict:
    """
    複数の動画をバッチアップロード

    Args:
        video_dir: 動画ファイルのディレクトリ
        metadata_file: メタデータJSONファイル
        confirm_callback: ユーザー確認用コールバック関数 (video_files, metadata) -> bool

    Returns:
        アップロード結果のサマリー
    """
    logger.info("=" * 60)
    logger.info("YouTube 動画バッチアップロード開始")
    logger.info("=" * 60)

    # 認証
    logger.info("YouTube APIに接続しています...")
    youtube = authenticate()

    # クォータマネージャーの初期化
    quota_manager = QuotaManager()

    # 動画ファイルの取得（作成時刻順）
    video_files = get_video_files_sorted_by_time(video_dir)

    if not video_files:
        logger.warning("アップロードする動画ファイルが見つかりません")
        return quota_manager.get_upload_summary()

    # メタデータの読み込み
    logger.info(f"メタデータを読み込んでいます: {metadata_file}")
    metadata = load_upload_metadata(metadata_file)

    # マッピングの検証
    is_valid, error_msg = validate_metadata_mapping(video_files, metadata)

    if not is_valid:
        logger.error(error_msg)
        # ユーザー確認が必要
        if confirm_callback:
            if not confirm_callback(video_files, metadata, error_msg):
                logger.info("ユーザーによりアップロードがキャンセルされました")
                return quota_manager.get_upload_summary()
        else:
            raise ValueError(error_msg)

    # 最終確認（エラーがない場合も確認）
    if confirm_callback:
        if not confirm_callback(video_files, metadata, None):
            logger.info("ユーザーによりアップロードがキャンセルされました")
            return quota_manager.get_upload_summary()

    # バッチアップロード実行
    video_metadata_list = metadata.get("videos", [])

    for i, (video_file, video_metadata) in enumerate(zip(video_files, video_metadata_list), 1):
        logger.info(f"\n[{i}/{len(video_files)}] {video_file.name}")
        logger.info(f"タイトル: {video_metadata.get('title')}")

        # クォータチェック
        if not quota_manager.can_upload():
            quota_manager.wait_for_quota_reset()

        # アップロード実行
        try:
            video_id = upload_video(youtube, video_file, video_metadata)
            quota_manager.add_upload_history(
                str(video_file), video_id, "success"
            )
            quota_manager.increment_upload_count()

        except Exception as e:
            error_msg = str(e)
            logger.error(f"✗ アップロード失敗: {video_file.name}")
            logger.error(f"  エラー: {error_msg}")
            quota_manager.add_upload_history(
                str(video_file), None, "failed", error_msg
            )
            # 失敗した動画はスキップして続行
            continue

    # 結果サマリー
    summary = quota_manager.get_upload_summary()
    logger.info("\n" + "=" * 60)
    logger.info("アップロード完了")
    logger.info("=" * 60)
    logger.info(f"総数: {summary['total']} 本")
    logger.info(f"成功: {summary['success']} 本")
    logger.info(f"失敗: {summary['failed']} 本")
    logger.info(f"本日のアップロード数: {summary['uploads_today']}/{MAX_UPLOADS_PER_DAY}")
    logger.info(f"次回クォータリセット: {summary['quota_reset_time']}")

    return summary


def main():
    """メイン関数（スタンドアロン実行用）"""
    import argparse

    parser = argparse.ArgumentParser(
        description="YouTube動画バッチアップロードツール"
    )
    parser.add_argument(
        "--video-dir",
        type=Path,
        default=Path(__file__).parent / "output",
        help="動画ファイルのディレクトリ（デフォルト: output/）"
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=Path(__file__).parent / "upload_metadata.json",
        help="メタデータJSONファイル（デフォルト: upload_metadata.json）"
    )

    args = parser.parse_args()

    try:
        summary = batch_upload(args.video_dir, args.metadata)

        if summary['failed'] > 0:
            sys.exit(1)

    except KeyboardInterrupt:
        logger.info("\n中断されました")
        sys.exit(130)

    except Exception as e:
        logger.exception(f"致命的なエラーが発生しました: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
