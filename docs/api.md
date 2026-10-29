# Contrato da API — v0.7

**Data:** 24/10/2026. **Estado:** `/api/health` e `/api/bible/passages` implementadas e verificadas na T03. Autenticação implementada com verificação local isolada: GET `/api/auth/csrf`, POST `/api/users`, POST `/api/auth/login`, POST `/api/auth/logout`, GET/PATCH `/api/me` e GET `/api/users/{id}`; integração Atlas e React pendentes. O código mantém sessões autenticadas de 12 horas. A política persistente e a rota de renovação desta versão são planejadas, ainda não implementadas. As rotas de vídeo, feed, mídia, reações, comentários, denúncias, bloqueios e moderação também estão implementadas (ver "Estado implementado das rotas de vídeo, interações e moderação"). Referência: [arquitetura](arquitetura.md) e RF01–RF13 do [escopo](escopo-e-requisitos.md).

## Convenções

Prefixo `/api`. JSON UTF-8, exceto upload multipart e mídia. Identificadores hexadecimais de 24 caracteres; data ISO 8601 UTC. Entrada inválida: 400; recurso ausente/invisível: 404. Campos desconhecidos ou protegidos no payload são rejeitados com 400, nunca aplicados diretamente como operadores MongoDB.

Resposta individual: `{"data": {...}}`. Listagem: `{"data": [], "pagination": {"next_cursor": null, "has_more": false}}`. Limite padrão 10, intervalo 1–30; cursor opaco inválido ou usado com filtro diferente: 400. Sem resultados: 200 com lista vazia. Exclusão bem-sucedida: 204 sem corpo.

Erro: `{"error": {"code": "validation_error", "message": "Dados inválidos.", "fields": {"email": "E-mail inválido."}}}`. `fields` é opcional. Não expor stack trace ou dados sensíveis.

Demais códigos: 401 sem sessão autenticada válida; 403 CSRF inválido ou ação conhecida sem permissão; 409 conflito/duplicação; 413 limite de upload; 415 tipo de conteúdo não aceito; 422 mídia ilegível ou fora dos limites; 429 limite de tentativas; 502 resposta inválida de provedor; 503 dependência indisponível. JSON inválido: 400. Erro inesperado: 500 com mensagem genérica.

Todos os métodos mutáveis exigem cookie e `X-CSRF-Token`; isso inclui rotas públicas de cadastro e login. Sessão sem autenticação não autoriza interações. Respostas de sessão/CSRF/usuário próprio: `Cache-Control: no-store`. O cookie `app_session` é um JWT HS256 HttpOnly; o React não o lê nem o envia em cabeçalho `Authorization`.

**Proteção de recursos implementada:** corpo das rotas atuais limitado a 16 KiB, com 413 em excesso. API, exceto health: 120 requisições/IP/minuto e 1.000 globais/minuto; CSRF: 30/IP/minuto; cadastro: 5/IP/15 minutos; login: 60/IP/15 minutos e 10/IP/conta (ou identificador, se a conta não existe)/15 minutos; consulta bíblica: 60/IP/minuto. Janelas fixas UTC, reserva atômica em armazenamento compartilhado e Retry-After no 429 do Flask. Sucesso também consome orçamento. Proxy pode devolver 413/429 fora do envelope JSON; tratar pelo status. Detalhes e limitações em [segurança](seguranca.md).

## Objetos públicos

| Objeto | Campos |
| --- | --- |
| UserPublic | `id`, `name`, `username` (string ou null), `bio`, `created_at` |
| UserSelf | UserPublic + `email`, `role` |
| Video | `id`, `author` (UserPublic), `caption`, `category`, `bible_reference` (string ou null), `status`, `created_at`, `updated_at`, `duration_seconds`, `media_url`, `reaction_count`, `comment_count`, `reacted_by_me` |
| VideoOwn/Moderation | Video + `review_reason` (string ou null) |
| Comment | `id`, `video_id`, `author` (UserPublic), `text`, `created_at` |
| Report | `id`, `video_id`, `reporter_id`, `reason`, `status`, `resolution_note`, `created_at`, `resolved_at` |

Coleções internas não definem diretamente o JSON público. Autor e administradores podem visualizar estados restritos; o público recebe somente aprovados e nunca notas internas.

## 1. Autenticação e perfis — RF01/RF08

| Método e rota | Entrada | Acesso | Sucesso |
| --- | --- | --- | --- |
| GET `/auth/csrf` | Sem corpo | Público | 200, `data.csrf_token`; cookie criado se necessário |
| POST `/users` | `name`, `username` (opcional), `email`, `password` | Público + CSRF | 201, UserSelf sem sessão autenticada; Location `/api/users/{id}` |
| POST `/auth/login` | `login` (e-mail ou nome de usuário) ou `email`, e `password` | Público + CSRF | 200, `data.user` UserSelf e `data.csrf_token`; cookie rotacionado |
| POST `/auth/logout` | Sem corpo | Autenticado + CSRF | 204, revogação no servidor e cookie expirado |
| GET `/me` | Sem corpo | Autenticado | 200, UserSelf |
| PATCH `/me` | `name` e/ou `bio` | Autenticado + CSRF | 200, UserSelf atualizado |
| GET `/users/{id}` | Sem corpo | Público | 200, UserPublic |
| GET `/me/videos` | `status` opcional, `cursor`, `limit` | Autenticado | 200, lista VideoOwn |
| GET `/me/profile` | Sem parâmetros | Autenticado | 200, `data.user` (UserSelf) e `data.counts` (`videos`, `amens_received`, `reacted`, `followers`, `following`) |
| GET `/me/reacted-videos` | `cursor`, `limit` | Autenticado | 200, lista Video que o usuário reagiu com "Amém", reação mais recente primeiro |
| GET `/me/followers` | `cursor`, `limit` | Autenticado | 200, lista UserPublic de quem segue o usuário, mais recente primeiro |
| GET `/me/following` | `cursor`, `limit` | Autenticado | 200, lista UserPublic de quem o usuário segue, mais recente primeiro |
| PUT `/users/{id}/follow` | Sem corpo | Autenticado + CSRF | 200, `data.following=true`; repetir não duplica; a si mesmo: 400; inexistente: 404 |
| DELETE `/users/{id}/follow` | Sem corpo | Autenticado + CSRF | 204, inclusive se não seguia |

Validações: nome 2–80 caracteres após trim; bio até 300; e-mail obrigatório, até 254 caracteres, normalizado com trim/lowercase e índice único; senha entre 12 e 128 caracteres, sem trim ou truncamento. Senha deve ser tratada como string; e-mail não aceita objetos ou operadores de consulta. Nome de usuário opcional no cadastro: 3–30 caracteres entre `a-z`, `0-9`, `.` e `_`, sem começar ou terminar com ponto nem `..`, normalizado com trim/lowercase e índice único parcial (contas antigas sem o campo ficam com `username` null). E-mail duplicado: 409 `email_conflict`; usuário duplicado: 409 `username_conflict`. Login: com `@` busca por e-mail, sem `@` por nome de usuário; o limite por IP+identificador conta pela conta quando ela existe, então alternar e-mail e usuário não amplia as tentativas. Cadastro não aceita `role`; falha de login retorna mensagem genérica e 401. Editar e-mail/senha não faz parte deste contrato.

Fluxo React planejado: obter CSRF → cadastrar → fazer login → atualizar token em memória → GET `/me`. Ao reabrir a página, consultar `/me` com o cookie preservado antes de mostrar login; 200 recupera a identidade, 401 indica ausência de sessão válida e erro de rede/503 apresenta indisponibilidade com nova tentativa. Obter CSRF para alterações e renovação, coordenando as chamadas entre abas. Logout limpa estado React após resposta; erro de rede não comprova revogação, portanto informar falha e permitir tentar novamente. Não guardar senha nem identificador de sessão no localStorage.

### Perfil do usuário logado

Nenhuma rota de perfil recebe o id do usuário: ele vem sempre da sessão, e qualquer parâmetro de consulta desconhecido (por exemplo `user_id`) devolve 400. Contadores: `videos` conta só os aprovados e não excluídos; `amens_received` soma as reações "Amém" nesses vídeos; `reacted` é quantas reações o usuário deu; `followers` e `following` ignoram quem o usuário bloqueou, igual às listas. `GET /me/reacted-videos` mostra só vídeos aprovados e de autores não bloqueados; a página pode vir com menos itens que `limit` quando um vídeo reagido deixou de ser visível, então use `has_more` para saber se há mais. Seguir e deixar de seguir têm limite de 30 por minuto.

### Estado implementado da autenticação

GET `/auth/csrf`, POST `/users`, POST `/auth/login`, POST `/auth/logout`, GET/PATCH `/me` e GET `/users/{id}` estão implementadas. Cada GET CSRF válido emite um novo token, invalidando o anterior, sem estender a expiração absoluta da sessão anônima de 30 minutos. Outra aba que mantenha token antigo precisará consultar CSRF novamente. Origem divergente de `APP_ORIGIN` é rejeitada nas duas rotas; cliente sem Origin ainda precisa do cookie e token no cadastro. Cadastro não adiciona identidade autenticada à sessão.

Contas e sessões exigem MongoDB e inicialização dos índices por `init-auth`, conforme [operação](operacao.md). Sem configuração ou com persistência indisponível, as rotas novas retornam 503 quando precisam acessar o repositório. Não há armazenamento local alternativo de contas. Login exige CSRF, aceita somente `login` (ou `email`, pelo contrato anterior) e `password` (identificador com trim/lowercase até 254 caracteres: com `@` é e-mail, sem `@` é nome de usuário; senha sem alteração até 128) e responde 200 com `data.user` e novo `data.csrf_token`. A sessão anterior (anônima ou não) é apagada e outra é criada com cookie `app_session` de 12 horas, duração absoluta e sem renovação. Falhas retornam 401 `invalid_credentials` com mensagem idêntica para e-mail ou usuário inexistente e senha incorreta. O orçamento agora é de 10 tentativas por janela fixa de 15 minutos para a combinação IP/conta (IP/identificador quando a conta não existe), incluindo sucesso, reservado antes do hash e sem ser zerado no sucesso. Os contadores novos usam `rate_limits`, com TTL, no MongoDB; somente desenvolvimento/teste sem URI usa SQLite para esses contadores, nunca para contas. IP encaminhado só é considerado de um endereço explicitamente configurado em `TRUSTED_PROXY_IPS`; o proxy deve sobrescrever o cabeçalho e impedir acesso direto ao backend.

Logout exige sessão autenticada e CSRF; apaga a sessão no servidor e expira o cookie (204). Sem login: 401; sem CSRF: 403. `GET /me` não exige CSRF, lê o papel atual no servidor e retorna 401 para sessão ausente, expirada, revogada ou de usuário removido. `PATCH /me` aceita somente `name` (2–80) e/ou `bio` (até 300), com trim; qualquer outro campo, inclusive `email`, `role` e `password`, retorna 400. `GET /users/{id}` é público e retorna somente UserPublic; identificador malformado retorna 400 e inexistente 404.

React e prova Atlas permanecem pendentes.

### Revisão planejada de sessões persistentes — D004

Esta seção substitui o planejamento de validade geral de 12 horas, sem alterar o comportamento implementado descrito acima. Preservar os corpos de login e os objetos UserSelf/UserPublic.

| Sessão | Validade planejada |
| --- | --- |
| Anônima para CSRF | 30 minutos absolutos, sem extensão automática. |
| Usuário comum | 30 dias desde a última renovação válida, com máximo de 90 dias desde o login. |
| Administrador | 12 horas absolutas, sem renovação deslizante. |

Os prazos são parâmetros iniciais do piloto. O servidor atribui a política e consulta o papel atual. Promoção para administrador exige novo login antes de autorizar moderação; retirada do papel impede novas ações administrativas.

| Método e rota planejada | Entrada | Acesso | Sucesso |
| --- | --- | --- | --- |
| POST `/auth/session/renew` | Sem corpo | Autenticado + CSRF | 200, `data.expires_at` e `data.absolute_expires_at` em ISO 8601 UTC; cookie atualizado quando necessário |

A rota renova somente sessões comuns válidas no novo formato. Para sessão administrativa válida, retorna os prazos existentes, sem estendê-los. Sessões antigas de 12 horas mantêm seu vencimento original até novo login; não ampliar seu prazo silenciosamente. Sessão ausente, expirada, revogada ou vinculada a usuário removido não pode ser renovada. Aplicam-se 401 para falta de autenticação, 403 para falha de CSRF/origem e 503 para persistência indisponível; como nas rotas atuais, uma falha de CSRF pode ser detectada antes da falta de autenticação.

Atualizar atomicamente apenas registro existente e válido, sem upsert; logout concorrente não pode ser desfeito por renovação atrasada. `expires_at` nunca ultrapassa `absolute_expires_at`. Emitir cookie coerente com o banco somente após confirmação da persistência. Manter `Cache-Control: no-store`; não devolver identificadores secretos nem hashes internos.

O React solicita renovação no retorno e durante uso ativo, consolidando chamadas. Definir intervalo e tolerância de consolidação antes da implementação, conforme arquitetura. Tratar múltiplas abas, rotação de CSRF e respostas fora de ordem; não repetir mutações automaticamente após falha de rede de resultado desconhecido.

Tela de dispositivos, encerramento remoto de outras sessões, OAuth, MFA, passkeys e confirmação adicional específica para moderação não fazem parte desta revisão do MVP. A proteção administrativa inicial utiliza o login existente com prazo menor.

## 2. Feed, publicação e mídia — RF02–RF05

| Método e rota | Entrada | Acesso | Sucesso |
| --- | --- | --- | --- |
| GET `/videos` | `category`, `author_id`, `cursor`, `limit` opcionais | Público/sessão opcional | 200, lista Video aprovado |
| POST `/videos` | multipart: `file`, `caption`, `category`, `bible_reference` opcional | Autenticado + CSRF | 201, VideoOwn pendente; Location do detalhe |
| GET `/videos/{id}` | Sem corpo | Visibilidade do recurso | 200, Video ou VideoOwn/Moderation |
| PATCH `/videos/{id}` | `caption`, `category`, `bible_reference`, pelo menos um | Autor + CSRF | 200, VideoOwn; aprovado volta a pendente |
| DELETE `/videos/{id}` | Sem corpo | Autor + CSRF | 204, invisibilidade imediata e limpeza retomável |
| GET `/videos/{id}/media` | Header Range opcional | Mesma visibilidade do detalhe | 200 ou 206, `video/mp4`; 416 para intervalo inválido |

Categorias fixas: `louvor`, `devocional`, `testemunho`, `humor`, `estudo-biblico`. Legenda 1–500 caracteres após trim; referência bíblica até 100, string ou null; categoria fora da lista: 400. Edição não substitui arquivo. Limites: arquivo 50 MiB, requisição 51 MiB, duração (0, 90] segundos, MP4/H.264 e AAC opcional. Detalhes de validação e armazenamento na arquitetura.

O cadastro de vídeo não depende de consultar o provedor bíblico naquele momento; referência é validada como entrada e a existência da passagem é verificada na consulta. Falha de API externa não pode impedir a publicação de vídeo sem passagem confirmada.

Feed em ordem `created_at desc, id desc`, com os filtros incorporados ao cursor. Autor consulta pendentes/rejeitados por `/me/videos`; o filtro `author_id` no feed público não amplia visibilidade. Vídeo restrito para visitante/terceiro retorna 404; autor ou administrador podem inspecioná-lo. Autor bloqueado pelo consultante é ocultado nas consultas públicas individuais e listagens, inclusive mídia. Para gerir conteúdo próprio/moderação, privilégios explícitos têm precedência sobre filtro de descoberta.

Mídia deve ser autorizada a cada pedido, não por URL pública de arquivo estático. No piloto usar `Cache-Control: private, no-store` para reduzir exposição após revisão. Não prometer recolher bytes já entregues ao navegador antes da rejeição.

## 3. Reações e comentários — RF06/RF07

| Método e rota | Entrada | Acesso | Sucesso |
| --- | --- | --- | --- |
| PUT `/videos/{id}/reaction` | Sem corpo | Autenticado, vídeo aprovado visível + CSRF | 200, `data.reacted=true`, `data.reaction_count` |
| DELETE `/videos/{id}/reaction` | Sem corpo | Autenticado, vídeo aprovado visível + CSRF | 204, inclusive se não houver reação |
| GET `/videos/{id}/comments` | `cursor`, `limit` | Vídeo aprovado visível | 200, lista Comment |
| POST `/videos/{id}/comments` | `text` | Autenticado, vídeo aprovado visível + CSRF | 201, Comment; Location do comentário |
| GET `/comments/{id}` | Sem corpo | Vídeo aprovado visível e autor do comentário não bloqueado | 200, Comment |
| DELETE `/comments/{id}` | Sem corpo | Autor do comentário + CSRF | 204 |

Texto de comentário 1–1000 após trim. Comentários em ordem `created_at asc, id asc` com cursor próprio. Filtrar autores bloqueados antes da paginação; `comment_count` reflete comentários visíveis ao consultante. Repetir PUT de reação não duplica; índice único garante a regra. Não permitir novas interações em vídeo pendente/rejeitado, mesmo para o autor. O autor de comentário pode excluí-lo mesmo se o vídeo deixou de ser público.

## 4. Denúncias e bloqueios — RF09/RF10

| Método e rota | Entrada | Acesso | Sucesso |
| --- | --- | --- | --- |
| POST `/videos/{id}/reports` | `reason` | Autenticado, vídeo aprovado visível + CSRF | 201, `data.id`, `data.status=open`; sem Location público |
| GET `/me/blocks` | `cursor`, `limit` | Autenticado | 200, lista `user` UserPublic e `created_at` |
| PUT `/me/blocks/{user_id}` | Sem corpo | Autenticado + CSRF | 200, `data.blocked=true` |
| DELETE `/me/blocks/{user_id}` | Sem corpo | Autenticado + CSRF | 204, inclusive se não houver bloqueio |

Motivo 10–1000 caracteres após trim. Denúncia cria revisão e não altera estado editorial. Rejeitar bloqueio de si próprio com 400; usuário inexistente: 404. PUT repetido não duplica. Bloqueio é unilateral, não remove conta ou conteúdo globalmente; visitantes anônimos continuam vendo conteúdo aprovado. Denúncias completas são restritas ao painel administrativo.

## 5. Moderação — RF11/RF12

| Método e rota | Entrada | Acesso | Sucesso |
| --- | --- | --- | --- |
| GET `/moderation/videos` | `status=pending` padrão, `cursor`, `limit` | Admin | 200, lista VideoOwn/Moderation |
| PATCH `/moderation/videos/{id}` | `status=approved` ou `rejected`, `reason`, `expected_updated_at` | Admin + CSRF | 200, VideoOwn/Moderation |
| GET `/moderation/reports` | `status=open` padrão, `cursor`, `limit` | Admin | 200, lista Report |
| PATCH `/moderation/reports/{id}` | `status=resolved`, `resolution_note` | Admin + CSRF | 200, Report |

Motivo de revisão e nota de resolução: 10–1000 caracteres após trim. Decisão de vídeo exige `expected_updated_at`; atualização condicional que perdeu corrida com edição do autor retorna 409 e pede recarregar. Isso evita aprovar metadados diferentes dos revisados. Aprovar pendente, rejeitar pendente/aprovado e reavaliar rejeitado são ações administrativas; histórico deve registrar autor da decisão e data, sem mudar o autor do vídeo.

Resolver denúncia não implica rejeitar vídeo automaticamente. Para remover conteúdo denunciado, administrador usa PATCH de vídeo e depois registra resolução da denúncia. Usuário comum recebe 403 no painel. Listagens administrativas usam data crescente para priorizar itens antigos, com cursor estável.

## 6. Passagens bíblicas — RF13

GET `/bible/passages?reference=John%203%3A16&translation=almeida`, público. `reference` obrigatório 1–100 caracteres; tradução permitida no piloto: somente `almeida` (padrão quando omitida). Parâmetros desconhecidos ou repetidos retornam 400.

Implementado na T03: formato de livro, capítulo e versículo/faixa, com espaços normalizados. Aliases de João sem/com acento e Salmo/Salmos/Psalm/Psalms são normalizados; demais nomes são encaminhados ao provedor sem promessa de reconhecimento. A prova real confirmou `João 3:16` e `Salmos 23:1`. Para Salmos, usa-se o identificador `psa`, pois os nomes ingleses testados retornaram 404 no provedor. Não aceita URL arbitrária ou referências vazias.

Resposta 200: `{"data": {"reference": "...", "translation": "almeida", "text": "...", "source": "bible-api.com", "cached": false}}`. Referência inexistente: 404; timeout, limite ou indisponibilidade externa: 503; payload externo inválido: 502. Provedor e URL base não são parâmetros do cliente.

Cache de 24 horas checado a cada consulta; falhas não são armazenadas. Cache indisponível retorna 503 (`cache_unavailable`), sem desviar para outro armazenamento. Acesso ao provedor usa conexão de até 2s, leitura de até 5s entre dados, sem retries/redirects e corpo máximo de 256 KiB. Esses timeouts não equivalem a limite absoluto de duração total; Gunicorn também tem timeout operacional. Respostas HTTP têm `Cache-Control: no-store`; `cached` indica o cache do servidor, não do navegador.

## 7. Verificação de operação

GET `/health`, público: 200 com `data.status=ok` e sem segredos quando o processo está de pé. Não consulta Atlas e não comprova readiness. Verificação de Atlas deve ser registrada separadamente, sem expor URI ou detalhes internos em endpoint público.

## Estado implementado das rotas de vídeo, interações e moderação

Todas as rotas das seções 2 a 5 estão implementadas (`app/video_api.py`, `app/social_api.py`, `app/moderation_api.py`), com testes automatizados e um fluxo completo verificado contra o Atlas de desenvolvimento (cadastro, publicação, moderação, reação, comentário, denúncia, bloqueio e exclusão). O `ffprobe` real ainda não foi exercitado: o fluxo usou um substituto, pois a ferramenta não está instalada no ambiente de desenvolvimento. React ainda não consome estas rotas.

**Convenções adotadas na implementação**

- **Visibilidade:** vídeo pendente, rejeitado, excluído ou de autor bloqueado pelo consultante responde **404** a quem não pode vê-lo. Quem pode ver mas não tem permissão para a ação recebe **403** (ex.: editar vídeo aprovado de outra pessoa). Interações (reação, comentário, denúncia) só existem em vídeo aprovado e visível; em qualquer outro caso a resposta é 404, inclusive para o autor.
- **Paginação:** `limit` de 1 a 30 (padrão 10) e `cursor` opaco. O cursor só vale para a mesma listagem e os mesmos filtros; outro filtro, cursor adulterado ou parâmetro desconhecido/repetido retorna 400. `has_more` vem de uma busca de `limit + 1` itens, e `next_cursor` é `null` na última página.
- **Identificadores** malformados na URL ou na query retornam 400.
- **Publicação** (`POST /videos`): `multipart/form-data` com exatamente um campo `file` (nome terminado em `.mp4`) e os campos `caption`, `category` e, opcionalmente, `bible_reference`; qualquer outro campo é 400. Erros específicos: 415 (tipo ou extensão), 413 (arquivo acima de 50 MiB ou requisição acima de 51 MiB), 422 `invalid_media` (não é MP4 H.264, com áudio AAC opcional, de até 90 s) e 503 `media_unavailable` (sem `ffprobe`; o arquivo nunca é aceito sem validação). Em falha, o arquivo temporário e o já gravado são removidos.
- **Edição** (`PATCH /videos/{id}`): vídeo aprovado volta a pendente e perde a revisão; pendente e rejeitado mantêm o estado.
- **Exclusão** (`DELETE /videos/{id}`): 204 e invisibilidade imediata; arquivo, reações e comentários são removidos em seguida. Se a limpeza física falhar, o vídeo continua invisível e `flask --app app purge-videos` conclui o trabalho. As denúncias são preservadas.
- **Mídia** (`GET /videos/{id}/media`): mesma visibilidade do detalhe, suporte a `Range` (206/416) e `Cache-Control: private, no-store`.
- **Comentários:** autores bloqueados pelo consultante saem da lista e da contagem antes da paginação. Apagar o próprio comentário é permitido mesmo depois de o vídeo deixar de ser público.
- **Denúncias:** uma denúncia aberta por usuário e vídeo; repetir retorna 409 `already_reported`. Denunciar não altera o vídeo.
- **Moderação:** exige papel `admin` lido do banco a cada requisição (403 para usuário comum). `PATCH /moderation/videos/{id}` exige exatamente `status` (`approved` ou `rejected`), `reason` e `expected_updated_at` (instante ISO 8601 com fuso, comparado em milissegundos); 409 `conflict` se o vídeo mudou ou a transição não é permitida, 404 se não existe. Resolver uma denúncia já resolvida retorna 409.
- **Administrador:** só pode ser definido no servidor, com `flask --app app create-admin <e-mail>` sobre uma conta já cadastrada. Nenhuma rota HTTP concede o papel; o cadastro e o `PATCH /me` rejeitam `role`.

## Entrega da primeira fatia (T04)

Concluir a integração de CSRF, cadastro, login, logout, GET/PATCH `/me` e GET `/users/{id}` com formulário React e estados de erro. Implementar a revisão de sessões/renovação e testar retorno entre visitas, revogação, limites de validade, concorrência entre abas e logout, CSRF, duplicação de e-mail, rejeição de `role` e ausência de dados sensíveis. Cada rota nova deve ter consumo correspondente no React. Banco fake só valida comportamento local; Atlas e navegador conectado ao Flask precisam de evidências separadas para CA01/CA07.
