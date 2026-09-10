"""Indicadores descritivos calculados exclusivamente sobre dados agregados."""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import threading

import pandas as pd
from vigia.sources import sha256

METRICS = ["notifications", "probable_cases", "confirmed_cases", "deaths"]


def sunday(value: date) -> date:
    return value - timedelta(days=(value.weekday() + 1) % 7)


def epi_label(value: date) -> str:
    midweek = value + timedelta(days=3)
    year = midweek.year
    first = sunday(date(year, 1, 4))
    return f"SE {(value - first).days // 7 + 1:02d}/{year}"


def comparison_counts(frame: pd.DataFrame, start: date, end: date) -> tuple[int, int] | None:
    """Contagens em 4 semanas completas e nas 4 anteriores."""
    last = sunday(end)
    if last + timedelta(days=6) > end:
        last -= timedelta(days=7)
    first = last - timedelta(days=49)
    if first < start:
        return None
    recent_start = last - timedelta(days=21)
    recent = int(frame.loc[(frame.index >= recent_start.isoformat()) & (frame.index <= last.isoformat()), "probable_cases"].sum())
    previous = int(frame.loc[(frame.index >= first.isoformat()) & (frame.index < recent_start.isoformat()), "probable_cases"].sum())
    return recent, previous


def comparison(frame: pd.DataFrame, start: date, end: date) -> float | None:
    counts = comparison_counts(frame, start, end)
    if counts is None or counts[1] == 0:
        return None
    recent, previous = counts
    return round(100 * (recent - previous) / previous, 1)


class DatasetStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self._key = None
        self._manifest = None
        self._weekly = None
        self._lock = threading.Lock()

    def load(self) -> tuple[dict, pd.DataFrame] | None:
        pointer = self.data_dir / "current.json"
        if not pointer.exists():
            return None
        with self._lock:
            key = pointer.read_text(encoding="utf-8")
            if key != self._key:
                snapshot = (self.data_dir / json.loads(key)["snapshot"]).resolve()
                if not snapshot.is_relative_to(self.data_dir / "snapshots"):
                    raise ValueError("Ponteiro de dados fora do diretório de snapshots.")
                manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
                gold = snapshot / "gold" / "weekly.parquet"
                expected = manifest.get("checksums", {}).get("gold/weekly.parquet")
                if manifest.get("schema_version") != 1 or not expected or sha256(gold) != expected:
                    raise ValueError("Integridade do snapshot inválida. Reprocesse a fonte antes de usar os indicadores.")
                weekly = pd.read_parquet(gold)
                required = {"municipality_code", "municipality_name", "week_start", "week_label", *METRICS}
                if set(weekly.columns) != required or weekly[METRICS].isna().any().any():
                    raise ValueError("Schema da camada Gold inválido.")
                weekly["week_start"] = weekly["week_start"].astype(str).str[:10]
                weekly["municipality_code"] = weekly["municipality_code"].astype(str)
                if weekly.duplicated(["municipality_code", "week_start"]).any():
                    raise ValueError("Grão município-semana duplicado no snapshot.")
                if not all(pd.api.types.is_integer_dtype(weekly[metric]) for metric in METRICS) or (weekly[METRICS] < 0).any().any():
                    raise ValueError("Contagens inválidas no snapshot.")
                if ((weekly["probable_cases"] > weekly["notifications"]) | (weekly["confirmed_cases"] > weekly["probable_cases"]) | (weekly["deaths"] > weekly["confirmed_cases"])).any():
                    raise ValueError("Contagens incompatíveis com as definições dos indicadores.")
                if not set(weekly["municipality_code"]).issubset({item["code"] for item in manifest["municipalities"]}):
                    raise ValueError("Municípios desconhecidos na camada Gold.")
                days = pd.to_datetime(weekly["week_start"], errors="raise")
                if not days.dt.dayofweek.eq(6).all():
                    raise ValueError("Semanas epidemiológicas devem iniciar no domingo.")
                self._manifest, self._weekly, self._key = manifest, weekly, key
            return self._manifest, self._weekly.copy()

    def municipality_options(self) -> list[dict]:
        loaded = self.load()
        return loaded[0]["municipalities"] if loaded else []

    def dashboard(self, params: dict[str, str]) -> dict:
        loaded = self.load()
        if not loaded:
            return {"meta": {"available": False}, "filters": {"municipalities": []}, "summary": {},
                    "series": [], "municipalities": [], "signals": [], "quality": {"checks": []}}
        manifest, weekly = loaded
        config = manifest["config"]
        source = manifest["source"]
        municipalities = manifest["municipalities"]
        codes = {item["code"] for item in municipalities}
        selected = params.get("municipality", "all")
        if selected != "all" and selected not in codes:
            raise ValueError("Município não pertence ao território deste conjunto.")
        observed_start = date.fromisoformat(manifest.get("event_start") or f"{config['year']}-01-01")
        min_date = sunday(observed_start)
        cutoff = date.fromisoformat(config["cutoff_date"])
        event_end = manifest.get("event_end")
        if not event_end:
            # Snapshots legados só informam a última semana com observações.
            # Não estender o gráfico até a data técnica de atualização do arquivo.
            event_end = (date.fromisoformat(str(manifest["period_end"])[:10]) + timedelta(days=6)).isoformat() if manifest.get("period_end") else None
        if not event_end:
            return {"meta": {"available": False}, "filters": {"municipalities": []}, "summary": {},
                    "series": [], "municipalities": [], "signals": [], "quality": manifest["quality"]}
        max_date = min(cutoff, date.fromisoformat(event_end), date(int(config["year"]), 12, 31))
        requested_start = date.fromisoformat(params.get("start") or min_date.isoformat())
        requested_end = date.fromisoformat(params.get("end") or max_date.isoformat())
        if requested_start > requested_end or requested_end < min_date or requested_start > max_date:
            raise ValueError("Selecione um intervalo válido dentro do período disponível.")
        start = max(min_date, sunday(requested_start))
        end = min(max_date, sunday(requested_end) + timedelta(days=6))
        calendar = [d.date().isoformat() for d in pd.date_range(start, end, freq="W-SUN")]
        scope = [item for item in municipalities if selected == "all" or item["code"] == selected]
        scoped_codes = {item["code"] for item in scope}
        frame = weekly.loc[weekly["municipality_code"].isin(scoped_codes) &
                           (weekly["week_start"] >= start.isoformat()) & (weekly["week_start"] <= end.isoformat())]
        totals = frame.groupby("week_start")[METRICS].sum().reindex(calendar, fill_value=0)
        summary = {metric: int(totals[metric].sum()) for metric in METRICS}
        population = sum(item["population"] for item in scope) if all(item.get("population") for item in scope) else None
        summary["incidence_per_100k"] = round(summary["probable_cases"] / population * 100000, 2) if population else None
        summary["comparison_pct"] = comparison(totals, max(start, observed_start), end)
        summary["municipalities_reporting"] = int(frame.loc[frame["notifications"] > 0, "municipality_code"].nunique())
        average = totals["probable_cases"].rolling(4, min_periods=4).mean()
        provisional_from = sunday(max_date) - timedelta(weeks=int(config.get("provisional_weeks", 2)) - 1)
        series = []
        for day, row in totals.iterrows():
            series.append({"week_start": day, "week_label": epi_label(date.fromisoformat(day)),
                           **{metric: int(row[metric]) for metric in METRICS},
                           "moving_average_4": None if pd.isna(average.loc[day]) else round(float(average.loc[day]), 2),
                           "provisional": date.fromisoformat(day) >= provisional_from})
        municipal = []
        for item in scope:
            per_city = frame.loc[frame["municipality_code"] == item["code"]].groupby("week_start")[METRICS].sum().reindex(calendar, fill_value=0)
            counts = {metric: int(per_city[metric].sum()) for metric in METRICS}
            window_counts = comparison_counts(per_city, max(start, observed_start), end)
            denominator = item.get("population")
            municipal.append({"code": item["code"], "name": item["name"], **counts, "population": denominator,
                              "incidence_per_100k": round(counts["probable_cases"] / denominator * 100000, 2) if denominator else None,
                              "recent_4_weeks": window_counts[0] if window_counts else None,
                              "previous_4_weeks": window_counts[1] if window_counts else None,
                              "comparison_pct": comparison(per_city, max(start, observed_start), end)})
        municipal.sort(key=lambda row: (-row["probable_cases"], row["name"]))
        notes = [
            "Contagens por município de residência e semana de início dos sintomas. Filtros abrangem semanas epidemiológicas inteiras, de domingo a sábado.",
            "Casos prováveis: notificações não descartadas, excluindo reclassificações para chikungunya; registros sem encerramento permanecem provisórios.",
            "Óbitos confirmados pelo agravo entre os casos com sintomas no período: classificação final de dengue, critério laboratorial ou clínico-epidemiológico e evolução 2. Não representa mortes pela data de ocorrência.",
            f"Denominador: {config.get('population_reference', 'IBGE')}. A taxa não é padronizada por idade.",
            "Variação: últimas 4 semanas completas do recorte versus as 4 anteriores. Base anterior zero ou menos de 8 semanas completas: indicador indisponível.",
            "As duas semanas mais recentes são destacadas como provisórias por regra de apresentação; períodos anteriores também podem sofrer revisões. Não é previsão nem classificação oficial de risco.",
            "Qualidade descreve a ingestão do território inteiro, independentemente dos filtros do painel. Ausências de início dos sintomas ficam fora da série e são contabilizadas na qualidade.",
            "A base aberta não fornece identificador estável suficiente para aferir duplicidades de pessoas. Notificações não equivalem a pessoas únicas.",
            f"Último início de sintomas observado no território: {max_date.isoformat()}. Data técnica do arquivo: {cutoff.isoformat()}. Não são criadas semanas posteriores ao último evento observado.",
            "Zero dentro do intervalo observado significa ausência de registros elegíveis no arquivo; não comprova ausência de transmissão ou completude da notificação.",
        ]
        if source.get("catalog_updated_at") and source.get("updated_at") != source.get("catalog_updated_at"):
            notes.append(f"Datas distintas na fonte: catálogo {source['catalog_updated_at']}; última modificação do arquivo {source.get('updated_at')}. A data de corte técnica utiliza o arquivo.")
        notes.extend(str(note) for note in manifest.get("notes", []))
        quality = {**manifest["quality"], "checks": []}
        check_labels = {
            "territorial_reconciliation": ("Reconciliação territorial", "O total recebido confere com a soma dos registros do território, de fora do território e sem município identificável."),
            "temporal_reconciliation": ("Reconciliação das datas", "Os registros do território foram distribuídos entre datas válidas e exclusões documentadas."),
            "aggregate_reconciliation": ("Conferência dos totais", "As contagens agregadas conferem com os registros elegíveis na fonte."),
            "pii_output_contract": ("Conteúdo das saídas", "As tabelas consumidas pela aplicação contêm somente agregados por município e semana."),
        }
        for check in manifest["quality"].get("checks", []):
            name, detail = check_labels.get(check["name"], (check["name"], check.get("detail", "")))
            quality["checks"].append({"name": name, "status": check["status"], "detail": detail})
        pending = quality.get("unknown_classification", 0)
        if pending:
            quality["checks"].append({"name": "Classificação dos casos", "status": "warning", "detail": f"{pending} registros do território sem classificação final reconhecida permanecem provisórios na contagem de casos prováveis."})
        if not quality.get("duplicates_checked"):
            quality["checks"].append({"name": "Duplicidade não aferida", "status": "warning", "detail": "Sem identificador estável suficiente para verificar pessoas únicas. A contagem representa registros de notificação."})
        return {"meta": {"available": True, "title": "Panorama de dengue", "territory": config.get("territory", "Território"),
                         "condition": config.get("condition", "Dengue"), "source_name": config["source_name"],
                         "source_url": config["source_url"], "source_updated_at": source.get("updated_at"),
                         "extracted_at": source.get("extracted_at"), "period_start": start.isoformat(), "period_end": end.isoformat(),
                         "population_year": config.get("population_year"), "population_reference": config.get("population_reference"),
                         "metric_label": "Casos prováveis", "notes": notes},
                "filters": {"municipalities": [{"code": item["code"], "name": item["name"]} for item in municipalities],
                            "min_date": min_date.isoformat(), "max_date": max_date.isoformat(),
                            "selected_start": start.isoformat(), "selected_end": end.isoformat()},
                "summary": summary, "series": series, "municipalities": municipal, "demographics": [],
                "quality": quality, "signals": [
                    {"municipality_code": row["code"], "municipality_name": row["name"], "level": "info",
                     "label": "Aumento observado entre blocos de 4 semanas",
                     "detail": f"De {row['previous_4_weeks']} para {row['recent_4_weeks']} casos prováveis (+{str(row['comparison_pct']).replace('.', ',')}%). Verifique pequenos números, atraso e encerramento antes de definir providências."}
                    for row in sorted(municipal, key=lambda item: item["probable_cases"], reverse=True)
                    if row["comparison_pct"] is not None and row["comparison_pct"] > 0
                ][:5]}


def bulletin(data: dict) -> str:
    if not data["meta"].get("available"):
        raise ValueError("Ainda não existe conjunto publicado localmente.")
    meta, summary = data["meta"], data["summary"]
    def shown(value):
        return "indisponível" if value is None else str(value)
    lines = ["# Boletim descritivo — Vigia Saúde", "", f"Território: {meta['territory']}",
             f"Período (início de sintomas): {meta['period_start']} a {meta['period_end']}",
             f"Municípios no recorte: {len(data['municipalities'])}",
             f"Arquivo da fonte modificado em: {meta['source_updated_at']}", f"Extração: {meta['extracted_at']}", "",
             f"- Notificações: {summary['notifications']}", f"- Casos prováveis: {summary['probable_cases']}",
             f"- Incidência por 100 mil habitantes: {shown(summary['incidence_per_100k'])}",
             f"- Óbitos confirmados entre casos do período: {summary['deaths']}",
             f"- Variação entre blocos de 4 semanas completas (%): {shown(summary['comparison_pct'])}", "",
             "## Municípios do recorte", "", "| Município | Casos prováveis | Incidência / 100 mil |", "|---|---:|---:|"]
    lines += [f"| {row['name'].replace('|', '/')} | {row['probable_cases']} | {shown(row['incidence_per_100k'])} |" for row in data["municipalities"]]
    lines += ["", "## Fonte e interpretação", "", f"[{meta['source_name']}]({meta['source_url']})", ""]
    lines += [f"- {note}" for note in meta["notes"]]
    lines += ["", "Este boletim descreve registros e requer avaliação pela equipe de vigilância. Não prescreve medidas sanitárias nem atribui efeito causal a intervenções.", ""]
    return "\n".join(lines)
