"""Aggregate-only ingestion for the Vigia Saúde dengue module."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

import pandas as pd


SCHEMA_VERSION = 1
REQUIRED_COLUMNS = ("ID_MN_RESI", "DT_SIN_PRI", "CLASSI_FIN", "EVOLUCAO", "CRITERIO")
GOLD_COLUMNS = (
    "municipality_code", "municipality_name", "week_start", "week_label",
    "notifications", "probable_cases", "confirmed_cases", "deaths",
)


def build_dataset(
    csv_path: Path,
    output_dir: Path,
    municipalities: list[dict[str, Any]],
    config: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    """Build an immutable municipality-by-week snapshot from a SINAN CSV.

    The raw input is read in chunks. Case rows are never written: Silver and
    Gold contain only the documented aggregate grain. ``output_dir`` must be a
    new (or empty) snapshot directory; an existing snapshot is never replaced.
    """
    csv_path, output_dir = Path(csv_path), Path(output_dir)
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV de origem não encontrado: {csv_path}")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Snapshot de saída já existe e não será alterado: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    settings = _validate_config(config)
    municipality_index, municipality_manifest = _validate_municipalities(municipalities)
    columns = _read_columns(csv_path, settings["encoding"], settings["separator"])
    missing = sorted(set(REQUIRED_COLUMNS) - set(columns))
    if missing:
        raise ValueError(f"CSV não atende ao contrato: colunas ausentes {missing}")

    quality = _empty_quality()
    aggregate: defaultdict[tuple[str, str, str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    input_hash = _sha256_file(csv_path)
    reader = pd.read_csv(
        csv_path, sep=settings["separator"], encoding=settings["encoding"],
        usecols=list(REQUIRED_COLUMNS), dtype="string", chunksize=settings["chunksize"],
        low_memory=False,
    )
    for chunk in reader:
        _process_chunk(chunk, municipality_index, settings, quality, aggregate)

    weekly = _weekly_frame(aggregate)
    _add_quality_checks(quality, weekly)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=str(output_dir.parent)))
    try:
        silver_path = staging / "silver" / "municipality_weekly.parquet"
        gold_path = staging / "gold" / "weekly.parquet"
        silver_path.parent.mkdir(parents=True)
        gold_path.parent.mkdir(parents=True)
        weekly.to_parquet(silver_path, engine="pyarrow", index=False)
        weekly.to_parquet(gold_path, engine="pyarrow", index=False)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "source": {**source, "input_checksum_sha256": input_hash},
            "config": _manifest_config(config, settings),
            "quality": quality,
            "municipalities": municipality_manifest,
            "period_start": None if weekly.empty else str(weekly["week_start"].min()),
            "period_end": None if weekly.empty else str(weekly["week_start"].max()),
            "event_start": quality["valid_onset_min"],
            "event_end": quality["valid_onset_max"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "row_count": int(len(weekly)),
            "checksums": {
                "silver/municipality_weekly.parquet": _sha256_file(silver_path),
                "gold/weekly.parquet": _sha256_file(gold_path),
            },
            "notes": [
                "Dados individuais não são persistidos nas camadas Silver ou Gold.",
                "Duplicidade não aferida sem identificador estável.",
                "Classificação confirmada depende de validação pela vigilância.",
            ],
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
        if output_dir.exists():
            output_dir.rmdir()
        os.replace(staging, output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def epidemiological_week(value: date) -> tuple[date, int, int]:
    """Return Sunday start, number and epi-year; week 1 has >=4 days in year."""
    sunday = value.fromordinal(value.toordinal() - ((value.weekday() + 1) % 7))
    # Thursday identifies the epidemiological year of a Sunday--Saturday week.
    # This covers both January dates in the prior epi-year and late December
    # dates in week 1 of the following epi-year.
    epi_year = (sunday + timedelta(days=3)).year
    first = _week_one_start(epi_year)
    return sunday, ((sunday - first).days // 7) + 1, epi_year


def _week_one_start(year: int) -> date:
    january_first = date(year, 1, 1)
    sunday = january_first.fromordinal(january_first.toordinal() - ((january_first.weekday() + 1) % 7))
    return sunday if 7 - (january_first - sunday).days >= 4 else sunday.fromordinal(sunday.toordinal() + 7)


def _validate_config(config: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in ("uf", "year", "source_updated_at", "case_definition") if key not in config]
    if missing:
        raise ValueError(f"Configuração obrigatória ausente: {missing}")
    uf, year = str(config["uf"]).zfill(2), int(config["year"])
    if not uf.isdigit() or len(uf) != 2 or not 1900 <= year <= 2100:
        raise ValueError("UF ou ano inválido na configuração")
    if not config["case_definition"]:
        raise ValueError("config.case_definition deve documentar a definição de caso")
    cutoff = _parse_date(config.get("cutoff_date", config["source_updated_at"]))
    separator, chunksize = str(config.get("separator", ";")), int(config.get("chunksize", 100_000))
    if len(separator) != 1 or chunksize < 1:
        raise ValueError("separator deve ter um caractere e chunksize deve ser positivo")
    confirmed = {_normalise_category(value) for value in config.get("confirmed_classifications", ("10", "11", "12"))}
    excluded = {_normalise_category(value) for value in config.get("excluded_classifications", ("5", "13"))}
    excluded |= {_normalise_category(value) for value in config.get("other_aggravation_classifications", ())}
    death_criteria = {_normalise_category(value) for value in config.get("death_confirmation_criteria", ("1", "2"))}
    confirmed.discard(None)
    excluded.discard(None)
    death_criteria.discard(None)
    if not confirmed or not death_criteria or confirmed & excluded:
        raise ValueError("Configuração de classificação/critério de óbito inválida")
    return {
        "uf": uf, "year": year, "cutoff": cutoff, "encoding": str(config.get("encoding", "utf-8")),
        "separator": separator, "chunksize": chunksize, "confirmed": confirmed,
        "excluded": excluded, "death_criteria": death_criteria,
    }


def _parse_date(value: Any) -> date:
    """Read a source-declared calendar date without timezone conversion."""
    text = str(value).strip()
    if len(text) >= 10 and text[:4].isdigit() and text[4:5] == "-":
        text = text[:10]
    parsed = pd.to_datetime(
        text,
        format="%Y-%m-%d" if len(text) == 10 and text[:4].isdigit() and text[4:5] == "-" else "%d/%m/%Y",
        errors="coerce",
    )
    if pd.isna(parsed):
        raise ValueError("cutoff_date/source_updated_at deve ser uma data válida")
    return parsed.date()


def _validate_municipalities(
    municipalities: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if not municipalities:
        raise ValueError("A lista de municípios do território não pode ser vazia")
    index: dict[str, dict[str, Any]] = {}
    cleaned: list[dict[str, Any]] = []
    for record in municipalities:
        code, name = _normalise_municipality(record.get("code")), str(record.get("name", "")).strip()
        if code is None or len(code) != 6 or not code.isdigit() or not name or code in index:
            raise ValueError("Municípios precisam de code IBGE único de seis dígitos e name")
        population, population_year = record.get("population"), record.get("population_year")
        if population is not None and (not isinstance(population, int) or population < 0):
            raise ValueError(f"População inválida para município {code}")
        if population_year is not None and not isinstance(population_year, int):
            raise ValueError(f"Ano populacional inválido para município {code}")
        item = {"code": code, "name": name, "population": population, "population_year": population_year}
        index[code] = item
        cleaned.append(item)
    return index, sorted(cleaned, key=lambda item: item["code"])


def _read_columns(path: Path, encoding: str, separator: str) -> list[str]:
    return pd.read_csv(path, sep=separator, encoding=encoding, nrows=0).columns.tolist()


def _empty_quality() -> dict[str, Any]:
    return {
        "rows_read": 0, "rows_in_scope": 0, "valid_rows": 0, "invalid_dates": 0,
        "missing_municipality": 0, "missing_onset": 0, "unknown_classification": 0,
        "duplicates_checked": False, "invalid_municipality": 0, "outside_uf": 0,
        "unknown_municipality": 0, "onset_outside_year": 0, "after_cutoff": 0,
        "discarded_or_other_aggravation": 0, "checks": [],
        "valid_onset_min": None, "valid_onset_max": None,
    }


def _process_chunk(
    chunk: pd.DataFrame, municipality_index: dict[str, dict[str, Any]], settings: dict[str, Any],
    quality: dict[str, Any], aggregate: defaultdict[tuple[str, str, str, str], list[int]],
) -> None:
    quality["rows_read"] += len(chunk)
    municipality = chunk["ID_MN_RESI"].map(_normalise_municipality)
    missing_municipality = municipality.isna()
    invalid_municipality = ~missing_municipality & ~municipality.str.match(r"^\d{6}$", na=False)
    formatted = ~(missing_municipality | invalid_municipality)
    outside_uf = formatted & ~municipality.str.startswith(settings["uf"], na=False)
    candidate = formatted & ~outside_uf
    known = candidate & municipality.isin(municipality_index)
    quality["missing_municipality"] += int(missing_municipality.sum())
    quality["invalid_municipality"] += int(invalid_municipality.sum())
    quality["outside_uf"] += int(outside_uf.sum())
    quality["unknown_municipality"] += int((candidate & ~known).sum())
    quality["rows_in_scope"] += int(known.sum())

    onset_raw = chunk["DT_SIN_PRI"]
    onset_missing = onset_raw.isna() | onset_raw.str.strip().eq("")
    onset = _parse_onset_series(onset_raw)
    invalid_date = known & ~onset_missing & onset.isna()
    parsed = known & ~onset_missing & ~onset.isna()
    wrong_year = parsed & onset.dt.year.ne(settings["year"])
    in_year = parsed & ~wrong_year
    after_cutoff = in_year & onset.dt.date.gt(settings["cutoff"])
    valid = in_year & ~after_cutoff
    quality["missing_onset"] += int((known & onset_missing).sum())
    quality["invalid_dates"] += int(invalid_date.sum())
    quality["onset_outside_year"] += int(wrong_year.sum())
    quality["after_cutoff"] += int(after_cutoff.sum())
    quality["valid_rows"] += int(valid.sum())
    if not valid.any():
        return
    valid_min = onset.loc[valid].min().date()
    valid_max = onset.loc[valid].max().date()
    if quality["valid_onset_min"] is None or valid_min.isoformat() < quality["valid_onset_min"]:
        quality["valid_onset_min"] = valid_min.isoformat()
    if quality["valid_onset_max"] is None or valid_max.isoformat() > quality["valid_onset_max"]:
        quality["valid_onset_max"] = valid_max.isoformat()

    classification = chunk["CLASSI_FIN"].map(_normalise_category)
    criteria = chunk["CRITERIO"].map(_normalise_category)
    evolution = chunk["EVOLUCAO"].map(_normalise_category)
    eligible = valid & ~classification.isin(settings["excluded"])
    confirmed = eligible & classification.isin(settings["confirmed"])
    deaths = confirmed & evolution.eq("2") & criteria.isin(settings["death_criteria"])
    quality["unknown_classification"] += int((valid & classification.isna()).sum())
    quality["discarded_or_other_aggravation"] += int((valid & ~eligible).sum())

    scoped = pd.DataFrame({
        "municipality_code": municipality[valid].astype(str), "onset": onset[valid].dt.date,
        "probable_cases": eligible[valid].astype("int64"), "confirmed_cases": confirmed[valid].astype("int64"),
        "deaths": deaths[valid].astype("int64"),
    })
    weeks = scoped["onset"].map(epidemiological_week)
    scoped["week_start"] = weeks.map(lambda item: item[0].isoformat())
    scoped["week_label"] = weeks.map(lambda item: f"SE {item[1]:02d}/{item[2]}")
    scoped["municipality_name"] = scoped["municipality_code"].map(lambda code: municipality_index[code]["name"])
    grouped = scoped.groupby(
        ["municipality_code", "municipality_name", "week_start", "week_label"], as_index=False, sort=True
    ).agg(notifications=("onset", "size"), probable_cases=("probable_cases", "sum"),
          confirmed_cases=("confirmed_cases", "sum"), deaths=("deaths", "sum"))
    for item in grouped.itertuples(index=False):
        values = aggregate[(item.municipality_code, item.municipality_name, item.week_start, item.week_label)]
        values[0] += int(item.notifications)
        values[1] += int(item.probable_cases)
        values[2] += int(item.confirmed_cases)
        values[3] += int(item.deaths)


def _weekly_frame(aggregate: defaultdict[tuple[str, str, str, str], list[int]]) -> pd.DataFrame:
    rows = [
        {"municipality_code": key[0], "municipality_name": key[1], "week_start": key[2], "week_label": key[3],
         "notifications": values[0], "probable_cases": values[1], "confirmed_cases": values[2], "deaths": values[3]}
        for key, values in aggregate.items()
    ]
    return pd.DataFrame(rows, columns=GOLD_COLUMNS).sort_values(
        ["week_start", "municipality_code"], kind="stable"
    ).reset_index(drop=True)


def _add_quality_checks(quality: dict[str, Any], weekly: pd.DataFrame) -> None:
    territorial = sum(quality[key] for key in (
        "missing_municipality", "invalid_municipality", "outside_uf", "unknown_municipality", "rows_in_scope"
    ))
    temporal = sum(quality[key] for key in (
        "missing_onset", "invalid_dates", "onset_outside_year", "after_cutoff", "valid_rows"
    ))
    notifications = 0 if weekly.empty else int(weekly["notifications"].sum())
    metric_ok = notifications == quality["valid_rows"] and (
        weekly.empty or bool((weekly["confirmed_cases"] <= weekly["probable_cases"]).all() and
                             (weekly["deaths"] <= weekly["confirmed_cases"]).all())
    )
    quality["checks"] = [
        _check("territorial_reconciliation", territorial == quality["rows_read"],
               f"rows_read={quality['rows_read']}; categorias_territoriais={territorial}"),
        _check("temporal_reconciliation", temporal == quality["rows_in_scope"],
               f"rows_in_scope={quality['rows_in_scope']}; categorias_temporais={temporal}"),
        _check("aggregate_reconciliation", metric_ok,
               f"notificações_gold={notifications}; linhas_válidas={quality['valid_rows']}"),
        _check("pii_output_contract", list(weekly.columns) == list(GOLD_COLUMNS),
               "Somente campos agregados por município e semana são persistidos."),
    ]


def _check(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {"name": name, "status": "passed" if passed else "failed", "detail": detail}


def _normalise_municipality(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text


def _normalise_category(value: Any) -> str | None:
    text = _normalise_municipality(value)
    return text if text is not None and text.isdigit() else None


def _parse_onset_series(values: pd.Series) -> pd.Series:
    """Parse only documented ISO and Brazilian date representations.

    Explicit parsing avoids locale-dependent interpretation of dates such as
    04/01/2026 and avoids treating a notification date as a fallback.
    """
    text = values.fillna("").str.strip()
    result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    iso = text.str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)
    brazil = text.str.match(r"^\d{2}/\d{2}/\d{4}$", na=False)
    if iso.any():
        result.loc[iso] = pd.to_datetime(text.loc[iso], format="%Y-%m-%d", errors="coerce")
    if brazil.any():
        result.loc[brazil] = pd.to_datetime(text.loc[brazil], format="%d/%m/%Y", errors="coerce")
    return result


def _manifest_config(config: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    exported = dict(config)
    exported.update({
        "cutoff_date": settings["cutoff"].isoformat(), "encoding": settings["encoding"],
        "separator": settings["separator"], "confirmed_classifications": sorted(settings["confirmed"]),
        "excluded_classifications": sorted(settings["excluded"]),
        "death_confirmation_criteria": sorted(settings["death_criteria"]),
    })
    return exported


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
