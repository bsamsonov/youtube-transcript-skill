---
name: youtube-transcript
description: |
  Gets YouTube transcripts without API keys: one video (to answer "what is this video
  about?") or every video of a channel (bulk, slow and polite so the IP is not blocked,
  runs in the background, resumable). Picks the language actually spoken in the video,
  not YouTube's machine translation.
  Use when the user sends a YouTube link (youtube.com, youtu.be) and asks what it is
  about, to summarize it, retell it, or to get its transcript/subtitles; or asks to
  download transcripts of all videos of a channel/author for later analysis.
  Triggers: "о чём этот ролик", "о чём видео", "перескажи ролик", "кратко о видео",
  "транскрипт ролика", "субтитры", "скачай все транскрипты канала", "все ролики автора",
  "what is this video about", "summarize this YouTube video", "get transcript",
  "download all transcripts of the channel".
---

# YouTube transcripts

Script: `scripts/yt_transcript.py` next to this file (installed:
`~/.claude/skills/youtube-transcript/scripts/yt_transcript.py`, below `$YT`).
It is a self-contained `uv` script: dependencies (`youtube-transcript-api`, `yt-dlp`)
install themselves on first run. Requires `uv` on PATH. No API keys.

```bash
YT=~/.claude/skills/youtube-transcript/scripts/yt_transcript.py
```

## Mode 1: one video ("о чём этот ролик?") — the common case

```bash
$YT video "<youtube url>"            # Markdown to stdout: title, channel, language, text
$YT video "<url>" -o /path/file.md   # save instead (for very long videos, then read in parts)
```

Output is `[mm:ss]` paragraphs (about one per minute) in the video's original language.
A 15-minute video is ~2-3k words, safe to read in full; for videos over ~1.5 hours save
to a file and read in chunks.

Then answer in the user's language, by default:
1. One-line gist: topic and who it is for.
2. Main points / arguments (5-8 bullets) with `[mm:ss]` references.
3. Conclusion or practical takeaways; note ads/sponsor segments if any.
If the user asked a specific question, answer that instead of the full summary.

Exit codes: `2` no captions / video unavailable (say so; fallback is audio + Whisper,
not part of this skill), `3` YouTube blocked the request (retry later or `--proxy`).
Global options go BEFORE the subcommand: `$YT --lang en video URL` forces a language.

## Mode 2: whole channel (hundreds of videos, run and come back later)

Polite by design: one listing request for the channel, then per video ~3 requests with a
random 30-90 s pause, a 5-15 min pause every 25 videos, and on a block a backoff of
30/60/120 min before stopping cleanly. Rough time: **~1.2 min per video** (300 videos ≈ 6-7 h).
Progress is saved after every video; rerunning the same command resumes and also
picks up new uploads.

1. Launch detached, so it survives the end of the Claude session (any video link of the
   author works; the channel is resolved automatically):
   ```bash
   mkdir -p ~/youtube_transcripts
   setsid nohup $YT channel "<channel or video url>" >> ~/youtube_transcripts/<slug>.log 2>&1 < /dev/null &
   echo $!
   ```
   Then start a background waiter (`run_in_background`) so you are notified at the end:
   `while kill -0 <PID> 2>/dev/null; do sleep 300; done; tail -5 <log>`
2. Tell the user: folder, number of videos (first log lines), ETA, how to check
   progress, and that they can close the session.
3. Progress at any time: `$YT status ~/youtube_transcripts/<Channel_Name>`
   or `tail ~/youtube_transcripts/<slug>.log`.

Options: `--tabs videos,shorts,streams` (default `videos`), `--limit N`,
`--min-delay/--max-delay` seconds, `--batch`, `--batch-pause-min/-max` minutes,
`-o` base folder (default `~/youtube_transcripts`), global `--proxy URL`.
Do NOT lower the delays or run two downloads in parallel unless the user explicitly
asks: avoiding a block matters more than speed. Never use browser cookies (account risk).

Result in `~/youtube_transcripts/<Channel_Name>/`:
- `transcripts/<video_id>.md` — one file per video, same format as Mode 1;
- `index.csv` — n, id, title, url, duration, status, lang, words (oldest first);
- `ALL_TRANSCRIPTS.md` — everything concatenated, oldest first, for analysis;
- `state.json`, `videos.json` — resume state and the listing.
Statuses: `done`, `no_transcript`, `unavailable` are final; `error` is retried on
the next run (up to 3 times); `pending` not reached yet.

Exit code `3` = stopped because of a block or 5 errors in a row: wait a few hours,
then run the same command again. `$YT build <dir>` rebuilds index/ALL files.

## Analysing a downloaded channel

`ALL_TRANSCRIPTS.md` for hundreds of videos is millions of characters: do not read it
whole. Start from `index.csv`, pick relevant videos by title, read their files. For
questions across all videos, split the transcript files into batches and give each
batch to a subagent (model `sonnet`) with the same question, then merge the answers.
