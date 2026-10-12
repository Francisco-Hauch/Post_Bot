# Bot de Shorts do YouTube

Pega os clipes `.mp4` publicados nas releases do
[Clip_Bot](https://github.com/Francisco-Hauch/Clip_Bot), converte para o formato
vertical 9:16 e publica automaticamente como Shorts no YouTube — de graça, via
GitHub Actions.

## Como funciona

1. Consulta as 3 releases mais recentes do `Clip_Bot` pela API do GitHub.
2. Descarta os clipes que já foram postados (`data/history.json`).
3. Ordena o restante por views e pega os 3 primeiros.
4. Converte cada um com ffmpeg para **1080x1920**, com barras pretas em cima e
   embaixo, cortado em no máximo 60s.
5. Publica no YouTube via YouTube Data API v3 e grava o histórico de volta no repo.

---

## Setup

### 1. Criar o projeto no Google Cloud

1. Acesse https://console.cloud.google.com/ e crie um projeto.
2. Em **APIs e serviços → Biblioteca**, ative a **YouTube Data API v3**.
3. Em **Tela de permissão OAuth**, configure como *Externo*, preencha o básico e
   adicione **sua própria conta do Google como usuário de teste**.
4. Em **Credenciais → Criar credenciais → ID do cliente OAuth**, escolha o tipo
   **Aplicativo para computador**. Baixe o JSON.

### 2. Gerar o refresh token (uma vez só, na sua máquina)

Salve o JSON baixado nesta pasta como `client_secret.json` e rode:

```bash
pip install -r requirements.txt
python auth_setup.py
```

O navegador vai abrir. Faça login **na conta do canal que vai receber os Shorts**.
No fim, o script imprime os três valores que você vai usar no próximo passo.

> Se aparecer "Google não verificou este app", clique em
> *Avançado → Acessar (não seguro)*. O app é seu, é esperado.

### 3. Subir para o GitHub e configurar os secrets

```bash
git init
git add .
git commit -m "feat: bot de shorts do youtube"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/SEU_REPO.git
git push -u origin main
```

Em **Settings → Secrets and variables → Actions**, adicione:

| Secret | De onde vem |
|---|---|
| `YOUTUBE_CLIENT_ID` | saída do `auth_setup.py` |
| `YOUTUBE_CLIENT_SECRET` | saída do `auth_setup.py` |
| `YOUTUBE_REFRESH_TOKEN` | saída do `auth_setup.py` |

Ainda em **Settings → Actions → General**, na seção *Workflow permissions*,
marque **Read and write permissions** (necessário para o bot commitar o histórico).

### 4. Testar

Vá em **Actions → Daily YouTube Shorts → Run workflow**, marque
`dry_run` e rode. Ele baixa e converte os clipes sem publicar nada. Se os logs
mostrarem os títulos que seriam postados, está tudo certo — rode de novo sem o
`dry_run`.

Depois disso o workflow roda sozinho todo dia às **12:00 UTC (09:00 em Brasília)**,
três horas depois do `Clip_Bot`.

---

## ⚠️ Importante: verificação da API do YouTube

Enquanto o seu projeto do Google Cloud **não passar pela auditoria do YouTube**,
todo vídeo enviado pela API fica **travado como privado**, mesmo com
`privacy_status: "public"` no config. Isso é uma regra do YouTube, não um bug do bot.

Isso **não atrapalha em nada** além do passo manual: o vídeo sobe e é processado
normalmente, e basta mudar a visibilidade para *Público* no YouTube Studio que ele
vira um Short comum, sem penalidade nenhuma.

Para facilitar, ao fim de cada execução o resumo do workflow (aba *Actions* →
clique na execução) lista os links de cada vídeo publicado, com atalho direto para
a tela de edição no Studio. Dá também para liberar vários de uma vez: selecione-os
pela caixinha e use *Editar → Visibilidade → Público*.

Para acabar de vez com o passo manual, preencha o formulário de auditoria da API:
https://support.google.com/youtube/contact/yt_api_form
Depois disso os uploads já saem públicos sozinhos, sem mudar nada no config.

## ⚠️ Cota da API

A cota diária padrão é de **10.000 unidades** e cada upload custa **1.600**, ou
seja, o teto real é de **6 vídeos por dia**. O bot está configurado para 3, o que
deixa margem para retentativas. Se estourar, ele para sozinho e loga o motivo —
a cota reseta à meia-noite no horário do Pacífico.

---

## Configuração

Tudo fica em [config/config.json](config/config.json):

| Campo | O que faz |
|---|---|
| `source_repo` | Repositório de onde vêm os clipes |
| `shorts_per_run` | Quantos Shorts publicar por execução |
| `releases_to_scan` | Quantas releases recentes olhar em busca de clipes novos |
| `max_duration_seconds` | Corta o vídeo neste tempo (Shorts aceita até 180s) |
| `min_duration_seconds` | Ignora clipes mais curtos que isso |
| `privacy_status` | `public`, `unlisted` ou `private` |
| `category_id` | `20` = Gaming |
| `title_template` | Placeholders: `{title}`, `{streamer}`, `{views}`, `{streamer_tag}` |
| `description_template` | Mesmos placeholders; use `\n` para quebra de linha |
| `extra_tags` | Tags adicionadas a todo vídeo |

## Rodando localmente

```bash
pip install -r requirements.txt
```

PowerShell:

```powershell
$env:YOUTUBE_CLIENT_ID    = "..."
$env:YOUTUBE_CLIENT_SECRET = "..."
$env:YOUTUBE_REFRESH_TOKEN = "..."
$env:DRY_RUN = "1"     # remova para publicar de verdade
python bot.py
```

Requer o `ffmpeg` no PATH.

## Estrutura

```
bot.py                          # o bot
auth_setup.py                   # gera o refresh token (uso local, uma vez)
config/config.json              # configurações
data/history.json               # clipes já postados (commitado automaticamente)
.github/workflows/daily-shorts.yml
```
