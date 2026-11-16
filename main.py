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

def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def load_json(path: str, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def resolve_channel_id(ch: dict, cache: dict) -> str:
    """多重備援解析頻道 UC ID（支援預設 ID 驗證、Consent Cookie 繞過、yt-dlp Android 偽裝搜尋）"""
    name = ch["name"]
    handle = ch.get("handle", "")
    cache_key = handle or name

    if cache_key in cache:
        return cache[cache_key]

    # 1. 若 config 已提供 channel_id，先驗證該 RSS 是否有效
    preset_id = ch.get("channel_id", "")
    if preset_id.startswith("UC") and len(preset_id) == 24:
        test_feed = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={preset_id}")
        if test_feed.entries:
            cache[cache_key] = preset_id
            save_json(CACHE_FILE, cache)
            log(f"✅ 驗證預設 ID 成功：{name} -> {preset_id}")
            return preset_id

    # 2. 帶入 CONSENT Cookie 抓取頻道首頁原始碼（繞過雲端 IP 同意閘道）
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Cookie": "CONSENT=YES+cb.20210328-17-p0.en+FX+999"
    }
    if handle:
        url = f"https://www.youtube.com/{handle}" if handle.startswith("@") else f"https://www.youtube.com/@{handle}"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            match = re.search(r'"externalId":"(UC[\w-]{22})"', resp.text) or re.search(r'"channelId":"(UC[\w-]{22})"', resp.text)
            if match:
                ch_id = match.group(1)
                cache[cache_key] = ch_id
                save_json(CACHE_FILE, cache)
                log(f"✅ 網頁解析成功：{name} ({handle}) -> {ch_id}")
                return ch_id
        except Exception as e:
            log(f"⚠️ 網頁解析 {name} 異常: {e}")

    # 3. 備援：用 yt-dlp Android Client 搜尋頻道名稱第 1 筆影片提取 channel_id
    try:
        cmd = [
            "yt-dlp",
            f"ytsearch1:{name} YouTube",
            "--print", "channel_id",
            "--extractor-args", "youtube:player_client=android,web",
            "--no-warnings"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        lines = [l.strip() for l in res.stdout.strip().splitlines() if l.strip().startswith("UC")]
        if lines and len(lines[0]) == 24:
            ch_id = lines[0]
            cache[cache_key] = ch_id
            save_json(CACHE_FILE, cache)
            log(f"✅ 搜尋備援解析成功：{name} -> {ch_id}")
            return ch_id
    except Exception as e:
        log(f"⚠️ 搜尋備援解析 {name} 異常: {e}")

    raise ValueError(f"無法解析頻道: {name} ({handle})")

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
        log(f"⚠️ 略過推播（未設定 Webhook）：{video['title']}")
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
    resp = requests.post(webhook_url, json=payload, timeout=10)
    resp.raise_for_status()
    log(f"📨 已推播至 Discord：[{tag}] {video['title']} ({review.score}分)")

def search_topic_videos(query: str, filters: dict) -> list[dict]:
    """引擎 B：使用 Android Client 偽裝搜尋全網主題影片"""
    log(f"🔍 啟動主題雷達搜尋：{query}")
    cmd = [
        "yt-dlp",
        f"ytsearchdate8:{query}",
        "--dump-json",
        "--flat-playlist",
        "--extractor-args", "youtube:player_client=android,web",
        "--no-warnings"
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    except Exception as e:
        log(f"⚠️ 主題雷達搜尋超時或失敗 ({query}): {e}")
        return []

    videos = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        try:
            item = json.loads(line)
            title = item.get("title", "")
            duration = item.get("duration") or 0
            if not isinstance(duration, (int, float)):
                duration = 0
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
        except Exception:
            continue
    log(f"🔍 主題雷達找到 {len(videos)} 部符合條件的候選影片")
    return videos

def main():
    log("🚀 啟動 YouTube 內容守門員...")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 GEMINI_API_KEY 環境變數")
    client = genai.Client(api_key=api_key)

    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    seen_videos = load_json(SEEN_FILE, [])
    id_cache = load_json(CACHE_FILE, {})
    new_seen = list(seen_videos)
    three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)

    # 1. 引擎 A：固定頻道 RSS
    for ch in config["channels"]:
        try:
            ch_id = resolve_channel_id(ch, id_cache)
        except ValueError as e:
            log(f"⏭️ 跳過頻道 {ch['name']}：{e}")
            continue

        feed = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}")
        for entry in feed.entries[:2]:
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                published_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                if published_dt < three_days_ago:
                    continue

            vid = entry.yt_videoid
            if vid in new_seen:
                continue

            log(f"🎬 正在審閱 [{ch['name']}]：{entry.title}")
            video = {
                "video_id": vid,
                "title": entry.title,
                "link": entry.link,
                "author": ch["name"],
                "description": entry.get("summary", ""),
                "published": entry.get("published", "")[:10]
            }
            try:
                transcript = fetch_transcript(vid)
                review = analyze_video(client, video["title"], video["description"], transcript, ch["category"])
                webhook_url = os.environ.get(config["webhooks"][ch["webhook"]])
                send_discord(webhook_url, ch["category"].upper(), video, review)
                new_seen.append(vid)
            except Exception as e:
                log(f"❌ 審閱或推播失敗 ({vid}): {e}")
            time.sleep(1)

    # 2. 引擎 B：花蓮 / 宜蘭主題雷達
    for radar in config["topic_radars"]:
        candidates = search_topic_videos(radar["query"], config["radar_filters"])
        for video in candidates[:3]:
            vid = video["video_id"]
            if vid in new_seen:
                continue
            log(f"🧭 正在審閱 [{radar['tag']}]：{video['title']}")
            try:
                transcript = fetch_transcript(vid)
                review = analyze_video(client, video["title"], video["description"], transcript, radar["category"])
                if review.score >= radar["min_score"]:
                    webhook_url = os.environ.get(config["webhooks"][radar["webhook"]])
                    send_discord(webhook_url, radar["tag"], video, review)
                else:
                    log(f"🗑️ 評分未達標 ({review.score}分)，略過：{video['title']}")
                new_seen.append(vid)
            except Exception as e:
                log(f"❌ 雷達影片審閱失敗 ({vid}): {e}")
            time.sleep(1)

    save_json(SEEN_FILE, new_seen[-500:])
    log("🏁 全部巡邏任務完成！")

if __name__ == "__main__":
    main()
