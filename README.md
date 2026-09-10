# Vigia Saúde

Aplicação local para transformar dados públicos de dengue em indicadores verificáveis e acompanhar providências da equipe de gestão. A primeira configuração cobre o **Paraná em 2026**. O território e as fontes ficam em um arquivo de configuração.

**Estado: MVP local funcional, com dados oficiais e testes.** Requer validação pela vigilância antes de adoção institucional. Não é uma previsão epidemiológica, um protocolo de risco nem um sistema clínico.

Código aberto sob a [licença MIT](LICENSE). O [repositório público](https://github.com/pedropaulofernandes88-stack/vigia-saude) disponibiliza código, documentação e testes para execução no próprio computador. O painel não está hospedado como serviço público; cada pessoa executa a aplicação localmente e adquire os dados diretamente das fontes oficiais.

## O que funciona

- Aquisição do CSV nacional SINAN/Dengue e dos denominadores municipais do IBGE/SIDRA.
- Processamento em lotes, rastreabilidade da origem e snapshots com checksums.
- Agregados município × semana epidemiológica em Apache Parquet; nenhuma linha individual na API.
- Filtros de município e período, contagens, incidência, média móvel de quatro semanas e comparação entre blocos de quatro semanas.
- Qualidade dos dados, sinais descritivos, tabela territorial ordenável e pesquisa municipal.
- Boletim Markdown reproduzível com os mesmos filtros do painel.
- Registro de ações em SQLite: município, providência, responsável, prazo, descrição, conclusão e histórico de mudanças.
- Criação idempotente de ações e restrição do servidor ao computador local.

## Executar

Requisitos: Python 3.12 e conexão com a internet para instalar dependências e adquirir fontes. A interface não usa CDN, fontes externas ou serviços de IA. Depois da carga, painel e ações funcionam localmente sem internet.

```sh
git clone https://github.com/pedropaulofernandes88-stack/vigia-saude.git
cd vigia-saude
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements.txt
python vigia.py sync --config config/parana-dengue.json
python vigia.py serve --port 8765
```

Abra **http://127.0.0.1:8765**. No Windows, `./start.ps1` encontra a virtualenv local ou o runtime disponível no Codex e inicia o servidor. O diretório de dados não acompanha o repositório; um clone começa com estado vazio até executar `sync`.

```sh
python vigia.py status
python vigia.py brief --output runtime/boletim.md
python -m unittest discover -s tests -v
node --check web/app.js
```

O comando `sync` é manual. Nenhuma atualização recorrente foi agendada. Um novo `sync` baixa a fonte novamente, processa em snapshot separado e troca o ponteiro `data/current.json` somente após sucesso. Uma falha não substitui o conjunto anterior.

## Fontes e datas

- [SINAN/Dengue — Ministério da Saúde](https://dadosabertos.saude.gov.br/dataset/arboviroses-dengue).
- [CSV 2026 no catálogo oficial](https://dadosabertos.saude.gov.br/dataset/arboviroses-dengue/resource/f71ec0f9-82eb-4177-bd6a-ff76f2dfa84d).
- [Dicionário ligado pelo catálogo](https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SINAN/Dengue/dic_dados_dengue.pdf).
- [Ficha oficial de investigação](https://www.portalsinan.saude.gov.br/images/documentos/Agravos/Dengue/Ficha_DENGCHIK_FINAL.pdf).
- [IBGE/SIDRA — tabela 6579](https://sidra.ibge.gov.br/tabela/6579), população municipal de **2025**, explicitamente usada como aproximação para incidência em 2026.

São preservadas três datas distintas: atualização do catálogo, última modificação do arquivo e extração. A extensão do gráfico depende dos **eventos efetivamente observados**, não da data técnica do arquivo. Na primeira carga de 10/09/2026, o arquivo tinha modificação em 05/09/2026, mas o último início de sintomas observado no Paraná era 29/08/2026.

Leia [o contrato das métricas](docs/metricas.md). Óbitos são os desfechos confirmados dos casos com início de sintomas no período; não formam uma série pela data do óbito. Variações são descritivas e sensíveis a pequenos números e revisões. Zero significa ausência de registros elegíveis na extração, sem assegurar ausência de doença ou completude da fonte.

## Arquitetura

```text
SINAN/Dengue + IBGE/SIDRA
        ↓ download HTTPS, hash, metadados
data/bronze/<execução>/             origem local, fora do Git
        ↓ validação, normalização, agregação
data/snapshots/<execução>/silver/   município × semana
        ↓ contrato analítico e reconciliação
data/snapshots/<execução>/gold/     Parquet + manifest.json na raiz do snapshot
        ↓ ponteiro current.json após sucesso
API local → painel / boletim
        ↘ SQLite local → ações e histórico
```

O consumidor verifica checksum, schema, grão e coerência das contagens antes de carregar um novo snapshot. Snapshots são tratados como imutáveis. As taxas e janelas comparativas são calculadas no recorte solicitado sobre agregados, sem consultar microdados no servidor.

## Organização

| Caminho | Responsabilidade |
|---|---|
| `config/parana-dengue.json` | Território, ano, fontes e população de referência |
| `src/vigia/sources.py` | Download, checksums, extração segura de ZIP e leitura do IBGE |
| `src/vigia/pipeline.py` | Validação, regras epidemiológicas e agregação |
| `src/vigia/analytics.py` | Integridade do snapshot, filtros, indicadores e boletim |
| `src/vigia/actions.py` | Persistência, idempotência e histórico |
| `src/vigia/server.py` | API e arquivos estáticos em loopback |
| `web/` | Interface própria em HTML, CSS e JavaScript |
| `tests/` | Fixtures sintéticas, testes analíticos e integração HTTP |
| `docs/validacao.md` | Evidências da primeira execução e limites |

## Uso e evolução

O servidor usa HTTP da biblioteca padrão do Python e aceita apenas `127.0.0.1`, com validação de Host/Origin e proteção de mutações. O armazenamento é local, sem autenticação multiusuário. **Não o exponha diretamente à internet.** Um ambiente institucional exige autenticação, autorização, servidor de produção, backups e operação definidos. Não registre identificadores de pacientes no texto das ações.

`data/`, `runtime/`, ambientes virtuais e arquivos de credenciais estão ignorados no Git. Os arquivos brutos de saúde ficam somente no ambiente local que executou a aquisição. Dados agregados também não são publicados automaticamente.

A configuração permite outro estado e ano de dengue, desde que sejam fornecidos arquivos oficiais compatíveis e denominadores explícitos. Outros agravos precisam de adaptador e definições próprios. Esta versão ainda não inclui mapa geográfico, imunização, SRAG, previsão, conexão com prontuários ou notificações automáticas.

A inspiração e a atribuição conceitual estão em [INSPIRATION.md](INSPIRATION.md). Esta implementação não incorpora código nem dados do painel histórico da 15ª Regional.

## Licença e contribuições

O código próprio e sua documentação são disponibilizados sob a [MIT License](LICENSE). O aviso de copyright e o texto da licença devem acompanhar cópias ou partes substanciais do software. Dados obtidos do SINAN, IBGE e outras fontes, assim como dependências de terceiros, seguem os termos e licenças dos respectivos titulares; a licença deste repositório não altera esses termos.

Sugestões e correções podem ser propostas por issues e pull requests. Ao reportar um problema, informe o comportamento esperado, a versão e uma forma de reproduzi-lo com dados sintéticos. Não inclua registros individuais de saúde, credenciais ou arquivos locais de ações. Os testes Python usam somente fixtures sintéticas; Node.js é opcional para conferir a sintaxe da interface com `node --check web/app.js`.
