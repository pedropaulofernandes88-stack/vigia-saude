# Contrato de métricas — módulo Dengue

## Escopo e grão

O módulo recebe CSV do Sinan Dengue. O recorte territorial é a lista
versionada de municípios, com código IBGE de seis dígitos em `ID_MN_RESI` e UF
conferida pelos dois primeiros dígitos. A data de ocorrência é exclusivamente
`DT_SIN_PRI` (início de sintomas); `DT_NOTIFIC` não a substitui quando falta.

Cada linha Silver e Gold representa **um município de residência e uma semana
epidemiológica**. A semana vai de domingo a sábado. A semana 1 é a primeira
com pelo menos quatro dias no ano epidemiológico. `week_label` segue
`SE NN/AAAA` e pode pertencer ao ano anterior na primeira semana civil.

## Elegibilidade

Um registro entra na série quando `ID_MN_RESI` contém seis dígitos, pertence à
UF e à lista territorial, e `DT_SIN_PRI` é válida, está no `year` configurado e
não é posterior ao corte inclusivo (`cutoff_date`; se omitido, a data de
`source_updated_at`). O manifesto conta ausência e invalidade de município,
fora de UF, município desconhecido, início ausente, data inválida, ano fora do
escopo e data posterior ao corte. Não se confunde ausência com zero.

## Indicadores

| Campo | Definição |
| --- | --- |
| `notifications` | Registros elegíveis na semana, inclusive descartados e outros agravos, para reconciliar o volume notificado. |
| `probable_cases` | Notificações elegíveis exceto `CLASSI_FIN=5` (descartado) e `CLASSI_FIN=13` (chikungunya). Outros códigos de agravo podem entrar na configuração. Classificação ausente ou fora de domínio fica provisoriamente nesta contagem e é marcada em `unknown_classification`. |
| `confirmed_cases` | Casos prováveis com `CLASSI_FIN=10` (dengue), `11` (com sinais de alarme) ou `12` (dengue grave), salvo alteração oficial documentada. |
| `deaths` | Casos confirmados com `EVOLUCAO=2` e `CRITERIO=1` ou `2`. É desfecho agregado pela semana de início de sintomas, não pela semana do óbito. |

As invariantes `confirmed_cases ≤ probable_cases ≤ notifications` e
`deaths ≤ confirmed_cases` são verificadas em cada execução. Confirmações e
óbitos continuam sujeitos à validação do responsável pela vigilância.

## Privacidade e limites

Não há deduplicação: **duplicidade não aferida sem identificador estável**. O
manifesto registra `duplicates_checked: false`; não apresenta pessoas únicas.
O CSV bruto não é versionado. Nenhum registro individual, nome, documento,
endereço, data individual ou identificador clínico é escrito em Silver/Gold;
os Parquets contêm somente agregados municipais semanais.

O painel descreve os registros no recorte e snapshot declarados. Não estima
subnotificação, não demonstra causalidade e não substitui investigação,
confirmação diagnóstica ou declaração de óbito pela vigilância.

`cutoff_date` descreve o corte técnico da extração. `event_start` e `event_end`
no manifesto descrevem a menor e a maior data de início de sintomas entre os
registros válidos do território. São datas de disponibilidade observada, não
uma alegação de completude: semanas posteriores a `event_end` não podem ser
preenchidas como zero sem uma avaliação específica de atraso ou cobertura.
