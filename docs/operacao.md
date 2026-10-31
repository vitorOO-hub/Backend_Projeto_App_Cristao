# Execução local e preparação de EC2

## Requisitos de consistência do banco

As gravações de interações e as reservas de upload usam transações curtas no MongoDB. É necessário um replica set ou cluster compatível; um servidor standalone não oferece essa garantia. A aplicação deve recusar a operação se a transação não puder ser executada. Não substituir esse controle por consultas e gravações separadas. Nenhuma transação permanece aberta durante recebimento do arquivo ou execução de `ffprobe`.

Criar índices com a credencial operacional antes de iniciar o serviço: `init-auth`, `init-cache` e `init-db`. Conferir a matriz runtime em [segurança](seguranca.md), incluindo os privilégios de `update` usados por reações, bloqueios e seguidores. A credencial operacional não pertence ao ambiente do serviço web.

Testes de concorrência com MongoDB são opt-in por `TEST_MONGODB_URI`, exigem endereço loopback e criam somente bancos novos com prefixo `app_cristao_security_test_` e identificador aleatório. Não fornecer a URI do aplicativo, de demonstração ou do Atlas. O teste remove o banco temporário que ele próprio criou. Testes locais não comprovam permissões, rede ou implantação no Atlas/AWS. A [documentação MongoDB sobre transações](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/crud/transactions/) descreve a configuração e os limites do driver.

## Diagnóstico e limpeza de vídeos excluídos

### Configuração de admissão

| Variável | Padrão | Efeito |
| --- | --- | --- |
| `MAX_USER_STORAGE_BYTES` | `1073741824` | 1 GiB por conta, incluindo arquivos excluídos ainda não removidos |
| `MAX_PENDING_VIDEOS` | `10` | Impede novos envios ao atingir o limite de pendentes e reservas |
| `MAX_STORAGE_USED_PERCENT` | `80` | Ocupação máxima projetada do volume |
| `MIN_STORAGE_FREE_BYTES` | `2147483648` | Reserva mínima de 2 GiB livres após a admissão |
| `UPLOAD_VOLUME_ID` | `local-media` | Identificador estável do volume local usado por todos os workers deste host |

Configurações inválidas impedem a inicialização. Guardar mídia e spool no mesmo volume protegido e fornecer o mesmo identificador a todos os workers. O modelo cobre um host com armazenamento local persistente; adicionar outro host exige revisar a coordenação das travas e do volume. Nenhum valor dessas variáveis vem de requisições dos usuários.

Na localização exata `/api/videos`, o modelo Nginx usa `proxy_request_buffering off` e proxy HTTP/1.1 para encaminhar o corpo à aplicação sem armazená-lo inteiro antes da admissão, inclusive em requisições chunked. Preservar limites de corpo/conexões e coordenar os timeouts com o Gunicorn. A [referência Nginx](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_request_buffering) explica essa dependência. Conferir a configuração efetiva no servidor de destino; o modelo local não demonstra deploy.

### Recuperação de tentativas interrompidas

`flask --app app reconcile-uploads` apenas diagnostica tentativas que podem exigir recuperação. A opção explícita `--apply` tenta recuperar tentativas abandonadas, pula uploads ainda travados por processos ativos e preserva arquivos associados a vídeos já confirmados. Não há liberação de reserva baseada apenas no tempo decorrido. Em falha de banco ou resultado incerto, manter os recursos reservados e repetir o diagnóstico depois de restabelecer o serviço.

### Limpeza de relações

`flask --app app audit-orphans` é somente leitura: informa identificadores de relações cujo vídeo está ausente ou excluído, sem revelar textos de comentários ou motivos de denúncias. Executar o diagnóstico antes de decidir uma intervenção sobre dados existentes.

`flask --app app purge-videos` mantém a limpeza retomável dos vídeos excluídos ainda pendentes. Para rever explicitamente vídeos excluídos já marcados como limpos, usar `flask --app app purge-videos --recheck --limit 50`; a opção `--after <id>` continua a varredura a partir do último identificador examinado. A rechecagem remove somente arquivos, reações e comentários dos tombstones selecionados e preserva denúncias. Preparar e conferir a operação em banco isolado antes de executá-la sobre dados reais.

## Estado da entrega

Backend implementa health, consulta bíblica, autenticação/perfis, vídeos, interações e moderação. Frontend base `5d79b9d` já consome autenticação, perfil/lista própria, publicação, moderação/denúncias, feed, Amém e versículo. As interfaces complementares estão em desenvolvimento na branch `feat/fluxos-mvp-locais-20261031`; verificar revisão/merge antes de tratá-las como disponíveis na `main`.

O registro de 29/10/2026 relata prova Atlas de desenvolvimento/ffprobe real, não repetida na revisão documental de 31/10. Não há evidência de integração atual completa React–Flask–Atlas nem URL pública verificada nesta entrega. Nenhum recurso AWS foi criado por esta atualização. Sessão atual: JWT HS256 em cookie HttpOnly, registro revogável no servidor e 12 horas absolutas.

**Planejamento revisado em 24/10/2026:** sessões comuns terão renovação controlada, janela de 30 dias e limite absoluto de 90 dias; administradores continuarão com 12 horas absolutas. Essa revisão ainda não está no código. As instruções executáveis abaixo usam a configuração atual, incluindo os controles de acesso do [contrato da API](api.md). Ver [arquitetura](arquitetura.md).

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

A suíte padrão, sem `TEST_MONGODB_URI`, usa dados sintéticos e provedor bíblico bloqueado/simulado. Ao definir essa variável para um replica set local descartável, os testes opt-in criam, escrevem e removem bancos novos com prefixo UUID; incluem concorrência real e uma credencial restrita por coleção. Não usam o banco configurado do aplicativo nem comprovam o Atlas. Dimensionar os limites de arquivos do MongoDB de teste conforme a [referência operacional](https://www.mongodb.com/docs/manual/reference/ulimit/).

Separadamente, `scripts/smoke_bible.py` é uma prova opt-in do provedor externo: usa a rota Flask com cache temporário isolado e faz duas chamadas externas mais uma consulta ao cache. Não imprime textos completos ou credenciais. Respeite os limites do provedor; não execute a prova em loop.

`health` comprova somente processo/rota; não consulta cache, Atlas ou API externa. `flask --app app init-cache` verifica o backend de cache e cria explicitamente o índice TTL no MongoDB quando configurado. A expiração é checada nas consultas, sem esperar a limpeza TTL.

## Configuração consumida pelo runtime

`APP_ENV`, `MONGODB_URI`, `MONGODB_DATABASE`, `BIBLE_API_BASE_URL` (fixa em `https://bible-api.com`), `BIBLE_CONNECT_TIMEOUT_SECONDS`, `BIBLE_READ_TIMEOUT_SECONDS`, `BIBLE_CACHE_TTL_SECONDS` e `MAX_REQUEST_BYTES` são lidas pela factory. Sem URI e fora de produção, o cache usa SQLite em `instance`.

Tradução é restrita a `almeida`; não aceitar provedor/URL enviados na query. `APP_ORIGIN`, `SESSION_COOKIE_SECURE` e `ANONYMOUS_SESSION_TTL_SECONDS` são consumidos na autenticação. A sessão anônima tem duração absoluta fixa de 1800 segundos. `SESSION_TTL_SECONDS` já é consumida pelo login e deve permanecer em 43200 até implementar a revisão D004: outra duração é rejeitada na inicialização. `UPLOAD_DIR` (padrão `instance/uploads`), `FFPROBE_PATH` (padrão `ffprobe`) e `JWT_SECRET` são consumidas pelas rotas de vídeo e pelo cookie de sessão; ver `.env.example`.

`TRUSTED_PROXY_IPS` configura a confiança no proxy e é obrigatória em produção. `MONGODB_SETUP_URI` é lida somente nos comandos de inicialização em produção; não pertence ao ambiente do serviço web. Seus valores reais ficam fora do Git.

## Preparação para EC2 (sem provisionamento nesta tarefa)

Arquivos disponíveis:

- `deploy/app-cristao.service`: modelo systemd com usuário dedicado, ambiente externo e Gunicorn em loopback.
- `deploy/nginx.conf.example`: modelo mesma origem com HTTPS, proxy `/api`, limites de taxa/conexões e corpo padrão de 16 KiB. A localização exata `/api/videos` aceita requisições de até 51 MiB (arquivo de até 50 MiB) e encaminha o corpo sem buffering antes da admissão no Flask. Não publica arquivos de upload.
- `gunicorn.conf.py`: dois workers, timeout de 30 segundos e sem access log com parâmetros de consulta.

Os modelos não foram instalados no sistema. Esta revisão não executou Nginx/HTTPS nem validou o sistema integrado. Só ativar o serviço depois de adaptar e conferir os arquivos no servidor autorizado.

### Pré-requisitos no servidor já autorizado

1. Instância EC2 acessível, Python 3.12, Nginx, usuário de serviço `appcristao`, domínio e certificado HTTPS. Abrir somente as portas necessárias; Gunicorn permanece em `127.0.0.1:8000`.
2. Backend em `/srv/app-cristao/backend`; criar virtualenv, instalar `requirements-bootstrap.txt` com `--no-deps` e depois `requirements.txt`. Executar testes em ambiente separado antes de publicar.
3. MongoDB Atlas com usuário restrito ao banco do projeto e acesso de rede autorizado para a EC2. Não desabilitar TLS para resolver falhas de conexão.
4. Criar `/var/lib/app-cristao` pertencente ao usuário do serviço, para mídia, spool e leases persistentes. Definir `UPLOAD_DIR=/var/lib/app-cristao/uploads` no ambiente e o mesmo `UPLOAD_VOLUME_ID` para todos os workers deste host. Código permanece separado dos dados persistentes.
5. Guardar ambiente em `/etc/app-cristao/backend.env`, legível apenas por root/serviço conforme a configuração operacional. Não usar exemplo como credencial real. Configurar `APP_ENV=production`, `MONGODB_URI` runtime restrita e banco dedicado; `mongodb://` exige TLS explícito e `mongodb+srv://` não pode desativá-lo. `APP_ORIGIN=https://seu-dominio`, `SESSION_COOKIE_SECURE=true`, `JWT_SECRET` aleatório de pelo menos 32 bytes, o mesmo para os dois workers (`python -c "import secrets; print(secrets.token_urlsafe(32))"`), e `TRUSTED_PROXY_IPS=127.0.0.1,::1` para o Nginx local. Não disponibilizar a credencial de inicialização ao serviço web.
6. Antes de ativar o serviço, carregar ambiente operacional restrito com `MONGODB_SETUP_URI` separada e TLS; executar `flask --app app init-auth`, `flask --app app init-db` e `flask --app app init-cache`. Retirar o segredo desse contexto após os comandos. Administradores só são criados por linha de comando, com a mesma credencial: `flask --app app set-role email@exemplo.com admin` (ou `user` para rebaixar). A conta precisa existir (cadastro normal pelo app); o comando encerra as sessões dela, então o papel novo vale a partir do próximo login. A API nunca altera papéis. Em produção, sua ausência impede inicialização de índices com a credencial runtime. Não copiar URI em logs, comandos compartilhados ou commits.
7. Adaptar domínio/certificados no exemplo Nginx; gerar e conferir o build React no repositório frontend antes da publicação. Validar com `nginx -t` e `systemd-analyze verify` no servidor, após os caminhos estarem instalados. O redirecionamento HTTP exige certificado previamente provisionado; este modelo não automatiza sua emissão.
8. Instalar unidade, recarregar systemd e iniciar somente após validação. Verificar health, uma passagem real e a segunda consulta em cache. Confirmar a coleção no Atlas sem divulgar URI. Documentar URL pública no README.

### Checklist de implantação pendente

- [ ] Acesso EC2 e configuração de domínio/HTTPS disponibilizados.
- [ ] Credenciais Atlas configuradas por canal privado e conectividade verificada.
- [ ] Modelos adaptados e validados no servidor de destino.
- [ ] Backend acessível publicamente; link real no README.
- [ ] Build React publicado e fluxo completo verificado no destino (T12).

O custo e a autorização de recursos continuam sob controle do usuário; este documento não executa provisionamento. Preparar esses arquivos atende à parte de preparação da T03, sem concluir RA05 ou CA12.

## Vídeos, ffprobe e comandos operacionais

Variáveis consumidas: `UPLOAD_DIR` (padrão `instance/uploads`, relativo à raiz do backend; em produção, um volume persistente fora do código, como `/var/lib/app-cristao/uploads`, pertencente ao usuário do serviço), `MAX_VIDEO_BYTES` (50 MiB), `MAX_REQUEST_BYTES` (51 MiB, limite do corpo somente na rota de upload) e `FFPROBE_PATH`. `MAX_VIDEO_SECONDS` documenta o limite de 90 s, que o código aplica por constante.

**ffprobe é obrigatório para publicar.** Sem ele, `POST /api/videos` retorna 503 `media_unavailable` e nenhum arquivo é aceito. Instalar o FFmpeg (que inclui o `ffprobe`) e conferir com `ffprobe -version`: no Windows, por exemplo, `winget install Gyan.FFmpeg`; na EC2 (Amazon Linux/Ubuntu), pelo gerenciador de pacotes. Se o executável não estiver no `PATH`, apontar `FFPROBE_PATH` para ele. O modelo Gunicorn tem timeout de 30 s; o Nginx permite 60 s de leitura/corpo na localização de upload (30 s de leitura no restante da API), e o cliente React aguarda até 120 s para FormData. Esses valores não garantem que um upload lento será aceito. Medir e ajustar os limites em conjunto no destino autorizado, incluindo comportamento de falha, antes de prometer suporte a arquivos no limite.

Comandos (`flask --app app <comando>`):

| Comando | Para que serve |
| --- | --- |
| `init-auth`, `init-cache`, `init-db` | Índices de contas/sessões/limites, cache e coleções do produto. Em produção exigem `MONGODB_SETUP_URI`. |
| `create-admin <e-mail>` | Promove uma conta **já cadastrada** a administrador. Em produção usa `MONGODB_SETUP_URI`. A conta precisa entrar de novo para usar o painel. |
| `purge-videos` | Conclui a remoção física (arquivo, reações, comentários) de vídeos excluídos cuja limpeza falhou; idempotente; executar apenas no ambiente autorizado, conferindo o escopo. Nenhum agendamento é criado por este guia. |

Fluxo de uma publicação: reservar recursos antes de ler multipart; receber spool e cópia privada no workspace da tentativa dentro de `UPLOAD_DIR/.upload-leases` (interrompendo a cópia acima de 50 MiB); fechar spools; validar com `ffprobe`; mover para `<uuid>.mp4`; confirmar vídeo e reserva na mesma transação. Falhas anteriores ao movimento limpam temporários e cancelam a reserva. Uma confirmação incerta após o movimento preserva arquivo e reserva para reconciliação explícita. O diretório de uploads nunca é servido diretamente.

No Windows, um arquivo de vídeo ainda aberto por uma resposta em andamento não pode ser apagado; nesse caso a exclusão do vídeo continua valendo (ele fica invisível) e a limpeza física fica para o `purge-videos`.

## Cadastro e CSRF — T04.1

Contas e sessões usam somente MongoDB; sem `MONGODB_URI`, as novas rotas retornam 503 controlado (sem fallback SQLite ou memória). Configure a URI fora do Git e execute antes do uso:

```bash
.venv/bin/flask --app app init-auth
.venv/bin/flask --app app init-db
```

`init-db` cria os índices de vídeos, reações, comentários, denúncias, bloqueios, seguidores e reservas/guardas de upload ([modelo de dados](modelo-de-dados.md)); exige `MONGODB_URI`, sem fallback local. O comando `init-auth` cria índices únicos de e-mail normalizado e hash de sessão, TTL de sessões e do novo limitador `rate_limits`, além de índices legados de `login_attempts`. A coleção legada não limita mais novos logins; preservar sua limpeza TTL sem migrar falhas antigas para os novos orçamentos. Conflitos/dados existentes ou banco inacessível causam falha controlada. Preserve os índices únicos para garantir duplicação inclusive em concorrência. Em produção, fornecer `MONGODB_SETUP_URI` apenas ao processo operacional conforme os pré-requisitos acima.

Fluxo: GET `/api/auth/csrf` → guardar cookie e `data.csrf_token` → POST `/api/users` com JSON `name`, `email`, `password` e cabeçalho `X-CSRF-Token`. Cadastro retorna 201, sem autenticar a sessão. Uma nova consulta CSRF rotaciona o token e preserva o prazo absoluto da sessão existente. Sessões armazenam somente hashes dos tokens; senhas usam scrypt.

`APP_ORIGIN` é a origem exata do cliente, sem caminho ou barra final. Requisições com Origin divergente são recusadas nas duas rotas; clientes sem Origin ainda precisam de cookie e CSRF no cadastro. Cookies são Secure por padrão; `SESSION_COOKIE_SECURE=false` só é aceito em testes ou `APP_ENV=development` com origem localhost HTTP. Desenvolvimento com Vite deve encaminhar `/api` ao Flask na mesma origem; não há CORS público. Sem `JWT_SECRET` em development, cada processo gera a própria chave e reiniciar o Flask encerra as sessões.

Os testes padrão usam repositórios injetados e adaptadores simulados. O modo opt-in com `TEST_MONGODB_URI` verifica também concorrência e privilégios em MongoDB local real, com dados sintéticos descartáveis. Nenhum desses modos comprova persistência no Atlas ou integração React no ambiente publicado. Sessão autenticada dura 12 horas (`SESSION_TTL_SECONDS=43200`, valor fixo validado na inicialização).

Os limites de acesso são compartilhados: MongoDB em produção e SQLite persistente somente no desenvolvimento/teste sem URI. Há orçamentos por IP, global, cadastro, CSRF e login IP/conta, reservados atomicamente antes do trabalho caro. Sucesso não zera o contador. Ver tabela e semântica de janela fixa no [guia de segurança](seguranca.md). Limites 429 da aplicação têm Retry-After; falha do armazenamento gera 503. Health não comprova que os contadores ou índices estejam prontos.

## Transição planejada para sessões persistentes — D004

Implementar em conjunto política de sessão, persistência, renovação, configuração, testes e cliente React. A revisão documental não exige alterar `.env` ou `.env.example` agora. Os nomes e limites das novas configurações serão definidos na implementação; os prazos iniciais estão na arquitetura.

- Preservar sessões antigas com a expiração original de 12 horas; não ampliar documentos existentes em lote. O próximo login cria a sessão no formato novo.
- Acrescentar política atribuída pelo servidor, última renovação e limite absoluto em UTC. Calcular validade efetiva sem ultrapassar o limite absoluto; manter verificação por acesso e TTL somente para limpeza.
- Renovação exige sessão válida, CSRF e atualização condicional atômica, sem recriar sessão excluída. Cookie atualizado somente após persistência confirmada.
- Sessões comuns elegíveis devem sobreviver a fechar/reabrir o navegador e retornar em outro dia com cookie preservado. Administradores não recebem extensão automática; mudança de papel deve respeitar a nova política de permissões.
- Validar múltiplas abas, obtenção de CSRF e renovação concorrente com logout. Falha de rede/503 deve permitir recuperação sem ser apresentada como credencial inválida.
- Antes do aceite, executar testes isolados e verificar React–Flask–Atlas, cookie HTTPS, expiração e revogação no ambiente publicado. Registrar evidências próprias; esta documentação não comprova implantação ou capacidade para 5.000 usuários simultâneos.

Gestão remota de dispositivos, OAuth, MFA, passkeys e confirmação adicional específica para moderação ficam para evolução posterior.

## Preparação local da versão candidata e retorno

Esta seção prepara o procedimento; não instala serviços, cria recursos ou executa integração. Usar [verificação local](verificacao-local.md) e [roteiro de demonstração](roteiro-demonstracao.md) antes de propor publicação.

### Inventário a conferir antes da autorização

| Elemento | Modelo/caminho previsto | Verificação pendente no destino |
| --- | --- | --- |
| Backend e virtualenv | `/srv/app-cristao/backend` e `.venv` | SHA revisado, dependências fixadas, permissões de leitura/execução do usuário `appcristao` |
| Frontend | `/srv/app-cristao/frontend/dist` | Build das mesmas fontes revisadas, fallback SPA e `/api` na mesma origem |
| Serviço | [modelo systemd](../deploy/app-cristao.service) | `EnvironmentFile=/etc/app-cristao/backend.env`, Gunicorn em loopback e diretório de trabalho válidos |
| Mídia | `UPLOAD_DIR=/var/lib/app-cristao/uploads` | Volume persistente fora do código, espaço/limites, acesso do serviço e ffprobe; nenhum alias público |
| Proxy | [modelo Nginx](../deploy/nginx.conf.example) | Domínio/certificado reais, proxy confiável, limites 16 KiB/51 MiB, HTTPS e Range por rota autorizada |
| Banco | `MONGODB_URI` runtime; `MONGODB_SETUP_URI` somente operacional | TLS/rede e privilégios por coleção; índices de auth/cache/produto criados explicitamente pelo responsável |
| Sessão | `JWT_SECRET`, `APP_ORIGIN`, cookies Secure | Mesma chave por worker, origem HTTPS e TTL atual de 12h; não imprimir segredos nos registros |

Conferir os artefatos e o diff local não valida Nginx/systemd no servidor. Após instalação autorizada, os responsáveis deverão executar `nginx -t` e `systemd-analyze verify` com os caminhos reais. Não executar os comandos `init-auth`, `init-db`, `init-cache`, `set-role` ou `purge-videos` contra um banco operacional apenas para validar documentação.

### Procedimento de retorno preparado

1. Antes de publicar, registrar SHAs dos dois repositórios, dependências, configuração revisada sem valores secretos, localização da mídia e versão anterior conhecida. Preservar os artefatos de build correspondentes às duas versões.
2. Confirmar com o responsável a compatibilidade da versão anterior com os dados atuais e a existência de backup/recuperação verificados em ambiente isolado. Alteração de aplicação não autoriza restaurar banco nem apagar/migrar dados.
3. Se a candidata falhar, limitar a exposição pelo procedimento aprovado do destino e registrar o erro sem segredos. Retornar somente código/build/configuração à versão revisada anterior, mantendo `UPLOAD_DIR`, banco e segredos operacionais intactos. Não usar reset/limpeza do diretório de dados como retorno.
4. Revalidar processo/health, entrega do frontend, sessão/permissões, reprodução privada e persistência pelo roteiro autorizado. Health sozinho não comprova recuperação. Registrar versões efetivas e resultado.
5. Se os dados forem incompatíveis ou a falha exigir restauração, interromper a troca e acionar o responsável pelo plano de recuperação; ensaiar restauração apenas em base isolada antes de qualquer decisão operacional.

Nenhuma dessas ações foi executada nesta preparação. A confirmação final de TLS, índices/privilégios, concorrência MongoDB entre workers, persistência após reinício e restauração isolada continua pendente de ambiente autorizado.
