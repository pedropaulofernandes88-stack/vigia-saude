"""Adaptadores de fontes públicas. Nenhum dado individual é enviado à interface."""
from __future__ import annotations

import csv
import codecs
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, target: Path, max_bytes: int = 512 * 1024 * 1024) -> dict:
    if not url.startswith("https://"):
        raise ValueError("As fontes devem usar HTTPS.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "Vigia-Saude/0.1 (public-data-research)"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as out:
            length = int(response.headers.get("Content-Length", "0"))
            if length > max_bytes:
                raise ValueError("Arquivo excede o limite de download configurado.")
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > max_bytes:
                    raise ValueError("Arquivo excede o limite de download configurado.")
                out.write(block)
            headers = {key: response.headers.get(key) for key in ("Last-Modified", "ETag", "Content-Type")}
        if length and total != length:
            raise ValueError("Download incompleto: tamanho recebido difere do anunciado.")
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"url": url, "extracted_at": utc_now(), "bytes": total, "sha256": sha256(target), **headers}


def extract_csv(archive: Path, target: Path) -> None:
    """Copia um membro CSV para caminho escolhido, sem confiar em paths do ZIP."""
    with zipfile.ZipFile(archive) as zf:
        candidates = [item for item in zf.infolist() if item.filename.lower().endswith(".csv")]
        if len(candidates) != 1:
            raise ValueError("Esperado exatamente um CSV no arquivo oficial.")
        member = candidates[0]
        if member.file_size > 2 * 1024**3:
            raise ValueError("CSV descompactado excede 2 GiB.")
        with zf.open(member) as source, target.open("wb") as dest:
            shutil.copyfileobj(source, dest)


def inspect_csv(path: Path) -> dict:
    with path.open("rb") as stream:
        sample = stream.read(65536)
    try:
        text = codecs.getincrementaldecoder("utf-8-sig")().decode(sample, final=False)
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        text = sample.decode("cp1252")
        encoding = "cp1252"
    header = text.splitlines()[0]
    delimiter = csv.Sniffer().sniff(header, delimiters=";,\t").delimiter
    fields = next(csv.reader([header], delimiter=delimiter))
    required = {"ID_MN_RESI", "DT_SIN_PRI", "DT_NOTIFIC", "CLASSI_FIN", "CRITERIO", "EVOLUCAO"}
    if not required.issubset(set(fields)):
        raise ValueError("Schema da fonte incompatível; faltam: " + ", ".join(sorted(required - set(fields))))
    return {"encoding": encoding, "separator": delimiter, "columns": fields}


def read_population(path: Path, uf: str, year: int) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    municipalities = []
    for row in rows:
        code = str(row.get("D1C", ""))
        if len(code) != 7 or not code.startswith(uf):
            continue
        value = str(row.get("V", ""))
        if not value.isdigit() or int(value) <= 0:
            raise ValueError("População inválida ou ausente no denominador municipal.")
        if str(row.get("D3N")) != str(year):
            raise ValueError("Ano da população difere da configuração.")
        name = str(row["D1N"]).rsplit(" - ", 1)[0]
        municipalities.append({"code": code[:6], "ibge_code": code, "name": name,
                               "population": int(value), "population_year": year})
    if not municipalities or len({row["code"] for row in municipalities}) != len(municipalities):
        raise ValueError("Cadastro populacional vazio ou com códigos duplicados.")
    return sorted(municipalities, key=lambda row: row["name"])


def acquire(config: dict, bronze_dir: Path) -> tuple[Path, list[dict], dict, dict]:
    bronze_dir.mkdir(parents=True, exist_ok=False)
    archive = bronze_dir / "dengue.csv.zip"
    print("Baixando arquivo oficial do SINAN...", flush=True)
    origin = download(config["download_url"], archive)
    csv_path = bronze_dir / "dengue.csv"
    extract_csv(archive, csv_path)
    csv_format = inspect_csv(csv_path)
    print("Validando denominadores municipais no IBGE...", flush=True)
    population_path = bronze_dir / "populacao.json"
    population_source = download(config["population_url"], population_path, 20 * 1024 * 1024)
    municipalities = read_population(population_path, str(config["uf"]), int(config["population_year"]))
    modified = origin.get("Last-Modified")
    cutoff = parsedate_to_datetime(modified).date().isoformat() if modified else datetime.now(timezone.utc).date().isoformat()
    resolved = {**config, **csv_format, "cutoff_date": cutoff}
    source = {"name": config["source_name"], "url": config["source_url"], "download": origin,
              "updated_at": cutoff, "catalog_updated_at": config.get("catalog_updated_at"),
              "extracted_at": origin["extracted_at"], "dictionary_url": config["dictionary_url"],
              "csv_sha256": sha256(csv_path), "population": population_source,
              "population_year": config["population_year"]}
    (bronze_dir / "source.json").write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path, municipalities, resolved, source
