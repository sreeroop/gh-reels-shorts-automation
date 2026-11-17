import os
import sys
import re
import json
import time
import requests
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request

SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
FB_PAGE_ID = os.getenv("FB_PAGE_ID")
IG_USER_ID = os.getenv("IG_USER_ID")
FB_PAGE_ACCESS_TOKEN = os.getenv("FB_PAGE_ACCESS_TOKEN")
YT_TOKEN_JSON = os.getenv("YT_TOKEN_JSON")

# Map platform argument to its specific Google Sheet columns
PLATFORM_CONFIG = {
    "yt": {"status_col": "yt_status", "caption_col": "Caption_YT"},
    "insta": {"status_col": "insta_status", "caption_col": "Caption_IG"},
    "fb": {"status_col": "fb_status", "caption_col": "Caption_FB"}
}

def get_google_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open(SHEET_NAME).sheet1

def extract_direct_drive_url(drive_url):
    if not drive_url:
        return None
    file_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)', drive_url) or re.search(r'id=([a-zA-Z0-9_-]+)', drive_url)
    if not file_id_match:
        raise ValueError(f"Invalid Google Drive URL: {drive_url}")
    return f"https://drive.google.com/uc?export=download&id={file_id_match.group(1)}"

def download_drive_file(drive_url, output_path):
    file_id_match = re.search(r'/d/([a-zA-Z0-9_-]+)', drive_url) or re.search(r'id=([a-zA-Z0-9_-]+)', drive_url)
    if not file_id_match:
        raise ValueError(f"Invalid Google Drive URL: {drive_url}")
    
    file_id = file_id_match.group(1)
    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    session = requests.Session()
    response = session.get(download_url, stream=True)
    
    for key, value in response.cookies.items():
        if key.startswith('download_warning'):
            response = session.get("https://drive.google.com/uc", params={'export': 'download', 'id': file_id, 'confirm': value}, stream=True)
            break

    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=32768):
            if chunk:
                f.write(chunk)
    return output_path

def upload_to_youtube(video_path, title, description, seo_keywords=""):
    token_info = json.loads(YT_TOKEN_JSON)
    creds = UserCredentials.from_authorized_user_info(token_info)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        
    youtube = build('youtube', 'v3', credentials=creds)
    tags = [tag.strip() for tag in seo_keywords.split(",") if tag.strip()]
    
    snippet = {
        'title': title,
        'description': description,
        'categoryId': '22'
    }
    if tags:
        snippet['tags'] = tags
        
    body = {
        'snippet': snippet,
        'status': {'privacyStatus': 'public', 'selfDeclaredMadeForKids': False}
    }
    
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part='snippet,status', body=body, media_body=media)
    
    response = None
    while response is None:
        _, response = request.next_chunk()
    print(f"YouTube Upload Complete! ID: {response.get('id')} | Tags applied: {tags}")

def upload_to_instagram(video_url, caption, cover_url=None):
    base_url = "https://graph.facebook.com/v22.0"
    container_url = f"{base_url}/{IG_USER_ID}/media"
    
    payload = {
        'media_type': 'REELS',
        'video_url': video_url,
        'caption': caption,
        'access_token': FB_PAGE_ACCESS_TOKEN
    }
    if cover_url:
        payload['cover_url'] = cover_url
    
    res = requests.post(container_url, data=payload).json()
    
    creation_id = res.get('id')
    if not creation_id:
        raise Exception(f"IG Container Error: {res}")

    # Poll container status until Meta finishes processing the video
    for _ in range(12):
        time.sleep(5)
        status_res = requests.get(f"{base_url}/{creation_id}", params={
            'fields': 'status_code',
            'access_token': FB_PAGE_ACCESS_TOKEN
        }).json()
        
        status_code = status_res.get('status_code')
        if status_code == 'FINISHED':
            break
        elif status_code == 'ERROR':
            raise Exception(f"IG Processing Failed: {status_res}")

    pub_res = requests.post(f"{base_url}/{IG_USER_ID}/media_publish", data={
        'creation_id': creation_id,
        'access_token': FB_PAGE_ACCESS_TOKEN
    }).json()
    
    if 'id' not in pub_res:
        raise Exception(f"IG Publish Error: {pub_res}")
    print("Instagram Published:", pub_res)

def upload_to_facebook(video_url, caption, thumb_drive_url=None):
    url = f"https://graph.facebook.com/v22.0/{FB_PAGE_ID}/videos"
    data = {
        'file_url': video_url,
        'description': caption,
        'access_token': FB_PAGE_ACCESS_TOKEN
    }
    
    thumb_path = None
    try:
        if thumb_drive_url:
            thumb_path = download_drive_file(thumb_drive_url, "temp_thumb.jpg")
            with open(thumb_path, "rb") as img_file:
                res = requests.post(url, data=data, files={'thumb': img_file}).json()
        else:
            res = requests.post(url, data=data).json()
            
        if 'id' not in res:
            raise Exception(f"FB Publish Error: {res}")
        print("Facebook Published:", res)
    finally:
        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in PLATFORM_CONFIG:
        raise ValueError("Specify platform argument: yt, insta, or fb")
        
    platform = sys.argv[1]
    cfg = PLATFORM_CONFIG[platform]
    target_col_name = cfg["status_col"]
    caption_col_name = cfg["caption_col"]
    
    sheet = get_google_sheet()
    headers = sheet.row_values(1)
    status_col_idx = headers.index(target_col_name) + 1
    records = sheet.get_all_records()
    
    for idx, row in enumerate(records, start=2):
        status = str(row.get(target_col_name, '')).strip().lower()
        if status != 'posted' and row.get('File_URL'):
            print(f"[{platform.upper()}] Processing row {idx}: {row.get('Title')}")
            drive_url = str(row['File_URL']).strip()
            thumb_drive_url = str(row.get('Thumbnail_URL', '')).strip()
            title = str(row.get('Title', '')).strip()
            seo_keywords = str(row.get('SEO_Keywords', '')).strip()
            caption = str(row.get(caption_col_name, '')).strip()
            
            direct_video_url = extract_direct_drive_url(drive_url)
            direct_thumb_url = extract_direct_drive_url(thumb_drive_url) if thumb_drive_url else None
            
            try:
                if platform == "yt":
                    video_file = download_drive_file(drive_url, "temp_video.mp4")
                    try:
                        upload_to_youtube(video_file, title, caption, seo_keywords)
                    finally:
                        if os.path.exists(video_file):
                            os.remove(video_file)
                elif platform == "insta":
                    upload_to_instagram(direct_video_url, caption, direct_thumb_url)
                elif platform == "fb":
                    upload_to_facebook(direct_video_url, caption, thumb_drive_url)
                
                sheet.update_cell(idx, status_col_idx, 'Posted')
                print(f"[{platform.upper()}] Row {idx} marked as Posted!")
            except Exception as e:
                print(f"[{platform.upper()}] Failed on row {idx}: {e}")
                raise e
            break

if __name__ == "__main__":
    main()
