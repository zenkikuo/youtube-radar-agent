import os
import re
import json
import yaml
import time
import subprocess
import feedparser
import requests
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, Field
from youtube_transcript_api import YouTubeTranscriptApi
from google import genai
from google.genai import types

SEEN_FILE = "seen_videos.json"
CACHE_FILE = "channel_id_cache.json"

# 4 大分類專屬 System Prompts
CATEGORY_PROMPTS = {
    "cooking": """
        你是一位家庭料理與生活顧問。請審閱這部影片的標題與字幕：
        1. 若影片為做菜教學：提取「菜名、核心食材、3個關鍵烹調技巧」，並評估備料難度（易/中/難）。
        2. 若該生活/親子頻道本集剛好拍了「旅遊、露營、花蓮、宜蘭」相關內容：請自動切換為旅遊視角，列出具體景點與親子友善程度，並在 reason 開頭加上【跨界旅遊情報】。
        3. 若純屬無重點日常閒聊，給予 2 分以下。
    """,
    "travel_outdoor": """
        你是一位精打細算的台灣自由行與露營規劃師。請審閱這部旅遊/露營影片：
        1. 強制提取具體資訊：營區或店家名稱、所在鄉鎮、交通/停車提示、真實優缺點或避坑提醒。
        2. 【特別加權】：若內容涉及「花蓮」或「宜蘭」的新開幕店家、秘境或親子景點，且資訊具體實用，請給予 4~5 分；若是純風景配樂、無具體地點資訊的流水帳，直接給予 1~2 分。
    """,
    "lifestyle": """
        你是一位幫忙碌上班族篩選配飯影片的娛樂編輯。請審閱這部生活/綜藝影片：
        1. 用 2~3 個重點提煉本集企劃核心與最大笑點/亮點。
        2. 評估業配比例：若整部片超過 70% 是單一產品業配且內容乏味，給予 2 分以下並提醒「純業配可略過」；若企劃有趣則給 4 分以上。
    """,
    "sports": """
        你是一位專業職業運動分析師，必須嚴格遵守【絕對防劇透原則】：
        1. 若為賽事精華（如愛爾達、NBA、MLB）：絕對禁止寫出最終比數與誰贏誰輸！只能摘要對戰組合、關鍵球星表現亮點（例如「第四節讀秒階段逆轉戰」、「單場飆進多記三分」）。
        2. 若為球評分析（如克里夫三）：提煉講者的 3 個核心戰術觀點、交易評估或數據論點。
    """
}

class VideoReview(BaseModel):
    score: int = Field(description="推薦指數 1 到 5 分")
    verdict: str = Field(description="簡短結論，例如：🟢 必看推薦、🟡 配飯可看、🔴 建議略過")
    embed_color: int = Field(description="Discord 卡片色碼：4-5分填 3066993(綠)、3分填 16776960(黃)、1-2分填 15158332(紅)")
    summary_bullets: list[str] = Field(description="3 個核心重點摘要，每點不超過 45 字")
    reason: str = Field(description="一句話守門員短評，點出這部片最值得看或最該避雷的原因")

def load_json(path: str, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def resolve_channel_id(handle: str, cache: dict) -> str:
    """
    透過 yt-dlp 或直接判斷來解析頻道 Handle 或 URL 為頻道 ID (UC開頭)。
    這是因為 YouTube 網頁結構變化，原始的 Regex 可能失效。
    """
    if handle in cache:
        return cache[handle]

    # 如果 handle 已經是標準的 YouTube Channel ID (UC開頭，24字元)，則直接返回
    if handle.startswith("UC") and len(handle) == 24 and re.match(r"UC[\w-]+", handle):
        cache[handle] = handle
        save_json(CACHE_FILE, cache)
        return handle

    # 構建用於 yt-dlp 的輸入 URL。yt-dlp 可以處理 @handle 或完整的 YouTube URL。
    if handle.startswith("http://") or handle.startswith("https://"):
        input_identifier = handle
    elif handle.startswith("@"): # 假設是 @handle 格式
        input_identifier = f"https://www.youtube.com/{handle}"
    else: # 可能是 legacy /user/ 或 /c/ 格式，或者直接是 channel ID 的一部分
        input_identifier = f"https://www.youtube.com/{handle}"

    cmd = ["yt-dlp", "--print", "channel_id", input_identifier, "--no-warnings"]
    
    try:
        # 增加 yt-dlp 的超時時間，避免解析延遲導致失敗
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
        channel_id = result.stdout.strip()
        
        if channel_id and channel_id.startswith("UC") and len(channel_id) == 24:
            cache[handle] = channel_id
            save_json(CACHE_FILE, cache)
            return channel_id
        else:
            # yt-dlp 成功執行但返回的不是有效的頻道 ID
            raise ValueError(f"yt-dlp 執行成功但無法解析出有效的頻道 ID (UC開頭且長度24): '{channel_id}' for handle: {handle}")
    except subprocess.CalledProcessError as e:
        # yt-dlp 執行失敗，通常是因為找不到頻道或 URL 無效
        print(f"yt-dlp 執行失敗，無法解析頻道 Handle ID '{handle}': {e.stderr.strip()}")
        raise ValueError(f"yt-dlp 無法解析頻道 Handle ID: {handle}") from e
    except subprocess.TimeoutExpired:
        # yt-dlp 執行超時
        print(f"yt-dlp 解析頻道 Handle ID '{handle}' 時超時。請嘗試再次執行或檢查網路連線。")
        raise ValueError(f"yt-dlp 無法解析頻道 Handle ID (超時): {handle}")
    except Exception as e:
        # 捕獲其他未預期的錯誤
        print(f"解析頻道 Handle ID '{handle}' 時發生未知錯誤: {e}")
        raise ValueError(f"未知錯誤，無法解析頻道 Handle ID: {handle}") from e

def fetch_transcript(video_id: str) -> str:
    try:
        ytt = YouTubeTranscriptApi()
        transcript_list = ytt.fetch(video_id, languages=['zh-TW', 'zh-Hant', 'zh-CN', 'en'])
        return " ".join([s.text for s in transcript_list])
    except Exception:
        return ""

def analyze_video(client: genai.Client, title: str, description: str, transcript: str, category: str) -> VideoReview:
    system_prompt = CATEGORY_PROMPTS.get(category, CATEGORY_PROMPTS["lifestyle"])
    content_source = transcript[:15000] if transcript else f"（無字幕，僅提供影片簡介）：{description}"
    prompt = f"影片標題：{title}\n內容資訊：{content_source}"

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=VideoReview,
            temperature=0.3,
        ),
    )
    return response.parsed

def send_discord(webhook_url: str, tag: str, video: dict, review: VideoReview):
    if not webhook_url:
        return
    stars = "⭐" * review.score + "☆" * (5 - review.score)
    bullets = "\n".join([f"• {b}" for b in review.summary_bullets])
    payload = {
        "username": "YouTube 內容守門員",
        "embeds": [{
            "title": f"[{tag}] {video['title']}",
            "url": video["link"],
            "color": review.embed_color,
            "author": {"name": f"頻道：{video['author']}"},
            "thumbnail": {"url": f"https://i.ytimg.com/vi/{video['video_id']}/hqdefault.jpg"},
            "fields": [
                {"name": "推薦評分", "value": f"{stars} ({review.score}/5) — **{review.verdict}**", "inline": False},
                {"name": "📝 核心懶人包", "value": bullets, "inline": False},
                {"name": "💡 守門員短評", "value": review.reason, "inline": False}
            ],
            "footer": {"text": f"發布時間：{video.get('published', '近期')}"}
        }]
    }
    requests.post(webhook_url, json=payload, timeout=10)

def search_topic_videos(query: str, filters: dict) -> list[dict]:
    """引擎 B：使用 yt-dlp 搜尋全網最新主題影片"""
    cmd = [
        "yt-dlp",
        f"ytsearchdate8:{query}",
        "--dump-json",
        "--flat-playlist",
        "--no-warnings"
    ]
    # 搜尋主題影片的超時時間可以設定長一點
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    videos = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        item = json.loads(line)
        title = item.get("title", "")
        duration = item.get("duration") or 0
        if duration < filters["min_duration_sec"] or duration > filters["max_duration_sec"]:
            continue
        if any(bad in title for bad in filters["blacklist_keywords"]):
            continue
        videos.append({
            "video_id": item["id"],
            "title": title,
            "link": f"https://www.youtube.com/watch?v={item['id']}",
            "author": item.get("uploader", "未知頻道"),
            "description": item.get("description", ""),
            "published": item.get("upload_date", "最新")
        })
    return videos

def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 GEMINI_API_KEY 環境變數")
    client = genai.Client(api_key=api_key)

    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    seen_videos = load_json(SEEN_FILE, [])
    id_cache = load_json(CACHE_FILE, {})
    new_seen = list(seen_videos)

    # 1. 執行引擎 A：固定頻道 RSS
    for ch in config["channels"]:
        # resolve_channel_id 函數已更新，能夠更穩定地將 Handle 或 URL 解析為頻道 ID。
        try:
            ch_id = resolve_channel_id(ch["handle"], id_cache)
        except ValueError as e:
            print(f"處理頻道 '{ch['handle']}' 時跳過，原因: {e}")
            continue # 如果解析頻道 ID 失敗，則跳過此頻道

        feed = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}")
        # 每次只檢查最新 2 部，避免初次啟動洗版
        for entry in feed.entries[:2]:
            vid = entry.yt_videoid
            if vid in new_seen:
                continue
            video = {
                "video_id": vid,
                "title": entry.title,
                "link": entry.link,
                "author": ch["name"],
                "description": entry.get("summary", ""),
                "published": entry.get("published", "")[:10]
            }
            transcript = fetch_transcript(vid)
            review = analyze_video(client, video["title"], video["description"], transcript, ch["category"])
            webhook_url = os.environ.get(config["webhooks"][ch["webhook"]])
            send_discord(webhook_url, ch["category"].upper(), video, review)
            new_seen.append(vid)
            time.sleep(2)

    # 2. 執行引擎 B：花蓮 / 宜蘭主題雷達
    for radar in config["topic_radars"]:
        candidates = search_topic_videos(radar["query"], config["radar_filters"])
        for video in candidates:
            vid = video["video_id"]
            if vid in new_seen:
                continue
            transcript = fetch_transcript(vid)
            review = analyze_video(client, video["title"], video["description"], transcript, radar["category"])
            if review.score >= radar["min_score"]:
                webhook_url = os.environ.get(config["webhooks"][radar["webhook"]])
                send_discord(webhook_url, radar["tag"], video, review)
            new_seen.append(vid)
            time.sleep(2)

    # 僅保留最近 500 筆紀錄避免檔案膨脹
    save_json(SEEN_FILE, new_seen[-500:])

if __name__ == "__main__":
    main()