#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "youtube-transcript-api>=1.2.0",
#     "yt-dlp>=2025.1.1",
# ]
# ///
"""Fetch YouTube transcripts: one video, or every video of a channel (politely).

Commands:
  video   URL                 print one transcript (original language) as Markdown
  channel URL [--out DIR]     download transcripts for a whole channel, resumable
  status  DIR                 show progress of a channel download
  build   DIR                 rebuild index.csv and ALL_TRANSCRIPTS.md

No API keys needed. Titles come from YouTube oEmbed, the video list from yt-dlp,
transcripts from youtube-transcript-api (the same captions the web player shows).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from youtube_transcript_api import (
    AgeRestricted,
    InvalidVideoId,
    IpBlocked,
    NoTranscriptFound,
    PoTokenRequired,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeTranscriptApi,
)
from youtube_transcript_api.proxies import GenericProxyConfig

DEFAULT_OUT = Path.home() / "youtube_transcripts"
PARAGRAPH_SECONDS = 60          # one timestamped paragraph per ~minute of speech
BACKOFF_MINUTES = (30, 60, 120)  # waits after a block before giving up
MAX_ERROR_ATTEMPTS = 3          # per video, across runs
TRANSLATED_VARIANT = "variant=timing-optimized"  # YouTube auto-dub translation track

# Statuses that are final: the video is not retried on the next run.
FINAL = {"done", "no_transcript", "unavailable"}

BLOCKED_ERRORS = (RequestBlocked, IpBlocked, PoTokenRequired)
NO_TRANSCRIPT_ERRORS = (TranscriptsDisabled, NoTranscriptFound)
UNAVAILABLE_ERRORS = (VideoUnavailable, VideoUnplayable, AgeRestricted, InvalidVideoId)


class Blocked(Exception):
    """YouTube refused our requests (rate limit / bot check)."""


# ---------------------------------------------------------------- helpers

def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def extract_video_id(url_or_id: str) -> str:
    s = url_or_id.strip()
    if re.fullmatch(r"[\w-]{11}", s):
        return s
    u = urllib.parse.urlparse(s)
    if u.hostname and u.hostname.endswith("youtu.be"):
        return u.path.lstrip("/")[:11]
    q = urllib.parse.parse_qs(u.query)
    if "v" in q:
        return q["v"][0][:11]
    m = re.search(r"/(?:shorts|live|embed|v)/([\w-]{11})", u.path)
    if m:
        return m.group(1)
    raise ValueError(f"cannot find a video id in: {url_or_id}")


def is_video_url(s: str) -> bool:
    try:
        extract_video_id(s)
        return True
    except ValueError:
        return False


def oembed(video_id: str) -> dict:
    """Original title and channel of a video. Official endpoint, no key, no quota."""
    url = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(
        f"https://www.youtube.com/watch?v={video_id}", safe="")
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.load(r)
    except Exception:
        return {}


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE).strip()
    return re.sub(r"[\s]+", "_", s)[:80] or "channel"


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def make_api(proxy: str | None) -> YouTubeTranscriptApi:
    if proxy:
        return YouTubeTranscriptApi(
            proxy_config=GenericProxyConfig(http_url=proxy, https_url=proxy))
    return YouTubeTranscriptApi()


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------- transcripts

def pick_original(transcript_list, prefer: list[str] | None):
    """Choose the transcript in the language actually spoken in the video.

    YouTube marks several tracks as auto-generated: the real speech recognition
    of the spoken language (plain, or `variant=gemini`) and machine-translated
    "auto-dub" tracks (`variant=timing-optimized`). A manual track in the
    spoken language beats ASR on quality.
    """
    tracks = list(transcript_list)
    if prefer:
        return transcript_list.find_transcript(prefer)
    generated = [t for t in tracks if t.is_generated]
    asr = [t for t in generated if TRANSLATED_VARIANT not in getattr(t, "_url", "")] or generated
    asr.sort(key=lambda t: "variant=" in getattr(t, "_url", ""))  # plain ASR first
    manual = [t for t in tracks if not t.is_generated]
    if asr:
        base = asr[0].language_code.split("-")[0]
        same_lang_manual = [t for t in manual if t.language_code.split("-")[0] == base]
        return (same_lang_manual or asr)[0]
    if manual:
        return manual[0]
    if tracks:
        return tracks[0]
    raise NoTranscriptFound(transcript_list.video_id, prefer or [], transcript_list)


def fetch_transcript(api, video_id: str, prefer: list[str] | None = None) -> dict:
    """Returns {lang, kind, segments:[(start, text)]}. Raises library errors."""
    t = pick_original(api.list(video_id), prefer)
    fetched = t.fetch()
    segments = [(s.start, s.text.replace("\n", " ").strip()) for s in fetched]
    return {
        "lang": t.language_code,
        "kind": "auto" if t.is_generated else "manual",
        "segments": [s for s in segments if s[1]],
    }


def to_paragraphs(segments, plain: bool = False) -> str:
    paras, cur, cur_start = [], [], None
    for start, text in segments:
        if cur_start is None:
            cur_start = start
        cur.append(text)
        if start - cur_start >= PARAGRAPH_SECONDS and text.rstrip().endswith((".", "?", "!", "…")):
            paras.append((cur_start, " ".join(cur)))
            cur, cur_start = [], None
    if cur:
        paras.append((cur_start, " ".join(cur)))
    if plain:
        return "\n\n".join(p for _, p in paras)
    return "\n\n".join(f"[{fmt_ts(s)}] {p}" for s, p in paras)


def render_markdown(video_id: str, meta: dict, tr: dict, plain: bool = False) -> str:
    words = sum(len(t.split()) for _, t in tr["segments"])
    last = tr["segments"][-1][0] if tr["segments"] else 0
    head = [
        f"# {meta.get('title') or video_id}",
        "",
        f"- URL: https://www.youtube.com/watch?v={video_id}",
        f"- Channel: {meta.get('author_name', '?')}",
        f"- Transcript: {tr['lang']} ({tr['kind']}), ~{fmt_ts(last)}, {words} words",
        "",
        "",
    ]
    return "\n".join(head) + to_paragraphs(tr["segments"], plain) + "\n"


# ------------------------------------------------------------- command: video

def cmd_video(args) -> int:
    vid = extract_video_id(args.url)
    api = make_api(args.proxy)
    try:
        tr = fetch_transcript(api, vid, args.lang)
    except NO_TRANSCRIPT_ERRORS:
        print(f"No transcript available for {vid} (captions disabled or absent).", file=sys.stderr)
        return 2
    except BLOCKED_ERRORS as e:
        print(f"YouTube blocked the request: {type(e).__name__}. Try later or use --proxy.", file=sys.stderr)
        return 3
    except UNAVAILABLE_ERRORS as e:
        print(f"Video unavailable: {type(e).__name__}", file=sys.stderr)
        return 2
    md = render_markdown(vid, oembed(vid), tr, args.plain)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"saved: {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(md)
    return 0


# ----------------------------------------------------------- command: channel

def resolve_channel_url(url: str) -> str:
    """Video link -> its channel; channel link -> base channel URL (no tab)."""
    if is_video_url(url) and "/@" not in url and "/channel/" not in url:
        meta = oembed(extract_video_id(url))
        if not meta.get("author_url"):
            raise SystemExit("could not resolve the channel from this video link")
        return urllib.parse.unquote(meta["author_url"])
    return re.sub(r"/(videos|shorts|streams|featured|playlists)/?$", "", url.rstrip("/"))


def list_channel(channel_url: str, tabs: list[str], proxy: str | None) -> tuple[str, list[dict]]:
    """Video list via yt-dlp flat extraction: a few requests for the whole channel."""
    import yt_dlp

    opts = {"extract_flat": "in_playlist", "quiet": True, "no_warnings": True,
            "skip_download": True}
    if proxy:
        opts["proxy"] = proxy
    name, videos, seen = None, [], set()
    with yt_dlp.YoutubeDL(opts) as ydl:
        for tab in tabs:
            try:
                info = ydl.extract_info(f"{channel_url}/{tab}", download=False)
            except yt_dlp.utils.DownloadError as e:
                if "does not have a" in str(e):   # e.g. channel has no shorts tab
                    continue
                raise
            name = name or info.get("channel") or info.get("uploader") or info.get("title")
            for e in info.get("entries") or []:
                if e.get("id") and e["id"] not in seen:
                    seen.add(e["id"])
                    videos.append({"id": e["id"], "title": e.get("title"),
                                   "duration": e.get("duration"), "tab": tab})
    return name or "channel", videos


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def polite_sleep(args, label: str = "") -> None:
    d = random.uniform(args.min_delay, args.max_delay)
    if label:
        log(f"  {label}: sleeping {d:.0f}s")
    time.sleep(d)


def download_one(api, vid: str, out_dir: Path, args) -> dict:
    """One video with block handling. Returns the state entry."""
    for attempt in range(len(BACKOFF_MINUTES) + 1):
        try:
            tr = fetch_transcript(api, vid, args.lang)
            meta = oembed(vid)
            md = render_markdown(vid, meta, tr)
            write_atomic(out_dir / "transcripts" / f"{vid}.md", md)
            return {"status": "done", "lang": tr["lang"], "kind": tr["kind"],
                    "title": meta.get("title"),
                    "words": sum(len(t.split()) for _, t in tr["segments"])}
        except NO_TRANSCRIPT_ERRORS:
            return {"status": "no_transcript"}
        except UNAVAILABLE_ERRORS as e:
            return {"status": "unavailable", "error": type(e).__name__}
        except BLOCKED_ERRORS as e:
            if attempt == len(BACKOFF_MINUTES):
                raise Blocked(type(e).__name__)
            wait = BACKOFF_MINUTES[attempt] * 60 * random.uniform(1.0, 1.2)
            log(f"  BLOCKED ({type(e).__name__}); backing off {wait / 60:.0f} min")
            time.sleep(wait)
    raise AssertionError("unreachable")


def cmd_channel(args) -> int:
    channel_url = resolve_channel_url(args.url)
    log(f"channel: {channel_url}")
    name, videos = list_channel(channel_url, args.tabs.split(","), args.proxy)
    out_dir = Path(args.out).expanduser() / slugify(name)
    (out_dir / "transcripts").mkdir(parents=True, exist_ok=True)

    pid_file = out_dir / "run.pid"
    if pid_file.exists() and pid_alive(int(pid_file.read_text() or 0)):
        log(f"another download is already running for this channel (pid {pid_file.read_text()})")
        return 1
    pid_file.write_text(str(os.getpid()))

    try:
        # Refresh the listing each run so new uploads are picked up; keep order.
        write_atomic(out_dir / "videos.json", json.dumps(
            {"channel": name, "url": channel_url, "listed_at": datetime.now().isoformat(),
             "videos": videos}, ensure_ascii=False, indent=1))
        state_path = out_dir / "state.json"
        state = load_json(state_path, {})

        todo = [v for v in videos
                if state.get(v["id"], {}).get("status") not in FINAL
                and state.get(v["id"], {}).get("attempts", 0) < MAX_ERROR_ATTEMPTS]
        if args.limit:
            todo = todo[:args.limit]
        log(f"'{name}': {len(videos)} videos listed, {len(todo)} to fetch -> {out_dir}")

        api = make_api(args.proxy)
        consecutive_errors = 0
        for i, v in enumerate(todo, 1):
            if i > 1:
                if (i - 1) % args.batch == 0:
                    pause = random.uniform(args.batch_pause_min, args.batch_pause_max) * 60
                    log(f"  batch pause {pause / 60:.0f} min")
                    time.sleep(pause)
                else:
                    polite_sleep(args)
            prev = state.get(v["id"], {})
            try:
                entry = download_one(api, v["id"], out_dir, args)
                consecutive_errors = 0
            except Blocked as e:
                log(f"STOP: still blocked after backoff ({e}). Progress saved; rerun later.")
                return 3
            except Exception as e:  # network hiccups, parsing changes
                consecutive_errors += 1
                entry = {"status": "error", "error": f"{type(e).__name__}: {e}"[:300],
                         "attempts": prev.get("attempts", 0) + 1}
                if consecutive_errors >= 5:
                    state[v["id"]] = {**prev, **entry}
                    write_atomic(state_path, json.dumps(state, ensure_ascii=False, indent=1))
                    log("STOP: 5 errors in a row, likely a block or a YouTube change. Rerun later.")
                    return 3
            entry["updated"] = datetime.now().isoformat(timespec="seconds")
            state[v["id"]] = {**prev, **entry}
            write_atomic(state_path, json.dumps(state, ensure_ascii=False, indent=1))
            log(f"[{i}/{len(todo)}] {v['id']} {entry['status']}"
                f" {entry.get('lang', '')} {entry.get('words', '')} {entry.get('error', '')}".rstrip())
        build(out_dir)
        log("finished")
        print_status(out_dir)
        return 0
    finally:
        pid_file.unlink(missing_ok=True)


# ------------------------------------------------------ status / build

def build(out_dir: Path) -> None:
    """index.csv + ALL_TRANSCRIPTS.md (oldest video first) from what is on disk."""
    listing = load_json(out_dir / "videos.json", {"videos": []})
    state = load_json(out_dir / "state.json", {})
    videos = list(reversed(listing["videos"]))  # channel tabs list newest first
    with open(out_dir / "index.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["n", "id", "title", "url", "duration_s", "status", "lang", "words"])
        for n, v in enumerate(videos, 1):
            s = state.get(v["id"], {})
            w.writerow([n, v["id"], s.get("title") or v.get("title"),
                        f"https://www.youtube.com/watch?v={v['id']}", v.get("duration"),
                        s.get("status", "pending"), s.get("lang", ""), s.get("words", "")])
    parts = [f"# {listing.get('channel', '')}: all transcripts\n\n"
             f"Source: {listing.get('url', '')}. Oldest video first.\n"]
    for v in videos:
        p = out_dir / "transcripts" / f"{v['id']}.md"
        if p.exists():
            parts.append(p.read_text(encoding="utf-8").replace("# ", "## ", 1))
    write_atomic(out_dir / "ALL_TRANSCRIPTS.md", "\n---\n\n".join(parts))


def print_status(out_dir: Path) -> None:
    listing = load_json(out_dir / "videos.json", {"videos": []})
    state = load_json(out_dir / "state.json", {})
    counts: dict[str, int] = {}
    for v in listing["videos"]:
        st = state.get(v["id"], {}).get("status", "pending")
        counts[st] = counts.get(st, 0) + 1
    pid_file = out_dir / "run.pid"
    pid = int(pid_file.read_text() or 0) if pid_file.exists() else 0
    running = pid not in (0, os.getpid()) and pid_alive(pid)
    words = sum(s.get("words", 0) or 0 for s in state.values())
    print(f"{listing.get('channel', out_dir.name)}: {len(listing['videos'])} videos | "
          + ", ".join(f"{k}={n}" for k, n in sorted(counts.items()))
          + f" | {words} words | {'RUNNING' if running else 'not running'}")
    print(f"folder: {out_dir}")


def cmd_status(args) -> int:
    print_status(Path(args.dir).expanduser())
    return 0


def cmd_build(args) -> int:
    build(Path(args.dir).expanduser())
    print_status(Path(args.dir).expanduser())
    return 0


# ---------------------------------------------------------------- main

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--proxy", help="http(s) proxy URL, e.g. a residential proxy")
    p.add_argument("--lang", nargs="+", help="force transcript language(s) instead of the original")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("video", help="one video -> Markdown on stdout")
    v.add_argument("url")
    v.add_argument("-o", "--out", help="write to file instead of stdout")
    v.add_argument("--plain", action="store_true", help="no [mm:ss] markers")
    v.set_defaults(func=cmd_video)

    c = sub.add_parser("channel", help="all videos of a channel, resumable")
    c.add_argument("url", help="channel URL (@handle, /channel/ID) or any video of the channel")
    c.add_argument("-o", "--out", default=str(DEFAULT_OUT), help=f"base folder (default {DEFAULT_OUT})")
    c.add_argument("--tabs", default="videos", help="comma list of videos,shorts,streams")
    c.add_argument("--min-delay", type=float, default=30, help="seconds between videos (min)")
    c.add_argument("--max-delay", type=float, default=90, help="seconds between videos (max)")
    c.add_argument("--batch", type=int, default=25, help="long pause after every N videos")
    c.add_argument("--batch-pause-min", type=float, default=5, help="long pause, minutes (min)")
    c.add_argument("--batch-pause-max", type=float, default=15, help="long pause, minutes (max)")
    c.add_argument("--limit", type=int, help="fetch at most N videos this run")
    c.set_defaults(func=cmd_channel)

    s = sub.add_parser("status", help="progress of a channel folder")
    s.add_argument("dir")
    s.set_defaults(func=cmd_status)

    b = sub.add_parser("build", help="rebuild index.csv and ALL_TRANSCRIPTS.md")
    b.add_argument("dir")
    b.set_defaults(func=cmd_build)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
