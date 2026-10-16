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

- `what is this video about? https://youtu.be/aircAruvnKk`
- `download transcripts of all videos of this channel: https://www.youtube.com/@3blue1brown`
- `what's the status of the channel download?`

## Use directly

```bash
S=skills/youtube-transcript/scripts/yt_transcript.py
$S video https://youtu.be/aircAruvnKk              # Markdown to stdout
$S channel https://youtu.be/aircAruvnKk            # whole channel -> ~/youtube_transcripts/<Channel>/
$S status ~/youtube_transcripts/<Channel>          # progress
$S --proxy http://user:pass@host:port channel URL  # through a proxy
```

Example (`video`):

```markdown
# But what is a neural network? | Deep learning chapter 1

- URL: https://www.youtube.com/watch?v=aircAruvnKk
- Channel: 3Blue1Brown
- Transcript: en (manual), ~18:25, 3357 words

[0:04] This is a 3. It's sloppily written and rendered at an extremely low resolution of 28x28 pixels, but your brain has no trouble recognizing it as a 3…
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
