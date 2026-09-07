#!/usr/bin/env python3
"""
Phase 2: YouTube Video Uploader
Automation pipeline for "The Hidden Why" YouTube channel.

Uploads a finished video to YouTube using YouTube Data API v3 with OAuth2.
Privacy status defaults strictly to PRIVATE so videos are never published automatically.
Token is cached locally (token.json) so login only occurs on the first run.
"""

import argparse
import os
import sys
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    def load_dotenv(dotenv_path=".env"):
        if os.path.exists(dotenv_path):
            with open(dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip().strip("'\"")
                        if key not in os.environ:
                            os.environ[key] = val
    load_dotenv()

# Google API client imports
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
except ImportError:
    print("[ERROR] Required Google API libraries are missing.")
    print("Please install dependencies by running: pip install -r requirements.txt")
    sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = "token.json"


def get_authenticated_service(client_secrets_file: str):
    """
    Authenticate with YouTube Data API v3 using OAuth2 installed-app flow.
    Caches token in token.json for seamless future runs.
    """
    creds = None

    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            print(f"[WARNING] Failed to load cached {TOKEN_FILE}: {e}")
            creds = None

    # If there are no valid credentials available, ask user to log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[INFO] Refreshing expired OAuth token...")
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"[WARNING] Could not refresh token ({e}). Starting new login flow...")
                creds = None

        if not creds:
            if not os.path.exists(client_secrets_file):
                # Smart fallback for Windows duplicate .json extension
                if os.path.exists("client_secret.json.json"):
                    client_secrets_file = "client_secret.json.json"
                else:
                    possible_files = [f for f in os.listdir(".") if f.startswith("client_secret") and f.endswith(".json")]
                    if possible_files:
                        client_secrets_file = possible_files[0]

            if not os.path.exists(client_secrets_file):
                print(f"[ERROR] Client secrets file '{client_secrets_file}' not found!")
                print("\nTo fix this:")
                print("1. Go to Google Cloud Console (https://console.cloud.google.com/)")
                print("2. Enable YouTube Data API v3")
                print("3. Create OAuth 2.0 Client ID (Application type: Desktop App)")
                print("4. Download the JSON credentials file and save it as 'client_secret.json' in this directory.")
                sys.exit(1)

            print(f"[INFO] Using client secrets file: {client_secrets_file}")
            print("[INFO] Opening browser for Google OAuth2 authentication...")
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save credentials for future runs
        with open(TOKEN_FILE, "w", encoding="utf-8") as token_out:
            token_out.write(creds.to_json())
        print(f"[INFO] OAuth token cached successfully in '{TOKEN_FILE}'.")

    return build("youtube", "v3", credentials=creds)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Upload a video to YouTube using YouTube Data API v3 (Private by default)."
    )
    parser.add_argument(
        "--video",
        "-v",
        required=True,
        help="Path to the video file to upload (e.g., ./video/final_video.mp4)",
    )
    parser.add_argument(
        "--title",
        "-t",
        default="Why You Can't Stop Scrolling | The Hidden Why",
        help="Video Title for YouTube",
    )
    parser.add_argument(
        "--description",
        "-d",
        default="An exploration into the psychology and interface design that keeps us scrolling late at night.\n\nSubscribe to The Hidden Why for more psychology & tech explainers.",
        help="Video Description text",
    )
    parser.add_argument(
        "--description-file",
        help="Path to a text file containing the video description",
    )
    parser.add_argument(
        "--tags",
        default="psychology,tech explainer,social media,doomscrolling,dopamine,ux design,the hidden why",
        help="Comma-separated tags for the video",
    )
    parser.add_argument(
        "--privacy",
        "-p",
        choices=["private", "unlisted", "public"],
        default="private",
        help="Privacy status for the video (DEFAULT: private — never auto-publishes)",
    )
    parser.add_argument(
        "--category-id",
        default="27",
        help="YouTube Category ID (default: 27 for Education, 28 for Science & Tech)",
    )
    parser.add_argument(
        "--client-secrets",
        default=os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secret.json"),
        help="Path to OAuth2 client_secret.json file",
    )
    return parser.parse_args()


def upload_video(youtube, video_path: str, title: str, description: str, tags: list, category_id: str, privacy: str):
    """Resumable video upload process with progress tracking."""
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    # Chunk size of 4MB for upload progress updates
    chunk_size = 4 * 1024 * 1024
    media = MediaFileUpload(video_path, chunksize=chunk_size, resumable=True, mimetype="video/*")

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    print(f"[INFO] Uploading '{os.path.basename(video_path)}' to YouTube (Privacy: {privacy.upper()})...")
    response = None
    file_size = os.path.getsize(video_path)

    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                print(f"[UPLOADING] {progress}% completed ({status.resumable_progress} / {file_size} bytes)", end="\r")
        except HttpError as e:
            if e.resp.status in [500, 502, 503, 504]:
                print(f"\n[WARNING] Server error {e.resp.status}. Retrying in 5 seconds...")
                time.sleep(5)
            else:
                raise e

    print(f"\n[SUCCESS] Upload finished successfully!")
    video_id = response.get("id")
    video_url = f"https://youtu.be/{video_id}"
    studio_url = f"https://studio.youtube.com/video/{video_id}/edit"

    print("=" * 65)
    print(" 🎉  YouTube Upload Completed")
    print("=" * 65)
    print(f" Video Title    : {title}")
    print(f" Privacy Status : {privacy.upper()} (Safe for human review!)")
    print(f" Video ID       : {video_id}")
    print(f" Direct Link    : {video_url}")
    print(f" YouTube Studio : {studio_url}")
    print("=" * 65)


def main():
    args = parse_args()

    video_path = os.path.abspath(args.video)
    if not os.path.exists(video_path):
        print(f"[ERROR] Video file '{video_path}' does not exist.")
        sys.exit(1)

    # Read description from file if specified
    description = args.description
    if args.description_file:
        if os.path.exists(args.description_file):
            with open(args.description_file, "r", encoding="utf-8") as f:
                description = f.read().strip()
        else:
            print(f"[WARNING] Description file '{args.description_file}' not found. Using default description.")

    tags_list = [tag.strip() for tag in args.tags.split(",") if tag.strip()]

    print("=" * 65)
    print(" 🚀  The Hidden Why - YouTube Video Uploader (Phase 2)")
    print("=" * 65)
    print(f"Video File    : {video_path}")
    print(f"Title         : {args.title}")
    print(f"Privacy       : {args.privacy.upper()} (Default)")
    print(f"Tags          : {tags_list}")
    print("-" * 65)

    # Authenticate via OAuth2
    youtube = get_authenticated_service(args.client_secrets)

    # Perform Upload
    try:
        upload_video(
            youtube=youtube,
            video_path=video_path,
            title=args.title,
            description=description,
            tags=tags_list,
            category_id=args.category_id,
            privacy=args.privacy,
        )
    except HttpError as err:
        print("\n[ERROR] YouTube API Error occurred:")
        print(f"HTTP Status : {err.resp.status}")
        print(f"Error Details: {err._get_reason()}")
        sys.exit(1)
    except Exception as err:
        print(f"\n[ERROR] An unexpected error occurred: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
