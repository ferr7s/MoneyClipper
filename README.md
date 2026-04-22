# MoneyClipper

MVP monolítico para transformar um vídeo em clipes curtos prontos para publicação.

CI básico via GitHub Actions valida sintaxe Python, build da imagem Docker e subida do app com `healthz`.

O projeto recebe um vídeo por upload ou link, faz a transcrição, detecta bons momentos para corte, gera clipes verticais 9:16, cria copy básica e mostra o status do processamento em uma interface web simples.

## Escopo atual

- Upload manual de vídeo
- Entrada por link de vídeo
- Transcrição com `faster-whisper`
- Detecção heurística de highlights
- Geração de clipes 9:16 com legendas
- Título, legenda e hashtags básicas
- Status por vídeo: `received`, `processing`, `ready`, `failed`

## Rodar com Docker

Pré-requisito:

- Docker com daemon ativo

Comandos:

```bash
docker build -t moneyclipper .
docker run --rm -p 8000:8000 -v ./data:/app/data moneyclipper
```

Abra:

- `http://localhost:8000`

## Estrutura mínima

- `app.py`: app web, persistência SQLite e controle de jobs
- `processor.py`: download, transcrição, corte, render e copy básica
- `templates/index.html`: interface simples com polling de status
- `Dockerfile`: imagem única para rodar localmente

## Reaproveitamento

- `MoneyPrinterTurbo`: referência para transcrição/SRT e pipeline de vídeo vertical
- `MoneyPrinterV2`: referência para saída de metadata de publicação

## Simplificações do MVP

- Sem microserviços
- Sem Redis
- Sem fila externa
- Sem automação de postagem
- Sem arquitetura distribuída
- Sem dependência obrigatória de LLM para gerar copy
