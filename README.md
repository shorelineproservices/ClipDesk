# ClipDesk

A small office of named AI employees that cuts vertical game clips from your footage and posts them to YouTube Shorts on a schedule. Built for the GTA 6 launch (Nov 2026), reusable for any game or event. You watch it work on a pixel-art office page in the browser, on a laptop or a phone.

Pure Python standard library plus `pyyaml`, SQLite for the ledger, ffmpeg for video, local Ollama models for the words. Free to run.

## The staff

| Name | Role | What they do |
|---|---|---|
| Dex | clip_director | Every 15 minutes scans `data/clips/inbox/` for new footage, splits long files into 20-minute chunks, queues one clipper job per chunk, and files a status report for the Clips tab. |
| Rico, Jax | clipper | Take chunks as they free up. Find the loudest moments (audio spikes over the local average), cut 30 s 9:16 clips with a blurred backdrop, then write title, description and tags from your notes. Clips land in `data/clips/ready/`. |
| Pia | publisher | Posts at most one ready clip per check: not before `post_after`, at most `max_per_day`, spaced `min_gap_minutes` apart. Uploads through the YouTube Data API, refreshes view counts hourly, and pushes each post to your phone. |

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
2. Dex picks it up within 15 minutes. Watch Rico and Jax work, or open the **Clips** tab.
3. To post: create a Google Cloud OAuth client (YouTube Data API v3, type Desktop), save it as `data/client_secrets.json`, run `python scripts/youtube_auth.py` once and approve in the browser. Until then clips wait in `data/clips/ready/`.
4. `python scripts/clips_check.py` lists what is still missing before launch.

## Release times

`config/clips.yaml` holds them, taken from press reports of Rockstar's staggered midnight-local rollout: New Zealand 11:00 UTC Wed 18 Nov 2026, US East 05:00 UTC Thu 19 Nov, US West 08:00 UTC. Verify against the PlayStation or Xbox store page and edit `post_after` if it moves. Nothing posts before `post_after`.

## Limits

- Clip picking is audio-only, so quiet or purely visual moments get missed. A vision model would be the next upgrade.
- YouTube's default API quota allows about 6 uploads a day. TikTok and Instagram have no free open upload route and are not wired up.
- Use footage you own or have permission to use. Game publishers' rules and YouTube's copyright system still apply.
- The worker machine must stay on and awake while posting is open.

## Tests

`python -m pytest -q tests`
