# Arquitetura e decisões técnicas — T01

**Atualização documental:** 31/10/2026. **Estado:** backend implementa autenticação, perfis, mídia, interações/moderação e consulta bíblica/cache. O frontend base `5d79b9d` já contém autenticação, perfil/lista própria, publicação, moderação/denúncias, feed, Amém e versículo; a branch `feat/fluxos-mvp-locais-20261031` complementa visitante, gestão, comentários, bloqueios e perfil, ainda sujeita a revisão/merge. Evidências históricas de Atlas/ffprobe em 29/10 não foram repetidas nesta revisão. Integração completa e implantação continuam sem aceite atual. A política D004 de 30/90 dias é planejamento posterior; o runtime atual usa 12 horas absolutas para toda sessão autenticada. Veja [operação](operacao.md).

## Stack e organização

Preservar Flask, React web, MongoDB Atlas e AWS EC2 conforme RA01–RA05. Backend e frontend permanecem separados (RA08). Priorizar uma implantação de mesma origem: Nginx serve o build React e encaminha `/api` ao Gunicorn/Flask. Em desenvolvimento, Vite encaminha `/api` ao Flask; não habilitar CORS amplo para compensar configuração incorreta.

- Backend: Python 3.12, Flask com application factory, blueprints por domínio, PyMongo síncrono e Requests para a API externa. Testes com Pytest e cliente Flask; banco de teste isolado por interface de repositório. Gunicorn para produção.
- Frontend: React com TypeScript e Vite; npm e `package-lock.json`. Componentes por fluxo e um cliente HTTP central para sessão, CSRF e erros. Sem estado global adicional até surgir necessidade real.
- Versões do backend resolvidas na T03 e registradas em `requirements.txt`/`requirements-dev.txt`; dependências instaladas somente no virtualenv local. Versões do frontend estão fixadas em `package.json` e `package-lock.json`. Não utilizar `latest` sem registrar o resultado.
- Runtime de frontend alvo: Node 22, versão 22.12 ou superior nessa linha. Selecionar explicitamente um runtime compatível; os checks locais de 31/10 usaram Node 22.23.3. Não rebaixar Vite para acomodar um runtime antigo do PATH.

Estrutura backend criada na T03: `app/`, `tests/`, dependências, `wsgi.py` e configuração Gunicorn. Frontend possui `src/`, configuração Vite e scripts de testes, lint e build.

## Autenticação atual e planejamento D004

**Runtime atual:** JWT HS256 em cookie HttpOnly `app_session`, registro obrigatório/revogável em MongoDB e `SESSION_TTL_SECONDS=43200` (12 horas absolutas), sem renovação. Sessão anônima de CSRF dura 1800 segundos. Logout remove o registro e expira o cookie; a assinatura sozinha não autoriza uma sessão removida.

**Revogação implementada em 31/10/2026:** `auth_version` no usuário é incrementada atomicamente com mudanças operacionais de papel. Cada sessão autenticada guarda a versão do login; acessos autenticados exigem igualdade com a versão atual. Campos legados ausentes equivalem a zero. Assim, uma sessão antiga não herda privilégios novos mesmo se a limpeza física de sessões falhar, e promoção/rebaixamento exigem novo login. O cookie continua JWT HS256, com registro server-side revogável, CSRF e validade absoluta de 12 horas. A política persistente abaixo continua planejada.

**Revisão de planejamento em 24/10/2026 (não implementada):** substituir o vencimento obrigatório de 12 horas para usuários comuns por continuidade entre visitas, mantendo uma política mais restritiva para administradores. O código atual ainda exige `SESSION_TTL_SECONDS=43200` para todas as sessões autenticadas. Os prazos desta seção são decisões iniciais do piloto, configuráveis e sujeitos a avaliação; não são exigências acadêmicas nem valores ótimos comprovados pela pesquisa de mercado.

Preservar sessão persistida no MongoDB; desde 27/10/2026 o cookie carrega um JWT assinado que aponta para esse registro. A escolha permite revogação no servidor e mantém o identificador inacessível ao JavaScript por meio de cookie HttpOnly. Fechar uma aba ou o navegador não deve encerrar a sessão quando o cookie persistente for preservado. O benefício esperado é reduzir o atrito no retorno; aumento de retenção ainda depende de validação com usuários.

### Política de validade planejada

| Tipo de sessão | Validade | Renovação e justificativa |
| --- | --- | --- |
| Anônima, usada para CSRF | 30 minutos absolutos | Sem extensão automática; preservar o comportamento atual. |
| Usuário comum | 30 dias desde a última renovação válida, limitados a 90 dias desde o login | Renovação automática controlada durante uso. Acomoda retornos diários e semanais, encerra autorizações abandonadas e mantém um limite máximo. |
| Administrador | 12 horas absolutas desde o login | Sem renovação deslizante. Preserva uma janela menor para contas com poder de moderação, utilizando o fluxo de login existente. Prazo provisório do projeto. |

Uma sessão comum pode ser renovada sem pedir senha, mas nunca ultrapassa o limite absoluto nem pode ser renovada após vencer. Ao ultrapassar qualquer limite aplicável, é necessário novo login. A janela maior aumenta a exposição potencial de uma credencial roubada; não dispensa proteção do cookie, autorização no servidor e revogação.

O papel atual continua sendo consultado no servidor. Promover uma conta de usuário para administrador não transforma sua sessão longa em autorização administrativa: exigir novo login para estabelecer a política de administrador. A retirada do papel administrativo deve impedir novas ações de moderação mesmo que a sessão ainda seja válida.

### Proteções preservadas

- O cookie é um JWT HS256 assinado com `JWT_SECRET`: `jti` aleatório criptográfico de 256 bits, `sub` com o id do usuário quando autenticado, `iat` e `exp`. Armazenar somente o SHA-256 do token na coleção `sessions`. Assinatura e `exp` são conferidos antes da consulta, mas o registro continua obrigatório: um JWT válido sem registro (revogado no logout ou forjado com a chave vazada) retorna 401. Não usar o cookie de sessão padrão do Flask como substituto silencioso desta decisão.
- Cookie `app_session`: HttpOnly, SameSite=Lax, Path=/, sem Domain; Secure em produção HTTPS. Usar Max-Age coerente com a validade efetiva no servidor. A única exceção de Secure é o desenvolvimento explícito em localhost HTTP.
- Validar `expires_at` e o limite absoluto aplicável em cada acesso; TTL só faz limpeza, não autorização. Sessão persistente é um registro de autorização, não uma conexão HTTP ou processo reservado por usuário.
- `GET /api/auth/csrf` cria/reutiliza sessão e retorna token CSRF; resposta sem cache. Guardar no servidor o hash do token e comparar em tempo constante. React mantém token em memória, envia `X-CSRF-Token` e cookies nas requisições mutáveis.
- Todas as operações POST/PUT/PATCH/DELETE, inclusive cadastro/login/logout, exigem CSRF; a renovação futura deverá preservar essa proteção. Validar Origin nas requisições do navegador contra origem configurada, rejeitar origem divergente e nunca retornar token via CORS público. Cliente sem Origin continua obrigado a apresentar cookie e token CSRF válido.
- Cadastro não autentica automaticamente. Login invalida a sessão anterior daquele navegador, gera outra e retorna novo CSRF. Logout exclui a sessão no servidor e expira o cookie. Sessão revogada ou expirada retorna 401 em rotas autenticadas, inclusive se o cliente reutilizar cookie antigo; falha de CSRF continua retornando 403. A revogação impede novas requisições autenticadas, sem desfazer operações já autorizadas.
- Hash de senha com `generate_password_hash` do Werkzeug, usando scrypt, e verificação pela função correspondente; senha nunca é registrada em log ou devolvida.
- Papel padrão `user`; `admin` só por comando operacional autenticado no ambiente do servidor, fora do cadastro público. Ler papel atual do usuário no servidor, não confiar em campos do cliente.
- Proteção de login implementada: reservar atomicamente até 10 tentativas por combinação IP/conta (e-mail ou usuário levam à mesma conta) em janela fixa de 15 minutos, incluindo sucesso e sem zerar no sucesso; limites adicionais por IP e global. Contadores compartilhados em `rate_limits` no MongoDB em produção; SQLite persistente somente em desenvolvimento/testes sem URI, nunca memória por worker ou fallback em produção. Resposta 429 com Retry-After; falha do armazenamento retorna 503. Mensagem de credenciais inválidas não distingue usuário inexistente de senha incorreta. Orçamentos completos em [segurança](seguranca.md).

### Renovação e persistência

Preservar a coleção `sessions`: `token_hash` único, `user_id` opcional, `csrf_hash`, `created_at` e `expires_at`. Para a nova política, acrescentar `policy` (anônima, usuário ou administrador), `last_renewed_at` e `absolute_expires_at`, com datas BSON UTC. A política é atribuída pelo servidor. `created_at` representa o início da sessão após login e não é reiniciado por renovação.

Para usuários comuns, `expires_at` é o menor valor entre `last_renewed_at + 30 dias` e `absolute_expires_at`, inicialmente `created_at + 90 dias`. Para administradores, expiração efetiva e absoluta coincidem em `created_at + 12 horas`. Preservar o índice TTL de `expires_at` com `expireAfterSeconds=0`; a limpeza de contadores de tentativas também pode usar TTL.

- Prever `POST /api/auth/session/renew`, consumido pelo cliente HTTP central do React, com sessão autenticada válida, CSRF e verificação da política. Sessões administrativas não recebem extensão de prazo.
- Renovar no retorno e durante uso ativo, consolidando chamadas para evitar gravação a cada requisição. Não manter uma aba abandonada conectada por chamadas periódicas sem finalidade de uso. O intervalo de consolidação e sua tolerância sobre a janela de inatividade devem ser definidos no contrato antes da implementação.
- Atualizar o registro de forma condicional e atômica, verificando que a sessão ainda existe e não venceu. Não utilizar upsert para renovar. Logout e requisições atrasadas de renovação não podem recriar uma autorização removida.
- Emitir o cookie atualizado somente após confirmar a persistência, com validade limitada à do registro. Tratar concorrência entre abas e respostas fora de ordem sem estender o limite absoluto. Um cookie remanescente nunca autoriza acesso se o registro já tiver sido removido.
- Não ampliar silenciosamente sessões antigas de 12 horas: elas mantêm sua validade original, sem renovação pela nova política. O próximo login cria o registro no formato novo.
- Na implementação, alinhar configuração, `api.md`, `operacao.md`, escopo e exemplos de ambiente com esta revisão. Os documentos distinguem planejamento e runtime; o código continua executando a política atual de 12 horas.

### Recuperação da conta no React

Ao iniciar, o React consulta `GET /api/me` com o cookie existente e mantém um estado de verificação antes de decidir sobre a tela de login. Resposta 200 recupera a identidade e permite solicitar renovação quando aplicável; 401 indica ausência de sessão válida. Erro de rede ou 503 apresenta indisponibilidade com tentativa de recuperação, sem ser tratado como logout. O feed público continua acessível conforme suas regras.

O cliente HTTP central coordena obtenção de CSRF; a renovação de sessão ainda não existe. Abrir outra aba não deve causar falhas recorrentes por invalidação de tokens compartilhados. A implementação atual troca o token a cada GET CSRF; definir e testar o comportamento entre abas antes de integrar a renovação. Não repetir automaticamente mutações sem saber se a tentativa anterior foi executada.

### Critérios de aceite da revisão

- Fechar e reabrir o navegador com cookie preservado recupera a conta; retornos após 20 segundos, dois minutos, um dia e sete dias não pedem senha para sessão comum elegível.
- Sessões comuns vencem por inatividade ou limite absoluto; renovação não altera a data de início. Sessões administrativas vencem após 12 horas, mesmo com atividade.
- Cookie antigo e documento ainda não removido pelo TTL não autorizam sessão expirada. Sessões antigas de 12 horas não recebem extensão silenciosa.
- Logout torna o cookie antigo inválido no servidor; renovação concorrente, repetida ou atrasada não ressuscita a sessão.
- Múltiplas abas funcionam com recuperação controlada de CSRF. Falhas de rede e indisponibilidade de banco não são apresentadas como credenciais incorretas.
- Papéis e propriedade são verificados no backend, incluindo promoção/rebaixamento de administrador; uma sessão comum não ganha acesso administrativo sem novo login.
- Testes de rotas cobrem validade, renovação, logout e concorrência. Verificar também o fluxo real React–Flask–Atlas e a persistência após reabrir o navegador; testes isolados não substituem essa evidência.

### Limite desta entrega

A revisão refina RF01 e preserva Flask, React, Atlas, consumo da API bíblica no backend e deploy na EC2. O [enunciado do Projeto 3](https://insper.github.io/ProgramacaoEficaz/projetos/projeto3/) exige autenticação nas duas camadas para o conceito A, mas não determina JWT, OAuth ou prazo de sessão.

Tela de dispositivos conectados, encerramento remoto de outras sessões, Google/Apple, MFA, passkeys, confirmação adicional específica para moderação e detecção comportamental ficam para evolução posterior. Capacidade para 5.000 usuários simultâneos também não integra o aceite acadêmico: depende de dimensionamento e testes próprios, não desta política de sessão. Priorizar os fluxos do MVP, testes e implantação completa.

## Upload e entrega de mídia — D005

Para o piloto, arquivos em `instance/uploads` localmente e volume persistente dedicado na EC2. Nunca no diretório público do React, no Git ou como binário no MongoDB.

- Um arquivo por requisição; limite do arquivo **50 MiB** (52.428.800 bytes). Limite total multipart **51 MiB** (53.477.376 bytes), já aplicado somente na rota de upload pelo Flask e na localização exata `/api/videos` do modelo Nginx. Fora do upload, o corpo continua limitado a 16 KiB. O modelo ainda precisa ser validado no destino.
- MP4 real com vídeo H.264, áudio AAC opcional e duração maior que zero e até 90 segundos. Nenhuma transcodificação nesta entrega.
- Validar nome/formato declarado, tamanho real e metadados com `ffprobe`; não aceitar só extensão/MIME. Não extrair arquivos, usar shell para nomes enviados ou aceitar caminhos fornecidos pelo cliente. `ffprobe` com timeout de 10 segundos e saída limitada; ausência da ferramenta gera indisponibilidade controlada (503), sem aceitar arquivo sem validação.
- Escrever progressivamente em área temporária, validar e mover com nome UUID gerado pelo servidor; limpar temporários em falha. Metadados incluem duração, codecs, bytes e localização privada. Não expor caminho de disco.
- `GET /api/videos/{id}/media` verifica as mesmas permissões do detalhe e só então entrega `video/mp4` com suporte a Range. O HTML `<video>` usa essa rota; não expor alias público que contorne a revisão.
- Exclusão lógica imediata retira o vídeo das consultas; remoção física e de relações é idempotente e retomável em caso de falha. Tombstone de exclusão não é um novo estado editorial público.
- Edição de metadados e nova aprovação invalidam imediatamente a visibilidade anterior; não usar cache público da mídia no piloto. Bloqueio é filtro por usuário autenticado e não impede acesso público anônimo a vídeo aprovado, coerente com o escopo.

Sem `ffprobe`, continuam possíveis testes com subprocesso simulado, formulários e regras; teste real de upload fica pendente até disponibilizar a ferramenta. Documentar codecs aceitos na UI antes de upload.

## Persistência e consultas — D006

Usar ObjectId no banco e IDs hexadecimais de 24 caracteres como strings na API. Datas BSON UTC; serialização ISO 8601 com `Z`. Nunca enviar senha, hashes de sessão ou caminhos internos.

O documento de `videos`, os campos técnicos do arquivo, o tombstone de exclusão e a lista completa de índices estão em [modelo de dados](modelo-de-dados.md), que prevalece sobre esta lista quando divergirem.

Índices iniciais: `users.email_normalized` único; `sessions.token_hash` único e TTL separado; `videos(status, created_at desc, _id desc)`; `videos(author_id, created_at desc, _id desc)`; `reactions(user_id, video_id)` único; `blocks(user_id, blocked_user_id)` único; `comments(video_id, created_at, _id)`; `reports(status, created_at, _id)`. Nomear índices e criá-los por rotina explícita, sem apagar dados.

Listagens têm limite padrão 10, máximo 30, e cursor estável. Vídeos ordenados por `created_at desc, _id desc`; cursor carrega último par e contexto do filtro. Mudanças de categoria exigem reinício da paginação. Aplicar visibilidade/bloqueio antes do limite; bloqueios novos entre páginas podem reduzir resultados, sem promessa de snapshot imutável.

Reações são documentos únicos; comentários e denúncias ficam separados. Contadores podem ser calculados inicialmente, evitando sincronização prematura de cópias. Atlas é obrigatório para a verificação final; fake ou MongoDB local não substituem essa evidência.

## API externa e infraestrutura — D007

Consulta bíblica implementada pelo Flask com bible-api.com, tradução Almeida, validada em prova real na T03. Usar URL base fixa e parâmetros codificados, sem aceitar URL do cliente. Timeout de conexão 2 segundos, leitura 5 segundos, cache por referência/tradução por 24 horas e nenhuma repetição automática em cascata. Sem redirects e com resposta limitada a 256 KiB. MongoDB mantém cache compartilhado quando configurado; sem URI, apenas em desenvolvimento/teste, SQLite persistente oferece cache local compartilhado entre workers. Produção exige MongoDB e não faz fallback. Esse SQLite não substitui Atlas para dados do produto nem comprova RA03.

Sem cache válido e com falha externa: 503; resposta inválida do provedor: 502; passagem inexistente: 404. Reprodução de vídeo não depende da consulta bíblica. Não marcar RA07 concluído com resposta simulada.

Nginx + Gunicorn + build React na mesma EC2, HTTPS em produção, uploads persistentes fora do diretório de código. Modelos de configuração já existem; instalação, validação no destino e URL pública continuam pendentes em T12. Não criar recursos pagos nem inferir acesso AWS nesta tarefa.

### Controles de segurança implementados após auditoria

Consultar [segurança](seguranca.md) e [auditoria](auditoria-seguranca-2026-10-24.md). A factory exige origem HTTPS e transporte MongoDB seguro em produção, restringe Host ao domínio configurado e aceita X-Forwarded-For/Proto de um único proxy somente se o endereço de conexão estiver na lista `TRUSTED_PROXY_IPS`. Nginx sobrescreve esses cabeçalhos; Gunicorn permanece em loopback. O modelo inclui limites de taxa, conexões e cabeçalhos de proteção, ainda dependentes de verificação no servidor.

Repositórios rejeitam seletores não escalares e atualizações de perfil fora dos campos permitidos. A aplicação web utiliza credencial restrita por coleção/ação; em produção, inicialização de índices exige `MONGODB_SETUP_URI` separada, fornecida somente ao processo operacional. O código não configura esses papéis no Atlas e não comprova backup/restauração. Esses itens são condições operacionais a verificar antes de exposição pública.

Instalar pip pelo bootstrap fixado com SHA-256 antes das demais dependências; certifi foi atualizado para 2026.10.19. A correção local não atualiza automaticamente computadores de colegas, imagens ou EC2. Estes controles não implementam a política planejada de sessões persistentes.

## Fontes técnicas consultadas em 19/10/2026

- [Flask — segurança](https://flask.palletsprojects.com/en/stable/web-security/): cookies, CSRF e limites de requisição.
- [Werkzeug — hash de senhas](https://werkzeug.palletsprojects.com/en/stable/utils/#werkzeug.security.generate_password_hash).
- [MongoDB — TTL](https://www.mongodb.com/docs/manual/core/index-ttl/): remoção não imediata; checar expiração na aplicação.
- [Vite — pré-requisitos](https://vite.dev/guide/): Node 20.19+ ou 22.12+; a decisão local é a linha 22.
- [FFprobe](https://ffmpeg.org/ffprobe.html): inspeção de contêiner e streams.

As escolhas de limites, duração de sessão e organização são decisões deste projeto, não valores impostos por essas fontes.
