# Backend_Projeto_App_Cristao
Backend do projeto agil e programação eficaz do app cristao

## Execução local

Backend Flask com application factory, autenticação, perfis, publicação e entrega privada de mídia, feed, reações, comentários, denúncias, bloqueios e moderação (ver [contrato da API](docs/api.md)). A sessão usa JWT HS256 em cookie HttpOnly com registro revogável no servidor e validade absoluta de 12 horas; o cliente React envia cookie e CSRF pelo cliente HTTP central.

O frontend da equipe, na base `5d79b9d`, já consome autenticação, perfil/lista própria, publicação, moderação/denúncias, feed, Amém e passagem bíblica. A branch frontend `feat/fluxos-mvp-locais-20261031` desenvolve acesso visitante, gestão de vídeos, comentários, bloqueios e edição/perfil público; sua existência não significa integração na `main` nem validação com banco real.

O registro de 29/10/2026 relata prova contra Atlas de desenvolvimento e `ffprobe` real; essa prova não foi repetida na revisão documental de 31/10. Publicar exige `ffprobe` (veja [operação](docs/operacao.md)). Integração real React–Flask–Atlas e deploy EC2 continuam pendentes de aceite. Renovação de sessão 30/90 dias é planejamento posterior, sem implementação atual.

Requer Python 3.12. No diretório deste repositório:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps -r requirements-bootstrap.txt
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/flask --app app run --host 127.0.0.1 --port 5000
```

```bash
curl --fail http://127.0.0.1:5000/api/health
curl --get --data-urlencode 'reference=João 3:16' http://127.0.0.1:5000/api/bible/passages
```

Testes offline: `.venv/bin/python -m pytest -q`. Prova real opt-in: `.venv/bin/python scripts/smoke_bible.py`. Dependências exatas em `requirements.txt` e `requirements-dev.txt`.

Sem URI MongoDB, o desenvolvimento usa cache SQLite em `instance/`, ignorado pelo Git. Produção exige MongoDB; não há fallback silencioso. O cache local não substitui Atlas para a entrega acadêmica.

**API pública:** ainda não implantada. Link real será acrescentado após verificação em EC2; os modelos de implantação não comprovam deploy.

## Documentação

- [Arquitetura](docs/arquitetura.md) e [contrato da API](docs/api.md): decisões e estado das rotas.
- [Execução e preparação de EC2](docs/operacao.md).
- [Verificação local e matriz de integração futura](docs/verificacao-local.md).
- [Roteiro de demonstração e entrega](docs/roteiro-demonstracao.md).
- [Controles de segurança e preparação para publicação](docs/seguranca.md): limites, proxy confiável, TLS, credenciais e recuperação de dados.

- [Documento de Escopo e Requisitos do Projeto](docs/escopo-e-requisitos.md): visão do produto, análise de lacunas, arquitetura proposta, funcionalidades, prioridades, cronograma e critérios de aceite.
- [Requisitos oficiais do Projeto 3](https://insper.github.io/ProgramacaoEficaz/projetos/projeto3/).

https://consonant-amnesty-election.ngrok-free.dev/login

3.85.111.97
