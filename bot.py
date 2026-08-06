#!/usr/bin/env python3
"""
Bot de Shorts do YouTube
------------------------
Pega os clipes .mp4 publicados nas GitHub Releases do Clip_Bot, converte
para o formato vertical 9:16 (1080x1920, com barras pretas) e publica como
Shorts no YouTube. Mantém um histórico para nunca repetir um clipe.

Variáveis de ambiente necessárias:
    YOUTUBE_CLIENT_ID
    YOUTUBE_CLIENT_SECRET
    YOUTUBE_REFRESH_TOKEN   (gere uma vez com: python auth_setup.py)

Opcionais:
    GITHUB_TOKEN            (aumenta o rate limit da API do GitHub)
    DRY_RUN=1               (faz tudo, menos o upload)

Uso:
    python bot.py
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = ROOT_DIR / "config" / "config.json"
HISTORY_FILE = ROOT_DIR / "data" / "history.json"
WORK_DIR = ROOT_DIR / "work"

GITHUB_API = "https://api.github.com"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# Limites da API do YouTube
MAX_TITLE_LEN = 100
MAX_DESCRIPTION_LEN = 5000
MAX_TAGS_TOTAL_LEN = 450  # o limite real é 500 chars somados; deixa folga

# Padrão de nome gerado pelo Clip_Bot: {streamer}_{views}views_{titulo}_{clipid}.mp4
CLIP_NAME_RE = re.compile(r"^(?P<streamer>.+?)_(?P<views>\d+)views_(?P<rest>.*)$")

# Separa título e clip_id. O ID da Twitch é tipo "SweetStupidCurlewSwiftRage-J1WyFZGEhRMQ_Sg1":
# palavras em CamelCase, opcionalmente seguidas de "-" e um sufixo que também pode ter "_".
# O título é non-greedy, então o casamento só acontece quando a cauda inteira é um ID válido.
CLIP_ID_RE = re.compile(
    r"^(?P<title>.*?)_(?P<clip_id>[A-Za-z][A-Za-z0-9]{3,}(?:-[A-Za-z0-9_-]{10,})?)$"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("youtube-shorts-bot")


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        log.error("Arquivo de configuração não encontrado: %s", CONFIG_FILE)
        sys.exit(1)
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Histórico
# ---------------------------------------------------------------------------

def load_history() -> dict:
    if not HISTORY_FILE.exists():
        return {"uploaded": {}}
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        data.setdefault("uploaded", {})
        return data
    except (json.JSONDecodeError, OSError):
        log.warning("Histórico corrompido ou ilegível, iniciando um novo.")
        return {"uploaded": {}}


def save_history(history: dict) -> None:
    history["last_updated"] = datetime.now(timezone.utc).isoformat()
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# GitHub Releases (fonte dos clipes)
# ---------------------------------------------------------------------------

def github_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "youtube-shorts-bot",
    })
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    return session


def humanize_title(raw: str) -> str:
    """O GitHub troca espaços por pontos no nome dos assets; desfaz isso."""
    return re.sub(r"\s+", " ", raw.replace(".", " ")).strip(" -")


def parse_clip_name(asset_name: str) -> dict:
    """Extrai streamer, views, título e clip_id do nome do arquivo."""
    stem = asset_name[:-4] if asset_name.lower().endswith(".mp4") else asset_name
    match = CLIP_NAME_RE.match(stem)
    if not match:
        return {
            "streamer": "twitch",
            "views": 0,
            "title": humanize_title(stem) or stem,
            "clip_id": asset_name,
        }

    rest = match.group("rest")
    id_match = CLIP_ID_RE.match(rest)
    if id_match:
        raw_title, clip_id = id_match.group("title"), id_match.group("clip_id")
    else:  # sem ID reconhecível: usa o nome do asset como chave de deduplicação
        raw_title, clip_id = rest, asset_name

    streamer = match.group("streamer")
    return {
        "streamer": streamer,
        "views": int(match.group("views")),
        "title": humanize_title(raw_title) or streamer,
        "clip_id": clip_id or asset_name,
    }


def collect_candidate_clips(session: requests.Session, cfg: dict, history: dict) -> list[dict]:
    """Busca as releases mais recentes e devolve os clipes ainda não postados."""
    resp = session.get(
        f"{GITHUB_API}/repos/{cfg['source_repo']}/releases",
        params={"per_page": cfg["releases_to_scan"]},
        timeout=30,
    )
    if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
        log.error(
            "Rate limit da API do GitHub esgotado. Defina GITHUB_TOKEN "
            "(no Actions ele já vem por padrão) para elevar o limite."
        )
        sys.exit(1)
    resp.raise_for_status()
    releases = resp.json()

    if not releases:
        log.warning("Nenhuma release encontrada em %s.", cfg["source_repo"])
        return []

    uploaded = history["uploaded"]
    candidates: list[dict] = []
    seen_ids: set[str] = set()

    for release in releases:
        for asset in release.get("assets", []):
            name = asset.get("name", "")
            if not name.lower().endswith(".mp4"):
                continue
            info = parse_clip_name(name)
            if info["clip_id"] in uploaded or info["clip_id"] in seen_ids:
                continue
            seen_ids.add(info["clip_id"])
            info["asset_name"] = name
            info["download_url"] = asset["browser_download_url"]
            info["size"] = asset.get("size", 0)
            info["release_tag"] = release.get("tag_name", "")
            candidates.append(info)

    candidates.sort(key=lambda c: c["views"], reverse=True)
    log.info(
        "%d clipe(s) novo(s) encontrado(s) em %d release(s) de %s.",
        len(candidates), len(releases), cfg["source_repo"],
    )
    return candidates


def download_asset(session: requests.Session, clip: dict, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / clip["asset_name"]
    log.info("Baixando %s (%.1f MB)...", clip["asset_name"], clip["size"] / 1_048_576)
    with session.get(clip["download_url"], stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    return dest


# ---------------------------------------------------------------------------
# Conversão para Shorts (9:16 com barras pretas)
# ---------------------------------------------------------------------------

def probe_duration(path: Path) -> float:
    """Duração do vídeo em segundos (0.0 se não for possível determinar)."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def convert_to_short(src: Path, dest_dir: Path, max_seconds: int) -> Path:
    """Converte para 1080x1920 mantendo o vídeo inteiro, com barras pretas."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"short_{src.stem}.mp4"

    video_filter = (
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,"
        "setsar=1"
    )

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-t", str(max_seconds),
        "-vf", video_filter,
        "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart",
        str(dest),
    ]

    log.info("Convertendo para 9:16: %s", src.name)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not dest.exists():
        raise RuntimeError(f"ffmpeg falhou em {src.name}: {result.stderr.strip()[:500]}")
    return dest


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------

def get_youtube_service():
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")

    missing = [
        name for name, value in [
            ("YOUTUBE_CLIENT_ID", client_id),
            ("YOUTUBE_CLIENT_SECRET", client_secret),
            ("YOUTUBE_REFRESH_TOKEN", refresh_token),
        ] if not value
    ]
    if missing:
        log.error("Variáveis de ambiente faltando: %s", ", ".join(missing))
        log.error("Rode 'python auth_setup.py' para gerar o refresh token.")
        sys.exit(1)

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    creds.refresh(GoogleRequest())
    log.info("Autenticado no YouTube com sucesso.")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def slugify_tag(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower()) or "twitch"


def clean_text(value: str) -> str:
    """Remove os caracteres que a API do YouTube rejeita em título/descrição."""
    return value.replace("<", "(").replace(">", ")")


def build_metadata(clip: dict, cfg: dict) -> dict:
    fields = {
        "title": clip["title"],
        "streamer": clip["streamer"],
        "streamer_tag": slugify_tag(clip["streamer"]),
        "views": clip["views"],
    }

    title = clean_text(cfg["title_template"].format(**fields)).strip()
    if len(title) > MAX_TITLE_LEN:
        title = title[: MAX_TITLE_LEN - 1].rstrip() + "…"

    description = clean_text(cfg["description_template"].format(**fields))[:MAX_DESCRIPTION_LEN]

    tags: list[str] = []
    total = 0
    for tag in [clip["streamer"], *cfg["extra_tags"]]:
        if not tag or tag in tags:
            continue
        if total + len(tag) > MAX_TAGS_TOTAL_LEN:
            break
        tags.append(tag)
        total += len(tag)

    return {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": str(cfg["category_id"]),
        },
        "status": {
            "privacyStatus": cfg["privacy_status"],
            "selfDeclaredMadeForKids": bool(cfg["made_for_kids"]),
        },
    }


def upload_short(youtube, video_path: Path, body: dict, max_retries: int = 3) -> str:
    """Sobe o vídeo e devolve o ID. Levanta exceção se falhar em definitivo."""
    for attempt in range(1, max_retries + 1):
        media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True, chunksize=-1)
        try:
            request = youtube.videos().insert(
                part="snippet,status", body=body, media_body=media
            )
            response = None
            while response is None:
                _, response = request.next_chunk()
            return response["id"]
        except HttpError as exc:
            retriable = exc.resp.status in (429, 500, 502, 503, 504)
            if not retriable or attempt == max_retries:
                raise
            wait = 2 ** attempt * 5
            log.warning(
                "Erro HTTP %s no upload (tentativa %d/%d). Nova tentativa em %ds.",
                exc.resp.status, attempt, max_retries, wait,
            )
            time.sleep(wait)
    raise RuntimeError("Upload falhou após todas as tentativas.")


def quota_exceeded(exc: HttpError) -> bool:
    detail = exc.content.decode("utf-8", errors="replace") if exc.content else ""
    return exc.resp.status == 403 and ("quotaExceeded" in detail or "uploadLimitExceeded" in detail)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = load_config()
    dry_run = os.environ.get("DRY_RUN") == "1"

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        log.error("ffmpeg/ffprobe não encontrados no PATH.")
        sys.exit(1)

    history = load_history()
    session = github_session()
    candidates = collect_candidate_clips(session, cfg, history)

    if not candidates:
        log.info("Nenhum clipe novo para postar. Encerrando.")
        write_github_output(uploaded=0)
        write_github_summary([])
        return

    youtube = None if dry_run else get_youtube_service()
    if dry_run:
        log.warning("DRY_RUN ativo: nada será enviado ao YouTube.")

    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)

    uploaded_count = 0
    published: list[dict] = []
    target = cfg["shorts_per_run"]

    for clip in candidates:
        if uploaded_count >= target:
            break

        raw_path = short_path = None
        try:
            raw_path = download_asset(session, clip, WORK_DIR / "raw")

            duration = probe_duration(raw_path)
            if duration and duration < cfg["min_duration_seconds"]:
                log.warning(
                    "Ignorando %s: duração de %.1fs abaixo do mínimo de %ds.",
                    clip["asset_name"], duration, cfg["min_duration_seconds"],
                )
                continue

            short_path = convert_to_short(raw_path, WORK_DIR / "shorts", cfg["max_duration_seconds"])
            body = build_metadata(clip, cfg)

            if dry_run:
                log.info("[DRY_RUN] Postaria: %s", body["snippet"]["title"])
                uploaded_count += 1
                continue

            video_id = upload_short(youtube, short_path, body)
            log.info(
                "Publicado: https://youtube.com/shorts/%s — %s",
                video_id, body["snippet"]["title"],
            )

            history["uploaded"][clip["clip_id"]] = {
                "video_id": video_id,
                "title": body["snippet"]["title"],
                "streamer": clip["streamer"],
                "views": clip["views"],
                "release_tag": clip["release_tag"],
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            }
            save_history(history)
            published.append({"video_id": video_id, "title": body["snippet"]["title"]})
            uploaded_count += 1

        except HttpError as exc:
            if quota_exceeded(exc):
                log.error("Cota diária da API do YouTube esgotada. Parando por hoje.")
                break
            log.error("Falha ao publicar %s: %s", clip["asset_name"], exc)
        except Exception as exc:  # noqa: BLE001 - um clipe ruim não derruba a execução
            log.error("Falha ao processar %s: %s", clip["asset_name"], exc)
        finally:
            for path in (raw_path, short_path):
                if path and path.exists():
                    path.unlink(missing_ok=True)

    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR, ignore_errors=True)

    log.info("Concluído. %d de %d Short(s) publicado(s).", uploaded_count, target)
    write_github_output(uploaded=uploaded_count)
    write_github_summary(published)


def write_github_output(uploaded: int) -> None:
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as fh:
            fh.write(f"uploaded_count={uploaded}\n")


def write_github_summary(published: list[dict]) -> None:
    """Escreve os links no resumo do Actions, para liberar a visibilidade no Studio."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    lines = [f"## Shorts publicados: {len(published)}", ""]
    if published:
        lines.append(
            "Enquanto o projeto não passar pela auditoria da API, os vídeos ficam "
            "privados. Libere em massa pelo [YouTube Studio]"
            "(https://studio.youtube.com/channel/UC/videos/upload) "
            "(selecione tudo → Editar → Visibilidade → Público)."
        )
        lines.append("")
        lines.append("| Short | Editar |")
        lines.append("| --- | --- |")
        for item in published:
            title = item["title"].replace("|", "\\|")
            lines.append(
                f"| [{title}](https://youtube.com/shorts/{item['video_id']}) "
                f"| [abrir no Studio](https://studio.youtube.com/video/{item['video_id']}/edit) |"
            )
    else:
        lines.append("Nenhum Short novo nesta execução.")

    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
