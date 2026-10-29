# Modelo de dados — coleções MongoDB

**Data:** 27/10/2026. **Estado:** as 10 coleções existem no Atlas de desenvolvimento (`app_cristao_dev`) com todos os índices, criadas por `init-auth`, `init-cache` e `init-db`. Os repositórios de `videos`, `reactions`, `comments`, `reports` e `blocks` estão implementados e foram exercitados contra o Atlas por um teste de fumaça de 37 verificações (documentos criados e removidos pelo próprio script), além dos testes automatizados com adaptador simulado. As rotas HTTP que usam os repositórios estão implementadas e um fluxo completo (publicar, moderar, reagir, comentar, denunciar, bloquear, excluir) foi verificado pela API contra o Atlas. O que o teste de fumaça não cobre: concorrência real entre processos e a matriz de permissões do usuário runtime. Complementa a decisão D006 da [arquitetura](arquitetura.md), o [contrato da API](api.md) e os [controles de segurança](seguranca.md).

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
  purged_at: null | Date,              // arquivo e relações já removidos
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
| Limpeza | Para cada tombstone sem `purged_at`: apagar o arquivo, `reactions` e `comments` do vídeo e então `mark_purged`. Cada passo é idempotente, então a limpeza pode ser repetida após falha. As denúncias são preservadas para a moderação. |

### Visibilidade (um só lugar)

- Consulta: `approved_query` define o que o feed público pode listar (`approved`, não excluído, filtros de categoria/autor e autores bloqueados excluídos **antes** do limite).
- Autorização de um vídeo: `can_view(video, viewer)`: aprovado é público; pendente ou rejeitado só para o autor ou administrador; excluído para ninguém.
- Saída: `video_public` e `video_restricted` montam o JSON; `file`, `storage_key`, `review.decided_by`, `deleted_at` e `purged_at` nunca saem. `review_reason` só aparece em `video_restricted` (autor/moderação).
- Toda rota nova deve usar essas funções; não montar filtros ou JSON à mão.

## Demais coleções

| Coleção | Documento | Estado |
| --- | --- | --- |
| `users` | `_id`, `name`, `email_normalized`, `password_hash`, `bio`, `role`, `created_at` | Implementada |
| `sessions` | `token_hash`, `user_id`, `csrf_hash`, `created_at`, `expires_at` (D004 acrescentará política e renovação) | Implementada |
| `rate_limits` | contadores com `_id` hash e `expires_at` | Implementada |
| `bible_cache` | `_id` por referência/tradução, `value`, `expires_at` | Implementada |
| `login_attempts` | legada, vence por TTL; não limita mais logins | Implementada |
| `reactions` | `_id`, `user_id`, `video_id`, `created_at` | Implementada (`MongoReactionRepository`) |
| `comments` | `_id`, `video_id`, `author_id`, `text`, `created_at` | Implementada (`MongoCommentRepository`) |
| `reports` | `_id`, `video_id`, `reporter_id`, `reason`, `status` (`open`/`resolved`), `resolution_note`, `resolved_by`, `created_at`, `resolved_at` | Implementada (`MongoReportRepository`) |
| `blocks` | `_id`, `user_id`, `blocked_user_id`, `created_at` | Implementada (`MongoBlockRepository`) |
| `follows` | `_id`, `follower_id`, `followee_id`, `created_at` | Implementada (`MongoFollowRepository`); seguir a si mesmo é recusado na rota (400) |

`resolved_by` (administrador que resolveu a denúncia) não consta na tabela `Report` do contrato público e fica somente no banco.

## Índices

Declarados em `app/db.py` e criados por `flask --app app init-db`. As coleções antigas continuam usando `init-auth` e `init-cache`.

| Índice | Chave | Atende |
| --- | --- | --- |
| `videos_feed` | `status, created_at↓, _id↓` | Feed por cursor. A fila de moderação (mais antigos primeiro) lê o mesmo índice no sentido inverso. |
| `videos_feed_category` | `status, category, created_at↓, _id↓` | Feed filtrado por categoria. |
| `videos_author` | `author_id, created_at↓, _id↓` | `GET /me/videos` e perfil. |
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

| Repositório | Operações | Pontos de atenção |
| --- | --- | --- |
| Reações | `add`, `remove`, `count_for`, `reacted_by`, `delete_for_video` | `add` e `remove` são idempotentes; corrida na criação cai no índice único e não duplica. Contagens de uma página em uma só agregação. |
| Comentários | `insert`, `find_by_id`, `list_for_video`, `count_for`, `delete`, `delete_for_video` | Lista cronológica; autores bloqueados saem **antes** do limite e da contagem. Só o autor remove o próprio comentário. |
| Denúncias | `insert`, `find_by_id`, `list_by_status`, `resolve` | Segunda denúncia aberta do mesmo usuário para o mesmo vídeo levanta `ReportConflict` (409 na rota). `resolve` só age em denúncia aberta. |
| Bloqueios | `add`, `remove`, `list_for`, `blocked_ids`, `exists` | `blocked_ids` alimenta `exclude_authors` do feed e dos comentários. Bloquear a si mesmo é recusado em `check_block` (400). |

## O que ainda precisa ser feito

1. Verificar concorrência real entre processos (ex.: duas reações simultâneas do mesmo par) e a matriz de permissões do usuário runtime no Atlas.
2. Instalar o `ffprobe` e exercitar o upload com um MP4 real (o fluxo usa hoje um substituto nos testes).
3. Consumir as rotas no React.
4. Estender a matriz de permissões do Atlas (ver [segurança](seguranca.md)) e auditar novamente as rotas de upload, exclusão e moderação.
5. Opcional: histórico completo de decisões (`moderation_events`) e validação `$jsonSchema` por coleção. Hoje `review` guarda somente a decisão mais recente.
