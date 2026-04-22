# Shorts MVP

MVP monolítico para:

- receber vídeo por upload ou link
- transcrever
- detectar bons momentos
- gerar clipes 9:16
- gerar título, legenda e hashtags básicas
- mostrar status simples por vídeo

## Rodar com Docker

Pré-requisito:

- Docker com daemon ativo

Comandos:

```bash
docker build -t shorts-mvp .
docker run --rm -p 8000:8000 -v ./data:/app/data shorts-mvp
```

Abra:

- `http://localhost:8000`

## Status do processamento

- `received`
- `processing`
- `ready`
- `failed`

## O que foi reaproveitado

- Ideia de transcrição/SRT e pipeline de vídeo vertical do `MoneyPrinterTurbo`
- Formato de metadata simples do `MoneyPrinterV2`

## O que foi simplificado

- Sem microserviços
- Sem Redis
- Sem fila externa
- Sem integração de publicação automática
- Sem LLM obrigatório para gerar copy
