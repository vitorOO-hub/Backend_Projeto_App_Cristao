# Execução local e preparação de EC2 — T03

## Estado da entrega

Implementados `GET /api/health` e `GET /api/bible/passages`. Também implementados GET `/api/auth/csrf`, POST `/api/users`, POST `/api/auth/login`, POST `/api/auth/logout`, GET/PATCH `/api/me` e GET `/api/users/{id}`; React e prova no Atlas permanecem pendentes. Nenhum recurso AWS foi criado e não existe URL pública de API nesta entrega.

**Planejamento revisado em 24/10/2026:** sessões comuns terão renovação controlada, janela de 30 dias e limite absoluto de 90 dias; administradores continuarão com 12 horas absolutas. Essa revisão ainda não está no código. As instruções executáveis abaixo usam a configuração atual, incluindo os controles de acesso do [contrato API v0.6](api.md). Ver [arquitetura](arquitetura.md).

## Preparar ambiente local

No diretório do backend, com Python 3.12:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps -r requirements-bootstrap.txt
.venv/bin/python -m pip install -r requirements-dev.txt
cp .env.example .env
.venv/bin/flask --app app init-cache
.venv/bin/flask --app app run --host 127.0.0.1 --port 5000
```

`cp` é para a primeira configuração; preserve `.env` existente. Não é necessário preencher MongoDB para testar a T03 local. O Flask CLI lê `.env` via python-dotenv; o Gunicorn depende de variáveis exportadas ou fornecidas pelo serviço systemd.

O bootstrap instala pip 26.2.2 por artefato fixado com SHA-256 antes das demais dependências; certifi está fixado em 2026.10.19. Aplicar também em imagens e servidor. Os [controles de segurança](seguranca.md) descrevem o incidente de certificados, os limites atuais e as condições pendentes para publicação.

Em outro terminal:

```bash
curl --fail http://127.0.0.1:5000/api/health
curl --get --data-urlencode 'reference=João 3:16' http://127.0.0.1:5000/api/bible/passages
```

O provedor exige acesso à internet. Falha de rede retorna 503, preservando o health check. Cache de 24 horas em SQLite local compartilhado pelo caminho `instance/bible-cache.sqlite3`; não serve como evidência de Atlas nem armazena usuários. Produção exige URI MongoDB e usa a coleção `bible_cache`, sem fallback local. Com URI configurada em desenvolvimento, o cache também usa MongoDB.

## Testes e prova real

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
.venv/bin/gunicorn --check-config --config gunicorn.conf.py wsgi:app
.venv/bin/python scripts/smoke_bible.py
```

Os testes são offline e não escrevem em banco real. A prova real é opt-in, usa a rota Flask com cache temporário isolado e faz duas chamadas externas mais uma consulta ao cache. Não imprime textos completos ou credenciais. Respeite os limites do provedor; não execute a prova em loop.

`health` comprova somente processo/rota; não consulta cache, Atlas ou API externa. `flask --app app init-cache` verifica o backend de cache e cria explicitamente o índice TTL no MongoDB quando configurado. A expiração é checada nas consultas, sem esperar a limpeza TTL.

## Configuração consumida nesta T03

`APP_ENV`, `MONGODB_URI`, `MONGODB_DATABASE`, `BIBLE_API_BASE_URL` (fixa em `https://bible-api.com`), `BIBLE_CONNECT_TIMEOUT_SECONDS`, `BIBLE_READ_TIMEOUT_SECONDS`, `BIBLE_CACHE_TTL_SECONDS` e `MAX_REQUEST_BYTES` são lidas pela factory. Sem URI e fora de produção, o cache usa SQLite em `instance`.

Tradução é restrita a `almeida`; não aceitar provedor/URL enviados na query. `APP_ORIGIN`, `SESSION_COOKIE_SECURE` e `ANONYMOUS_SESSION_TTL_SECONDS` são consumidos na autenticação. A sessão anônima tem duração absoluta fixa de 1800 segundos. `SESSION_TTL_SECONDS` já é consumida pelo login e deve permanecer em 43200 até implementar a revisão D004: outra duração é rejeitada na inicialização. `UPLOAD_*`, `FFPROBE_PATH` e `MAX_VIDEO_*` permanecem contratos futuros.

`TRUSTED_PROXY_IPS` configura a confiança no proxy e é obrigatória em produção. `MONGODB_SETUP_URI` é lida somente nos comandos de inicialização em produção; não pertence ao ambiente do serviço web. Seus valores reais ficam fora do Git.

## Preparação para EC2 (sem provisionamento nesta tarefa)

Arquivos disponíveis:

- `deploy/app-cristao.service`: modelo systemd com usuário dedicado, ambiente externo e Gunicorn em loopback.
- `deploy/nginx.conf.example`: modelo mesma origem com HTTPS, proxy `/api`, limites de taxa/conexões e corpo padrão de 16 KiB. A futura rota de upload precisará de exceção explícita de 51 MiB no proxy e Flask. Não publica arquivos de upload.
- `gunicorn.conf.py`: dois workers, timeout de 30 segundos e sem access log com parâmetros de consulta.

Os modelos não foram instalados no sistema. Nginx/HTTPS e frontend ainda não estão disponíveis localmente para validação integral. Só ativar o serviço depois de adaptar e conferir os arquivos no servidor autorizado.

### Pré-requisitos no servidor já autorizado

1. Instância EC2 acessível, Python 3.12, Nginx, usuário de serviço `appcristao`, domínio e certificado HTTPS. Abrir somente as portas necessárias; Gunicorn permanece em `127.0.0.1:8000`.
2. Backend em `/srv/app-cristao/backend`; criar virtualenv, instalar `requirements-bootstrap.txt` com `--no-deps` e depois `requirements.txt`. Executar testes em ambiente separado antes de publicar.
3. MongoDB Atlas com usuário restrito ao banco do projeto e acesso de rede autorizado para a EC2. Não desabilitar TLS para resolver falhas de conexão.
4. Criar `/var/lib/app-cristao` pertencente ao usuário do serviço, para uploads futuros. Código permanece separado dos dados persistentes.
5. Guardar ambiente em `/etc/app-cristao/backend.env`, legível apenas por root/serviço conforme a configuração operacional. Não usar exemplo como credencial real. Configurar `APP_ENV=production`, `MONGODB_URI` runtime restrita e banco dedicado; `mongodb://` exige TLS explícito e `mongodb+srv://` não pode desativá-lo. `APP_ORIGIN=https://seu-dominio`, `SESSION_COOKIE_SECURE=true`, `JWT_SECRET` aleatório de pelo menos 32 bytes, o mesmo para os dois workers (`python -c "import secrets; print(secrets.token_urlsafe(32))"`), e `TRUSTED_PROXY_IPS=127.0.0.1,::1` para o Nginx local. Não disponibilizar a credencial de inicialização ao serviço web.
6. Antes de ativar o serviço, carregar ambiente operacional restrito com `MONGODB_SETUP_URI` separada e TLS; executar `flask --app app init-auth` e `flask --app app init-cache`. Retirar o segredo desse contexto após os comandos. Em produção, sua ausência impede inicialização de índices com a credencial runtime. Não copiar URI em logs, comandos compartilhados ou commits.
7. Adaptar domínio/certificados no exemplo Nginx; publicar build React somente quando existir. Validar com `nginx -t` e `systemd-analyze verify` no servidor, após os caminhos estarem instalados. O redirecionamento HTTP exige certificado previamente provisionado; este modelo não automatiza sua emissão.
8. Instalar unidade, recarregar systemd e iniciar somente após validação. Verificar health, uma passagem real e a segunda consulta em cache. Confirmar a coleção no Atlas sem divulgar URI. Documentar URL pública no README.

### Checklist de implantação pendente

- [ ] Acesso EC2 e configuração de domínio/HTTPS disponibilizados.
- [ ] Credenciais Atlas configuradas por canal privado e conectividade verificada.
- [ ] Modelos adaptados e validados no servidor de destino.
- [ ] Backend acessível publicamente; link real no README.
- [ ] Frontend implementado/publicado e fluxo completo verificado (T12).

O custo e a autorização de recursos continuam sob controle do usuário; este documento não executa provisionamento. Preparar esses arquivos atende à parte de preparação da T03, sem concluir RA05 ou CA12.

## Cadastro e CSRF — T04.1

Contas e sessões usam somente MongoDB; sem `MONGODB_URI`, as novas rotas retornam 503 controlado (sem fallback SQLite ou memória). Configure a URI fora do Git e execute antes do uso:

```bash
.venv/bin/flask --app app init-auth
```

O comando cria índices únicos de e-mail normalizado e hash de sessão, TTL de sessões e do novo limitador `rate_limits`, além de índices legados de `login_attempts`. A coleção legada não limita mais novos logins; preservar sua limpeza TTL sem migrar falhas antigas para os novos orçamentos. Conflitos/dados existentes ou banco inacessível causam falha controlada. Preserve os índices únicos para garantir duplicação inclusive em concorrência. Em produção, fornecer `MONGODB_SETUP_URI` apenas ao processo operacional conforme os pré-requisitos acima.

Fluxo: GET `/api/auth/csrf` → guardar cookie e `data.csrf_token` → POST `/api/users` com JSON `name`, `email`, `password` e cabeçalho `X-CSRF-Token`. Cadastro retorna 201, sem autenticar a sessão. Uma nova consulta CSRF rotaciona o token e preserva o prazo absoluto da sessão existente. Sessões armazenam somente hashes dos tokens; senhas usam scrypt.

`APP_ORIGIN` é a origem exata do cliente, sem caminho ou barra final. Requisições com Origin divergente são recusadas nas duas rotas; clientes sem Origin ainda precisam de cookie e CSRF no cadastro. Cookies são Secure por padrão; `SESSION_COOKIE_SECURE=false` só é aceito em testes ou `APP_ENV=development` com origem localhost HTTP. Desenvolvimento com Vite deve encaminhar `/api` ao Flask na mesma origem; não há CORS público. Sem `JWT_SECRET` em development, cada processo gera a própria chave e reiniciar o Flask encerra as sessões.

Testes usam repositório fake injetado e adaptador Mongo simulado, sem rede ou dados reais. Não comprovam persistência no Atlas, concorrência real no banco ou integração React. Sessão autenticada dura 12 horas (`SESSION_TTL_SECONDS=43200`, valor fixo validado na inicialização).

Os limites de acesso são compartilhados: MongoDB em produção e SQLite persistente somente no desenvolvimento/teste sem URI. Há orçamentos por IP, global, cadastro, CSRF e login IP/e-mail, reservados atomicamente antes do trabalho caro. Sucesso não zera o contador. Ver tabela e semântica de janela fixa no [guia de segurança](seguranca.md). Limites 429 da aplicação têm Retry-After; falha do armazenamento gera 503. Health não comprova que os contadores ou índices estejam prontos.

## Transição planejada para sessões persistentes — D004

Implementar em conjunto política de sessão, persistência, renovação, configuração, testes e cliente React. A revisão documental não exige alterar `.env` ou `.env.example` agora. Os nomes e limites das novas configurações serão definidos na implementação; os prazos iniciais estão na arquitetura.

- Preservar sessões antigas com a expiração original de 12 horas; não ampliar documentos existentes em lote. O próximo login cria a sessão no formato novo.
- Acrescentar política atribuída pelo servidor, última renovação e limite absoluto em UTC. Calcular validade efetiva sem ultrapassar o limite absoluto; manter verificação por acesso e TTL somente para limpeza.
- Renovação exige sessão válida, CSRF e atualização condicional atômica, sem recriar sessão excluída. Cookie atualizado somente após persistência confirmada.
- Sessões comuns elegíveis devem sobreviver a fechar/reabrir o navegador e retornar em outro dia com cookie preservado. Administradores não recebem extensão automática; mudança de papel deve respeitar a nova política de permissões.
- Validar múltiplas abas, obtenção de CSRF e renovação concorrente com logout. Falha de rede/503 deve permitir recuperação sem ser apresentada como credencial inválida.
- Antes do aceite, executar testes isolados e verificar React–Flask–Atlas, cookie HTTPS, expiração e revogação no ambiente publicado. Registrar evidências próprias; esta documentação não comprova implantação ou capacidade para 5.000 usuários simultâneos.

Gestão remota de dispositivos, OAuth, MFA, passkeys e confirmação adicional específica para moderação ficam para evolução posterior.
