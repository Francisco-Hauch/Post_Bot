#!/usr/bin/env python3
"""
Gerador de refresh token do YouTube (rodar UMA vez, na sua máquina)
-------------------------------------------------------------------
Abre o navegador, pede autorização na conta do YouTube e imprime o
refresh token que deve ser salvo como secret YOUTUBE_REFRESH_TOKEN no
repositório do GitHub.

Antes de rodar:
  1. Crie um projeto em https://console.cloud.google.com/
  2. Ative a "YouTube Data API v3"
  3. Em "Credenciais", crie um OAuth client do tipo "Aplicativo para computador"
  4. Baixe o JSON e salve nesta pasta como client_secret.json
     (ou exporte YOUTUBE_CLIENT_ID e YOUTUBE_CLIENT_SECRET)

Uso:
    python auth_setup.py
"""

import json
import os
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

ROOT_DIR = Path(__file__).resolve().parent
CLIENT_SECRET_FILE = ROOT_DIR / "client_secret.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def load_client_config() -> dict:
    if CLIENT_SECRET_FILE.exists():
        data = json.loads(CLIENT_SECRET_FILE.read_text(encoding="utf-8"))
        print(f"Usando credenciais de {CLIENT_SECRET_FILE.name}")
        return data

    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            "Erro: coloque o client_secret.json nesta pasta ou defina as variáveis "
            "YOUTUBE_CLIENT_ID e YOUTUBE_CLIENT_SECRET.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("Usando credenciais das variáveis de ambiente.")
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def main() -> None:
    client_config = load_client_config()
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)

    print("\nAbrindo o navegador. Faça login na conta do canal do YouTube...")
    print("Se aparecer 'Google não verificou este app', clique em")
    print("'Avançado' -> 'Acessar (não seguro)' — o app é seu.\n")

    # prompt='consent' + access_type='offline' garantem que venha um refresh token
    creds = flow.run_local_server(
        port=8080,
        access_type="offline",
        prompt="consent",
        authorization_prompt_message="",
        success_message="Autorizado! Pode fechar esta aba e voltar ao terminal.",
    )

    if not creds.refresh_token:
        print(
            "\nNão veio refresh token. Revogue o acesso do app em "
            "https://myaccount.google.com/permissions e rode de novo.",
            file=sys.stderr,
        )
        sys.exit(1)

    installed = client_config["installed"]
    print("\n" + "=" * 70)
    print("Salve estes valores como secrets do repositório no GitHub:")
    print("(Settings -> Secrets and variables -> Actions -> New repository secret)")
    print("=" * 70)
    print(f"\nYOUTUBE_CLIENT_ID\n{installed['client_id']}\n")
    print(f"YOUTUBE_CLIENT_SECRET\n{installed['client_secret']}\n")
    print(f"YOUTUBE_REFRESH_TOKEN\n{creds.refresh_token}\n")
    print("=" * 70)
    print("Não commite estes valores. O client_secret.json já está no .gitignore.")


if __name__ == "__main__":
    main()
