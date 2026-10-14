"""YouTube upload (Data API v3): video + localized metadata, captions, thumbnail, playlist, scheduled publishing.

One-time setup: Google Cloud project → enable "YouTube Data API v3" → OAuth client (Desktop app) → download the JSON
to .gizli/youtube_client_secret.json → `python studio.py youtube-baglan` (opens the browser once, stores a token).

Important: until your Google Cloud project passes YouTube's API audit, videos uploaded through the API are locked
to PRIVATE (YouTube policy for projects created after July 2020). Scheduled publishing needs the audit.
Multi-language audio tracks cannot be added through the API; studio.py prints a reminder with the files to add in
YouTube Studio (Languages → Add audio).
"""
from __future__ import annotations

import time
from pathlib import Path

from .util import log, secrets_dir, t

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]


def client_secret() -> Path:
    return secrets_dir() / "youtube_client_secret.json"


def token_file() -> Path:
    return secrets_dir() / "youtube_token.json"


def connect() -> str:
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not client_secret().exists():
        raise SystemExit(t(f"OAuth dosyası yok: {client_secret()}  (README > YouTube bağlantısı)",
                           f"OAuth client file missing: {client_secret()}  (README > Connecting YouTube)"))
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret()), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    token_file().parent.mkdir(parents=True, exist_ok=True)
    token_file().write_text(creds.to_json())
    yt = service()
    ch = yt.channels().list(part="snippet", mine=True).execute()["items"][0]["snippet"]["title"]
    return ch


def service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    if not token_file().exists():
        raise SystemExit(t("YouTube bağlı değil. Çalıştırın:  python studio.py youtube-baglan",
                           "YouTube is not connected. Run:  python studio.py youtube-connect"))
    creds = Credentials.from_authorized_user_file(str(token_file()), SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        token_file().write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _resumable(req):
    from googleapiclient.errors import HttpError
    resp, n = None, 0
    while resp is None:
        try:
            status, resp = req.next_chunk()
            if status:
                log(t(f"  yükleniyor %{int(status.progress() * 100)}", f"  uploading {int(status.progress() * 100)}%"))
        except HttpError as e:
            if e.resp.status not in (500, 502, 503, 504) or n >= 6:
                raise
            n += 1; time.sleep(2 ** n)
        except (ConnectionError, TimeoutError, OSError):
            if n >= 6:
                raise
            n += 1; time.sleep(2 ** n)
    return resp


def upload(cfg, file: Path, meta: dict, lang: str, publish_at: str | None, captions: dict | None = None,
           thumbnail: Path | None = None, other_langs: list[str] | None = None) -> str:
    """meta = {lang: {title, description, tags}}. `lang` is the video's own language. → video id"""
    from googleapiclient.http import MediaFileUpload
    yt = service()
    y = cfg["youtube"]
    m = meta[lang]
    privacy = y["privacy"]
    status = dict(privacyStatus="private" if privacy == "scheduled" else privacy,
                  selfDeclaredMadeForKids=bool(cfg["channel"].get("made_for_kids")),
                  containsSyntheticMedia=bool(y.get("synthetic_media", True)), embeddable=True)
    if privacy == "scheduled" and publish_at:
        status["publishAt"] = publish_at
    body = dict(snippet=dict(title=m["title"], description=m["description"], tags=m["tags"], categoryId=str(y["category_id"]),
                             defaultLanguage=lang, defaultAudioLanguage=lang), status=status)
    locs = {lg: dict(title=meta[lg]["title"], description=meta[lg]["description"]) for lg in (other_langs or []) if lg in meta and lg != lang}
    parts = "snippet,status" + (",localizations" if locs else "")
    if locs:
        body["localizations"] = locs
    log(t(f"YouTube'a yükleniyor: {file.name}", f"Uploading to YouTube: {file.name}"))
    req = yt.videos().insert(part=parts, body=body, media_body=MediaFileUpload(str(file), chunksize=8 * 1024 * 1024, resumable=True))
    vid = _resumable(req)["id"]
    for lg, srt in (captions or {}).items():
        try:
            yt.captions().insert(part="snippet", body=dict(snippet=dict(videoId=vid, language=lg, name="", isDraft=False)),
                                 media_body=MediaFileUpload(str(srt), mimetype="application/octet-stream")).execute()
        except Exception as e:
            log(t(f"altyazı yüklenemedi ({lg}): {e}", f"caption upload failed ({lg}): {e}"))
    if thumbnail and thumbnail.exists():
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumbnail))).execute()
        except Exception as e:
            log(t(f"kapak yüklenemedi (kanal doğrulaması gerekebilir): {e}", f"thumbnail failed (channel may need verification): {e}"))
    if y.get("playlist_id"):
        try:
            yt.playlistItems().insert(part="snippet", body=dict(snippet=dict(
                playlistId=y["playlist_id"], resourceId=dict(kind="youtube#video", videoId=vid)))).execute()
        except Exception as e:
            log(t(f"oynatma listesine eklenemedi: {e}", f"playlist insert failed: {e}"))
    return vid
