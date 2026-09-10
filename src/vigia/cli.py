"""Comandos de aquisição, processamento, conferência e execução local."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

from vigia.analytics import DatasetStore, bulletin
from vigia.server import serve
from vigia.sources import acquire

ROOT = Path(__file__).resolve().parents[2]


def publish_pointer(root: Path, snapshot: Path) -> None:
    # Apenas após build/validação. Mantém snapshot anterior recuperável.
    relative = snapshot.resolve().relative_to((root / "data").resolve()).as_posix()
    temporary = root / "data" / f"current.{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps({"snapshot": relative}), encoding="utf-8")
    temporary.replace(root / "data" / "current.json")


def main():
    parser = argparse.ArgumentParser(description="Vigia Saúde — vigilância epidemiológica local")
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync", help="Baixar fontes oficiais e publicar um snapshot local validado")
    sync.add_argument("--config", type=Path, default=ROOT / "config" / "parana-dengue.json")
    process = sub.add_parser("process", help="Reprocessar aquisição local preparada, sem novo download")
    process.add_argument("prepared", type=Path)
    run = sub.add_parser("serve", help="Abrir servidor apenas em 127.0.0.1")
    run.add_argument("--port", type=int, default=8765)
    sub.add_parser("status", help="Resumo agregado do conjunto atual")
    brief = sub.add_parser("brief", help="Gerar boletim descritivo Markdown")
    brief.add_argument("--output", type=Path, default=ROOT / "runtime" / "boletim.md")
    args = parser.parse_args()
    if args.command == "serve":
        serve(ROOT, args.port)
        return
    if args.command in {"sync", "process"}:
        from vigia.pipeline import build_dataset
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        if args.command == "sync":
            config = json.loads(args.config.read_text(encoding="utf-8"))
            bronze = ROOT / "data" / "bronze" / run_id
            csv_path, municipalities, config, source = acquire(config, bronze)
            prepared = {"csv_path": str(csv_path.resolve()), "municipalities": municipalities, "config": config, "source": source}
            (bronze / "prepared.json").write_text(json.dumps(prepared, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            prepared = json.loads(args.prepared.read_text(encoding="utf-8"))
            csv_path = Path(prepared["csv_path"])
            if not csv_path.is_absolute():
                csv_path = ROOT / csv_path
            municipalities, config, source = prepared["municipalities"], prepared["config"], prepared["source"]
        snapshot = ROOT / "data" / "snapshots" / run_id
        manifest = build_dataset(csv_path, snapshot, municipalities, config, source)
        publish_pointer(ROOT, snapshot)
        print(json.dumps({"snapshot": run_id, "quality": manifest["quality"]}, ensure_ascii=False, indent=2))
    elif args.command == "status":
        result = DatasetStore(ROOT / "data").dashboard({})
        print(json.dumps({"meta": result["meta"], "summary": result["summary"]}, ensure_ascii=False, indent=2))
    elif args.command == "brief":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(bulletin(DatasetStore(ROOT / "data").dashboard({})), encoding="utf-8")
        print(f"Boletim salvo em {args.output.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        print(f"Falha: {exc}", file=sys.stderr)
        raise SystemExit(1)
