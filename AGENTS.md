## Cost3D v2.1 — Estrutura do Projeto

### Módulos
- **`app/constants.py`** — Cores, funções de formatação (`fmt_currency`, `fmt_time`, `fmt_status`)
- **`app/widgets.py`** — `RoundedButton` (com suporte a `tooltip`, `configure` alias), `LoadingDialog`
- **`app/exporter.py`** — `export_quotes_csv()`, `export_quote_detail_csv()`
- **`app/database.py`** — Inicialização, `get_connection()`, `get_setting()`, `update_setting()`, `DB_PATH`
- **`app/filament.py`** — Modelo `Filament` com `density`
- **`app/printer.py`** — Modelo `Printer`
- **`app/cost_calculator.py`** — `CostCalculator` para custos de impressão
- **`app/gcode_parser.py`** — `GCodeParser`, `extract_thumbnail_from_gcode()`
- **`app/mesh_reader.py`** — `estimate_from_3mf()`, `extract_thumbnail_from_3mf()` (3MF info panel)
- **`app/slicer_cli.py`** — Registro de slicers, `slice_3mf()`, `find_available_slicers()`
- **`app/slicer_importer.py`** — `scan_filaments()`, `scan_printers()` dos perfis dos slicers
- **`app/slicer_engine.py`** — Built-in slicer independente (~800 linhas)
- **`app/ui.py`** — Interface gráfica principal (`Cost3DApp` ~2370 linhas)

### Melhorias v2.1
- **Barra de status** inferior com contagem de orçamentos/pendentes
- **Busca/filtro** no histórico de orçamentos
- **Exportação CSV** de orçamentos
- **Backup do banco de dados** nas Configurações
- **Atalhos de teclado**: Ctrl+N (orçamento), Ctrl+S (salvar), Ctrl+F (fatiar), Ctrl+H (histórico), Ctrl+D (painel), F5 (atualizar)
- **Tooltips** em todos os botões
- **Botões de status** no histórico com tooltips
- Correção: `density` agora é salvo/recuperado corretamente no modelo `Filament`
- **Built-in slicer** (`app/slicer_engine.py`): fatiamento 3MF→G-code independente, sem dependência externa
- **3MF Parser robusto** (`slicer_engine.py` + `mesh_reader.py`): suporta `m:` prefix, default xmlns (`xmlns=...`), sem namespace, sub-models em `3D/Objects/`, componentes com transformação

### Atalhos de Teclado
| Atalho | Ação |
|--------|------|
| Ctrl+N | Novo Orçamento |
| Ctrl+S | Salvar Orçamento |
| Ctrl+F | Aba Fatiar |
| Ctrl+H | Aba Histórico |
| Ctrl+D | Aba Painel |
| F5 | Atualizar Painel |
| Escape | Fechar diálogo |

## Slicing Tab ("Fatiar")

### Workflow Completo
1. **Selecionar .3mf** → `_analyze_slice_file()` chama `mesh_reader.estimate_from_3mf()` que lê metadados e malha
2. Info panel mostra: cores, printer, altura/camadas, volume mm³, peso estimado
3. **Escolher filamento** do banco de dados
4. **Escolher slicer**: externo (AnycubicSlicerNext CLI, BambuStudio GUI) ou **"FATIAR (Interno)"** (built-in)
5. Botão **"FATIAR"** → chama slicer externo em background thread
6. Botão **"FATIAR (Interno)"** → chama `slicer_engine.builtin_slice_3mf()` em background
7. Após fatiar, mostra resultado no text area + elapsed time
8. **"Ir para Precificação"** → muda para aba de orçamento com dados pré-preenchidos

### Slicer Externo
- `slicer_cli.py`: unified registry for BambuStudio (GUI only), AnycubicSlicerNext, AnycubicSlicer, OrcaSlicer
- `find_available_slicers()` returns each with `has_cli` flag
- CLI slicers use `--slice` / `--export-gcode` flags
- Profile matching via `_match_profile()` with word-scoring
- System detection finds: BambuStudio, AnycubicSlicerNext (CLI), AnycubicSlicer (CLI)

### OrcaSlicer Sanitization (`slicer_cli.py`)
- **Problema**: Orca 2.3.2 CLI rejeita `.3mf` do BambuStudio 2.7 com `Version Check: File Version 2.7.0.55 not supported`
- **Detecção da versão**: Orca lê `<metadata name="Application">BambuStudio-02.07.00.55</metadata>` no `.model` (XML), **não** o campo `version` nos JSONs
- **Sanitização**: `_sanitize_3mf_for_orca()` remove Application metadata dos `.model` + `Metadata/slice_info.config` inteiro (contém X-BBL-Client-Version). Deixa valores de configuração inalterados.
- **Crash conhecido**: Orca 2.3.2 crasha (access violation 3221225477) ao tentar fatiar modelos complexos do BambuStudio 2.7 após sanitização total (todos valores corrigidos). Provavelmente bug do Orca 2.3.2 com o modelo específico, não da sanitização.
- **Fallback GUI**: Se sanitização foi aplicada e Orca CLI falha (qualquer erro), `_run_slicer_orca()` abre o arquivo **original** no Orca GUI para fatiamento manual.

### Built-in Slicer (`slicer_engine.py`)
- **Parser 3MF**: `_scan_3mf_volumes()` lê qualquer `.model` no ZIP, suporta 3 variantes de namespace (`m:`, default xmlns, sem namespace), sub-models em `3D/Objects/`, componentes com transformação 4×4, `model_settings.config` para detecção de placas
- **Mesh slicing**: sweep-Z algorithm com filtragem por Z → só triangulos relevantes em cada camada
- **Polygon assembly**: hash-map O(1) endpoint lookup para cadeias fechadas
- **Polygon simplification**: remove colinear points (min_d=0.05–0.1); RDP simplification para infill (tolerance 0.8mm) e para perímetros (tolerance 0.3mm sólido / 0.15mm infill)
- **Perimeters**: offset polygon (Minkowski-sum via segment offset), `perimeter_count` configurável
- **Infill**: grid lines (two angles ±45°), fill density controlada via `fill_density` ou `sparse_infill_density`
- **Infill spacing**: `infill_sp = 2 * ew / fill_density` (fator 2 compensa grid de ±45° que dobra densidade)
- **Solid layers**: top/bottom solid layers com preenchimento denso
- **Suporte**: detection from slicer config keys (`support_enable`, `generate_support` etc.)
- **G-code writer**: M84/M104/M140/M190, M83 relative E, G1 moves, velocidade variável por tipo
- **Timeout**: SLICE_TIMEOUT=30s para evitar hangs
- **Performance**: cube 10mm (12 tri) → **~0.05s**, modelo complexo 30mm (1728 tri, 150 layers) → **~3.7s**; R36s (116K tri, 110 layers) → **~38s**
- **Peso estimado**: baseado em `total_e * cs_area * filament_density / 1000` (extrusão real do G-code, com clamp ao peso maciço `total_mesh_vol * density / 1000` quando a extrusão excede o volume da malha). A correção RDP (tolerância 0.3mm sólido / 0.15mm infill) antes da geração de perímetros evita artefatos de montagem em camadas inferiores. O peso vem do caminho real de extrusão, sem `shell_fraction` arbitrário.

## Observações
- `RoundedButton.configure()` delega para `RoundedButton.config()` (BUG 6 fix)
- DB path quando frozen: mesmo diretório do .exe; quando dev: raiz do projeto
- Build: `python -m PyInstaller Cost3D.spec --clean --noconfirm`
