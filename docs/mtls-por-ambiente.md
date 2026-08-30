# mTLS por ambiente

## Decisão

`certs/` é material local e permanece ignorado pelo Git. Cada ambiente usa uma
CA e certificados próprios; nunca reutilizar staging em produção.

| Ambiente | CA e certificados | Uso |
| --- | --- | --- |
| Local | CA de desenvolvimento gerada no repositório `asaas_payment` | Docker e testes manuais |
| Staging | CA privada separada, guardada em Secrets Manager | Integração entre serviços |
| Produção | CA privada separada, rotação controlada | Tráfego real |

ACM atende o certificado HTTPS público do ALB; não substitui o certificado de
cliente mTLS entre serviços. AWS Private CA não faz parte do estágio inicial:
para uma microempresa, seu custo recorrente não se justifica enquanto uma CA
privada gerenciada internamente atende o volume atual.

## Local

No checkout de `asaas_payment`, gerar o conjunto para o cliente DevButter:

```bash
./scripts/generate_dev_certs.sh devbutter-platform
```

Copiar apenas `ca.crt`, `devbutter-platform.crt` e
`devbutter-platform.key` para `devbutter_backend/certs/`. Não adicionar esses
arquivos ao Git. O script recria a CA a cada execução; para manter vários
clientes válidos, não o rode novamente sem preservar a CA atual.

## Staging e produção

1. Criar CA e certificado de cliente fora do repositório, com CA exclusiva do
   ambiente.
2. Salvar CA, certificado e chave no secret JSON do serviço em Secrets Manager:
   `/devbutter/staging/devbutter-backend` ou equivalente de produção.
3. Na task ECS, materializar os três valores em arquivos com permissões mínimas
   antes de iniciar a aplicação; configurar `ASAAS_MTLS_CA_PATH`,
   `ASAAS_MTLS_CERT_PATH` e `ASAAS_MTLS_KEY_PATH` com esses caminhos.
4. Rotacionar certificado e chave, fazer rollout controlado e revogar o par
   anterior somente após os clientes aceitarem o novo material.

Nenhum certificado real, chave privada ou URL de secret deve entrar em testes
unitários. Testes de assinatura usam clientes falsos; a validação mTLS real é
um teste de integração explícito, com certificados locais ou do ambiente.
