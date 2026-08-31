Coloque `scripts/run_proactive_rotation.py` para rodar de fato em produção, conforme
`docs/auditoria-seguranca-2026-08-29.md` (item 6). Leia o documento inteiro antes de começar.
Hoje o script é código morto: seu próprio docstring (`scripts/run_proactive_rotation.py:1-19`)
diz que depende de um scheduler externo (cron, Kubernetes CronJob, ECS scheduled task), e não há
nenhum agendador in-process (`apscheduler` ou equivalente) em `pyproject.toml`, nem qualquer
definição de CronJob/crontab neste repositório.

**Pré-requisito**: se `docs/prompts/resolver-bloqueio-relay-selolibertaria.md` ainda não foi
implementado, agendar este script antes disso só vai gerar alertas permanentes (todo run termina
com `exit_code = 1`, linhas 78-82, porque todo link fica `blocked`) -- avise quem for revisar isso
e considere agendar em modo silencioso (sem alerta de falha) até o item 1 do relatório ser
resolvido, se a ordem de entrega exigir agendar antes.

## 1. Decida o mecanismo de agendamento

Este repositório não tem um Kubernetes manifest, Dockerfile de infraestrutura ou definição de
deploy visível no escopo revisado -- confirme onde o deploy real deste serviço é definido (outro
repositório de infra, um Helm chart, um `docker-compose.yml` de produção, etc.) antes de escolher
o mecanismo. As opções mais prováveis, em ordem de preferência dado que o docstring já assume um
desses:

- **Kubernetes CronJob**, se o serviço já roda em k8s: crie o manifest do CronJob (provavelmente
  em um repositório de infra separado, não aqui) rodando
  `poetry run python scripts/run_proactive_rotation.py` na mesma imagem de container do backend,
  com uma cadência inicial de 1x/hora (ajustável depois com dados reais de quanto tempo as janelas
  de rotação do Asaas -- ver comentário sobre "D.1 2-hour management window" no próprio script,
  linha 13 -- ficam abertas).
- **ECS Scheduled Task**, se o serviço roda em ECS -- mesma lógica, definição também
  provavelmente fora deste repositório.
- Se nenhuma infra de orquestração existir ainda e o deploy for mais simples (ex.: uma única
  instância), documente isso explicitamente e proponha um cron de sistema operacional como medida
  temporária, deixando claro no PR/changelog que isso não escala e deve ser substituído assim que
  houver orquestração.

Se a definição do agendamento pertencer a outro repositório, este prompt só cobre o que pertence a
`devbutter_backend`: garantir que o script seja seguro para chamar em qualquer cadência (idempotente,
o que já é o caso por construção -- ver comentário nas linhas 11-16 do script) e documentar
a cadência recomendada e o comando exato no próprio repositório.

## 2. Adicione a documentação operacional que falta

Em `README.md` (ou em um novo `docs/operacao-rotacao-proativa.md`, seguindo o estilo de
`docs/devbutter-backend-fluxos.md`), documente:

- o comando exato (`poetry run python scripts/run_proactive_rotation.py`);
- a cadência recomendada e por quê;
- o significado de `exit_code = 1` (rotação bloqueada em pelo menos um link) e o que a equipe de
  operação deve fazer ao ver esse alerta (checar `selolibertaria_relay_last_error` nos links
  afetados via admin, não apenas re-executar);
- a dependência do achado 1 (relay bloqueado por redação de `apiKey` no `asaas_payment`) --
  enquanto não resolvido, `exit_code = 1` é esperado e não deve gerar page/alerta de on-call, só
  log.

## 3. Métricas/observabilidade mínimas (opcional, mas recomendado)

Se este serviço já expõe métricas (Prometheus, CloudWatch custom metrics, etc. -- confirme antes
de adicionar uma dependência nova), emita uma métrica de contagem de `touched`/`blocked` a cada
run do script, além do log já existente (linhas 66-77), para permitir alertar sobre tendência
(ex.: "blocked subiu de X para Y") em vez de depender só do exit code do job.

## 4. Testes

- Não é necessário um teste de integração com um scheduler real (fora do escopo deste
  repositório). Garanta que `scripts/run_proactive_rotation.py` continue com cobertura de teste
  unitário para `main()` (mock de `session_factory`, `get_asaas_client`, `get_selolibertaria_client`)
  cobrindo: nenhum link tocado -> `exit_code == 0`; pelo menos um link `blocked` ->
  `exit_code == 1`; exceção não tratada dentro do `async with session_factory()` ainda fecha os
  clients no `finally` (linhas 83-85).

Ao terminar, rode `pytest`, `ruff check` e `mypy` antes de considerar concluído.
