# Validação da versão inicial

Execução local em 10/09/2026, Windows, Python 3.12.14, pandas 3.0.1 e pyarrow 25.0.1. As dependências utilizadas estão fixadas em `requirements.txt`.

## Dados reais

A aquisição consultou o recurso oficial SINAN/Dengue 2026 e a tabela 6579 do SIDRA/IBGE para população municipal de 2025. O catálogo do recurso indicava 15/08/2026 e o cabeçalho HTTP do arquivo indicava última modificação em 05/09/2026. O manifesto local guarda URLs, headers, data de extração e hashes.

| Controle | Resultado na primeira carga |
|---|---:|
| Registros recebidos no arquivo nacional | 449.101 |
| Municípios cadastrados no Paraná | 399 |
| Registros elegíveis do Paraná | 10.932 |
| Grupos município × semana | 2.650 |
| Casos prováveis no recorte | 10.932 |
| Classificações confirmadas de dengue | 6.018 |
| Óbitos confirmados entre casos do período | 5 |
| Registros do território sem classificação reconhecida | 1.834 |

O período de início de sintomas observado foi **04/01/2026 a 29/08/2026**. As contagens foram reconciliadas por uma segunda implementação usando `csv` da biblioteca padrão, sem depender das agregações pandas do pipeline. Ela conferiu os 2.650 grupos e os quatro totais de métricas. Isso valida a transformação da extração, não a completude ou exatidão epidemiológica da fonte.

O arquivo original permanece em `data/bronze`, fora do Git. Silver e Gold contêm apenas agregados. A confirmação de duplicidade de pessoas não foi possível: o extrato não fornece identificador estável suficiente. Os 1.834 registros com classificação ausente ou não reconhecida continuam provisórios e são destacados no painel.

## Verificações de software

Quinze testes automatizados da versão inicial passaram, cobrindo:

- Elegibilidade, datas ausentes/inválidas, códigos municipais, ano e corte.
- Reconciliação de totais e independência do tamanho dos lotes.
- Calendário epidemiológico, incluindo a passagem de dezembro para o ano seguinte.
- Ausência de campos individuais em Parquet e preservação de snapshots existentes.
- Contagens, denominadores, filtros e comparação entre blocos de quatro semanas.
- Recusa de snapshot alterado sem checksum correspondente.
- Diferença entre a data técnica do arquivo e o último evento observado.
- Persistência SQLite, idempotência e histórico de ações.
- Contratos HTTP, exportação de boletim, bloqueio de origem externa e de acesso a arquivos fora da interface.

Comandos: `python -m unittest discover -s tests -v`, `node --check web/app.js` e `git diff --check`.

O mesmo conjunto de testes também passou no GitHub Actions em Ubuntu com Python 3.12. O workflow mantém permissões somente de leitura e fixa as ações oficiais por SHA.

## Navegador

Verificado no navegador integrado do Codex:

- Carregamento com dados oficiais e mudança do filtro para Maringá.
- Concordância entre cartões e tabela do município: 234 casos prováveis e 1 óbito confirmado entre casos do período.
- Gráfico e tabela acessível da série, tabela municipal com rolagem interna e metodologia.
- Criação e conclusão de ação, seguida de recarregamento para confirmar persistência. Essa ação foi criada em armazenamento de teste isolado, sem contaminar o cadastro principal.
- Layout em largura de desktop e larguras 360 e 768 pixels. Ausência de rolagem horizontal global nas larguras menores após a correção.
- Nenhum erro ou aviso no console durante o fluxo de ações testado.

Não foi realizada certificação completa de acessibilidade, teste de carga multiusuário ou homologação sanitária. A aplicação é um MVP local e não foi implantada em serviço público na internet.

## Correções relevantes feitas durante a validação

1. Impedida a criação artificial de uma semana com zero casos entre o último evento observado e a atualização técnica do arquivo. A data final do painel agora respeita `event_end`.
2. Corrigido o calendário para os últimos dias de dezembro pertencentes ao ano epidemiológico seguinte.
3. Fechamento explícito de conexões SQLite para evitar retenção de arquivos no Windows.
4. Verificação de checksum, schema, grão e invariantes antes do consumo de cada novo snapshot.
5. Ajuste do layout em 360 pixels e da idempotência do formulário quando o conteúdo muda após uma falha.
