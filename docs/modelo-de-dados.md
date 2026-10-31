# Modelo de dados — coleções MongoDB

**Revisão:** 31/10/2026. Este inventário inclui a versão de autenticação, a coordenação transacional de interações e as coleções de reservas de upload. Criar os índices com `init-auth`, `init-cache` e `init-db` antes do uso. Testes locais isolados não comprovam que os novos campos, índices ou privilégios foram configurados no Atlas. O registro anterior relata uma prova de integração contra o banco de desenvolvimento em 29/10; essa evidência não cobre as mudanças desta revisão. Complementa a [arquitetura](arquitetura.md), o [contrato da API](api.md) e os [controles de segurança](seguranca.md).

## Princípios

- IDs `ObjectId` no banco e hexadecimais de 24 caracteres na API. Os repositórios aceitam somente `ObjectId` como identificador: string ou objeto vindo do cliente é erro de programação (`ValueError`), nunca um filtro.
- Datas BSON UTC. O MongoDB guarda milissegundos, e o Python tem microssegundos: toda data gravada passa por `to_millis` (`app/videos.py`). Sem isso, o `expected_updated_at` devolvido pelo cliente nunca bateria com o valor do banco e a moderação retornaria 409 sempre.
- O arquivo MP4 fica no armazenamento do servidor; o MongoDB guarda só a **chave relativa** (`<uuid hex>.mp4`). A raiz vem de `UPLOAD_DIR`, o que torna o dado portável entre o computador local e a EC2 e permite trocar o armazenamento depois sem alterar o banco. Caminho absoluto nunca é gravado nem exposto.
- Autor, estado, datas e dados técnicos são definidos pelo servidor. O cliente só envia `caption`, `category` e `bible_reference` (mais o arquivo).
- Contadores (`reaction_count`, `comment_count`) são calculados, não armazenados: o `comment_count` precisa respeitar os autores que o consultante bloqueou, então um contador no vídeo estaria errado. Para uma página do feed, usar uma agregação (`$group` sobre os IDs da página), e não uma consulta por vídeo.
- Nome dos índices: `<coleção>_<finalidade>`, criados por rotina explícita e sem apagar dados.

## `videos`

```js
{
  _id: ObjectId,
  author_id: ObjectId,                 // vem da sessão, nunca do corpo da requisição
  caption: "…",                        // 1–500 caracteres após trim
  category: "louvor",                  // louvor | devocional | testemunho | humor | estudo-biblico
  bible_reference: null | "João 3:16", // até 100 caracteres; vazio vira null
  status: "pending",                   // pending | approved | rejected
  file: {                              // escrito só pelo servidor; nunca sai pela API
    storage_key: "3f2c…e1.mp4",        // 32 hex + .mp4, gerado pelo servidor
    bytes: 18234567,
    duration_seconds: 42.5,            // maior que 0 e até 90
    video_codec: "h264",
    audio_codec: "aac" | null,
    width: 1080 | null, height: 1920 | null
  },
  review: {                            // decisão de moderação mais recente
    reason: null | "…",                // 10–1000 caracteres
    decided_by: null | ObjectId,       // administrador
    decided_at: null | Date
  },
  deleted_at: null | Date,             // tombstone da exclusão lógica
  file_removed_at: null | Date,        // confirmação da remoção física; libera a cota de bytes
  purged_at: null | Date,              // arquivo e relações já removidos
  interaction_version: Number,        // contador interno criado no primeiro touch transacional
  created_at: Date,
  updated_at: Date                     // muda em edição do autor e em decisão da moderação
}
```

### Regras do ciclo de vida

| Evento | Efeito no documento |
| --- | --- |
| Upload | Criado `pending`, `review` vazio, `created_at = updated_at`. |
| Edição pelo autor | Atualiza campos e `updated_at`. Se estava `approved`, volta a `pending` e `review` é zerado, numa única atualização atômica. Se estava `pending` ou `rejected`, o estado é mantido (reavaliar um rejeitado é ação administrativa). |
| Decisão do administrador | Condicional a `updated_at == expected_updated_at` e à transição: aprovar `pending`/`rejected`, rejeitar `pending`/`approved`. Grava estado, `review` e `updated_at`. Se o vídeo mudou, sumiu ou a transição é inválida, nada é alterado e a rota responde 409 (ou 404 se não existir). |
| Exclusão pelo autor | Define `deleted_at`: o vídeo some de toda consulta imediatamente. O documento permanece como tombstone. |
| Limpeza | Para cada tombstone sem `purged_at`: apagar o arquivo, confirmar `file_removed_at`, remover `reactions`/`comments` e então `mark_purged`. Cada passo é idempotente. Se a confirmação de remoção física falhar, a cobrança de bytes continua conservadora até a retomada. Denúncias são preservadas. |

A criação de comentário, reação ou denúncia executa um incremento condicional de `interaction_version` no vídeo aprovado e não excluído, na mesma transação que grava a interação. Essa escrita disputa o documento usado por edição, moderação e exclusão, impedindo que uma leitura antiga autorize uma interação após a exclusão. O contador é interno e não altera `updated_at` nem o token de revisão editorial. Transações são curtas; não incluem acesso à rede externa ou ao arquivo de mídia.

O diagnóstico `audit-orphans` consulta relações cujo vídeo não existe ou foi excluído e não modifica dados. A opção operacional explícita `purge-videos --recheck` permite rever tombstones já marcados como limpos, com limite e cursor por identificador; denúncias continuam preservadas. Isso permite diagnosticar e reconciliar relações históricas sem apagar arquivos legados automaticamente.

### Visibilidade (um só lugar)

- Consulta: `approved_query` define estado aprovado, ausência de exclusão e filtros de categoria/autor. O filtro de bloqueio usa um anti-join por consultante no banco antes do limite, sem materializar toda a lista de bloqueados no processo.
- Autorização de um vídeo: `can_view(video, viewer)`: aprovado é público; pendente ou rejeitado só para o autor ou administrador; excluído para ninguém.
- Saída: `video_public` e `video_restricted` montam o JSON; `file`, `storage_key`, `review.decided_by`, `deleted_at` e `purged_at` nunca saem. `review_reason` só aparece em `video_restricted` (autor/moderação).
- Toda rota nova deve usar essas funções; não montar filtros ou JSON à mão.

## Demais coleções

| Coleção | Documento | Estado |
| --- | --- | --- |
| `users` | `_id`, `name`, `email_normalized`, `password_hash`, `bio`, `role`, `auth_version`, `created_at` | Implementada |
| `sessions` | `token_hash`, `user_id`, `auth_version` nas autenticadas, `csrf_hash`, `created_at`, `expires_at` (D004 acrescentará política e renovação) | Implementada |
| `rate_limits` | contadores com `_id` hash e `expires_at` | Implementada |
| `bible_cache` | `_id` por referência/tradução, `value`, `expires_at` | Implementada |
| `login_attempts` | legada, vence por TTL; não limita mais logins | Implementada |
| `reactions` | `_id`, `user_id`, `video_id`, `created_at` | Implementada (`MongoReactionRepository`) |
| `comments` | `_id`, `video_id`, `author_id`, `text`, `created_at` | Implementada (`MongoCommentRepository`) |
| `reports` | `_id`, `video_id`, `reporter_id`, `reason`, `status` (`open`/`resolved`), `resolution_note`, `resolved_by`, `created_at`, `resolved_at` | Implementada (`MongoReportRepository`) |
| `blocks` | `_id`, `user_id`, `blocked_user_id`, `created_at` | Implementada (`MongoBlockRepository`) |
| `follows` | `_id`, `follower_id`, `followee_id`, `created_at` | Implementada (`MongoFollowRepository`); seguir a si mesmo é recusado na rota (400) |
| `upload_guards` | `_id` do recurso e `revision`; uma guarda por conta e uma por volume | Coordenação das reservas entre processos |
| `upload_reservations` | `_id` da tentativa, `author_id`, `volume_id`, `storage_key`, `user_bytes`, `volume_bytes`, `pending_slots`, `state`, datas e `video_id` quando confirmado | `reserved`, `recovering`, `committed`, `cancelled`; sem TTL ou remoção automática |

`resolved_by` (administrador que resolveu a denúncia) não consta na tabela `Report` do contrato público e fica somente no banco.

`auth_version` é um inteiro interno não negativo. Novas contas começam em zero; ausência em contas e sessões legadas também significa zero. `create-admin` e `set-role` incrementam a versão na mesma escrita que altera o papel. A sessão registra a versão lida no login e só autentica enquanto ela coincide com a versão atual da conta. A limpeza de documentos de sessões antigas é complementar: sua falha não restaura a autorização. Os campos de versão nunca são editáveis pelo cliente ou incluídos no JSON público. A duração atual continua em 12 horas.

As reservas de upload são criadas e conferidas sob guardas transacionais compartilhadas. A conclusão grava vídeo e estado `committed` na mesma transação. Recebimento, validação de mídia e remoção de arquivos ficam fora das transações. Estados terminais não são reaproveitados; uma confirmação atrasada não pode reviver uma tentativa cancelada. Reservas `recovering` continuam retendo recursos até a remoção física e o cancelamento confirmados. O arquivo de um vídeo confirmado nunca é removido como se fosse um upload abandonado.

## Índices

Declarados em `app/db.py` e criados por `flask --app app init-db`. As coleções antigas continuam usando `init-auth` e `init-cache`.

| Índice | Chave | Atende |
| --- | --- | --- |
| `videos_feed` | `status, created_at↓, _id↓` | Feed por cursor. A fila de moderação (mais antigos primeiro) lê o mesmo índice no sentido inverso. |
| `videos_feed_category` | `status, category, created_at↓, _id↓` | Feed filtrado por categoria. |
| `videos_author` | `author_id, created_at↓, _id↓` | `GET /me/videos` e perfil. |
| `videos_storage_key` | `file.storage_key` | Reconciliação segura de arquivo associado a vídeo confirmado. |
| `videos_purge` | `deleted_at` (parcial: só tombstones) | Localizar o que falta limpar. |
| `reactions_user_video_unique` | `user_id, video_id` único | Uma reação por usuário e vídeo, inclusive em concorrência. |
| `reactions_video` | `video_id` | Contagem por vídeo. |
| `reactions_user` | `user_id, created_at↓, _id↓` | `GET /me/reacted-videos` e o contador `reacted`. |
| `comments_video` | `video_id, created_at, _id` | Lista de comentários com cursor estável. |
| `reports_queue` | `status, created_at, _id` | Fila de denúncias. |
| `reports_open_unique` | `video_id, reporter_id` único, parcial `status = open` | Uma denúncia aberta por usuário e vídeo; duplicata deve responder 409. |
| `blocks_pair_unique` | `user_id, blocked_user_id` único | Bloqueio repetido não duplica. |
| `blocks_list` | `user_id, created_at↓, _id↓` | `GET /me/blocks`. |
| `follows_pair_unique` | `follower_id, followee_id` único | Um seguidor por par, inclusive em concorrência. |
| `follows_followers` | `followee_id, created_at↓, _id↓` | `GET /me/followers` e o contador de seguidores. |
| `follows_following` | `follower_id, created_at↓, _id↓` | `GET /me/following` e o contador de seguindo. |
| `upload_guards._id_` | `_id` | Guarda única por recurso; criação explícita da coleção na inicialização. |
| `upload_reservations_author` | `author_id, state` | Recursos e vagas reservados por conta. |
| `upload_reservations_volume` | `volume_id, state` | Espaço reservado no volume. |
| `upload_reservations_storage_unique` | `storage_key` único | Uma tentativa por chave de arquivo gerada pelo servidor. |

Não há índice TTL em dados permanentes. Os filtros dos índices parciais usam apenas operadores aceitos pelo MongoDB (`$type`, igualdade de texto).

## Repositório de vídeos

`app/video_repository.py` segue o padrão de `auth_repository.py`: `Protocol`, implementação Mongo e uma versão indisponível que nunca usa armazenamento alternativo. Falha do banco vira `DataUnavailable` (503 `data_unavailable`) sem detalhes internos.

| Método | Uso futuro |
| --- | --- |
| `insert` | `POST /videos` |
| `find_by_id` | `GET /videos/{id}`, mídia, reações e comentários (autorizar depois com `can_view`) |
| `list_approved` | `GET /videos`: categoria, autor e autores bloqueados, página `limit + 1` para `has_more` |
| `list_by_author` | `GET /me/videos` |
| `list_for_review` | `GET /moderation/videos` (mais antigos primeiro) |
| `update_metadata` | `PATCH /videos/{id}`: atualização atômica; valores entram com `$literal`, então texto iniciado por `$` nunca é avaliado como expressão |
| `decide` | `PATCH /moderation/videos/{id}` |
| `soft_delete`, `list_pending_purge`, `mark_purged` | `DELETE /videos/{id}` e a rotina de limpeza |

Cursor: tupla `(created_at, _id)` do último item entregue; a codificação opaca fica na rota. Limite de 1 a 30.

## Repositórios de interações (`app/social_repository.py`)

Mesmo padrão do repositório de vídeos: identificadores só como `ObjectId`, falha do banco vira `DataUnavailable` e, sem `MONGODB_URI`, versões "indisponíveis" que nunca usam armazenamento alternativo. Validação e JSON público ficam em `app/social.py`.

As rotas usam `MongoInteractions` para gravar reações, comentários e novas denúncias sob a transação de disponibilidade do vídeo. Os métodos genéricos dos repositórios não substituem esse serviço nas mutações de negócio.

| Repositório | Operações | Pontos de atenção |
| --- | --- | --- |
| Reações | `add`, `remove`, `count_for`, `reacted_by`, `delete_for_video` | `add` e `remove` são idempotentes; corrida na criação cai no índice único e não duplica. Contagens de uma página em uma só agregação. |
| Comentários | `insert`, `find_by_id`, `list_for_video`, `count_for`, `delete`, `delete_for_video` | Lista cronológica; autores bloqueados saem **antes** do limite e da contagem. Só o autor remove o próprio comentário. |
| Denúncias | `insert`, `find_by_id`, `list_by_status`, `resolve` | Segunda denúncia aberta do mesmo usuário para o mesmo vídeo levanta `ReportConflict` (409 na rota). `resolve` só age em denúncia aberta. |
| Bloqueios | `add`, `remove`, `list_for`, `exists` | Listagens e contagens usam filtro por consultante no banco; consultas individuais usam `exists`. Bloquear a si mesmo é recusado em `check_block` (400). |

`viewer_id` é um argumento interno dos repositórios, obtido da sessão, nunca um seletor confiado ao cliente. O helper `block_filter.py` consulta no máximo um bloqueio por candidato e o exclui antes da paginação/contagem. Isso cobre feed, comentários, curtidos e listas/contagens de seguidores; curtidos também exigem um vídeo aprovado e não excluído antes de limitar a página. Contagens globais de reações não são removidas por um bloqueio unilateral. Visitantes continuam vendo conteúdo aprovado; o acesso administrativo individual necessário à moderação é preservado.

## O que ainda precisa ser feito

1. Verificar concorrência real entre processos (ex.: duas reações simultâneas do mesmo par) e a matriz de permissões do usuário runtime no Atlas.
2. Instalar o `ffprobe` e exercitar o upload com um MP4 real (o fluxo usa hoje um substituto nos testes).
3. Verificar o fluxo React → Flask → banco publicado, incluindo persistência após recarregar a página.
4. Estender a matriz de permissões do Atlas (ver [segurança](seguranca.md)) e auditar novamente as rotas de upload, exclusão e moderação.
5. Opcional: histórico completo de decisões (`moderation_events`) e validação `$jsonSchema` por coleção. Hoje `review` guarda somente a decisão mais recente.
