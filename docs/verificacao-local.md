# Verificação local e roteiro de integração

**Revisão:** 31/10/2026. **Escopo desta preparação:** comandos isolados, matriz de permissões e roteiro para execução futura. Não foram executados integração real, escrita em Atlas, consulta externa ou deploy para produzir este documento. Nenhum CA final é concluído pelo roteiro.

## Bases e tipos de evidência

A auditoria de 31/10 sobre backend `7f9448c` e frontend `5d79b9d` registrou 682 testes backend, 102 testes frontend, lint e build aprovados com Node 22.23.3. Esses resultados pertencem a essas bases, com dados isolados e simulações. As interfaces adicionais na branch `feat/fluxos-mvp-locais-20261031` exigem seus próprios checks e revisão antes do merge.

Registrar cada execução com data, SHA de cada repositório, ambiente, comando/fluxo, resultado, responsável e limitação. Usar três categorias: teste isolado; fluxo integrado em ambiente dedicado; fluxo publicado. Build, health e número de testes não substituem as outras categorias. Guardar apenas resultados sem cookies, senhas, tokens, URIs ou dados pessoais.

## Comandos de verificação isolada

Executar no backend, com o virtualenv já preparado conforme [operação](operacao.md):

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
git diff --check
```

Os testes usam fixtures/repositórios simulados e diretórios temporários; não inicializar índices reais para executá-los. Confirmar que o comando é rodado no repositório correto e não substituir as fixtures por credenciais operacionais. A chamada `scripts/smoke_bible.py` é prova externa opt-in, não integra esta etapa isolada.

No repositório frontend, selecionar Node 22.12+ na linha 22, conforme `.nvmrc`, e conferir a versão antes dos checks:

```bash
node --version
npm test
npm run lint
npm run build
git diff --check
```

Usar as dependências do lockfile (`npm ci` somente se ainda não instaladas). Os testes do cliente simulam HTTP; não apontar os testes para uma API ou banco real. O build deve gerar `dist/`; isso não comprova que o servidor entregará a aplicação ou a mídia.

## Matriz local por fluxo

Para cada linha, combinar teste de interface com os testes de autorização/validação das rotas. Os contratos e códigos de resposta estão na [API](api.md). O roteiro integrado permanece pendente até autorização de ambiente dedicado.

| IDs | Verificação isolada esperada | Fluxo integrado futuro |
| --- | --- | --- |
| CA01, CA07 / RF01 | Cadastro/login/logout, CSRF recuperável, senha inválida, sessão expirada e erro de rede/503 sem logout silencioso; nenhuma credencial no localStorage | Criar conta de demonstração, entrar, recarregar, sair e confirmar rejeição do cookie anterior nas operações protegidas |
| CA02 / RF04–RF05 | Upload FormData sem Content-Type manual, validações/413/415/422/503; erro/timeout sem repetir POST nem informar sucesso; PATCH próprio e DELETE 204 sem tentar ler JSON | MP4 próprio/autorizado passa por ffprobe, aparece pendente após recarga; editar aprovado devolve a pendente; excluir retira da consulta e conclui limpeza ou registra retomada |
| CA03, CA07 / RF11 | Usuário comum não ganha acesso ao painel/rotas; revisão exige motivo e expected_updated_at, com 409 recuperável | Administrador aprova/rejeita o item exato; autor vê motivo; visitante e terceiro não veem pendentes/rejeitados |
| CA04–CA05 / RF02–RF03 | Visitante acessa feed/versículo; filtros reiniciam cursor, resultados não duplicam, vazio/erro/fim; player possui controles e reprodução manual | Navegador em telefone/desktop reproduz mídia autorizada, pagina categorias e trata autoplay recusado; pendente/rejeitado não acessível nem por URL direta |
| CA06 / RF06 | Amém otimista confirma/reverte em erro, desfazer 204 funciona e duplicidade é evitada; visitante recebe caminho para login sem mutação | Reagir/desfazer e recarregar conservam estado e contagem no Atlas |
| CA06–CA07 / RF07 | Comentários públicos paginados; criar exige sessão, texto válido e envio único; só autor remove, 204; erro preserva texto e lista; contagem atualizada | Comentário persiste após recarga; usuário diferente não pode remover; exclusão própria permanece permitida se o vídeo sair do público |
| CA06–CA07 / RF08 | PATCH /me somente name/bio; limites e erros preservam formulário; identidade global atualizada; perfil público usa /users/{id} + /videos?author_id= | Perfil editado persiste; terceiro vê apenas vídeos aprovados, sem e-mail, papel, hash ou notas privadas |
| CA08 / RF09, RF12 | Denúncia com motivo, conflito de duplicação, resolução/retirada autenticada por admin e erro recuperável | Denunciar não remove automaticamente; administrador resolve e pode rejeitar com motivo; novas consultas públicas não mostram retirado |
| CA08 / RF10 | Bloquear/desbloquear/listar, falha sem sucesso falso; invalidar todo feed/comentários já carregados e cursores; troca de identidade não reaproveita dados privados | Conta bloqueadora não vê vídeos/comentários do bloqueado, inclusive consulta/mídia direta; outra conta/visitante continuam vendo aprovado; desbloqueio restaura a visibilidade prevista |
| CA09–CA10 / RF13 | Texto/tradução, cache indicado, referência inválida, timeout/502/503 simulados e nova tentativa sem desmontar player | Flask consulta passagem real, React exibe texto/tradução e consulta repetida usa cache; testar falha em ambiente controlado sem provocar carga no provedor |
| CA11 | Sucesso/erro/permissões por rota, testes UI pertinentes, lint/build e revisão de regressões | Registrar matriz completa no ambiente dedicado; mocks não concluem persistência ou operação real |

## Papéis e dados do ambiente futuro

| Papel | Ações a confirmar | Ações proibidas a confirmar |
| --- | --- | --- |
| Visitante | Feed, reprodução, comentários públicos, perfil público e passagem | Publicar, reagir, comentar, denunciar, bloquear, alterar perfil e moderar |
| Usuário comum / terceiro | Interações próprias e bloqueios | Editar/excluir vídeo ou comentário de outro; executar moderação |
| Autor | Publicar, acompanhar todos os estados próprios, editar/excluir próprios | Tornar aprovado seu próprio vídeo por uma rota de autor |
| Administrador | Revisar e tratar denúncias com motivo/conflito | Obter permissões só por esconder/mostrar botão ou por campo role no cliente |

Utilizar contas distintas e conteúdo próprio/autorizado; uma aba anônima ajuda a verificar privacidade. Atribuir identificadores aos artefatos de teste sem registrar senhas. Manter dados de teste separados de produção. A validação de arquivos/relações após exclusão e eventual `purge-videos` deve ser limitada ao ambiente autorizado; nunca testar restauração ou limpeza destrutiva sobre dados de produção.

## Checklist antes de liberar integração ou publicação

- [ ] Identificar PRs, SHAs revisados e ordem de integração; preservar autoria e trabalhos da equipe.
- [ ] Checks isolados das versões candidatas aprovados, incluindo casos adversariais de permissões.
- [ ] Ambiente e contas de teste autorizados, dados isolados e conteúdo com licença/consentimento.
- [ ] Atlas com permissões/índices e mídia privada configurados sem expor segredos.
- [ ] Registrar a matriz no navegador e confirmar persistência após recarga antes de concluir CA01–CA11.
- [ ] Aplicar a preparação e as validações do destino em [operação](operacao.md) antes de CA12–CA13.
- [ ] Preparar [demonstração](roteiro-demonstracao.md), confirmação docente e entrega privada para CA14–CA15.

Cada pendência deve indicar responsável e próxima ação. Uma dependência externa não invalida os checks locais, mas continua impedindo o aceite correspondente.
