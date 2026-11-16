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

# 軌道 A：常看頻道（只做重點整理，不打分數淘汰）
CHANNEL_SUMMARY_PROMPTS = {
    "cooking": "請用 3 個簡短重點列出這集教了什麼菜名、核心食材與烹調關鍵（每點不超過 35 字）。若本集剛好是出遊/露營紀錄，則改列景點與特色。",
    "travel_outdoor": "請用 3 個簡短重點列出這集去了哪個營區或景點、交通/環境特色與最大看點（每點不超過 35 字）。",
    "lifestyle": "請用 3 個簡短重點提煉這集的企劃主題、有趣看點，並註明是否有特定產品業配（每點不超過 35 字）。",
    "sports": "【絕對防劇透原則：嚴禁寫出最終比數與誰贏誰輸！】請用 3 個簡短重點列出對戰組合、球星亮點或球評的核心論點（每點不超過 35 字）。"
}

# 軌道 B：陌生主題雷達（嚴格評分排雷）
RADAR_AUDIT_PROMPT = """
你是一位精打細算的台灣自由行與露營規劃師。請嚴格審閱這部陌生頻道的旅遊/露營影片：
1. 強制提取具體資訊：營區或店家名稱、所在鄉鎮、交通/停車提示、真實優缺點或避坑提醒。
2. 評分標準：必須包含具體新開幕店家、秘境或親子實用資訊才給 4~5 分；若是純風景配樂、標題黨、業配文或無具體地點資訊的流水帳，直接給 1~2 分。
"""

class ChannelPreview(BaseModel):
    bullets: list[str] = Field(description="3 個簡短重點預覽，每點不超過 35 字")
    quick_note: str = Field(description="一句話看點提示（例如：適合配飯、週末備料參考、純戰術分析）")

class RadarReview(BaseModel):
    score: int = Field(description="實用情報推薦評分 1 到 5 分")
    verdict: str = Field(description="例如：🟢 必看情報、🟡 普通參考、🔴 建議略過")
    summary_bullets: list[str] = Field(description="3 個核心情報重點（含具體店名/地點），每點不超過 40 字")
    reason: str = Field(description="一句話說明值得看或避雷的原因")

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

def call_gemini_with_backoff(client: genai.Client, prompt: str, schema):
    """內建指數退避重試，遇 429 自動等待，徹底避免限流報錯"""
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.2,
                ),
            )
            return response.parsed
        except Exception as e:
            if ("429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)) and attempt < 2:
                wait_sec = 25 * (attempt + 1)
                log(f"⏳ 觸及免費 API 速率限制，自動退避等待 {wait_sec} 秒後重試...")
                time.sleep(wait_sec)
            else:
                raise

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
                log(f"✅ 網頁解析成功：{name} -> {ch_id}")
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
            log(f"✅ 搜尋備援解析成功：{name} -> {ch_id}")
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

def send_discord_channel_card(webhook_url: str, video: dict, preview: ChannelPreview):
    """軌道 A 卡片：乾淨呈現常看頻道新片預覽，不打分數"""
    if not webhook_url:
        return
    bullets = "\n".join([f"• {b}" for b in preview.bullets])
    payload = {
        "username": "YouTube 訂閱小秘書",
        "embeds": [{
            "title": f"🆕 {video['title']}",
            "url": video["link"],
            "color": 3447003,  # 沉穩藍色邊條，代表訂閱頻道更新
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
    """軌道 B 卡片：陌生主題雷達精選（僅 4 分以上推播）"""
    if not webhook_url:
        return
    stars = "⭐" * review.score + "☆" * (5 - review.score)
    bullets = "\n".join([f"• {b}" for b in review.summary_bullets])
    payload = {
        "username": "YouTube 情報守門員",
        "embeds": [{
            "title": f"[{tag}] {video['title']}",
            "url": video["link"],
            "color": 3066993,  # 綠色邊條，代表通過嚴格篩選
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
    log("🚀 啟動雙軌 YouTube 內容助理...")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 GEMINI_API_KEY 環境變數")
    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})

    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

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

    # ==========================================
    # 軌道 A：常看頻道（全部保留，僅做快速重點摘要）
    # ==========================================
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

            video = {
                "video_id": vid,
                "title": entry.title,
                "link": entry.link,
                "author": ch["name"],
                "description": entry.get("summary", ""),
                "published": entry.get("published", "")[:10]
            }
            try:
                rule = CHANNEL_SUMMARY_PROMPTS.get(ch["category"], CHANNEL_SUMMARY_PROMPTS["lifestyle"])
                # 常看頻道直接用標題與簡介做快速預覽，省下大量字幕 Token
                prompt = f"【摘要規則】：{rule}\n【頻道】：{ch['name']}\n【標題】：{video['title']}\n【簡介】：{video['description'][:1500]}"
                preview = call_gemini_with_backoff(client, prompt, ChannelPreview)
                webhook_url = os.environ.get(config["webhooks"][ch["webhook"]])
                send_discord_channel_card(webhook_url, video, preview)
                report["channel_pushed"].append({"author": ch["name"], "title": video["title"], "note": preview.quick_note})
                new_seen.append(vid)
                time.sleep(4)
            except Exception as e:
                log(f"❌ 訂閱頻道摘要失敗 ({vid}): {e}")
                report["error_count"] += 1

    # ==========================================
    # 軌道 B：花蓮 / 宜蘭主題雷達（嚴格抓字幕評分排雷）
    # ==========================================
    for radar in config["topic_radars"]:
        candidates = search_topic_videos(radar["query"], config["radar_filters"])
        for video in candidates[:3]:
            vid = video["video_id"]
            if vid in new_seen:
                continue
            try:
                transcript = fetch_transcript(vid)
                content = transcript[:12000] if transcript else f"（無字幕，僅提供簡介）：{video['description'][:1500]}"
                prompt = f"【嚴格審閱準則】：{RADAR_AUDIT_PROMPT}\n【標題】：{video['title']}\n【內容】：{content}"
                review = call_gemini_with_backoff(client, prompt, RadarReview)
                if review.score >= radar["min_score"]:
                    webhook_url = os.environ.get(config["webhooks"][radar["webhook"]])
                    send_discord_radar_card(webhook_url, radar["tag"], video, review)
                    report["radar_pushed"].append({"tag": radar["tag"], "title": video["title"], "score": review.score})
                else:
                    log(f"🗑️ [雷達過濾 {review.score}分] {video['title']}")
                    report["radar_filtered_out"] += 1
                new_seen.append(vid)
                time.sleep(4)
            except Exception as e:
                log(f"❌ 主題雷達審閱失敗 ({vid}): {e}")
                report["error_count"] += 1

    save_json(SEEN_FILE, new_seen[-500:])
    save_json(REPORT_FILE, report)

    if report["error_count"] > 0:
        log(f"⚠️ 本輪發生 {report['error_count']} 筆異常，觸發 Auto-Fixer...")
        sys.exit(1)
    log("🏁 雙軌巡邏任務圓滿完成！")

if __name__ == "__main__":
    main()
