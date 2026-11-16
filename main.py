import os
import re
import sys
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
REPORT_FILE = "run_report.json"

MODEL_POOL = ["gemini-2.5-flash", "gemini-3.5-flash-lite"]

RADAR_AUDIT_PROMPT = """
你是一位精打細算的台灣自由行與露營規劃師。請嚴格審閱這部陌生頻道的旅遊/露營影片：
1. 強制提取具體資訊：營區或店家名稱、所在鄉鎮、交通/停車提示、真實優缺點或避坑提醒。
2. 評分標準：必須包含具體新開幕店家、秘境或親子實用資訊才給 4~5 分；若是純風景配樂、標題黨、業配文或無具體地點資訊的流水帳，直接給 1~2 分。
"""

class SingleChannelPreview(BaseModel):
    video_id: str = Field(description="對應輸入的 video_id")
    bullets: list[str] = Field(description="3 個簡短重點預覽，每點不超過 35 字")
    quick_note: str = Field(description="一句話看點提示（例如：適合配飯、週末備料參考、戰術分析）")

class BatchChannelPreviews(BaseModel):
    items: list[SingleChannelPreview] = Field(description="所有輸入影片的摘要列表")

class RadarReview(BaseModel):
    score: int = Field(description="實用情報推薦評分 1 到 5 分")
    verdict: str = Field(description="例如：🟢 必看情報、🟡 普通參考、🔴 建議略過")
    summary_bullets: list[str] = Field(description="3 個核心情報重點（含具體店名/地點），每點不超過 40 字")
    reason: str = Field(description="一句話說明值得看或避雷的原因")

class QuotaExhaustedError(Exception):
    pass

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

def call_gemini_smart(client: genai.Client, prompt: str, schema):
    for model_name in MODEL_POOL:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.2,
                ),
            )
            log(f"🤖 模型 {model_name} 審閱完成")
            return response.parsed
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                log(f"⚠️ 模型 {model_name} 觸及配額上限，切換下一個備援模型...")
                time.sleep(3)
                continue
            raise
    raise QuotaExhaustedError("所有免費模型今日配額皆已用盡")

def resolve_channel_id(ch: dict, cache: dict) -> str:
    name = ch["name"]
    handle = ch.get("handle", "")
    cache_key = handle or name

    if cache_key in cache:
        return cache[cache_key]

    preset_id = ch.get("channel_id", "")
    if preset_id.startswith("UC") and len(preset_id) == 24:
        test_feed = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={preset_id}")
        if test_feed.entries:
            cache[cache_key] = preset_id
            save_json(CACHE_FILE, cache)
            return preset_id

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
                return ch_id
        except Exception:
            pass

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
            return ch_id
    except Exception:
        pass

    raise ValueError(f"無法解析頻道: {name}")

def fetch_transcript(video_id: str) -> str:
    try:
        ytt = YouTubeTranscriptApi()
        transcript_list = ytt.fetch(video_id, languages=['zh-TW', 'zh-Hant', 'zh-CN', 'en'])
        return " ".join([s.text for s in transcript_list])
    except Exception:
        return ""

def send_discord_channel_card(webhook_url: str, video: dict, preview: SingleChannelPreview):
    if not webhook_url:
        return
    bullets = "\n".join([f"• {b}" for b in preview.bullets])
    payload = {
        "username": "YouTube 訂閱小秘書",
        "embeds": [{
            "title": f"🆕 {video['title']}",
            "url": video["link"],
            "color": 3447003,
            "author": {"name": f"📺 {video['author']}"},
            "thumbnail": {"url": f"https://i.ytimg.com/vi/{video['video_id']}/hqdefault.jpg"},
            "fields": [
                {"name": "📝 30 秒內容預覽", "value": bullets, "inline": False},
                {"name": "💡 看點提示", "value": preview.quick_note, "inline": False}
            ],
            "footer": {"text": f"上架日期：{video.get('published', '近期')}"}
        }]
    }
    requests.post(webhook_url, json=payload, timeout=10).raise_for_status()
    log(f"📨 [訂閱推播] {video['author']} - {video['title']}")

def send_discord_radar_card(webhook_url: str, tag: str, video: dict, review: RadarReview):
    if not webhook_url:
        return
    stars = "⭐" * review.score + "☆" * (5 - review.score)
    bullets = "\n".join([f"• {b}" for b in review.summary_bullets])
    payload = {
        "username": "YouTube 情報守門員",
        "embeds": [{
            "title": f"[{tag}] {video['title']}",
            "url": video["link"],
            "color": 3066993,
            "author": {"name": f"發現頻道：{video['author']}"},
            "thumbnail": {"url": f"https://i.ytimg.com/vi/{video['video_id']}/hqdefault.jpg"},
            "fields": [
                {"name": "情報含金量", "value": f"{stars} ({review.score}/5) — **{review.verdict}**", "inline": False},
                {"name": "📍 實用情報摘要", "value": bullets, "inline": False},
                {"name": "💡 入選理由", "value": review.reason, "inline": False}
            ],
            "footer": {"text": f"發布日期：{video.get('published', '近期')}"}
        }]
    }
    requests.post(webhook_url, json=payload, timeout=10).raise_for_status()
    log(f"📨 [雷達精選 {review.score}分] {video['title']}")

def search_topic_videos(query: str, filters: dict) -> list[dict]:
    log(f"🔍 啟動主題雷達搜尋：{query}")
    cmd = [
        "yt-dlp",
        f"ytsearch8:{query}",
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
    sixty_days_ago = datetime.now(timezone.utc) - timedelta(days=60)
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

            upload_date = item.get("upload_date", "")
            if upload_date and len(upload_date) == 8:
                try:
                    up_dt = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
                    if up_dt < sixty_days_ago:
                        continue
                except ValueError:
                    pass

            videos.append({
                "video_id": item["id"],
                "title": title,
                "link": f"https://www.youtube.com/watch?v={item['id']}",
                "author": item.get("uploader", "未知頻道"),
                "description": item.get("description", ""),
                "published": upload_date or "近期"
            })
        except Exception:
            continue
    return videos

def main():
    log("🚀 啟動雙軌 YouTube 內容助理（省額度批次＋雙模型備援版）...")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 GEMINI_API_KEY 環境變數")
    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})

    try:
        with open("config.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        log(f"❌ 讀取或解析 config.yaml 失敗: {e}")
        sys.exit(1)

    seen_videos = load_json(SEEN_FILE, [])
    id_cache = load_json(CACHE_FILE, {})
    new_seen = list(seen_videos)
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)

    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "skipped_channels": [],
        "channel_pushed": [],
        "radar_pushed": [],
        "radar_filtered_out": 0,
        "error_count": 0
    }

    try:
        channel_new_videos = {}
        for ch in config["channels"]:
            try:
                ch_id = resolve_channel_id(ch, id_cache)
            except ValueError as e:
                log(f"⏭️ 跳過頻道 {ch['name']}：{e}")
                report["skipped_channels"].append(ch["name"])
                continue

            feed = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}")
            for entry in feed.entries[:2]:
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    published_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    if published_dt < seven_days_ago:
                        continue
                vid = entry.yt_videoid
                if vid in new_seen:
                    continue
                channel_new_videos[vid] = {
                    "video_id": vid,
                    "title": entry.title,
                    "link": entry.link,
                    "author": ch["name"],
                    "category": ch["category"],
                    "webhook": ch["webhook"],
                    "description": entry.get("summary", "")[:600],
                    "published": entry.get("published", "")[:10]
                }

        if channel_new_videos:
            log(f"📦 共蒐集到 {len(channel_new_videos)} 部訂閱頻道新片，發送 1 次批次摘要請求...")
            batch_input = [
                {
                    "video_id": v["video_id"],
                    "channel": v["author"],
                    "category": v["category"],
                    "title": v["title"],
                    "description": v["description"]
                }
                for v in channel_new_videos.values()
            ]
            batch_prompt = f"""
            你是一位貼心的影音秘書。請針對以下每部訂閱頻道新片，各自產出 3 個簡短重點預覽（每點不超過 35 字）與一句話看點提示。
            【分類規則】：
            - cooking：列出菜名、核心食材與烹調技巧；若為出遊則改列景點。
            - travel_outdoor：列出營區/景點名稱、特色與看點。
            - lifestyle：提煉企劃亮點，並註明是否有特定業配。
            - sports：【絕對防劇透！嚴禁寫出最終比數與勝負結果！】僅列對戰組合、球星亮點或球評核心論點。

            【新片清單】：
            {json.dumps(batch_input, ensure_ascii=False)}
            """
            batch_res: BatchChannelPreviews = call_gemini_smart(client, batch_prompt, BatchChannelPreviews)
            for item in batch_res.items:
                v = channel_new_videos.get(item.video_id)
                if not v:
                    continue
                webhook_url = os.environ.get(config["webhooks"][v["webhook"]])
                send_discord_channel_card(webhook_url, v, item)
                report["channel_pushed"].append({"author": v["author"], "title": v["title"], "note": item.quick_note})
                new_seen.append(item.video_id)
                time.sleep(1)

        for radar in config["topic_radars"]:
            candidates = search_topic_videos(radar["query"], config["radar_filters"])
            for video in candidates[:2]:
                vid = video["video_id"]
                if vid in new_seen:
                    continue
                transcript = fetch_transcript(vid)
                content = transcript[:10000] if transcript else f"（無字幕，僅提供簡介）：{video['description'][:1200]}"
                prompt = f"【嚴格審閱準則】：{RADAR_AUDIT_PROMPT}\n【標題】：{video['title']}\n【內容】：{content}"
                review: RadarReview = call_gemini_smart(client, prompt, RadarReview)
                if review.score >= radar["min_score"]:
                    webhook_url = os.environ.get(config["webhooks"][radar["webhook"]])
                    send_discord_radar_card(webhook_url, radar["tag"], video, review)
                    report["radar_pushed"].append({"tag": radar["tag"], "title": video["title"], "score": review.score})
                else:
                    log(f"🗑️ [雷達過濾 {review.score}分] {video['title']}")
                    report["radar_filtered_out"] += 1
                new_seen.append(vid)
                time.sleep(3)

    except QuotaExhaustedError as e:
        log(f"⏸️ {e}，已將目前進度安全存檔，待配額重置後自動接力。")
    except Exception as e:
        log(f"❌ 執行過程發生未預期錯誤: {e}")
        report["error_count"] += 1

    save_json(SEEN_FILE, new_seen[-500:])
    save_json(REPORT_FILE, report)

    if report["error_count"] > 0:
        sys.exit(1)
    log("🏁 雙軌巡邏任務完成！")

if __name__ == "__main__":
    main()