# Auditoria de segurança de consultas e proteção contra perda de dados

**Data:** 24/10/2026. **Base auditada:** `fd3ade3e22e2289bbb7e1fcdf63bc94af32332f9`. **Escopo:** código e testes disponíveis do backend, modelos de implantação, arquivos rastreados e histórico local alcançável. Frontend funcional, Atlas e EC2 não foram auditados em execução.

**Conclusão:** não foi identificado caminho HTTP de NoSQL Injection ou exclusão global nas rotas implementadas. As tentativas examinadas não alteraram contas de terceiros, privilégios ou filtros do banco. Foram reproduzidas fragilidades de controle de recursos e de tentativas de login. A resistência à perda total de dados depende também de permissões e recuperação no Atlas, ainda não verificadas. Não há evidência suficiente para declarar a plataforma pronta para exposição pública.

A auditoria inicial utilizou repositórios sintéticos em memória, SQLite temporário e cliente de teste Flask. Nenhum teste foi dirigido ao Atlas ou a um site publicado. As requisições do provedor foram bloqueadas ou simuladas; a consulta pública de vulnerabilidades recebeu somente nomes e versões de pacotes. Nessa etapa inicial, não foram alteradas dependências, regras de negócio, credenciais ou infraestrutura. As mudanças posteriores de código e dependências estão registradas a seguir. Não foram executados comandos de exclusão contra banco real.

## Atualização após correções locais

O diagnóstico abaixo preserva as evidências da base original `fd3ade3`. Após a auditoria, foram implementadas correções por pedido do usuário; portanto, seus resultados de reprodução não descrevem o estado atual. O [guia de segurança](seguranca.md) apresenta o comportamento vigente e as condições de implantação.

| Achado original | Correção local | Verificação externa ainda necessária |
| --- | --- | --- |
| SEC-01 | Orçamentos por origem/rota/global, antes de hashing/inserções; limites no modelo Nginx | Dimensionamento e configuração do proxy/Atlas reais. |
| SEC-02 | Reservas atômicas em `rate_limits`, sem apagar o orçamento no sucesso; janela fixa documentada | Concorrência MongoDB entre workers em banco isolado. |
| SEC-03 | Cabeçalhos encaminhados aceitos somente de proxy explicitamente autorizado | Topologia e bloqueio de acesso direto ao Gunicorn. |
| SEC-04 | Limite padrão de 16 KiB nas rotas atuais, no Flask e no modelo Nginx | Validação do proxy publicado; exceção futura de upload. |
| SEC-05 | Rejeição de origem HTTP/TLS inseguro em produção e restrição de Host | TLS, domínio e papéis do ambiente real. |
| SEC-06 | pip local atualizado para 26.2.2, certifi para 2026.10.19 e bootstrap com SHA-256 | Repetir atualização em colegas, imagens e servidor. |

Também foram adicionadas defesas de tipo/campos nos repositórios, cabeçalhos de proteção e separação de credencial operacional de índices em produção. Não foram alterados dados nem papéis do Atlas. A revisão de sessões persistentes continua planejada, sem implementação nesta correção.

### Evidências após as correções

- Suíte final: **242 passed in 26.21s**, com provider bloqueado/simulado e dados sintéticos. Os testes incluem 24 reservas concorrentes compartilhadas por dois adaptadores SQLite com admissão de somente dez, e doze logins concorrentes com somente dez verificações de senha e dois HTTP 429.
- Cadastro excedente não chega ao hash/inserção, novas sessões anônimas são limitadas, corpo excessivo é recusado com 413 e proxy confiável diferencia clientes. Cabeçalhos forjados de clientes não confiáveis não alteram o IP reconhecido.
- Produção rejeita origem HTTP, opções TLS inseguras, host divergente e lista de proxy vazia. A inicialização de índices não utiliza a credencial runtime quando falta a credencial operacional.
- `pip check` e `gunicorn --check-config` passaram. pip 26.2.2/certifi 2026.10.19 instalados no virtualenv do projeto. A raiz emSign ECC G3 afetada não foi encontrada nos bundles runtime ou vendorizado do pip.
- Nova consulta OSV: 22 pacotes incluindo pip, nenhum advisory retornado e nenhuma página adicional. É uma fotografia da base consultada, não garantia de inexistência de vulnerabilidades.
- Nginx não está instalado neste ambiente; seu modelo foi revisado, mas não passou por `nginx -t` aqui. Concorrência MongoDB real, papéis, índices, rede e recuperação no Atlas continuam pendentes de verificação isolada/autorizada.

## Evidências executadas

| Verificação | Resultado | Limite |
| --- | --- | --- |
| Suíte existente | 149 testes passaram em 17,59 s | Banco fake/adaptadores simulados; não certifica Atlas. |
| Novos testes adversariais | 65 testes passaram em 0,75 s | Casos descritos abaixo, sem pretensão de cobrir todos os payloads possíveis. |
| Suíte completa com os novos testes | 214 testes passaram em 18,27 s | Valida o código atual, ainda com sessões de 12 horas. |
| Consistência das dependências | `pip check`: sem requisitos quebrados | Compatibilidade declarada não implica ausência de vulnerabilidades. |
| Consulta OSV | 21 pacotes de runtime/desenvolvimento consultados; dois identificadores para o evento certifi/emSign | Alerta refinado por inspeção do bundle; não são dois incidentes independentes. |
| Varredura de segredos por padrões | 27 arquivos rastreados e 50 blobs históricos; nenhum candidato aos padrões examinados | Não prova ausência de todos os formatos de segredo; refs locais alcançáveis, sem leitura de `.env`. |
| Provas limitadas de abuso | Reprodução de criação sem limitação, corpo excessivo, corrida no login e efeito de proxy | Não foi executado teste de saturação nem medida a capacidade da produção. |

Os [65 testes adicionais](../tests/test_security_audit.py) cobrem operadores `$ne`, `$gt`, `$regex`, `$where`, `$expr` e `$or`, objetos/listas em campos escalares, mass assignment, alteração de identidade por cabeçalhos/query, IDs malformados, métodos destrutivos, entradas de SSRF, cookies/CSRF manipulados, exposição de hashes e SQL Injection no cache SQLite.

Comandos reproduzidos a partir da raiz do backend:

```bash
env -u MONGODB_URI .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m pip check
```

As fixtures especificam banco sintético ou SQLite temporário e simulam os adaptadores MongoDB. Os testes não devem ser substituídos por consultas destrutivas em um banco de demonstração ou produção.

## Inventário da superfície atual

| Recurso | Métodos de negócio implementados | Acesso e efeito relevante |
| --- | --- | --- |
| `/api/health` | GET | Público; não consulta banco ou readiness. |
| `/api/bible/passages` | GET | Público; lê/grava cache e consulta provedor fixo. |
| `/api/auth/csrf` | GET | Público; cria sessão anônima ou troca CSRF da sessão existente. |
| `/api/users` | POST | Público com CSRF; cria usuário comum. |
| `/api/auth/login` | POST | CSRF; lê credenciais, registra falhas e cria sessão autenticada. |
| `/api/auth/logout` | POST | Sessão e CSRF; exclui a própria sessão. |
| `/api/me` | GET/PATCH | Sessão; PATCH com CSRF altera somente nome/bio próprios. |
| `/api/users/{id}` | GET | Perfil público com projeção explícita de campos. |

O inventário não inclui rotas funcionais de upload, exclusão de conta/vídeo ou moderação. Contratos futuros nesses domínios precisam de nova auditoria quando implementados. HEAD e OPTIONS fornecidos pelo framework não constituem operações destrutivas adicionais.

## NoSQL Injection e exclusão pela API

### Consultas e atualizações examinadas

Em [auth.py](../app/auth.py), os campos de cadastro/login/perfil passam por verificação explícita de tipo string. Cadastro e login rejeitam campos desconhecidos; atualização de perfil permite somente `name` e `bio`. O ID de perfil público deve ter formato hexadecimal válido e é convertido para ObjectId.

Em [auth_repository.py](../app/auth_repository.py), os nomes dos campos e operadores são definidos no servidor. A atualização usa o usuário recuperado da sessão como alvo, e não um ID enviado no corpo, query ou cabeçalho. Não foram encontrados `eval`, consultas construídas por concatenação ou aceitação de pipeline/operadores MongoDB fornecidos pelo cliente no código de produção examinado.

Um texto contendo JSON permanece texto quando o campo exige string; não precisa ser proibido apenas por conter `$`. O controle central é impedir que objetos e nomes de campos fornecidos pelo cliente se tornem estrutura de consulta. Essa distinção é sustentada pela [OWASP NoSQL Security](https://cheatsheetseries.owasp.org/cheatsheets/NoSQL_Security_Cheat_Sheet.html).

### Operações que removem ou substituem dados

| Local | Operação | Restrição observada |
| --- | --- | --- |
| `auth_repository.py:101` | `sessions.delete_one` | Hash de sessão derivado do cookie; utilizado no login/logout. |
| `auth_repository.py:120` | `login_attempts.delete_many` | Chave calculada pelo servidor para a combinação IP/e-mail; apaga falhas após sucesso. |
| `cache.py` | `bible_cache.replace_one` com upsert | Chave SHA-256 calculada no servidor a partir da referência validada e tradução fixa. |
| `cache.py` | DELETE no SQLite | Remove apenas entradas vencidas; instrução e parâmetros separados. SQLite é cache local, não banco de contas. |

Não foi encontrado `drop_database`, `collection.drop`, remoção MongoDB com filtro vazio ou rota genérica de execução de comandos. Os métodos HTTP destrutivos tentados retornaram 404/405 e preservaram o estado sintético. O cache SQLite preservou registros após uma chave contendo uma tentativa de SQL Injection.

Isso demonstra o comportamento dos caminhos examinados; não garante que uma credencial Atlas exposta ou futura falha de execução de código não permita remoção por outros meios.

## Achados confirmados e prioridades

As severidades abaixo são avaliação contextual, não pontuação CVSS calculada. Não foi confirmada vulnerabilidade crítica de exclusão remota pela API atual.

### SEC-01 Alta Criação pública sem orçamento de recursos

**Evidência:** `app/api/auth.py:157`, `app/api/auth.py:177` e modelo Nginx sem limites de taxa. Vinte clientes sintéticos sem cookie criaram vinte sessões, todos com HTTP 200. Três cadastros consecutivos criaram três usuários, todos com 201. A sessão CSRF não é obstáculo contra automação que consegue obtê-la normalmente.

**Impacto:** consumo de armazenamento e operações do banco, crescimento de usuários sem TTL e trabalho de hashing de senha. O potencial de esgotamento de recursos é alto; não foi provocado esgotamento real. TTL de sessões reduz retenção posterior, mas não impede o custo e o pico de criação.

**Recomendação:** orçamento de requisições por IP/origem operacional para CSRF/cadastro, controle global de concorrência e pressão no banco, observabilidade e limites proporcionais ao piloto. Reutilizar sessão válida quando existente, como já ocorre, mas limitar clientes que sempre chegam sem cookie. Rejeitar carga excedente antes de calcular hash ou inserir usuário. Não incluir CAPTCHA ou serviço pago automaticamente.

**Aceite:** testes verificam limite e Retry-After, inclusive múltiplos workers; carga legítima do piloto continua funcionando. Medir volume de inserções e duração de hashing sem usar dados reais.

### SEC-02 Média Limite de login não é atômico

**Evidência:** `app/api/auth.py:203–212` primeiro consulta falhas e depois registra a tentativa; `auth_repository.py:105–116` não reserva a tentativa atomicamente. Na reprodução sincronizada, 12 requisições viram o mesmo estado inicial e todas retornaram 401, com 12 falhas registradas e nenhum 429, embora o limite declarado seja 10.

**Impacto:** ultrapassagem do orçamento de tentativas e maior consumo de hashing durante concorrência. A prova usou threads e snapshots de repositório fake, com verificação de senha simulada; demonstra a corrida da aplicação. Não comprova doze verificações simultâneas no deploy atual, cujo modelo tem dois workers síncronos.

**Recomendação:** reservar/contabilizar tentativas atomicamente em armazenamento compartilhado, com janela e TTL definidos. Não considerar um contador em memória por worker suficiente. Rever também a limpeza de falhas em concorrência com login bem-sucedido.

**Aceite:** teste concorrente com banco isolado comprova o limite definido entre workers e o comportamento correto no vencimento da janela.

### SEC-03 Média Proxy concentra o limite por e-mail e permite bloqueio compartilhado

**Evidência:** `app/api/auth.py:202` usa `request.remote_addr`; Nginx configura X-Forwarded-For em `deploy/nginx.conf.example:21`, mas o Flask não aplica tratamento de proxy confiável. Onze IPs encaminhados diferentes, com o mesmo REMOTE_ADDR de proxy, produziram uma única chave. Depois de dez falhas, login com a senha correta retornou 429.

**Impacto:** um cliente pode prejudicar temporariamente outro usuário atrás do mesmo proxy para um e-mail conhecido. Além disso, mudar o e-mail-alvo evita um orçamento apenas por IP/e-mail; o mecanismo atual não protege a capacidade total do serviço.

**Recomendação:** definir a topologia de proxy e aceitar cabeçalhos somente dos proxies confiáveis; manter o backend inacessível diretamente. Combinar limites por conta, origem e global, evitando que um bloqueio facilmente acionável seja a única proteção. Não confiar indiscriminadamente em X-Forwarded-For de um cliente externo.

**Aceite:** clientes encaminhados distintos são diferenciados quando deveriam, cabeçalho forjado não concede uma origem arbitrária e abuso de uma conta não derruba autenticação de todas as outras.

### SEC-04 Média Corpo de autenticação usa limite de upload

**Evidência:** `app/__init__.py:30` e `deploy/nginx.conf.example:15` admitem até 51 MiB. Cadastro com 524.380 bytes, contendo espaços antes de um nome válido, foi aceito com 201 após normalização. Não foi preciso enviar um arquivo de vídeo.

**Impacto:** alocação e processamento desproporcionais para operações pequenas; requisições podem ocupar memória e workers antes da validação semântica. Não foi medida saturação.

**Recomendação:** limites menores por rota para autenticação/perfil, coerentes no proxy e Flask; reservar o limite grande à futura rota de upload. Validar tamanho antes da leitura completa e preservar erro 413. Uma faixa como 16–64 KiB é uma proposta a dimensionar, não requisito acadêmico ou garantia de capacidade.

### SEC-05 Média Produção aceita configuração insegura sem falhar

**Evidência:** a factory aceitou APP_ENV production com APP_ORIGIN HTTP e URI MongoDB com `tls=false`. O construtor MongoClient foi substituído por mock: nenhuma conexão foi feita. Cookies Secure continuaram ativos no ensaio.

**Impacto:** uma configuração operacional incorreta pode reduzir proteção de transporte ou causar autenticação inconsistente. Não há evidência de que o Atlas ou a EC2 reais estejam assim configurados; não é um bypass remoto de configuração pelo usuário HTTP.

**Recomendação:** exigir origem HTTPS em produção e recusar opções que desativem TLS/validação de certificado. Validar URI com parser adequado, sem registrar seus componentes secretos. Verificar CA e conexão TLS na configuração real. Preservar a exceção explícita de localhost somente em desenvolvimento/testes.

### SEC-06 Alta Ferramenta pip mantém raízes de certificação afetadas

**Evidência local:** pip 24.0 do virtualenv contém certifi vendorizado 2023.07.22. A inspeção do trust store dessa cópia encontrou as quatro raízes emSign, incluindo ECC Root CA G3. O incidente upstream informa comprometimento de chaves intermediárias e risco de interceptação TLS por adversário em posição de rede. Recomenda pip 26.2.2 ou posterior. [Comunicado de segurança Python de 20/10/2026](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576).

**Impacto:** risco na cadeia de instalação/atualização de dependências. O atacante precisa de condições adicionais de rede e material de certificado comprometido; nenhuma interceptação foi executada. Não é prova de que o aplicativo foi comprometido ou que seus dados foram apagados.

**Distinção importante:** OSV retornou GHSA-6q48-6c7w-9gw2 e PYSEC-2026-4320 para certifi 2026.10.18, mas a versão usada diretamente pela aplicação não contém raízes emSign, conforme inspeção dos 117 certificados de seu bundle. O aviso do mantenedor identifica 2026.10.18 como remoção dessas raízes; Python recomenda 2026.10.19 por compatibilidade. O alerta de runtime foi, portanto, classificado como sinalização com metadados divergentes, sem exploração confirmada nesse bundle. [Aviso do mantenedor](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2), [registro agregado](https://github.com/advisories/GHSA-6q48-6c7w-9gw2).

**Recomendação:** atualizar a ferramenta pip de forma autenticada seguindo o comunicado oficial; alinhar certifi à versão recomendada e repetir testes/consulta OSV. Não usar `verify=False` nem desativar TLS. Nenhuma dependência foi alterada nesta auditoria.

## Como ainda seria possível perder o banco inteiro

Os cenários abaixo são ameaças condicionais e verificações operacionais pendentes, não vulnerabilidades confirmadas no Atlas do projeto.

| Cenário | Condição necessária | Redução de risco |
| --- | --- | --- |
| Credencial de banco vazada | Credencial válida, rede acessível e privilégios suficientes | Usuário de aplicação restrito por coleção/ação, segredo fora do Git e imagem, rotação após exposição, lista de acesso de rede restrita. |
| Comprometimento do processo/servidor | Execução de código ou acesso ao ambiente da aplicação | Limitar o que a credencial runtime pode fazer; separar credencial de inicialização de índices; atualizar ferramentas/dependências e restringir sistema operacional. |
| Conta administrativa Atlas comprometida | Acesso ao painel/API com privilégios de gestão | MFA de infraestrutura, menor número de administradores, revisão de chaves e acesso, alertas de operações destrutivas. Isso é proteção da infraestrutura, não inclusão de MFA no MVP do aplicativo. |
| Remoção de documentos em massa | Permissão `remove` ou `update` em coleções críticas, mesmo sem `dropDatabase` | Restringir ações e coleções, backups recuperáveis e teste de restauração. Negar drop não impede apagar documentos individualmente ou sobrescrevê-los. |
| TTL configurado incorretamente | Índice TTL em coleção/campo de dados permanentes | Revisão explícita dos índices reais; TTL somente em sessões, falhas e cache, como planejado. |
| Erro de operação ou chave com acesso a vários ambientes | Credencial/base compartilhada e privilégios amplos | Separar desenvolvimento/teste/produção, conferir destino dos comandos e preparar recuperação. |

Papéis de banco expressam ações permitidas e escopo, e o Atlas permite papéis personalizados. Atribuir um papel restrito além de outro amplo não retira privilégios do papel amplo. [MongoDB Custom Roles](https://www.mongodb.com/docs/atlas/security-add-mongodb-roles/).

Não tratar `readWrite` como sinônimo de “não pode apagar”: os privilégios podem incluir remoção de documentos e exclusão de coleções, conforme o papel efetivo. Verificar as ações do usuário Atlas real, pois papéis de Atlas e instalações próprias têm diferenças. [Privilégios MongoDB](https://www.mongodb.com/docs/manual/reference/privilege-actions/), [capacidades e papéis](https://www.mongodb.com/docs/compass/connect/required-access/).

### Matriz mínima proposta para o código atual

| Coleção | Necessidade runtime observada | O que separar/restringir |
| --- | --- | --- |
| users | find, insert e update de perfil | Sem remove, drop ou administração no runtime atual; inicialização do índice único em etapa operacional separada. |
| sessions | find, insert, update e remove para login/logout | Sem drop; remoção é necessária, portanto backups e controles continuam relevantes se o runtime for comprometido. |
| login_attempts | find, insert e remove de falhas após sucesso | Sem drop; mudança futura do contador pode requerer update. |
| bible_cache | find e insert/update para replace/upsert | Sem remoção direta pelo runtime atual; limpeza TTL pelo banco; índices criados operacionalmente. |

Essa matriz deve ser validada em banco isolado antes de aplicar no Atlas. Privilégios MongoDB não restringem por si sós o filtro de cada operação conforme o usuário final do app; isso continua sendo responsabilidade do backend. O fluxo atual usa a mesma configuração de conexão para runtime e comandos `init-auth`/`init-cache`, sem separação operacional implementada.

### Evidências necessárias no Atlas e AWS

- Papel e ações efetivas do usuário de banco, sem fornecer sua senha ou URI.
- Escopo de rede autorizado e proteção do acesso administrativo; backend não exposto diretamente ao público.
- Conexão TLS e validação de certificado; ausência de opções de desativação no ambiente real.
- Índices existentes: únicos de e-mail/token e TTL apenas nas coleções previstas. A factory não comprova execução de `init-auth`.
- Plano de backup adequado ao cluster e evidência de restauração em destino isolado, com perda máxima e tempo de recuperação definidos.
- Separação entre credenciais da aplicação e administração/backup. Uma cópia sujeita às mesmas credenciais destrutivas não é proteção suficiente.

Atlas oferece Backup Compliance Policy com proteção contra remoção de snapshots, mas o recurso documentado é para clusters dedicados M10+ e tem consequências de custo e gestão. Não presumir disponibilidade no plano do grupo nem ativar serviços pagos nesta auditoria. Se indisponível, planejar cópia protegida e restauração compatíveis com o piloto. [Documentação de backup protegido](https://www.mongodb.com/docs/atlas/backup/cloud-backup/backup-compliance-policy/).

Não houve inspeção autenticada desses itens. Não é possível certificar proteção contra apagamento total sem essa etapa.

## Outros controles e limites observados

| Área | Resultado |
| --- | --- |
| CSRF e origem | Operações mutáveis exigem cookie/token; origem divergente é recusada. Ausência de Origin não dispensa token. |
| Senhas | scrypt e comparação por Werkzeug; não devolvidas em perfil. Custos de hashing reforçam a necessidade de controle de taxa. |
| Sessões | Identificadores aleatórios, somente hashes no banco, expiração checada na aplicação e revogação no logout. A política 30/90 dias é apenas planejamento e não foi certificada. |
| SSRF e comandos | Provedor bíblico fixo, referência validada/codificada e redirects desativados; nenhuma execução de shell/eval encontrada no runtime examinado. |
| Respostas | JSON, no-store, nosniff e mensagens genéricas; testes não encontraram hashes ou erros internos sensíveis nos caminhos cobertos. |
| Redeploy e cabeçalhos | Modelo Nginx usa HTTPS e backend em loopback, mas não foi executado. CSP/HSTS/proteção de enquadramento não foram observados no cliente Flask nem configurados no modelo; avaliar no deploy do React. Ausência em uma API JSON não comprova XSS ou clickjacking explorável. |
| XSS | Nome/bio podem conter texto HTML/JSON; não houve execução em navegador. O frontend deve renderizar como texto e evitar HTML não confiável; ainda não existe interface funcional para auditar. |
| Logs | Modelo desativa access log da API e logs atuais são mínimos. Faltam evidências de alertas de abuso; adicionar eventos sanitizados sem senha, cookies, tokens ou URI. |
| Cadastro e privacidade | Conflito de e-mail é distinguível por 409; comportamento já previsto no contrato e capaz de revelar existência de conta. Limitar abuso e avaliar a política de mensagem, sem confundir isso com bypass de senha. |
| Dependências | Consulta OSV por versão é uma fotografia; não valida integridade dos pacotes, imagens, sistema operacional ou todas as bibliotecas vendorizadas. |

## Ordem de correção proposta

1. **Preparação segura:** atualizar ferramenta pip conforme aviso oficial, alinhar certifi e repetir auditoria de dependências. Revisar privilégios, rede e recuperação no Atlas antes de exposição pública.
2. **Proteção contra abuso:** corrigir a atomicidade do contador e a identificação de IP atrás do proxy; implementar limites para cadastro/CSRF e limites pequenos de corpo por rota.
3. **Configuração e operação:** validar HTTPS/TLS em produção, separar criação de índices de runtime, verificar índices e evidenciar restauração.
4. **Integração e evolução:** adicionar testes dos novos controles; auditar novamente frontend, upload/exclusão e moderação quando implementados. A revisão de sessões persistentes exige análise própria antes de ampliar a validade.

O diagnóstico original encontrou as fragilidades descritas; a seção de atualização registra as correções posteriores. Os 214 testes da primeira auditoria não verificavam todas as correções: a suíte de controles foi ampliada para esse propósito. Lacunas de configuração e recuperação no Atlas/AWS continuam pendentes de evidências reais. A publicação das correções no Git não comprova implantação ou aplicação dos controles no ambiente real.

## Referências

Fontes consultadas em 24/10/2026, citadas junto dos pontos correspondentes:

- [OWASP NoSQL Security](https://cheatsheetseries.owasp.org/cheatsheets/NoSQL_Security_Cheat_Sheet.html).
- [Flask Security Considerations e limites de recursos](https://flask.palletsprojects.com/en/stable/web-security/).
- [Werkzeug e hashing de senha](https://werkzeug.palletsprojects.com/en/stable/utils/).
- [MongoDB ações de privilégio](https://www.mongodb.com/docs/manual/reference/privilege-actions/) e [papéis personalizados no Atlas](https://www.mongodb.com/docs/atlas/security-add-mongodb-roles/).
- [Atlas Backup Compliance Policy](https://www.mongodb.com/docs/atlas/backup/cloud-backup/backup-compliance-policy/).
- [OSV API de consulta em lote](https://google.github.io/osv.dev/post-v1-querybatch/).
- [Comunicado Python sobre emSign](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576) e [aviso do mantenedor certifi](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2).
