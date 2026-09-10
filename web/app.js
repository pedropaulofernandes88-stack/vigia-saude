(() => {
  "use strict";

  const state = { dashboard: null, actions: [], sort: { key: "incidence_per_100k", direction: "desc" }, search: "", request: null, sending: false, actionIdempotencyKey: null, actionRequestBody: null };
  const $ = (id) => document.getElementById(id);
  const nodes = {
    municipality: $("municipality-filter"), start: $("start-filter"), end: $("end-filter"), apply: $("apply-filters"), brief: $("brief-link"),
    message: $("app-message"), feedback: $("filter-feedback"), content: $("dashboard-content"), unavailable: $("unavailable-state"), sidebarContext: $("sidebar-context-value"),
    sidebar: $("sidebar-status"), source: $("source-link"), sourceUpdated: $("source-updated"), period: $("period-description"), title: $("page-title"),
    chart: $("series-chart"), chartWrap: $("chart-wrap"), seriesTable: $("series-table-body"), municipalityTable: $("municipalities-table-body"), municipalitySearch: $("municipality-search"), municipalitiesDescription: $("municipalities-description"),
    qualitySummary: $("quality-summary"), qualityChecks: $("quality-checks"), methodNotes: $("method-notes"), actionMunicipality: $("action-municipality"), actionForm: $("action-form"), actionSubmit: $("action-submit"), actionMessage: $("action-form-message"), actionsList: $("actions-list"), actionsCount: $("actions-count"), refreshActions: $("refresh-actions")
  };
  const number = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
  const integer = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
  const date = new Intl.DateTimeFormat("pt-BR", { timeZone: "UTC" });

  function el(tag, options = {}, children = []) {
    const element = document.createElement(tag);
    Object.entries(options).forEach(([key, value]) => {
      if (key === "text") element.textContent = value == null ? "" : String(value);
      else if (key === "class") element.className = value;
      else if (key === "dataset") Object.assign(element.dataset, value);
      else if (key.startsWith("aria-")) element.setAttribute(key, value);
      else element[key] = value;
    });
    children.forEach((child) => element.append(child));
    return element;
  }
  function clear(element) { element.replaceChildren(); }
  function safeText(value, fallback = "—") { return value === null || value === undefined || value === "" ? fallback : String(value); }
  function valueNumber(value, digits = 0) { return value === null || value === undefined || Number.isNaN(Number(value)) ? "—" : (digits ? number : integer).format(Number(value)); }
  function formatPct(value) { if (value === null || value === undefined || Number.isNaN(Number(value))) return "—"; return `${Number(value) > 0 ? "+" : ""}${number.format(Number(value))}%`; }
  function formatDate(value) { if (!value) return "—"; const parsed = new Date(`${value}T00:00:00Z`); return Number.isNaN(parsed.valueOf()) ? String(value) : date.format(parsed); }
  function setMessage(text = "", isError = false) { nodes.message.hidden = !text; nodes.message.textContent = text; nodes.message.classList.toggle("is-error", isError); }
  function setFormMessage(text = "", success = false) { nodes.actionMessage.textContent = text; nodes.actionMessage.classList.toggle("is-success", success); }
  function apiPath(path, params) { const url = new URL(path, window.location.origin); Object.entries(params || {}).forEach(([key, value]) => { if (value) url.searchParams.set(key, value); }); return `${url.pathname}${url.search}`; }
  function setSource(sourceName, sourceUrl) {
    nodes.source.textContent = safeText(sourceName, "Fonte não informada");
    try { const url = new URL(sourceUrl); if (url.protocol === "http:" || url.protocol === "https:") { nodes.source.href = url.href; nodes.source.hidden = false; return; } } catch (_) { /* invalid external URL */ }
    nodes.source.removeAttribute("href");
  }
  function setLoading(isLoading) {
    nodes.apply.disabled = isLoading; nodes.apply.textContent = isLoading ? "Atualizando…" : "Atualizar análise"; nodes.chartWrap.setAttribute("aria-busy", String(isLoading));
    nodes.feedback.textContent = isLoading ? "Atualizando dados…" : "";
  }
  function currentFilters() { return { municipality: nodes.municipality.value || "all", start: nodes.start.value, end: nodes.end.value }; }
  function updateBriefLink() { nodes.brief.href = apiPath("/api/brief", currentFilters()); }
  function addOptions(select, municipalities, includeAll) {
    const previous = select.value;
    clear(select);
    if (includeAll) select.append(el("option", { value: "all", text: "Todos os municípios" }));
    else select.append(el("option", { value: "", text: "Selecione um município" }));
    (municipalities || []).forEach((item) => select.append(el("option", { value: safeText(item.code, ""), text: safeText(item.name, "Município sem nome") })));
    if ([...select.options].some((option) => option.value === previous)) select.value = previous;
  }
  function populateFilters(data) {
    const filters = data.filters || {};
    addOptions(nodes.municipality, filters.municipalities, true);
    addOptions(nodes.actionMunicipality, filters.municipalities, false);
    const min = filters.min_date || ""; const max = filters.max_date || "";
    nodes.start.min = min; nodes.start.max = max; nodes.end.min = min; nodes.end.max = max;
    if (filters.selected_start) nodes.start.value = filters.selected_start;
    else if (!nodes.start.value && min) nodes.start.value = min;
    if (filters.selected_end) nodes.end.value = filters.selected_end;
    else if (!nodes.end.value && max) nodes.end.value = max;
  }
  function renderSummary(summary) {
    const metrics = [
      ["metric-probable", "metric-probable-note", summary.probable_cases, "No período selecionado", 0],
      ["metric-incidence", "metric-incidence-note", summary.incidence_per_100k, $("metric-incidence-note").textContent, 1],
      ["metric-deaths", "metric-deaths-note", summary.deaths, "Entre casos do período", 0],
      ["metric-comparison", "metric-comparison-note", summary.comparison_pct, "Comparação com as 4 semanas anteriores", "pct"]
    ];
    metrics.forEach(([id, noteId, raw, note, precision]) => {
      const target = $(id); target.textContent = precision === "pct" ? formatPct(raw) : valueNumber(raw, precision);
      target.classList.toggle("positive", precision === "pct" && Number(raw) > 0); target.classList.toggle("negative", precision === "pct" && Number(raw) < 0);
      $(noteId).textContent = raw === null || raw === undefined ? "Indicador indisponível" : note;
    });
  }
  function svgElement(name, attrs = {}) { const node = document.createElementNS("http://www.w3.org/2000/svg", name); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value))); return node; }
  function renderChart(series) {
    clear(nodes.chart); nodes.chart.append(svgElement("desc", { id: "chart-desc" }));
    const desc = nodes.chart.firstElementChild;
    if (!series.length) { desc.textContent = "Não há série temporal no recorte selecionado."; nodes.chart.append(elSvgText("Não há dados para o recorte selecionado.", 380, 128, "middle")); return; }
    desc.textContent = "Gráfico de linha com casos prováveis por semana e média móvel de quatro semanas.";
    const width = 760, height = 270, left = 44, right = 18, top = 18, bottom = 42;
    const values = series.flatMap((row) => [Number(row.probable_cases) || 0, Number(row.moving_average_4) || 0]); const max = Math.max(1, ...values); const x = (index) => left + ((width - left - right) * index / Math.max(1, series.length - 1)); const y = (raw) => top + (height - top - bottom) * (1 - (Number(raw) || 0) / max);
    for (let step = 0; step <= 4; step += 1) { const level = top + ((height - top - bottom) * step / 4); nodes.chart.append(svgElement("line", { x1:left, y1:level, x2:width-right, y2:level, class:"chart-grid" })); const label = svgElement("text", { x:left-8, y:level+4, "text-anchor":"end", class:"chart-label" }); label.textContent = integer.format(Math.round(max * (1 - step / 4))); nodes.chart.append(label); }
    const casePoints = series.map((row, index) => `${x(index)},${y(row.probable_cases)}`).join(" ");
    nodes.chart.append(svgElement("polyline", { points:casePoints, class:"chart-line" }));
    const avgRows = series.filter((row) => row.moving_average_4 !== null && row.moving_average_4 !== undefined);
    if (avgRows.length > 1) nodes.chart.append(svgElement("polyline", { points:avgRows.map((row) => `${x(series.indexOf(row))},${y(row.moving_average_4)}`).join(" "), class:"chart-average" }));
    series.forEach((row, index) => { const dot = svgElement("circle", { cx:x(index), cy:y(row.probable_cases), r:row.provisional ? 4 : 2.7, class:row.provisional ? "chart-provisional" : "chart-dot" }); const title = svgElement("title"); title.textContent = `${safeText(row.week_label, row.week_start)}: ${valueNumber(row.probable_cases)} casos prováveis`; dot.append(title); nodes.chart.append(dot); });
    const interval = Math.max(1, Math.ceil(series.length / 6));
    series.forEach((row, index) => { if (index % interval === 0 || index === series.length - 1) { const label = svgElement("text", { x:x(index), y:height-14, "text-anchor": index === 0 ? "start" : index === series.length - 1 ? "end" : "middle", class:"chart-label" }); label.textContent = safeText(row.week_label, row.week_start); nodes.chart.append(label); } });
  }
  function elSvgText(text, x, y, anchor) { const node = svgElement("text", { x, y, "text-anchor":anchor, class:"chart-label" }); node.textContent = text; return node; }
  function renderSeriesTable(series) {
    clear(nodes.seriesTable);
    if (!series.length) { const row = el("tr"); row.append(el("td", { text:"Sem registros no recorte.", colSpan:5 })); nodes.seriesTable.append(row); return; }
    series.forEach((item) => { const row = el("tr"); [safeText(item.week_label, item.week_start), valueNumber(item.probable_cases), valueNumber(item.moving_average_4, 1), valueNumber(item.deaths), item.provisional ? "Provisória" : "Consolidada"].forEach((value) => row.append(el("td", { text:value }))); nodes.seriesTable.append(row); });
  }
  function renderSignals(signals) {
    const panel = $("signals-panel"); const list = $("signals-list"); clear(list);
    if (!Array.isArray(signals) || !signals.length) { panel.hidden = true; return; }
    signals.forEach((signal) => { const item = el("article", { class:"signal-item" }); const heading = [signal.municipality_name, signal.label].filter(Boolean).join(" · ") || "Sinal a verificar"; item.append(el("h3", { text:heading }), el("p", { text:safeText(signal.detail, "Sem detalhe disponível.") })); list.append(item); });
    panel.hidden = false;
  }
  function comparator(left, right) { const key = state.sort.key; const a = left[key], b = right[key]; if (key === "name") return String(a || "").localeCompare(String(b || ""), "pt-BR"); return (Number(a) || -Infinity) - (Number(b) || -Infinity); }
  function renderMunicipalities(rows) {
    const search = state.search.trim().toLocaleLowerCase("pt-BR"); const filtered = rows.filter((row) => !search || String(row.name || "").toLocaleLowerCase("pt-BR").includes(search)).sort((a,b) => comparator(a,b) * (state.sort.direction === "asc" ? 1 : -1));
    clear(nodes.municipalityTable);
    nodes.municipalitiesDescription.textContent = `${integer.format(filtered.length)} de ${integer.format(rows.length)} municípios no recorte.`;
    if (!filtered.length) { const row = el("tr"); row.append(el("td", { text:"Nenhum município encontrado.", colSpan:5 })); nodes.municipalityTable.append(row); return; }
    const maxCases = Math.max(1, ...filtered.map((row) => Number(row.probable_cases) || 0));
    filtered.forEach((item) => { const row = el("tr"); row.append(el("td", { text:safeText(item.name, "Não informado") })); const cases = el("td"); const bar = el("div", { class:"number-bar" }); const progress = el("progress", { max:maxCases, value:Math.max(0, Number(item.probable_cases) || 0) }); progress.setAttribute("aria-label", `${valueNumber(item.probable_cases)} casos prováveis, máximo visual de ${valueNumber(maxCases)}`); bar.append(progress, el("span", { text:valueNumber(item.probable_cases) })); cases.append(bar); row.append(cases); row.append(el("td", { text:valueNumber(item.incidence_per_100k, 1) })); row.append(el("td", { text:valueNumber(item.deaths) })); const comparison = el("td", { text:formatPct(item.comparison_pct), class:"variation" }); comparison.classList.toggle("up", Number(item.comparison_pct) > 0); comparison.classList.toggle("down", Number(item.comparison_pct) < 0); row.append(comparison); nodes.municipalityTable.append(row); });
    document.querySelectorAll(".sort-button").forEach((button) => button.setAttribute("aria-sort", button.dataset.sort === state.sort.key ? (state.sort.direction === "asc" ? "ascending" : "descending") : "none"));
  }
  function qualityStatus(status) { const normalized = String(status || "").toLowerCase(); return normalized.includes("ok") || normalized.includes("aprov") || normalized.includes("pass") ? "is-ok" : normalized.includes("alert") || normalized.includes("erro") || normalized.includes("fail") ? "is-alert" : "is-warn"; }
  function qualityStatusLabel(status) { const normalized = String(status || "").toLowerCase(); if (normalized.includes("pass") || normalized.includes("ok") || normalized.includes("aprov")) return "Conferido"; if (normalized.includes("fail") || normalized.includes("erro")) return "Falha"; if (normalized.includes("warn") || normalized.includes("alert") || normalized.includes("atenç")) return "Atenção"; return "Sem classificação"; }
  function renderQuality(quality = {}) {
    clear(nodes.qualitySummary); clear(nodes.qualityChecks);
    [["Registros na fonte nacional", quality.rows_read], ["Registros do território", quality.rows_in_scope], ["Registros válidos", quality.valid_rows], ["Datas inválidas", quality.invalid_dates]].forEach(([label, raw]) => { const card = el("div", { class:"quality-stat" }); card.append(el("span", { text:label }), el("strong", { text:valueNumber(raw) })); nodes.qualitySummary.append(card); });
    const checks = Array.isArray(quality.checks) ? quality.checks : [];
    if (!checks.length) { nodes.qualityChecks.append(el("p", { class:"muted", text:"Nenhuma verificação de qualidade foi informada para este recorte." })); return; }
    checks.forEach((check) => { const card = el("article", { class:`quality-check ${qualityStatus(check.status)}` }); card.append(el("h3", { text:safeText(check.name, "Verificação") }), el("span", { class:"small", text:qualityStatusLabel(check.status) }), el("p", { text:safeText(check.detail, "Sem detalhe disponível.") })); nodes.qualityChecks.append(card); });
  }
  function renderMeta(meta, summary) {
    nodes.title.textContent = meta.title && meta.title !== "Vigia Saúde" ? meta.title : "Panorama de dengue"; nodes.period.textContent = meta.period_start && meta.period_end ? `${safeText(meta.territory, "Paraná")} · ${formatDate(meta.period_start)} a ${formatDate(meta.period_end)}` : safeText(meta.territory, "Período não informado"); setSource(meta.source_name, meta.source_url); nodes.sourceUpdated.textContent = meta.source_updated_at ? `Atualizada em ${formatDate(meta.source_updated_at)}` : ""; nodes.methodNotes.textContent = Array.isArray(meta.notes) ? meta.notes.filter(Boolean).join("\n") : "";
    const populationReference = String(meta.population_reference || "");
    const populationSource = /ibge/i.test(populationReference) ? "IBGE" : "referência";
    const populationYear = meta.population_year ? ` ${meta.population_year}` : "";
    const isApproximation = /aproxima|estimativa/i.test(populationReference);
    $("metric-incidence-note").textContent = meta.population_year ? `Por 100 mil · população ${populationSource}${populationYear}${isApproximation ? " (aproximação)" : ""}` : "Por 100 mil habitantes";
    nodes.sidebarContext.textContent = [meta.condition, meta.territory].filter(Boolean).join(" · ") || "Vigilância epidemiológica";
    const ready = Boolean(meta.available && summary); nodes.sidebar.textContent = ready ? "Dados disponíveis" : "Dados indisponíveis"; document.querySelector(".data-dot").classList.toggle("is-ready", ready);
  }
  function renderDashboard(data) {
    const meta = data.meta || {}; const summary = data.summary;
    state.dashboard = data; populateFilters(data); renderMeta(meta, summary); updateBriefLink();
    const unavailable = meta.available === false || !summary;
    nodes.unavailable.hidden = !unavailable; nodes.content.hidden = unavailable;
    if (unavailable) { renderQuality(data.quality); return; }
    renderSummary(summary); renderSignals(data.signals); const series = Array.isArray(data.series) ? data.series : []; renderChart(series); renderSeriesTable(series); renderMunicipalities(Array.isArray(data.municipalities) ? data.municipalities : []); renderQuality(data.quality);
  }
  async function fetchDashboard() {
    if (state.request) state.request.abort(); state.request = new AbortController(); setLoading(true); setMessage("");
    try { const response = await fetch(apiPath("/api/dashboard", currentFilters()), { signal:state.request.signal, headers:{ Accept:"application/json" } }); const payload = await response.json().catch(() => ({})); if (!response.ok) throw new Error(payload.error || "Não foi possível atualizar os dados."); renderDashboard(payload); }
    catch (error) { if (error.name !== "AbortError") { setMessage(error.message || "Não foi possível carregar os dados. Tente novamente.", true); nodes.sidebar.textContent = "Falha ao carregar"; } }
    finally { setLoading(false); state.request = null; }
  }
  function actionStatusLabel(status) { return status === "concluida" ? "Concluída" : "Aberta"; }
  function renderActions() {
    clear(nodes.actionsList); nodes.actionsCount.textContent = `${integer.format(state.actions.length)} ${state.actions.length === 1 ? "ação registrada" : "ações registradas"}`;
    if (!state.actions.length) { nodes.actionsList.append(el("p", { class:"muted", text:"Nenhuma ação registrada até o momento." })); return; }
    state.actions.forEach((action) => { const item = el("article", { class:`action-item ${action.status === "concluida" ? "is-complete" : ""}` }); const title = el("h4", { text:safeText(action.title, "Ação sem título") }); const pill = el("span", { class:`status-pill ${action.status === "concluida" ? "done" : ""}`, text:actionStatusLabel(action.status) }); const header = el("header", {}, [title, pill]); const meta = el("div", { class:"action-meta" }); [action.municipality_name, action.owner ? `Responsável: ${action.owner}` : "", action.due_date ? `Prazo: ${formatDate(action.due_date)}` : ""].filter(Boolean).forEach((text) => meta.append(el("span", { text }))); item.append(header, meta, el("p", { text:safeText(action.description, "Sem descrição.") })); if (action.status !== "concluida") { const button = el("button", { type:"button", class:"button button-secondary complete-button", text:"Marcar como concluída" }); button.addEventListener("click", () => completeAction(action.id, button)); item.append(button); } nodes.actionsList.append(item); });
  }
  async function fetchActions() {
    nodes.refreshActions.disabled = true;
    try { const response = await fetch("/api/actions", { headers:{ Accept:"application/json" } }); const payload = await response.json().catch(() => ({})); if (!response.ok) throw new Error(payload.error || "Não foi possível carregar as ações."); state.actions = Array.isArray(payload.actions) ? payload.actions : []; renderActions(); }
    catch (error) { setFormMessage(error.message || "Não foi possível carregar as ações."); }
    finally { nodes.refreshActions.disabled = false; }
  }
  async function completeAction(id, button) {
    if (!id || button.disabled) return; button.disabled = true; button.textContent = "Concluindo…";
    try { const response = await fetch(`/api/actions/${encodeURIComponent(id)}`, { method:"PATCH", headers:{ "Content-Type":"application/json", "X-Vigia-Client":"local", Accept:"application/json" }, body:JSON.stringify({ status:"concluida" }) }); const payload = await response.json().catch(() => ({})); if (!response.ok) throw new Error(payload.error || "Não foi possível concluir a ação."); await fetchActions(); }
    catch (error) { button.disabled = false; button.textContent = "Marcar como concluída"; setFormMessage(error.message || "Não foi possível concluir a ação."); }
  }
  async function submitAction(event) {
    event.preventDefault(); if (state.sending) return; const form = new FormData(nodes.actionForm); const body = { municipality_code:String(form.get("municipality_code") || "").trim(), title:String(form.get("title") || "").trim(), owner:String(form.get("owner") || "").trim(), due_date:String(form.get("due_date") || "").trim(), description:String(form.get("description") || "").trim() };
    if (Object.values(body).some((value) => !value)) { setFormMessage("Preencha todos os campos para registrar a ação."); return; }
    state.sending = true; nodes.actionSubmit.disabled = true; nodes.actionSubmit.textContent = "Registrando…"; setFormMessage("");
    const requestBody = JSON.stringify(body);
    if (state.actionRequestBody !== requestBody) { state.actionRequestBody = requestBody; state.actionIdempotencyKey = crypto.randomUUID(); }
    try { const response = await fetch("/api/actions", { method:"POST", headers:{ "Content-Type":"application/json", "X-Vigia-Client":"local", "Idempotency-Key":state.actionIdempotencyKey, Accept:"application/json" }, body:requestBody }); const payload = await response.json().catch(() => ({})); if (!response.ok) throw new Error(payload.error || "Não foi possível registrar a ação."); nodes.actionForm.reset(); state.actionIdempotencyKey = null; state.actionRequestBody = null; setFormMessage("Ação registrada.", true); await fetchActions(); }
    catch (error) { setFormMessage(error.message || "Não foi possível registrar a ação."); }
    finally { state.sending = false; nodes.actionSubmit.disabled = false; nodes.actionSubmit.textContent = "Registrar ação"; }
  }
  function bindEvents() {
    nodes.apply.addEventListener("click", fetchDashboard); [nodes.municipality, nodes.start, nodes.end].forEach((field) => field.addEventListener("change", updateBriefLink)); nodes.municipalitySearch.addEventListener("input", () => { state.search = nodes.municipalitySearch.value; if (state.dashboard) renderMunicipalities(state.dashboard.municipalities || []); });
    document.querySelectorAll(".sort-button").forEach((button) => button.addEventListener("click", () => { const key = button.dataset.sort; state.sort.direction = state.sort.key === key && state.sort.direction === "desc" ? "asc" : "desc"; state.sort.key = key; if (state.dashboard) renderMunicipalities(state.dashboard.municipalities || []); }));
    nodes.actionForm.addEventListener("submit", submitAction); nodes.refreshActions.addEventListener("click", fetchActions); document.querySelectorAll(".nav-link").forEach((link) => link.addEventListener("click", () => { document.querySelectorAll(".nav-link").forEach((nav) => nav.classList.toggle("is-active", nav === link)); }));
  }
  bindEvents(); fetchDashboard(); fetchActions();
})();
