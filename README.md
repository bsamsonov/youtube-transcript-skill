# youtube-transcript — a Claude Code skill

Ask Claude Code *"what is this video about? https://youtu.be/…"* and get an answer based
on the actual transcript. Or ask it to download the transcripts of **every video of a
channel** — hundreds of them — slowly and politely, in the background, so your IP is
not blocked.

- No API keys: transcripts via [youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api),
  channel listing via [yt-dlp](https://github.com/yt-dlp/yt-dlp), titles via YouTube oEmbed.
- Picks the **language actually spoken** in the video. YouTube now offers machine-translated
  "auto-dub" tracks (`variant=timing-optimized`) next to the real speech recognition; the
  script skips them and prefers a manual track in the spoken language when there is one.
- Bulk mode is resumable, rate-limited (random 30–90 s between videos, a long pause every
  25 videos, 30/60/120 min backoff on a block) and stops cleanly if YouTube keeps blocking.

## Install

Requires [uv](https://docs.astral.sh/uv/) (dependencies install themselves on first run).

```bash
git clone https://github.com/bsamsonov/youtube-transcript-skill.git
ln -s "$PWD/youtube-transcript-skill/skills/youtube-transcript" ~/.claude/skills/youtube-transcript
```

## Use from Claude Code

- `о чём этот ролик? https://youtu.be/LhFe3U9XISw`
- `скачай транскрипты всех роликов автора https://youtu.be/LhFe3U9XISw`
- `what's the status of the channel download?`

## Use directly

```bash
S=skills/youtube-transcript/scripts/yt_transcript.py
$S video https://youtu.be/LhFe3U9XISw              # Markdown to stdout
$S channel https://youtu.be/LhFe3U9XISw            # whole channel -> ~/youtube_transcripts/<Channel>/
$S status ~/youtube_transcripts/<Channel>          # progress
$S --proxy http://user:pass@host:port channel URL  # through a proxy
```

Example (`video`):

```markdown
# По первым 90 секундам видно, возьмут тебя или нет

- URL: https://www.youtube.com/watch?v=LhFe3U9XISw
- Channel: Сергей про Карьеру
- Transcript: ru (auto), ~12:59, 2051 words

[0:00] Собеседование идёт целый час, а решение о тебе формируется буквально за первые полторы минуты…
```

Channel output: `transcripts/<video_id>.md`, `index.csv`, `ALL_TRANSCRIPTS.md`
(oldest first), plus `state.json` / `videos.json` used to resume.

## Limits and caveats

- Transcripts come from YouTube's internal caption endpoint, the same one the web player
  uses. It has no official quota but is protected against bots: from home IPs a few
  hundred videos at the default pace work fine; cloud/server IPs get blocked quickly
  and need a residential proxy (`--proxy`).
- Automated access is against YouTube's Terms of Service. Use it for personal research,
  at a modest pace, and respect the authors' rights to the content.
- Videos without captions are reported as `no_transcript`; transcribing audio (Whisper)
  is out of scope.

## Development

```bash
uv run --no-project --with pytest --with youtube-transcript-api --with yt-dlp pytest -q tests
```

Tests cover the offline logic (URL parsing, original-language selection, formatting).

## License

MIT
