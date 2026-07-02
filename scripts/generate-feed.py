#!/usr/bin/env python3
"""Central feed generator for last30days-cn.

Run this on a trusted machine or CI job, then publish data/feeds/*.json to a
static URL and set LAST30DAYS_CN_FEED_BASE for local skill runs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).parent.resolve()
SKILL_DIR = SCRIPT_DIR.parent
CONFIG_PATH = SKILL_DIR / "config" / "sources.json"
OUT_DIR = SKILL_DIR / "data" / "feeds"
X_API_BASE = "https://api.x.com/2"
POD2TXT_BASE = "https://pod2txt.vercel.app/api"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def request_json(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def request_text(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def post_json(url: str, payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "last30days-cn/0.1"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_sources() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def write_feed(name: str, payload: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_x(accounts: list[dict[str, str]], bearer: str, lookback_hours: int, max_per_user: int) -> dict[str, Any]:
    errors: list[str] = []
    cutoff = utc_now() - dt.timedelta(hours=lookback_hours)
    handles = [a["handle"].lstrip("@") for a in accounts if a.get("handle")]
    account_by_handle = {a["handle"].lstrip("@").lower(): a for a in accounts if a.get("handle")}
    user_map: dict[str, dict[str, Any]] = {}

    for i in range(0, len(handles), 100):
        batch = handles[i : i + 100]
        params = urllib.parse.urlencode({"usernames": ",".join(batch), "user.fields": "name,description"})
        try:
            data = request_json(
                f"{X_API_BASE}/users/by?{params}",
                headers={"Authorization": f"Bearer {bearer}", "User-Agent": "last30days-cn/0.1"},
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"X user lookup failed: {exc}")
            continue
        for user in data.get("data", []):
            user_map[user["username"].lower()] = user

    results = []
    for handle in handles:
        user = user_map.get(handle.lower())
        if not user:
            continue
        params = urllib.parse.urlencode({
            "max_results": "5",
            "tweet.fields": "created_at,public_metrics,referenced_tweets,note_tweet",
            "exclude": "retweets,replies",
            "start_time": cutoff.isoformat().replace("+00:00", "Z"),
        })
        try:
            data = request_json(
                f"{X_API_BASE}/users/{user['id']}/tweets?{params}",
                headers={"Authorization": f"Bearer {bearer}", "User-Agent": "last30days-cn/0.1"},
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"X tweets failed for @{handle}: {exc}")
            continue
        tweets = []
        for tweet in data.get("data", [])[:max_per_user]:
            metrics = tweet.get("public_metrics", {}) or {}
            tweets.append({
                "id": tweet.get("id"),
                "text": (tweet.get("note_tweet") or {}).get("text") or tweet.get("text", ""),
                "createdAt": tweet.get("created_at"),
                "url": f"https://x.com/{handle}/status/{tweet.get('id')}",
                "likes": metrics.get("like_count", 0),
                "retweets": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
                "isQuote": any(r.get("type") == "quoted" for r in tweet.get("referenced_tweets", []) or []),
            })
        if tweets:
            configured = account_by_handle.get(handle.lower(), {})
            results.append({
                "source": "x",
                "name": configured.get("name") or user.get("name") or handle,
                "handle": handle,
                "bio": user.get("description", ""),
                "tweets": tweets,
            })
        time.sleep(0.2)

    return {
        "generatedAt": utc_now().isoformat(),
        "lookbackHours": lookback_hours,
        "x": results,
        "stats": {"xBuilders": len(results), "totalTweets": sum(len(r["tweets"]) for r in results)},
        "errors": errors or None,
    }


def parse_rss_items(xml: str) -> list[dict[str, str | None]]:
    items = []
    for block in re.findall(r"<item>([\s\S]*?)</item>", xml, flags=re.I):
        title = _xml_text(block, "title") or "Untitled"
        guid = _xml_text(block, "guid") or _xml_text(block, "link") or title
        published = _xml_text(block, "pubDate")
        link = _xml_text(block, "link")
        items.append({"title": title, "guid": guid, "publishedAt": _parse_rss_date(published), "link": link})
    return items


def _xml_text(block: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}[^>]*><!\[CDATA\[([\s\S]*?)\]\]></{tag}>", block, flags=re.I)
    if not match:
        match = re.search(rf"<{tag}[^>]*>([\s\S]*?)</{tag}>", block, flags=re.I)
    return match.group(1).strip() if match else None


def _parse_rss_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return None


def youtube_atom_url(url: str) -> str | None:
    playlist = re.search(r"[?&]list=([A-Za-z0-9_-]+)", url)
    if playlist:
        return f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist.group(1)}"
    channel = re.search(r"/channel/(UC[A-Za-z0-9_-]+)", url)
    if channel:
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel.group(1)}"
    return None


def parse_youtube_atom(xml: str) -> list[dict[str, str]]:
    videos = []
    for block in re.findall(r"<entry>([\s\S]*?)</entry>", xml, flags=re.I):
        title = _xml_text(block, "title") or "Untitled"
        video_id = _xml_text(block, "yt:videoId")
        published = _xml_text(block, "published")
        if video_id:
            videos.append({
                "source": "youtube",
                "title": title,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "publishedAt": published,
                "text": title,
            })
    return videos


def fetch_podcast_transcript(rss_url: str, guid: str, api_key: str) -> str | None:
    for _ in range(5):
        data = post_json(f"{POD2TXT_BASE}/transcript", {"feedurl": rss_url, "guid": guid, "apikey": api_key}, timeout=45)
        if data.get("status") == "ready" and data.get("url"):
            return request_text(data["url"], timeout=45)
        if data.get("status") != "processing":
            return None
        time.sleep(30)
    return None


def fetch_podcasts(podcasts: list[dict[str, str]], api_key: str | None, lookback_hours: int) -> dict[str, Any]:
    errors: list[str] = []
    cutoff = utc_now() - dt.timedelta(hours=lookback_hours)
    results = []
    youtube_items = []

    for podcast in podcasts:
        rss_url = podcast.get("rssUrl")
        if not rss_url:
            continue
        try:
            rss = request_text(rss_url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, */*"})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"RSS failed for {podcast.get('name')}: {exc}")
            continue
        candidates = []
        for ep in parse_rss_items(rss)[:3]:
            published = ep.get("publishedAt")
            parsed = dt.datetime.fromisoformat(published) if published else None
            if parsed is None or parsed >= cutoff:
                candidates.append(ep)
        if candidates and api_key:
            selected = candidates[0]
            transcript = fetch_podcast_transcript(rss_url, str(selected["guid"]), api_key)
            if transcript:
                results.append({
                    "source": "podcast",
                    "name": podcast.get("name", "Podcast"),
                    "title": selected.get("title"),
                    "guid": selected.get("guid"),
                    "url": selected.get("link") or podcast.get("url", ""),
                    "publishedAt": selected.get("publishedAt"),
                    "transcript": transcript,
                })
        yt = youtube_atom_url(podcast.get("url", ""))
        if yt:
            try:
                youtube_items.extend(parse_youtube_atom(request_text(yt))[:5])
            except Exception as exc:  # noqa: BLE001
                errors.append(f"YouTube atom failed for {podcast.get('name')}: {exc}")

    return {
        "podcasts": {
            "generatedAt": utc_now().isoformat(),
            "lookbackHours": lookback_hours,
            "podcasts": results,
            "stats": {"podcastEpisodes": len(results)},
            "errors": errors or None,
        },
        "youtube": {
            "generatedAt": utc_now().isoformat(),
            "lookbackHours": lookback_hours,
            "items": youtube_items,
            "stats": {"youtubeItems": len(youtube_items)},
            "errors": errors or None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate central feeds for last30days-cn")
    parser.add_argument("--x-only", action="store_true")
    parser.add_argument("--podcasts-only", action="store_true")
    args = parser.parse_args()

    sources = load_sources()
    x_token = os.environ.get("X_BEARER_TOKEN")
    pod2txt_key = os.environ.get("POD2TXT_API_KEY")

    run_x = not args.podcasts_only
    run_podcasts = not args.x_only

    if run_x:
        if not x_token:
            print("X_BEARER_TOKEN not set; writing empty feed-x.json", file=sys.stderr)
            write_feed("feed-x.json", {"generatedAt": utc_now().isoformat(), "x": [], "errors": ["X_BEARER_TOKEN not set"]})
        else:
            write_feed("feed-x.json", fetch_x(sources.get("x_accounts", []), x_token, sources.get("xLookbackHours", 24), sources.get("maxTweetsPerUser", 3)))

    if run_podcasts:
        result = fetch_podcasts(sources.get("podcasts", []), pod2txt_key, sources.get("podcastLookbackHours", 336))
        write_feed("feed-podcasts.json", result["podcasts"])
        write_feed("feed-youtube.json", result["youtube"])

    for empty_name in ("feed-github.json", "feed-web.json"):
        path = OUT_DIR / empty_name
        if not path.exists():
            write_feed(empty_name, {"generatedAt": utc_now().isoformat(), "items": [], "stats": {}})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
