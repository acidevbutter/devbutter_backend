# Imagem AWS

`Dockerfile` e `docker-compose.yml` continuam exclusivos do desenvolvimento
local. O deploy usa `Dockerfile.aws`, que executa como usuário sem privilégios
e nunca recebe certificados ou arquivos `.env` no contexto de build.

Build local, ainda sem publicar:

```bash
docker build -f Dockerfile.aws -t devbutter-backend:staging-local .
```

Segredos, `DATABASE_URL` e material mTLS entram apenas na task ECS via Secrets
Manager. A migration continua sendo uma task controlada, não parte do startup
da imagem.
