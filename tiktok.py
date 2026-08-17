from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger(__name__)

USERNAME_RE = re.compile(r"^[a-zA-Z0-9._]{1,24}$")
TIKTOK_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?tiktok\.com/@([a-zA-Z0-9._]+)",
    re.IGNORECASE,
)
UNIVERSAL_DATA_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">(.*?)</script>',
    re.DOTALL,
)

TIKWM_POSTS = "https://www.tikwm.com/api/user/posts"
PROFILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
        "Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIKWM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://www.tikwm.com/",
}


@dataclass
class TikTokVideo:
    video_id: str
    title: str
    cover: str | None
    create_time: int
    nickname: str | None
    username: str


@dataclass
class TikTokUser:
    username: str
    nickname: str
    exists: bool
    sec_uid: str | None = None


def parse_username(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None

    match = TIKTOK_URL_RE.search(raw)
    if match:
        candidate = match.group(1)
    else:
        candidate = raw.lstrip("@").split("?")[0].split("/")[0].strip()

    if USERNAME_RE.fullmatch(candidate):
        return candidate.lower()
    return None


def video_url(username: str, video_id: str) -> str:
    return f"https://www.tiktok.com/@{username}/video/{video_id}"


def escape(text: str | None) -> str:
    return html.escape(text or "", quote=False)


def _user_from_profile(html_text: str, username: str) -> TikTokUser | None:
    match = UNIVERSAL_DATA_RE.search(html_text)
    if not match:
        return None

    try:
        payload = json.loads(match.group(1))
        detail = payload["__DEFAULT_SCOPE__"]["webapp.user-detail"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None

    status = detail.get("statusCode")
    if status not in (0, None):
        return None

    user = (detail.get("userInfo") or {}).get("user") or {}
    unique_id = (user.get("uniqueId") or user.get("unique_id") or "").lower()
    if unique_id and unique_id != username.lower():
        return None
    if not unique_id:
        unique_id = username.lower()

    nickname = user.get("nickname") or unique_id
    return TikTokUser(
        username=unique_id,
        nickname=nickname,
        exists=True,
        sec_uid=user.get("secUid") or user.get("sec_uid"),
    )


def _videos_from_profile(html_text: str, username: str) -> list[TikTokVideo]:
    match = UNIVERSAL_DATA_RE.search(html_text)
    if not match:
        return []

    try:
        payload = json.loads(match.group(1))
        items = (
            payload["__DEFAULT_SCOPE__"]["webapp.user-detail"]
            .get("userInfo", {})
            .get("itemList")
            or []
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return []

    videos: list[TikTokVideo] = []
    for item in items:
        video_id = str(item.get("id") or item.get("aweme_id") or "")
        if not video_id:
            continue
        author = item.get("author") or {}
        video = item.get("video") or {}
        videos.append(
            TikTokVideo(
                video_id=video_id,
                title=(item.get("desc") or item.get("title") or "").strip(),
                cover=video.get("cover") or video.get("originCover"),
                create_time=int(item.get("createTime") or item.get("create_time") or 0),
                nickname=author.get("nickname"),
                username=author.get("uniqueId") or username,
            )
        )
    return videos


def fetch_videos_fast(username: str, sec_uid: str | None = None, proxy: str | None = None) -> list[TikTokVideo]:
    return _load_via_ytdlp(username, sec_uid, proxy)


def _videos_via_ytdlp(sec_uid: str, username: str, proxy: str | None = None) -> list[TikTokVideo]:
    from yt_dlp import YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlistend": 5,
        "skip_download": True,
        "ignoreerrors": True,
        "socket_timeout": 20,
    }
    if proxy:
        options["proxy"] = proxy
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"tiktokuser:{sec_uid}", download=False)
    except Exception as exc:
        logger.warning("yt-dlp failed for @%s: %s", username, exc)
        return []

    if not info:
        return []

    videos: list[TikTokVideo] = []
    for entry in info.get("entries") or []:
        if not entry:
            continue
        video_id = str(entry.get("id") or "")
        if not video_id:
            continue
        thumbnails = entry.get("thumbnails") or []
        cover = None
        if thumbnails:
            cover = thumbnails[-1].get("url")
        videos.append(
            TikTokVideo(
                video_id=video_id,
                title=(entry.get("title") or entry.get("description") or "").strip(),
                cover=cover,
                create_time=int(entry.get("timestamp") or 0),
                nickname=entry.get("channel") or entry.get("uploader"),
                username=entry.get("uploader") or username,
            )
        )
    return videos


def _videos_from_tikwm(data: dict, username: str) -> list[TikTokVideo]:
    items = data.get("videos") or data.get("aweme_list") or []
    videos: list[TikTokVideo] = []
    for item in items:
        video_id = str(item.get("video_id") or item.get("aweme_id") or "")
        if not video_id:
            continue
        author = item.get("author") or {}
        videos.append(
            TikTokVideo(
                video_id=video_id,
                title=(item.get("title") or item.get("desc") or "").strip(),
                cover=item.get("cover") or item.get("origin_cover"),
                create_time=int(item.get("create_time") or 0),
                nickname=author.get("nickname"),
                username=author.get("unique_id") or username,
            )
        )
    return videos


async def _fetch_profile_html(
    session: aiohttp.ClientSession,
    username: str,
) -> str | None:
    url = f"https://www.tiktok.com/@{username}"
    try:
        async with session.get(
            url,
            headers=PROFILE_HEADERS,
            timeout=aiohttp.ClientTimeout(total=20),
            allow_redirects=True,
        ) as response:
            if response.status != 200:
                logger.warning("TikTok profile @%s -> HTTP %s", username, response.status)
                return None
            return await response.text()
    except (aiohttp.ClientError, TimeoutError) as exc:
        logger.warning("TikTok profile request failed: %s", exc)
        return None


async def get_user(session: aiohttp.ClientSession, username: str) -> TikTokUser | None:
    html_text = await _fetch_profile_html(session, username)
    if not html_text:
        return None
    return _user_from_profile(html_text, username)


_FETCH_LOCK: asyncio.Lock | None = None


def _fetch_lock() -> asyncio.Lock:
    global _FETCH_LOCK
    if _FETCH_LOCK is None:
        _FETCH_LOCK = asyncio.Lock()
    return _FETCH_LOCK


def _videos_from_html_ids(html_text: str, username: str) -> list[TikTokVideo]:
    found = re.findall(r'"aweme_id":"(\d{15,})"', html_text)
    found += re.findall(r'"id":"(\d{19})"', html_text)
    unique: list[str] = []
    for video_id in found:
        if video_id not in unique:
            unique.append(video_id)
    return [
        TikTokVideo(
            video_id=video_id,
            title="",
            cover=None,
            create_time=0,
            nickname=None,
            username=username,
        )
        for video_id in unique[:10]
    ]


def _load_via_ytdlp(username: str, sec_uid: str | None, proxy: str | None) -> list[TikTokVideo]:
    videos = _videos_via_ytdlp(sec_uid or "", username, proxy) if sec_uid else []
    if videos:
        return videos

    from yt_dlp import YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlistend": 5,
        "skip_download": True,
        "ignoreerrors": True,
        "socket_timeout": 20,
    }
    if proxy:
        options["proxy"] = proxy
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"https://www.tiktok.com/@{username}", download=False)
    except Exception as exc:
        logger.warning("yt-dlp url failed for @%s: %s", username, exc)
        return []

    videos = []
    for entry in (info or {}).get("entries") or []:
        if not entry or not entry.get("id"):
            continue
        videos.append(
            TikTokVideo(
                video_id=str(entry["id"]),
                title=(entry.get("title") or "").strip(),
                cover=None,
                create_time=int(entry.get("timestamp") or 0),
                nickname=entry.get("channel") or entry.get("uploader"),
                username=entry.get("uploader") or username,
            )
        )
    return videos


async def get_latest_videos(
    session: aiohttp.ClientSession,
    username: str,
    count: int = 10,
) -> list[TikTokVideo] | None:
    from config import TELEGRAM_PROXY

    async with _fetch_lock():
        html_text = await _fetch_profile_html(session, username)
        user = _user_from_profile(html_text, username) if html_text else None
        if html_text:
            from_page = _videos_from_profile(html_text, username)
            if from_page:
                from_page.sort(key=lambda item: item.create_time, reverse=True)
                logger.info("Loaded %s videos for @%s from page", len(from_page), username)
                return from_page[:count]

        try:
            videos = await asyncio.wait_for(
                asyncio.to_thread(
                    _load_via_ytdlp,
                    username,
                    user.sec_uid if user else None,
                    TELEGRAM_PROXY or None,
                ),
                timeout=50,
            )
        except TimeoutError:
            logger.warning("yt-dlp timed out for @%s", username)
            videos = []
        if videos:
            videos.sort(key=lambda item: item.create_time, reverse=True)
            logger.info("Loaded %s videos for @%s via yt-dlp", len(videos), username)
            return videos[:count]

        try:
            async with session.get(
                TIKWM_POSTS,
                params={"unique_id": username, "count": count},
                headers=TIKWM_HEADERS,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status == 200:
                    payload = await response.json(content_type=None)
                    if isinstance(payload, dict) and payload.get("code") == 0:
                        videos = _videos_from_tikwm(payload.get("data") or {}, username)
                        videos.sort(key=lambda item: item.create_time, reverse=True)
                        if videos:
                            logger.info("Loaded %s videos for @%s via fallback", len(videos), username)
                            return videos[:count]
        except (aiohttp.ClientError, json.JSONDecodeError, TimeoutError) as exc:
            logger.warning("Fallback video request failed: %s", exc)

        if html_text:
            from_ids = _videos_from_html_ids(html_text, username)
            if from_ids:
                logger.info("Loaded %s video ids for @%s from html", len(from_ids), username)
                return from_ids[:count]

        logger.warning("Could not load videos for @%s", username)
        return None
