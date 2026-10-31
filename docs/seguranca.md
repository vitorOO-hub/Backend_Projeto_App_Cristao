# Controles de segurança e preparação para publicação

**Atualização:** 24/10/2026. Controles implementados no código local; verificação no Atlas, Nginx/EC2 e navegador publicado continua necessária. Este guia complementa a [arquitetura](arquitetura.md), a [operação](operacao.md) e o [contrato API](api.md).

## Consultas e proteção de dados

- O backend constrói filtros e operadores. Nunca encaminhar JSON do cliente como filtro, pipeline ou comando MongoDB; não aceitar `$where`, `$out`, `$merge`, operadores ou nomes de coleção fornecidos pelo usuário.
- Exigir tipos escalares e campos permitidos antes da consulta. Os repositórios também rejeitam seletores que sejam objetos/listas e atualizações de perfil fora de `name`/`bio` textuais. Texto que contém `$` pode continuar sendo texto legítimo.
- Obter o alvo de alterações próprias pela sessão validada. Não usar `user_id`, `_id` ou papel de cabeçalhos, query ou corpo para ampliar acesso. Validar autorização novamente em qualquer futura rota de exclusão/moderação.
- Não introduzir remoções globais, filtros vazios ou comandos administrativos na API. A exclusão da sessão usa seu hash; a conta de aplicação não deve ter privilégios de drop.
- Preservar a verificação de expiração na aplicação; TTL faz limpeza. Não criar TTL em usuários, publicações ou outros dados permanentes.

## Limites implementados

As janelas são fixas, alinhadas ao relógio UTC. Um limite de L pode admitir uma rajada próxima de 2L na transição entre janelas; não é janela deslizante. Os números são orçamentos iniciais do piloto, devem ser ajustados com testes e não constituem garantia de capacidade.

| Escopo | Orçamento por janela |
| --- | --- |
| API por IP, exceto health | 120 requisições/minuto |
| API global, exceto health | 1.000 requisições/minuto |
| CSRF por IP | 30 requisições/minuto |
| Cadastro por IP | 5 requisições/15 minutos |
| Login por IP | 60 requisições/15 minutos |
| Login por IP/conta (IP/identificador normalizado se a conta não existe) | 10 tentativas/15 minutos, incluindo sucesso |
| Consulta bíblica por IP | 60 requisições/minuto |
| Publicação de vídeo por IP | 10 requisições/15 minutos |
| Comentário por IP | 30 requisições/minuto |
| Denúncia por IP | 10 requisições/15 minutos |
| Mídia (`/videos/{id}/media`) por IP | 600 requisições/minuto, em vez do orçamento geral de 120, porque um player faz várias requisições `Range` |

O limite por origem é verificado antes de consumir o orçamento global; um IP já bloqueado não gasta novamente o orçamento global. Os contadores não são apagados no login bem-sucedido. CSRF é proteção contra ações induzidas por outros sites, não contra automação que obtém sua própria sessão.

Produção utiliza `rate_limits` no MongoDB, com reserva atômica condicional e índice `_id` único. Uma corrida na criação do contador é tratada sem ultrapassar o teto. O índice TTL de `expires_at` limita a retenção, mas a janela faz parte da chave e não depende da rapidez de limpeza. As chaves são hashes; e-mail e IP não são armazenados em texto nessa coleção.

Somente desenvolvimento/teste sem URI utiliza SQLite persistente, ao lado do cache local, compartilhado entre workers que acessam o mesmo arquivo. Produção nunca faz fallback de contadores para memória/SQLite. Se o armazenamento de limites falhar, retornar 503 em vez de permitir acesso sem limitação; `/api/health` permanece disponível e não comprova readiness.

As rotas atuais de API aceitam corpo de até **16 KiB** no Flask e no modelo Nginx. Conteúdo excedente declarado é recusado antes da leitura; a leitura de conteúdo também respeita o limite do Flask. O upload (`POST /api/videos`) é a única rota liberada para 51 MiB, no Flask (`protect_requests`) e, no modelo Nginx, em `location = /api/videos`. Qualquer outra rota continua em 16 KiB.

O Nginx modelo acrescenta limites por IP (5 req/s, burst 20), servidor (30 req/s, burst 60) e 10 requisições simultâneas por IP. A área compartilhada dos limites deve ficar no contexto `http`. Respostas 413/429 produzidas pelo proxy podem não ter o envelope JSON ou Retry-After do Flask; o cliente precisa tratar o status HTTP e oferecer nova tentativa sem repetir mutações de resultado desconhecido.

Esses limites reduzem consumo de banco/hashing; não eliminam DDoS distribuído. Redes de igreja, faculdade e operadora podem compartilhar IP e atingir limites legitimamente. Medir esse comportamento antes de ampliar público ou modificar orçamentos.

## Proxy e transporte

Em produção, `APP_ORIGIN` deve ser HTTPS e o host da aplicação deve corresponder ao domínio configurado. A factory rejeita origem HTTP, URI sem TLS explícito para `mongodb://`, opções TLS inseguras e opções repetidas de URI. `mongodb+srv://` preserva TLS padrão, sem opções de desativação. Não utilizar `tlsInsecure`, certificados inválidos, hostname inválido ou `verify=False` como solução de conectividade.

Para o modelo Nginx local, definir `TRUSTED_PROXY_IPS=127.0.0.1,::1`; produção recusa lista vazia para evitar concentrar os limites no IP do proxy por omissão. O middleware considera X-Forwarded-For/Proto de exatamente um proxy e somente quando o endereço de conexão pertence à lista explícita. Não confia em X-Forwarded-Host. Em desenvolvimento direto, deixar a lista vazia.

O Nginx deve sobrescrever X-Forwarded-For com o endereço que realmente recebeu e Gunicorn deve permanecer em loopback, sem porta pública alternativa. Não habilitar configuração de real-IP baseada em cabeçalhos de qualquer cliente. Se a topologia incluir outro proxy/load balancer, reavaliar a configuração antes de publicar.

Cookies continuam HttpOnly, SameSite=Lax e Secure em produção. O valor de `app_session` é um JWT HS256; `JWT_SECRET` tem pelo menos 32 bytes, é obrigatório em produção e fica fora do Git. Trocar a chave encerra todas as sessões. A API retorna no-store, nosniff e proteção de enquadramento; produção acrescenta HSTS. O modelo Nginx também inclui CSP para o build React de mesma origem. Validar carregamento do build e recursos com essa política antes do aceite.

## Credenciais runtime e inicialização de índices

Usar credenciais diferentes para a aplicação web e as tarefas de inicialização. Não fornecer a credencial operacional ao serviço web.

| Coleção atual | Ações runtime a validar no Atlas |
| --- | --- |
| users | find, insert, update; sem remove/drop |
| sessions | find, insert, update, remove; sem drop |
| rate_limits | find, insert, update; sem remove/drop |
| bible_cache | find, insert, update; sem drop |
| videos | find, insert, update; sem remove/drop (exclusão é lógica) |
| reactions | find, insert, update, remove; sem drop (`update_one` com upsert exige update e insert) |
| comments | find, insert, remove; sem update/drop |
| reports | find, insert, update; sem remove/drop |
| blocks | find, insert, update, remove; sem drop (`update_one` com upsert exige update e insert) |
| follows | find, insert, update, remove; sem drop (`update_one` com upsert exige update e insert) |
| upload_guards | find, insert, update; sem remove/drop |
| upload_reservations | find, insert, update; sem remove/drop/TTL; estados terminais preservam o fencing |

Não atribuir `atlasAdmin`, acesso a todos os bancos ou administração de usuários ao runtime. As permissões reais são a união de todos os papéis atribuídos; adicionar um papel restrito não remove permissões amplas existentes. Validar a matriz contra o código e o plano de cluster em banco isolado. Permissão update/remove pode permitir destruição de documentos em caso de comprometimento do processo, mesmo sem drop; backups continuam necessários.

As operações com `upsert` precisam de `update` e `insert`, inclusive quando usam somente `$setOnInsert`. Consultas com `$lookup` precisam de `find` nas coleções consultadas. Transações não substituem os privilégios das operações que contêm. A [referência de privilégios MongoDB](https://www.mongodb.com/docs/manual/reference/privilege-actions/) detalha essas exigências. Conferir os papéis reais em banco isolado; esta matriz não demonstra configuração do Atlas.

## Cotas e recuperação de uploads

O piloto usa um host e um volume local de mídia, identificado por `UPLOAD_VOLUME_ID`. Reservas de conta e volume são compartilhadas entre processos no MongoDB; não há contador privado por worker. Antes de ler multipart, a admissão reserva até 50 MiB da conta e espaço conservador para duas requisições de 51 MiB no volume, cobrindo spool e cópia. O spool multipart permanece no volume protegido. A configuração padrão limita a conta a 1 GiB e impede novos envios com dez pendentes/reservas. A projeção de ocupação deve permanecer em até 80% e manter ao menos 2 GiB livres.

Cada tentativa mantém uma trava de arquivo do sistema operacional. Uma reconciliação não pode liberar a reserva de uma tentativa que ainda mantenha essa trava. Reservas não expiram por TTL: depois de interrupção, a operação explícita de recuperação verifica a trava, o estado no banco e a existência de vídeo confirmado antes de remover temporários ou cancelar a tentativa. Estados terminais impedem uma requisição atrasada de confirmar uma reserva já cancelada. Em resultado de commit incerto, preservar mídia e reserva até reconciliação segura.

Vídeos existentes acima das cotas continuam preservados. Aprovar, editar ou excluir não depende de cota disponível; uma edição pode devolver um aprovado à fila mesmo se houver dez pendentes. A cota de bytes só deixa de considerar o arquivo após remoção física confirmada. Esses controles protegem a admissão de uploads deste serviço; não substituem monitoramento do volume nem configuram armazenamento distribuído entre hosts.

Em production, `init-auth` e `init-cache` exigem **MONGODB_SETUP_URI** com TLS, fornecida apenas ao processo operacional, além da configuração normal. Esses comandos não substituem silenciosamente a credencial operacional pela runtime. `init-auth` cria índices de usuários/sessões, contadores novos e índices legados de `login_attempts`; `init-db` cria os índices de `videos`, `reactions`, `comments`, `reports` e `blocks` e também exige `MONGODB_SETUP_URI` em produção; os registros legados vencem pelo TTL e não são mais usados para limitar novos logins.

Fornecer MONGODB_SETUP_URI por arquivo de ambiente restrito ou mecanismo de segredo do ambiente operacional, executar os comandos e retirar o segredo desse contexto. Não colar URI em terminal compartilhado, Git, logs ou variáveis de build React. O serviço systemd deve receber somente a URI runtime. A separação de código não comprova que os papéis reais foram configurados corretamente.

## Instalação de dependências

Criar o virtualenv e instalar primeiro o bootstrap fixado com SHA-256, publicado no [comunicado de segurança Python](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576). Depois instalar as dependências da aplicação:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps -r requirements-bootstrap.txt
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip check
```

Bootstrap: pip 26.2.2. Runtime: certifi 2026.10.19. A atualização local foi executada; outros computadores, imagens e EC2 precisam executar o mesmo procedimento. Não desativar TLS nem aceitar certificado inválido para atualizar. Revisões futuras devem consultar avisos oficiais e atualizar pins/hash de forma deliberada.

## Aceite antes de exposição pública

- Executar suíte isolada e testes de concorrência; verificar o limitador MongoDB entre workers em banco de teste dedicado. A prova concorrente SQLite não substitui essa evidência.
- Executar `init-auth` e `init-cache` com credencial operacional; conferir os índices e a rejeição das ações não autorizadas da credencial runtime em destino isolado, sem comandos destrutivos sobre dados reais.
- Conferir lista de acesso de rede, TLS, domínio, proxy, CSP e limites com `nginx -t` e testes de fluxo no servidor autorizado.
- Demonstrar backup e restauração em destino isolado; separar acesso de administração/backup do runtime, com retenção e perda máxima aceitáveis registradas. Recursos de backup pago exigem escolha explícita do grupo.
- Registrar eventos sanitizados de 429/503 e acompanhar taxa de criação de usuários/sessões. Não registrar senha, cookie, JWT, `JWT_SECRET`, token CSRF, URI ou corpo completo de autenticação.
- Repetir a auditoria ao alterar upload, exclusão, interações ou moderação. Manter a revisão de sessões persistentes como tarefa separada; seu prazo maior não está implementado por estas correções.

A conclusão desses itens depende de evidências reais. Este guia e os testes locais não certificam a configuração do Atlas ou a recuperação de um banco publicado.
