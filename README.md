# ClipDesk

A small office of named AI employees that cuts vertical game clips from your footage and posts them to YouTube Shorts on a schedule. Built for the GTA 6 launch (Nov 2026), reusable for any game or event. You watch it work on a pixel-art office page in the browser, on a laptop or a phone.

Pure Python standard library plus `pyyaml`, SQLite for the ledger, ffmpeg for video, local Ollama models for the words. Free to run.

## The staff

| Name | Role | What they do |
|---|---|---|
| Dex | clip_director | Every 15 minutes scans `data/clips/inbox/` for new footage, splits long files into 20-minute chunks, queues one clipper job per chunk, and files a status report for the Clips tab. |
| Rico, Jax | clipper | Take chunks as they free up. Transcribe them with faster-whisper when installed and ask local Ollama to score the moments on what was actually said (falling back to loudness when it is not), cut 30 s 9:16 clips with a blurred backdrop and burned-in captions, pick a thumbnail frame, then write title, description and tags from your notes and the transcript only. Near-duplicates of an already-ready/posted clip are held, never posted twice. Each clip also gets a TikTok/Instagram caption sidecar (`.social.txt`) for hand-posting. Clips land in `data/clips/ready/`. |
| Pia | publisher | Posts at most one ready clip per check: not before `post_after`, at most `max_per_day`, spaced `min_gap_minutes` apart. Skips — and logs the reason in `data/clips/publisher_skip_log.jsonl` — any clip whose provenance is unknown, that matches the spoiler/leak blocklist, or that was already posted; never re-uploads the same clip. Uploads through the YouTube Data API, refreshes view counts hourly into `data/clips/feedback.json` (1h/24h/72h/7d readings, real API numbers only), and pushes each post to your phone. |

## How it fits together

```
  browser / phone  ──►  HQ server  (office page, JSON API, scheduler, SQLite ledger)   python -m office serve
                           ▲
                           │ workers claim tasks over HTTP
                    worker (the machine with the footage + ffmpeg)                      python -m office work
```

HQ and the worker can be the same machine (use `-Local`) or two machines on a private network such as Tailscale. Footage and ffmpeg live wherever the worker runs.

- `office/` the framework: `server.py` (routes, scheduler), `worker.py` (claims tasks, runs handlers), `db.py` (ledger), `llm.py` (model tiers with fallbacks), `client.py` (SDK), `web/` (the pixel office).
- `departments/clips/` the Clip Desk: `director.py`, `clipper.py`, `publisher.py`, `common.py`.
- `config/office.yaml` roster, rooms and machines. `config/clips.yaml` release times, clip length, posting limits. `config/schedule.yaml` recurring jobs. `config/models.yaml` model tiers.
- A new venture is a new folder under `departments/` with a `HANDLERS = {role: run}` map, plus a department and employees in `config/office.yaml`.

## Run it on one machine

```
pip install -r requirements.txt
copy .env.example .env            # then set OFFICE_TOKEN (python -m office token prints a generated one)
python -m office serve            # HQ on http://127.0.0.1:8810  (--host 0.0.0.0 to reach it from other devices)
python -m office work --machine pc   # in a second terminal; OFFICE_URL=http://127.0.0.1:8810
```

On Windows, `scripts\start_office.ps1` and `scripts\start_worker.ps1 -Local` do the same. `scripts/push-to-mac.ps1` and `scripts/install-mac.sh` install HQ as a systemd user service on a Linux box.

## Using it

1. Put footage in `data/clips/inbox/` (`.mp4 .mov .mkv .webm`). Optional: a text file with the same name saying what happens ("bank heist chase from minute 12"). The clippers use it for titles and are told never to invent plot.
   Declare the footage's permission, or Pia will not post it: a `<footage>.json` sidecar `{"permission": "own" | "rockstar-official" | "permission-granted" | "creative-commons", "source": "...", "credit": "..."}` next to the file, or a `permission: own` line in its `.txt` notes file. `python scripts/provenance_audit.py` lists every clip by permission status.
2. Dex picks it up within 15 minutes. Watch Rico and Jax work, or open the **Clips** tab.
3. To post: create a Google Cloud OAuth client (YouTube Data API v3, type Desktop), save it as `data/client_secrets.json`, run `python scripts/youtube_auth.py` once and approve in the browser. Until then clips wait in `data/clips/ready/`.
4. `python scripts/clips_check.py` lists what is still missing before launch.
5. Feedback: `python scripts/feedback_report.py` ranks posted clips against the channel median from real YouTube API view counts (1h/24h/72h/7d). Paste real YouTube Studio reads (viewed-vs-swiped %, average % viewed) with `scripts/feedback_report.py --record <video_id> --window 24h --views N --viewed-pct X --avd-pct Y`. Verdicts follow the A/B Testing Lab rules (1,000 views / 72h, ≥15% lift, ≥4/5 pairs) and are logged in the lab sheet — this report never invents a winner.
6. TikTok / Instagram are manual only: `python scripts/hand_post_kit.py` lists ready clips and their `.social.txt` captions; post by hand in the app, then `scripts/hand_post_kit.py --mark-posted <clip_stem> --platform tiktok|instagram`.

## Release times

`config/clips.yaml` holds them, taken from press reports of Rockstar's staggered midnight-local rollout: New Zealand 11:00 UTC Wed 18 Nov 2026, US East 05:00 UTC Thu 19 Nov, US West 08:00 UTC. Verify against the PlayStation or Xbox store page and edit `post_after` if it moves. Nothing posts before `post_after`.

## Limits

- Clip picking scores transcript moments with local Ollama when faster-whisper is installed (`pip install faster-whisper`); without it, picking is audio-only, so quiet or purely visual moments get missed. A vision model would be the next upgrade.
- Captions are burned in from the Whisper transcript (toggle `captions.enabled` in `config/clips.yaml`). They need ffmpeg with the `subtitles` filter (libass); if your ffmpeg lacks it, turn captions off and the clips still cut.
- Provenance is a hard gate, not a suggestion: a clip with no declared permission, or one matching the spoiler/leak blocklist in `config/clips.yaml`, is held for manual review and never auto-posted.
- Duplicates are held the same way: a clip whose sampled frames are within 6 Hamming bits of an already-ready/posted clip (≥2 of 3 frames) is held as a duplicate. Re-cut the moment with a new hook instead of re-uploading the same file — a re-cut with different pictures is not a duplicate.
- Feedback is real numbers only: `data/clips/feedback.json` holds YouTube API view/like/comment counts at 1h/24h/72h/7d, plus Studio reads the owner pastes in. No metric is estimated. Native A/B testing does not exist for Shorts, so the lab uses matched pairs; see the A/B Testing Lab in the LeonidaAfterDark repo.
- YouTube's default API quota allows about 6 uploads a day. TikTok and Instagram have no free open upload route and are not wired up — hand-post only, via `scripts/hand_post_kit.py`.
- Use footage you own or have permission to use. Game publishers' rules and YouTube's copyright system still apply.
- The worker machine must stay on and awake while posting is open.

## Tests

`python -m pytest -q tests`
