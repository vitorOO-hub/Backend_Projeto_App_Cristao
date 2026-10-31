# Roteiro de demonstração e checklist de entrega

**Revisão:** 31/10/2026. Este é um roteiro preparado, ainda sem ensaio integrado ou publicação comprovados. Preencher os campos abaixo pela equipe; nenhum campo significa conta criada, professor consultado ou infraestrutura disponível. O prazo registrado no escopo não foi revalidado nesta revisão.

## Preparação da equipe

| Item | Registro a preencher sem segredos | Condição de prontidão |
| --- | --- | --- |
| Versões | Backend: PREENCHER SHA/PR; frontend: PREENCHER SHA/PR | PRs revisados, ordem de merge combinada e checks aprovados |
| Apresentação | PREENCHER responsável e divisão dos trechos | Cada pessoa consegue explicar sua contribuição real |
| Aprovação docente | PREENCHER data/canal e confirmação do tema/recorte | Confirmação humana registrada, sem inferência por funcionamento |
| Ambiente | PREENCHER identificação e URLs verificadas | Frontend/API acessíveis no ambiente autorizado; URL da API no README após prova |
| Contas | PREENCHER identificadores de autor, usuário/terceiro e admin | Contas de demonstração distintas; senhas só em canal privado |
| Conteúdo | PREENCHER identificadores dos MP4, licença/autorização, categoria e referência | Conteúdo próprio/autorizado; MP4 H.264, AAC opcional, até 50 MiB/90s |
| Dados de ensaio | PREENCHER vídeo de demonstração e vídeo descartável de teste | Alterações de estado e exclusão não comprometem o conteúdo principal |
| Configuração privada | PREENCHER responsável/canal/status, nunca os valores | Configuração necessária entregue pelo canal exigido, fora de Git/PR/slides |

Não promover contas pela API. O papel administrativo depende de comando operacional por responsável autorizado sobre uma conta já cadastrada e de novo login. Não executar essa preparação em produção apenas para ensaiar.

## Sequência sugerida

1. **Visitante:** abrir feed sem sessão, filtrar categoria e mostrar paginação/fim. Reproduzir um vídeo aprovado com controles manuais e abrir o versículo/tradução. Mostrar que interagir pede login.
2. **Autor:** entrar, exibir perfil e publicar o MP4 de demonstração com legenda, categoria e referência opcional. Mostrar o estado pendente na lista própria após recarga.
3. **Privacidade:** em contexto visitante/terceiro, confirmar que o pendente não aparece no feed e que a consulta/mídia direta não o expõe. Usar apenas identificadores de teste preparados.
4. **Administrador:** abrir fila, revisar o item e aprovar com motivo. Em item descartável, demonstrar rejeição e motivo visível ao autor. Não encenar aprovação sem a resposta confirmada do servidor.
5. **Interações:** mostrar aprovado no feed; usuário comum reage/desfaz Amém, comenta e recarrega. Remover seu comentário; mostrar que controles e servidor não permitem alteração de conteúdo de terceiros.
6. **Perfil e gestão:** editar nome/bio; consultar perfil público com aprovados. Editar metadados de um vídeo aprovado e confirmar retorno a pendente; reaprovar antes de continuar a demonstração pública.
7. **Convivência:** denunciar um vídeo de teste; mostrar que isso não o remove automaticamente. Administrador resolve a denúncia e, se aplicável, retira o vídeo com rejeição motivada. Usar dados distintos para não perder o vídeo principal.
8. **Bloqueios:** bloquear o autor de teste e mostrar que seus vídeos/comentários já carregados desaparecem para a conta bloqueadora; conferir lista de bloqueados e desbloquear. Explicar o filtro individual, sem remoção global.
9. **Falhas e limpeza:** demonstrar erro bíblico por teste isolado previamente executado, sem interromper vídeo nem induzir falhas/carga no provedor real. Excluir publicação descartável e confirmar ausência; se houver limpeza física pendente, registrar o fato e a ação operacional autorizada.
10. **Evidências:** apresentar versões, resultados de testes e limites. Mostrar o link real da API apenas se verificado, e identificar as contribuições individuais sem atribuições inventadas.

Todos os passos são verificações a executar no ensaio futuro. Se um fluxo falhar, registrar a pendência e sua evidência, corrigir na branch e repetir somente o trecho afetado e suas regressões pertinentes. Não apresentar resposta simulada como persistência real ou vídeo demonstrativo como aplicação publicada.

## Critérios de encerramento do ensaio

- [ ] Fluxos CA01–CA11 registrados conforme [matriz de verificação](verificacao-local.md), incluindo permissões e persistência.
- [ ] Frontend/backend publicados e URLs verificadas, quando a implantação for autorizada (CA12).
- [ ] Nenhuma senha/cookie/token/URI aparece em Git, PRs, terminal compartilhado, captura de tela ou slides; canal privado concluído (CA13).
- [ ] Tema/recorte confirmados pelo professor (CA14).
- [ ] Contas/conteúdo preparados e autorizados, ensaio concluído e contribuições identificadas (CA15).
- [ ] Registrar pendências finais e responsáveis; não ocultar integração ou implantação ainda não verificadas.

## Contribuições individuais

| Integrante | Entrega real e explicação | Evidência de contribuição | Trecho da apresentação |
| --- | --- | --- | --- |
| PREENCHER | PREENCHER | PR/commit/artefato verificável | PREENCHER |

Preservar autoria factual e coautorias aplicáveis. Commits organizados ajudam a revisão, mas não comprovam sozinhos entendimento, participação de toda a equipe ou aceite docente.
