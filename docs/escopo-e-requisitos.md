# Documento de Escopo e Requisitos do Projeto

**Versão:** 1.4

**Data:** 31/10/2026
**Status:** proposta de referência para validação pelo grupo e pelo professor  
**Meta acadêmica:** atender aos critérios de A+, sujeito à avaliação docente.

## 1. Objetivo e fontes

Adequar a ideia de uma rede de vídeos curtos cristãos ao Projeto 3 de Programação Eficaz, preservando seu diferencial e limitando a implementação a um MVP demonstrável.

- [Enunciado e rubrica oficiais](https://insper.github.io/ProgramacaoEficaz/projetos/projeto3/).
- Ideia original fornecida pelo grupo: vídeos verticais, feed contínuo, interações sociais e curadoria para a comunidade evangélica brasileira.
- [Backend](https://github.com/vitorOO-hub/Backend_Projeto_App_Cristao).
- [Frontend](https://github.com/vitorOO-hub/Frontend_Projeto_App_Cristao).

Este documento registra o escopo proposto; não certifica o estado de implementação dos repositórios. As decisões de produto e arquitetura abaixo são propostas do grupo, e não exigências adicionais do enunciado.

## 2. Visão do produto

Uma plataforma web responsiva de vídeos curtos cristãos, com feed por categoria, publicação sujeita à curadoria, interações básicas e consulta de versículos vinculados aos vídeos.

**Problema:** dificuldade de descobrir e compartilhar conteúdos cristãos em um ambiente com regras de convivência e revisão claras.

**Proposta de valor:** vídeos cristãos com curadoria e ferramentas de denúncia e bloqueio.

**Persona de referência:** Ana, 19 anos, líder de louvor que deseja compartilhar vídeos e encontrar conteúdo relacionado à sua comunidade.

**Público inicial:** grupo piloto de espectadores, criadores e administradores. Não haverá área infantil nesta entrega.

**Categorias iniciais:** louvor, pregações/devocionais, testemunhos, humor cristão e estudo bíblico.

**Ciclo principal:** publicar → revisar → aprovar → assistir → interagir.

## 3. Requisitos acadêmicos e rastreabilidade

| ID | Exigência | Evidência prevista |
| --- | --- | --- |
| RA01 | Servidor Flask | API executável e instruções no README |
| RA02 | REST no nível 2 de Richardson | Recursos, métodos HTTP, validações e códigos de resposta coerentes |
| RA03 | MongoDB hospedado no Atlas | Dados persistidos e recuperados pela API |
| RA04 | Cliente React integrado por HTTP | Fluxos reais de leitura e alteração de dados |
| RA05 | Deploy em EC2 na AWS | Aplicação acessível no ambiente publicado |
| RA06 | Tema validado pelo professor | Confirmação da aprovação pelo grupo |
| RA07 | Uso de API externa | Flask consulta serviço bíblico e React apresenta o resultado |
| RA08 | Repositórios separados | Backend e frontend nos respectivos repositórios |
| RA09 | Credenciais fora do Git | Variáveis de ambiente, arquivos reais ignorados e exemplos sem segredos |
| RA10 | Entrega das credenciais pelo Blackboard | Configuração necessária fornecida conforme o enunciado |

### Complementos da rubrica para a meta A+

| Critério | Entrega prevista |
| --- | --- |
| C+ | API REST nível 2, Atlas, uso das rotas pelo React e consumo de API externa pelo servidor |
| B | Servidor acessível na AWS e link da API no README |
| B+ | Testes de unidade para as rotas da API |
| A | Autenticação no React e no Flask |
| A+ | Deploy do cliente React na AWS |

**Prazo registrado na consulta anterior, não revalidado em 31/10:** apresentação em 03/11/2026; commits até 07h30 desse dia. Entrega por Blackboard e GitHub. O grupo deve confirmar o fuso do horário com a disciplina.

As contribuições individuais, coautorias quando aplicáveis e avaliações por pares também fazem parte da avaliação. A implementação deve permitir que cada integrante produza contribuições identificáveis.

## 4. Análise de lacunas

| Aspecto | Lacuna na ideia original | Adequação |
| --- | --- | --- |
| Backend | Firebase, Supabase ou Node/NestJS como alternativas | Adotar Flask como servidor principal |
| Banco | Serviços alternativos ou PostgreSQL | Usar MongoDB Atlas para os dados da aplicação |
| Cliente | Mobile nativo como prioridade e web na fase 2 | Priorizar React web responsivo |
| REST | Funcionalidades sem contrato de API | Definir recursos, métodos, respostas e permissões |
| Integração externa | Serviços citados sem integração fechada | Consultar passagens bíblicas por meio do Flask |
| Infraestrutura | Várias opções sem implantação definida | Publicar frontend e backend em EC2 |
| Credenciais | Ausência de procedimento | Variáveis de ambiente e documentação sem segredos |
| Testes | Não especificados | Cobrir rotas, autorização e falhas de integração |
| Curadoria | Promessa ampla de segurança | Definir revisão prévia, estados e responsabilidades |
| Feed | Recomendação e carregamento indefinidos | Ordenação cronológica, categorias e paginação |
| Publicação | Ciclo de vida ausente | Pendente, aprovado e rejeitado |
| Permissões | Responsabilidades implícitas | Visitante, usuário, autor e administrador |
| Demonstração | Sem estratégia de conteúdo | Preparar contas e vídeos próprios ou autorizados |
| Conclusão | Lista de recursos sem aceite | Critérios verificáveis por fluxo |

## 5. Conflitos e decisões

| Conflito ou tensão | Decisão proposta |
| --- | --- |
| Flutter não atende à exigência de React | Entregar React web |
| React Native deixa incerta a aderência ao cliente esperado | Adiar aplicativo nativo |
| Backend alternativo substitui Flask | Centralizar API e regras no Flask |
| PostgreSQL substitui Atlas | Persistir dados no MongoDB Atlas |
| API externa somente no navegador não evidencia consumo pelo servidor | Integrar pelo Flask |
| Garantia absoluta de conteúdo seguro não é verificável | Comunicar curadoria e controles, sem promessa absoluta |
| Publicação aberta pode superar a capacidade de revisão | Usar piloto controlado e aprovação prévia |
| Muitos recursos competem com os critérios avaliados | Priorizar o ciclo principal e cortar extras |
| Feed infinito e intenção de evitar consumo compulsivo | Paginação progressiva, indicação de fim e controles de reprodução |

## 6. Escopo funcional do MVP

| ID | Requisito | Condição principal |
| --- | --- | --- |
| RF01 | Cadastro, login e logout por e-mail e senha | Senhas armazenadas como hash; sessão/token invalidado ou encerrado conforme a estratégia adotada |
| RF02 | Feed vertical com filtro de categoria | Apenas vídeos aprovados, ordenados do mais recente para o mais antigo, com desempate estável e paginação sem repetição |
| RF03 | Reprodução de vídeo | Controles acessíveis; autoplay sem som quando permitido pelo navegador e reprodução manual disponível |
| RF04 | Publicação de arquivo MP4 | Arquivo, legenda e categoria validados; publicação inicia pendente |
| RF05 | Gestão dos vídeos próprios | Autor acompanha estado, edita metadados e exclui sua publicação |
| RF06 | Reação “Amém” | Uma reação por usuário e vídeo, com possibilidade de desfazer |
| RF07 | Comentários | Usuário comenta e remove seus próprios comentários |
| RF08 | Perfil básico | Nome, bio e vídeos aprovados; autor tem acesso aos próprios vídeos pendentes/rejeitados |
| RF09 | Denúncia de vídeo | Motivo obrigatório e registro disponível ao administrador |
| RF10 | Bloqueio e desbloqueio de usuários | Conteúdos e comentários do bloqueado deixam de aparecer para quem bloqueou |
| RF11 | Painel de revisão | Administrador lista pendências, aprova ou rejeita e registra motivo |
| RF12 | Tratamento de denúncias | Administrador consulta, resolve e pode retirar um vídeo do feed mediante rejeição motivada |
| RF13 | Versículo associado ao vídeo | Flask consulta API externa e React exibe passagem, referência e tradução |

### Regras de negócio

- Visitantes podem assistir a vídeos aprovados e consultar suas passagens bíblicas. Publicar e interagir exige autenticação.
- Usuários não podem alterar publicações ou comentários de terceiros.
- Somente administradores executam ações de moderação.
- Edição de legenda, categoria ou referência bíblica de vídeo aprovado devolve a publicação ao estado pendente.
- Denunciar não remove automaticamente um vídeo; a decisão cabe ao administrador.
- Um vídeo rejeitado não aparece no feed público. Seu autor pode consultar o motivo.
- Bloqueio é um filtro para quem o executa; não implica remoção global do conteúdo.
- Exclusão de um vídeo deve remover ou tratar suas relações e seu arquivo, evitando referências órfãs.
- Igreja e denominação não serão solicitadas no cadastro inicial.
- As diretrizes devem descrever condutas observáveis, como ataques pessoais, ameaças, golpes e conteúdo sexual explícito. Divergências doutrinárias não devem ser avaliadas por regras vagas.
- O conteúdo da demonstração deve ser próprio ou autorizado.

## 7. Priorização e exclusões

**Obrigatório:** RF01–RF13, integração completa entre camadas, testes, documentação e deploy das duas aplicações.

**Após concluir o núcleo:** salvar vídeos, seguir criadores, aba “Seguindo”, escolha de interesses e compartilhamento por link.

**Fora desta entrega:** login Google/Apple, gravação pela câmera, aplicativos nativos, transcodificação e streaming adaptativo, moderação automática, recomendação personalizada avançada, notificações push, lives, duetos, mensagens diretas, monetização, selos institucionais e área infantil.

Os diferenciais do MVP são a reação “Amém” e o versículo vinculado. “Orar por isso” fica para evolução posterior.

## 8. Arquitetura proposta

```text
Navegador → React → /api → Flask → MongoDB Atlas
                              → API bíblica externa
                              → arquivos de vídeo
```

| Componente | Responsabilidade |
| --- | --- |
| React | Telas responsivas, reprodução, formulários e estados de carregamento/erro |
| Flask | Autenticação, autorização, validações, regras e integração externa |
| MongoDB Atlas | Usuários, metadados, interações e moderação |
| EC2 | Hospedar frontend, proxy reverso e servidor de aplicação Flask |
| Armazenamento de arquivos | Guardar vídeos fora do MongoDB |
| Pytest | Verificar rotas e regras do backend |

Para o piloto acadêmico, os MP4 podem ficar em diretório persistente na EC2, com limite de tamanho, nomes gerados pelo servidor e validação de formato. O diretório não deve permitir execução de arquivos. S3 pode substituir esse armazenamento caso a equipe já domine a configuração.

O limite de duração é de até 90 segundos e só deve ser apresentado como garantido após implementação de validação real. Usar vídeos previamente preparados na demonstração, respeitando os limites técnicos abaixo.

**Refinamento técnico T01 e revisão D004 (v1.2):** adotados arquivo de até 50 MiB, requisição de até 51 MiB e duração de até 90 segundos, com validação ffprobe e armazenamento privado persistente na EC2. Esses controles de mídia estão implementados; o registro de 29/10/2026 relata verificação com `ffprobe` real, não repetida nesta revisão. Para RF01, o runtime atual usa JWT HS256 em cookie HttpOnly e registro revogável no MongoDB, com 12 horas absolutas. A revisão futura planeja: usuários comuns com 30 dias desde a última renovação válida, limitados a 90 dias desde o login; administradores com 12 horas absolutas, sem extensão automática. Preservar revogação no logout, CSRF e recuperação da identidade pelo React ao reabrir o site. São parâmetros iniciais do piloto, não exigências acadêmicas. O código atual ainda aplica 12 horas a todas as sessões autenticadas; a nova política permanece pendente de implementação e integração. Detalhes e justificativas em [arquitetura](arquitetura.md).

A revisão refina RF01 sem acrescentar login Google/Apple, tela de dispositivos, encerramento remoto de outras sessões, MFA, passkeys, confirmação adicional específica para moderação ou detecção comportamental ao MVP. Suporte a 5.000 usuários simultâneos não é critério de aceite desta entrega.

### Integração bíblica

Candidata: [bible-api.com](https://bible-api.com/), que documenta consulta de passagens e tradução Almeida em português. A escolha depende de uma prova de conceito com as referências da demonstração.

1. Autor associa uma referência ao vídeo.
2. Espectador solicita o texto pelo React.
3. Flask consulta cache ou provedor externo.
4. React apresenta passagem, referência e tradução.
5. Em falha, informa indisponibilidade sem impedir a reprodução do vídeo.

Implementar timeout, cache e tratamento de referência inválida. A documentação consultada informa limite de 15 requisições por 30 segundos por IP e ausência de garantia de disponibilidade; confirmar as condições antes da integração. Nos testes, simular respostas e falhas sem depender do serviço real. Na apresentação, demonstrar também uma consulta real.

### Modelo de dados mínimo

| Coleção | Dados principais |
| --- | --- |
| `users` | Nome, e-mail único, hash da senha, bio e papel |
| `videos` | Autor, legenda, categoria, localização do arquivo, referência bíblica, estado, motivo de revisão e datas |
| `reactions` | Usuário e vídeo, com índice único composto |
| `comments` | Autor, vídeo, texto e data |
| `reports` | Denunciante, vídeo, motivo, estado e decisão |
| `blocks` | Usuário e bloqueado, com índice único composto |

## 9. Contrato inicial da API

As rotas abaixo foram definidas na T01 e já estão implementadas no backend. O [contrato vigente](api.md) detalha corpos, respostas e permissões; cada operação de negócio deve ter consumo e verificação correspondentes no React.

A existência de rotas e telas não comprova integração real, persistência atual nem aceite final.

| Métodos | Recurso | Uso |
| --- | --- | --- |
| POST | `/api/users` | Cadastro |
| POST | `/api/auth/login` | Autenticação |
| POST | `/api/auth/logout` | Encerramento da sessão, conforme estratégia adotada |
| GET, PATCH | `/api/me` | Perfil próprio |
| GET | `/api/users/{id}` | Perfil público |
| GET | `/api/me/videos` | Publicações próprias em todos os estados |
| GET, POST | `/api/videos` | Feed paginado ou upload multipart |
| GET, PATCH, DELETE | `/api/videos/{id}` | Consulta, edição ou exclusão |
| PUT, DELETE | `/api/videos/{id}/reaction` | Reagir ou desfazer |
| GET, POST | `/api/videos/{id}/comments` | Consultar ou comentar |
| DELETE | `/api/comments/{id}` | Remover comentário próprio |
| POST | `/api/videos/{id}/reports` | Denunciar |
| GET | `/api/me/blocks` | Listar bloqueios |
| PUT, DELETE | `/api/me/blocks/{user_id}` | Bloquear ou desbloquear |
| GET | `/api/moderation/videos` | Listar publicações para revisão |
| PATCH | `/api/moderation/videos/{id}` | Aprovar ou rejeitar |
| GET | `/api/moderation/reports` | Consultar denúncias |
| PATCH | `/api/moderation/reports/{id}` | Registrar resolução |
| GET | `/api/bible/passages?reference=...` | Consultar passagem |

O feed aceita categoria e cursor. Consultas individuais e de comentários também devem respeitar visibilidade e bloqueios; conhecer um identificador não autoriza acessar conteúdo pendente.

Usar respostas coerentes, incluindo `201` para criação, `400` para entrada inválida, `401` para falta de autenticação, `403` para falta de permissão, `404` para recurso inexistente e `409` para conflitos como e-mail duplicado. Não retornar erros internos ou segredos ao cliente.

## 10. Requisitos de qualidade e verificação

- Aplicar os [controles de segurança](seguranca.md): consultas com campos/tipos permitidos, limites atômicos compartilhados, corpo reduzido fora de upload, proxy confiável e validação de TLS em produção. Separar credencial runtime da inicialização de índices e verificar permissões, rede e recuperação no Atlas antes de exposição pública. Esses controles refinam a qualidade do MVP, sem acrescentar funcionalidades.
- Validar permissões no Flask; esconder botões no React não é controle de acesso.
- Definir a estratégia de sessão/token e sua expiração antes de implementar autenticação.
- Proteger credenciais com variáveis de ambiente; manter `.env.example` sem valores reais e ignorar arquivos reais no Git.
- Paginar listagens e limitar uploads e entradas textuais.
- Disponibilizar estados de carregamento, erro, vazio e fim do feed.
- Documentar dependências, configuração, execução, testes e implantação em ambos os repositórios.
- Usar ambiente de teste isolado dos dados de demonstração; testes não devem alterar dados de produção.
- Cobrir sucesso e falha nas rotas, credenciais inválidas, acesso sem autenticação, ações sobre dados de terceiros, duplicações, visibilidade de vídeos e bloqueios.
- Testar timeout e resposta inválida da API externa com mocks.
- Verificar no ambiente publicado o ciclo completo e a persistência após recarregar a página.

## 11. Plano de execução

Planejamento de referência, a ajustar após avaliar o código existente e a disponibilidade dos integrantes.

| Período de 2026 | Resultado |
| --- | --- |
| 19–20/10 | Validar recorte, avaliar repositórios, fechar contrato, testar API externa e realizar primeiro deploy |
| 21–23/10 | Autenticação, Atlas e primeiro fluxo React–Flask–banco |
| 24–26/10 | Publicação, revisão, feed e reprodução |
| 27–29/10 | Reações, comentários, denúncias, bloqueios e versículos |
| 30–31/10 | Testes, correções, permissões e deploy completo |
| 01–02/11 | Congelar funcionalidades, concluir documentação e ensaiar apresentação |
| 03/11 | Entregar e apresentar, respeitando o limite de commits |

Concluir cada funcionalidade com backend, interface e verificação antes de iniciar extras. Distribuir responsáveis entre produto, backend, frontend e infraestrutura/testes conforme a equipe, preservando contribuições individuais.

## 12. Riscos e ações

| Risco | Ação |
| --- | --- |
| Escopo maior que o prazo | Congelar o MVP e adiar extras |
| Problemas de infraestrutura tardios | Fazer primeiro deploy no início |
| API externa indisponível ou limitada | Prova de conceito, cache, timeout e tratamento de erro |
| Upload e reprodução inconsistentes | Restringir formato, validar tamanho e preparar conteúdo de demonstração |
| Falta de conteúdo | Usar conjunto inicial próprio ou autorizado |
| Sobrecarga de moderação | Piloto controlado e aprovação prévia |
| Exposição de credenciais | Configuração externa e revisão antes dos commits |
| Mudanças de contrato entre equipes | Registrar contrato e integrar continuamente |

## 13. Critérios de aceite finais

- [ ] CA01 — Usuário cria conta, entra e sai; operações protegidas recusam acesso sem autenticação (RF01).
- [ ] CA02 — Autor publica, acompanha a revisão, edita e exclui sua publicação (RF04–RF05).
- [ ] CA03 — Administrador aprova/rejeita com motivo; pendentes e rejeitados não são públicos (RF11).
- [ ] CA04 — Feed filtra categorias, pagina sem repetição e apresenta fim dos resultados (RF02).
- [ ] CA05 — Vídeos reproduzem com controles e alternativa manual ao autoplay (RF03).
- [ ] CA06 — Reações, comentários e perfil persistem no Atlas após recarregar a página (RF06–RF08).
- [ ] CA07 — Usuário não altera conteúdo de terceiros nem executa ações administrativas.
- [ ] CA08 — Denúncias são revisadas e bloqueio/desbloqueio produz o efeito definido (RF09–RF12).
- [ ] CA09 — Flask consulta a API bíblica e React apresenta o texto e a tradução (RF13).
- [ ] CA10 — Falha da API bíblica é tratada sem impedir assistir ao vídeo.
- [ ] CA11 — Testes das rotas cobrem os fluxos principais e os casos de erro definidos.
- [ ] CA12 — Frontend e backend estão acessíveis na AWS; README contém link da API e instruções.
- [ ] CA13 — Segredos estão fora do Git; configuração necessária é entregue pelo canal solicitado.
- [ ] CA14 — Tema e recorte estão validados pelo professor.
- [ ] CA15 — Demonstração usa contas e conteúdo preparados, e contribuições individuais estão identificáveis.

**Revisão em 31/10/2026.** Os CA finais continuam sem marcação: implementação e testes isolados não substituem as evidências integradas, operacionais e humanas. O registro de 29/10 relatou rotas no Atlas de desenvolvimento e `ffprobe` real; não foi repetido nesta revisão. A base frontend `5d79b9d` já contém autenticação, perfil/lista própria, publicação, moderação/denúncias, feed, Amém e versículo. A branch `feat/fluxos-mvp-locais-20261031` desenvolve as demais interfaces, sujeitas a revisão e merge.

| Critério | Implementação e evidência disponível | Aceite restante |
| --- | --- | --- |
| CA01, CA07 | Autenticação e papéis no backend; telas React e testes isolados | Fluxos atuais no navegador e permissões cruzadas |
| CA02, CA03 | Upload/ffprobe e moderação no backend; publicar/listar/moderar no React | Gestão própria em desenvolvimento; prova integrada de estados e privacidade |
| CA04, CA05 | Feed paginado/mídia Range no backend; feed/player manual no React | Visitante em desenvolvimento; reprodução e paginação integradas |
| CA06, CA08 | Interações, perfis e bloqueios no backend; Amém/denúncia/resolução no React | Comentários/bloqueios/perfil em desenvolvimento; persistência e filtros após recarga |
| CA09, CA10 | Consulta bíblica pelo Flask e versículo no cartão React com erro recuperável | Retorno externo real atual e falha sem impedir o vídeo |
| CA11 | Auditoria sobre backend `7f9448c` e frontend `5d79b9d`: 682/102 testes, lint/build passaram em 31/10; dados isolados | Verificações das novas interfaces e matriz integrada; não inferir produção |
| CA12, CA13 | Modelos EC2 e exemplos sem credenciais; nenhum URL AWS verificado | Publicação autorizada, URL no README, controles operacionais e entrega privada |
| CA14, CA15 | Dependem da equipe e do professor | Aprovação docente, roteiro/ensaio com conteúdo autorizado e contribuições identificáveis |

## 14. Pendências de decisão

- Registrar aprovação do tema e do recorte pelo professor.
- Confirmar responsáveis e capacidade de execução do grupo.
- Verificar as decisões implementadas de autenticação/upload no fluxo integrado e configurar armazenamento persistente no destino autorizado.
- Revalidar o provedor bíblico e as referências em português no roteiro de integração.
- Registrar URLs públicas após o deploy.

## 15. Manutenção

Este arquivo, no backend, é a fonte principal do escopo. O frontend deve apontar para ele em seu README, evitando cópias divergentes. Mudanças de escopo devem atualizar requisitos, critérios de aceite, cronograma e versão deste documento.

**Histórico:** v1.1 (19/10/2026): refinamento técnico T01, contratos e limites definidos; nenhum requisito funcional removido e nenhum critério de aceite marcado sem implementação.

**v1.4 (31/10/2026):** conciliação documental com runtime e frontend existente, distinguindo branch em desenvolvimento, provas históricas e aceites pendentes. Sem alteração de requisitos RF/RA ou conclusão de CA.
