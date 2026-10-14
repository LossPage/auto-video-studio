# Auto Video Studio

**[Türkçe](README.tr.md)** · English

Turn a channel idea into a fully automatic YouTube channel: scripts → voices → pictures → finished video and Shorts
→ SEO metadata and thumbnail → scheduled upload. You answer a few questions once; after that new episodes are
produced on your schedule, with optional approval steps where you stay in control.

Built from a real kids' animation channel pipeline and generalized for any format: kids' stories, educational
explainers, facts/list videos, storytelling, sleep stories, or your own.

## What it does

| Step | What happens | Options |
| --- | --- | --- |
| Setup wizard | Asks about your channel and writes `kanal/kanal.yaml` + a personal channel bible `kanal/KANAL.md` | AI can flesh out the concept: characters, visual style, keywords, first episode ideas |
| Script | Full episode with scenes, shots, dialogue per language, image prompts and Shorts picks | **AI writes everything** · **you give an idea, AI writes** · **you write, AI translates/storyboards** |
| Voices | One clip per line and language, cleaned, level-matched, cached | **ElevenLabs** · **Google Cloud TTS (Chirp 3 HD)** · **Gemini TTS** · free test voice |
| Pictures | One image per shot (or per scene) with slow camera moves | **AI images** (Gemini / OpenAI, with character reference images for consistency) · **puppets** (PNG layers + lip sync) · **your own images** · test cards · **plugin** |
| Video | 1080p H.264, scene fades, intro card, optional burned subtitles, music bed with ducking, loudness at −14 LUFS | multi-language as **extra audio tracks** in one video or **separate videos** |
| Shorts | 9:16 cuts from the episode, camera follows the speaker, subtitles | per language |
| SEO | Title, description (hook in first 150 chars, chapters), tags (≤ 500 chars), hashtags, thumbnail — per language | AI-written, then checked against YouTube limits by code |
| Upload | Video + localized titles/descriptions, subtitles, thumbnail, playlist, made-for-kids and AI disclosure flags | scheduled / private / unlisted / public |
| Schedule | Produces on the days/times you choose, publishes at your slots | Windows Task Scheduler, cron, or a foreground loop |

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/download.html) on your PATH (Windows: `winget install Gyan.FFmpeg`; macOS: `brew install ffmpeg`; Linux: `apt install ffmpeg`)
- Optional: [Rhubarb Lip Sync](https://github.com/DanielSWolf/rhubarb-lip-sync/releases) on PATH for more accurate puppet mouths
- API keys only for the services you pick (none needed for the free test mode)

```bash
git clone https://github.com/<you>/auto-video-studio.git
cd auto-video-studio
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python studio.py setup          # or: python studio.py kurulum
python studio.py doctor         # checks tools, keys, voices
python studio.py new            # produce the first episode
```

Try it for free first: `python studio.py setup --answers ornekler/cevaplar-ornek.yaml`, copy
`ornekler/senaryo-ornegi.en.md` into `kanal/icerik/senaryolar/`, then `python studio.py new`.
You get a short two-language puppet episode with test voices, subtitles, a Short, SEO text and a thumbnail.

## Commands

Every command has an English and a Turkish name.

| Command | What it does |
| --- | --- |
| `setup` / `kurulum` | Setup wizard (`--answers file.yaml` for non-interactive) |
| `doctor` / `doktor` | Checks ffmpeg, keys, voice ids, references, YouTube login |
| `new` / `yeni` | New episode from the queue (`--idea "…"` or `--file script.md` to override) |
| `status` / `durum` | All episodes, their stage and YouTube links |
| `approve <id>` / `onayla <id>` | Pass an approval gate (script or upload) and continue |
| `revise <id> "feedback"` / `duzelt` | AI rewrites the script with your feedback |
| `run [id]` / `uret [id]` | Continue / retry an episode after an error |
| `tick` | Do whatever is due now (call it every 15 min from cron / Task Scheduler) |
| `scheduler` / `zamanlayici` | Keep running and tick every minute; `--install` sets up the OS task |
| `youtube-connect` / `youtube-baglan` | One-time Google sign-in for uploads |
| `references` / `referans` | Draw character reference images (consistency for AI images) |
| `puppet-example` / `kukla-ornek` | Write simple example puppet packs |
| `voices` / `sesler` | List your ElevenLabs voices with ids |

## How an episode flows

```
script → [script review] → pictures → voices + render → SEO + thumbnail → [upload review] → upload → done
```

Gates in brackets are on by default. During script review open `cikti/<id>/senaryo.md`; edit it directly or run
`revise`, then `approve`. Everything for an episode lives in `cikti/<id>/`: `senaryo.json/.md`, `gorseller/`,
`video.mp4`, `ses.<lang>.m4a`, `altyazi.<lang>.srt`, `shorts/`, `seo.json`, `yayin.md` (copy-paste sheet),
`kapak.<lang>.jpg`, `durum.json` (stage, costs, YouTube ids).

## Script modes

- **ai** — the AI picks a fresh topic (it sees earlier episodes and avoids repeats) and writes it.
- **idea** — put one idea per line in `kanal/icerik/fikirler.md`; the top one is used and moved to "Used".
- **manual** — write scripts in a simple Markdown format (`ornekler/senaryo-ornegi.*.md`) into
  `kanal/icerik/senaryolar/`. With an AI configured, missing translations, image prompts and Shorts are filled in
  while your text stays word for word.

The AI always reads `kanal/KANAL.md`, your channel bible. Edit it to steer tone, rules and characters.

## Pictures

- **ai_images** — one image per shot (or per scene to save cost). Put a reference picture of each character at
  `kanal/karakterler/<id>/referans.png` (`python studio.py references` draws one); it is sent with every image that
  shows the character, which keeps them consistent.
- **puppet** — your own cut-out characters animate over AI or own backgrounds: mouths follow the voice, eyes blink,
  bodies breathe, speakers turn to listeners. Pack format (same canvas size, transparent, facing right):
  `kanal/karakterler/<id>/kukla/govde.png`, `agiz_X.png … agiz_H.png` (at least `agiz_X` closed + `agiz_D` open),
  optional `goz_kapali.png`, `kukla.json` `{"height": 0.42}`.
- **local_images** — drop images named by shot or scene id (`S1_01.png`, `S2.jpg`) into `cikti/<id>/gorseller/`.
- **plugin** — your own renderer: a Python file with `render_picture(cfg, script, timeline, images_dir, out, size)`
  that writes a silent video; sound, subtitles, SEO and upload stay automatic.

## Voices

Set a voice per character and for the narrator in `kanal/kanal.yaml` (`voice.id`). ElevenLabs: any voice id
from your library (`python studio.py voices`). Google / Gemini: a voice name such as `Leda`, `Puck`, `Kore`.
Emotions written in the script become acting directions for expressive models.

Multi-language channels share one timeline: when a language runs much longer, it is re-spoken faster if the
model honours a speed setting (learned automatically, some models ignore it), then the longest clip is sped
up ≤ 8 % and shorter ones slowed ≤ 12 % without changing pitch. Ask the AI (or write) lines of similar length per
language for the best sync. Every line can optionally be transcribed and re-generated if words are missing
(`voice.transcript_check`, needs a Gemini key); if transcription is unavailable the line is accepted, never re-billed.

## Connecting YouTube

1. [Google Cloud Console](https://console.cloud.google.com/) → new project → enable **YouTube Data API v3**.
2. OAuth consent screen (External, add yourself as test user) → Credentials → **OAuth client ID → Desktop app**.
3. Download the JSON to `.gizli/youtube_client_secret.json`, then `python studio.py youtube-connect`.

Know the limits before you automate:
- **Audit:** videos uploaded through the API by an unverified project stay **private** until the project passes
  YouTube's [API compliance audit](https://support.google.com/youtube/contact/yt_api_form). Until then use
  `privacy: private` and publish from YouTube Studio.
- **Quota:** 10,000 units/day by default. One upload costs 1,600, each subtitle 400, thumbnail/playlist 50 each →
  about one episode plus two Shorts per day.
- **Extra audio tracks** (multi-language) cannot be added through the API: add `ses.<lang>.m4a` in YouTube Studio →
  Languages. Or choose `multi_language: separate_videos`.
- **Thumbnails** need a verified channel (phone verification).

## Scheduling

```yaml
schedule:
  timezone: Europe/Istanbul
  produce: [{days: [mon, thu], time: "09:00"}]
  publish_times: ["17:00"]
  publish_days: [tue, fri]       # empty = any day
```

`python studio.py scheduler --install` registers a Windows Task Scheduler job or prints the cron line; both run
`studio.py tick` every 15 minutes. A slot missed while the computer was off still runs within 12 hours.
Approved episodes continue on the next tick. To run without your computer, run `tick` on a small server or a
scheduled GitHub Actions job (store keys as repository secrets; keep `kanal/` in a private repository).

## Costs (rough)

| Item | Typical |
| --- | --- |
| Script + SEO (Claude / Gemini) | a few cents to ~$0.30 per episode |
| ElevenLabs | ~$0.08 per 1,000 characters (v3/v4); 10 min in two languages ≈ 25k chars |
| Google Chirp 3 HD | 1M characters/month free tier, then per-character billing |
| AI images | ~$0.02–0.04 each; 10 min ≈ 40–60 shots (use `per: scene` to cut this) |

Every episode records what it used in `durum.json` → `costs`.

## Playing by YouTube's rules

Made-for-kids is declared per upload (comments and personalized ads turn off). AI-made realistic content must be
disclosed; the upload sets the synthetic-media flag by default. YouTube does not monetize mass-produced,
repetitive content: keep each episode original, keep a human in the loop (the approval gates exist for this) and
publish at a sensible pace. You are responsible for the content you publish and for music/sound licenses.

## Privacy

API keys live only in `.env`; the YouTube login lives in `.gizli/`. Both, together with your personal channel data
(`kanal/`) and outputs (`cikti/`), are in `.gitignore`. Never paste keys into issues or commits.

## Project layout

```
studio.py            command line entry
studio/              engine: wizard, script, tts, visuals, puppet, render, seo, youtube, scheduler, pipeline
ornekler/            example scripts and a free test setup
tests/               pytest suite (pytest -q)
kanal/  cikti/  .env  .gizli/     your data (created by setup, git-ignored)
```

## License

MIT
