# Imagem Hostinger / Coolify

`Dockerfile` e `docker-compose.yml` continuam exclusivos do desenvolvimento
local. O deploy usa `Dockerfile.hostinger`, que executa como usuário sem privilégios
e nunca recebe certificados ou arquivos `.env` no contexto de build.

Build local, ainda sem publicar:

```bash
docker build -f Dockerfile.hostinger -t devbutter-backend:staging-local .
```

Segredos, `DATABASE_URL` e material mTLS entram apenas na Coolify via variáveis de ambiente. A migration continua fora do startup da imagem.
