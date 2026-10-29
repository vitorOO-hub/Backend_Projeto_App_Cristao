# Backend_Projeto_App_Cristao
Backend do projeto agil e programação eficaz do app cristao

## Execução — entrega T03

Backend Flask com application factory, `GET /api/health` e `GET /api/bible/passages`. Integração real com bible-api.com em Almeida verificada para João 3:16 e Salmos 23:1. Cadastro, login, logout e perfil; publicação, feed e entrega de vídeo; reações, comentários, denúncias e bloqueios; e moderação estão implementados e testados (ver [contrato da API](docs/api.md)). Publicar vídeo exige `ffprobe` instalado (veja [operação](docs/operacao.md)). O fluxo completo (publicar, moderar, listar, reproduzir, reagir, comentar e denunciar) foi verificado em 29/10/2026 contra o Atlas de desenvolvimento e com o `ffprobe` real. Ainda faltam o consumo pelo React, o deploy na EC2 e a rota opcional de renovação de sessão (D004).

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
- [Controles de segurança e preparação para publicação](docs/seguranca.md): limites, proxy confiável, TLS, credenciais e recuperação de dados.

- [Documento de Escopo e Requisitos do Projeto](docs/escopo-e-requisitos.md): visão do produto, análise de lacunas, arquitetura proposta, funcionalidades, prioridades, cronograma e critérios de aceite.
- [Requisitos oficiais do Projeto 3](https://insper.github.io/ProgramacaoEficaz/projetos/projeto3/).
