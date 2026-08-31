Adicione rate limiting às rotas de autenticação mais sensíveis a flood, priorizando
`POST /accounts/password-recovery` (`app/accounts/router.py:190-199`), conforme
`docs/auditoria-seguranca-2026-08-29.md` (item 3). Leia o documento inteiro antes de começar.
Hoje `grep -rn "rate_limit\|slowapi\|limiter\|Limiter" app/` não retorna nada e
`app/settings/middleware.py` só registra CORS e logging -- não existe nenhum throttle na
aplicação.

## 1. Escolha e adicione a dependência

Adicione `slowapi` (`^0.1.9` ou mais recente compatível com FastAPI `^0.115.0`, já em
`pyproject.toml`) ao grupo principal de dependências em `pyproject.toml`. `slowapi` é baseado em
`limits`/`Flask-Limiter` e integra direto com Starlette/FastAPI via middleware + decorator, sem
exigir Redis (usa memória local por padrão, o que é aceitável aqui pois o serviço já não tem
nenhum estado compartilhado entre instâncias documentado para esse tipo de dado -- se o deploy for
multi-instância e isso for um problema real, configure o backend `slowapi` com Redis usando a
mesma configuração de conexão já usada em outro lugar do projeto, se houver; senão trate como
"por instância" e documente essa limitação no changelog).

## 2. Registre o limiter na aplicação

Em `app/settings/middleware.py`:

- Instancie um `Limiter` (`from slowapi import Limiter`, `from slowapi.util import
  get_remote_address`) usando `get_remote_address` como key function -- consistente com o
  `remote_ip` já usado no fluxo de payment-methods (ver `docs/devbutter-backend-fluxos.md` seção
  4, que já observa que esse valor não considera proxy confiável; não introduza uma segunda
  implementação de extração de IP, reaproveite a mesma função/atributo se já existir um helper
  central, senão crie um único helper e referencie-o dos dois lugares).
- Registre `app.state.limiter = limiter` e o exception handler
  `app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)` em `register_middleware`
  (ou em `app/main.py`, onde os demais exception handlers de `app/core/exceptions.py` já são
  registrados -- siga o mesmo padrão de registro central usado lá).

## 3. Aplique o limite nas rotas

Em `app/accounts/router.py`:

- Decore `request_password_recovery` (linha 196) com `@limiter.limit("5/minute")` por IP (ajuste
  o número após revisar com o time -- 5/minuto é um ponto de partida razoável para um fluxo que
  dispara e-mail). A rota precisa receber `request: Request` como parâmetro para o `slowapi`
  extrair o IP -- adicione esse parâmetro à assinatura se não estiver presente.
- Aplique o mesmo tratamento a `/accounts/login` e `/accounts/signup` (mesmo arquivo) -- a
  auditoria cita password-recovery como exemplo, mas o mesmo argumento (nenhum throttle, endpoint
  disparando efeito colateral custoso ou side-channel de enumeração) vale para as três.
- Não aplique rate limit a rotas administrativas autenticadas (`/admin/*`) neste prompt -- fora de
  escopo aqui, já protegidas por `CurrentAdminAccount`.

## 4. Resposta de limite excedido

Confirme que a resposta HTTP 429 do `slowapi` não vaza informação: a mensagem padrão
(`"Rate limit exceeded: X per Y"`) é aceitável, mas não inclua o e-mail ou qualquer dado do corpo
da requisição no corpo do erro. Não é necessário customizar o handler além do padrão do
`slowapi` para esta rodada.

## 5. Testes

Siga o padrão de teste de rota já usado em `tests/test_accounts_router.py` (ou arquivo
equivalente para o router de accounts):

- Teste que a 6ª requisição consecutiva a `/accounts/password-recovery` do mesmo IP dentro da
  janela retorna 429, usando o `TestClient`/`AsyncClient` já configurado nos testes existentes
  (pode ser necessário resetar o estado do limiter entre testes -- `slowapi` expõe
  `limiter.reset()` para isso; chame no fixture/teardown para não vazar estado entre testes).
- Teste que requisições de IPs diferentes (`client.post(..., headers={"X-Forwarded-For": ...})`
  ou equivalente, dependendo de como `get_remote_address` resolve o IP nos testes) não competem
  pelo mesmo limite.
- Confirme que as respostas 202 normais dentro do limite continuam funcionando sem regressão
  (reaproveite os testes existentes da rota).

Ao terminar, rode `pytest`, `ruff check` e `mypy` antes de considerar concluído.
