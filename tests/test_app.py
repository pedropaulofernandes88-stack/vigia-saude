from datetime import date, timedelta
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from vigia.actions import ActionStore
from vigia.analytics import DatasetStore, epi_label, bulletin
from vigia.server import create_server
from vigia.sources import read_population, inspect_csv, sha256


def fixture(root):
    data = root / "data"
    snap = data / "snapshots" / "fixture"
    (snap / "gold").mkdir(parents=True)
    towns = [{"code": "410001", "name": "Alfa", "population": 1000},
             {"code": "410002", "name": "Beta", "population": 2000}]
    rows = []
    for index in range(8):
        day = date(2026, 1, 11) + timedelta(weeks=index)
        for town in towns:
            cases = (10 if index < 4 else 20) if town["name"] == "Alfa" else 5
            rows.append({"municipality_code": town["code"], "municipality_name": town["name"], "week_start": day.isoformat(),
                         "week_label": epi_label(day), "notifications": cases, "probable_cases": cases,
                         "confirmed_cases": cases, "deaths": 0})
    pd.DataFrame(rows).to_parquet(snap / "gold" / "weekly.parquet", index=False)
    manifest = {"schema_version": 1, "checksums": {"gold/weekly.parquet": sha256(snap / "gold" / "weekly.parquet")},
                "config": {"year": 2026, "cutoff_date": "2026-03-07", "source_name": "Fixture sintética", "source_url": "https://example.invalid", "territory": "Teste"},
                "source": {"updated_at": "2026-03-07"}, "municipalities": towns, "quality": {"checks": []},
                "event_start": "2026-01-11", "event_end": "2026-03-07", "period_end": "2026-03-01"}
    (snap / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (data / "current.json").write_text(json.dumps({"snapshot": "snapshots/fixture"}), encoding="utf-8")
    return data, towns


class AnalyticsTests(unittest.TestCase):
    def test_counts_rates_comparison_and_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            data, _ = fixture(Path(directory))
            store = DatasetStore(data)
            result = store.dashboard({})
            self.assertEqual(result["summary"]["probable_cases"], 160)
            self.assertEqual(result["summary"]["incidence_per_100k"], 5333.33)
            self.assertEqual(result["summary"]["comparison_pct"], 66.7)
            self.assertEqual(sum(row["probable_cases"] for row in result["series"]), 160)
            self.assertEqual(sum(row["probable_cases"] for row in result["municipalities"]), 160)
            selected = store.dashboard({"municipality": "410001"})
            self.assertEqual(selected["summary"]["probable_cases"], 120)
            self.assertEqual(selected["summary"]["comparison_pct"], 100)
            self.assertEqual(selected["summary"]["incidence_per_100k"], 12000)
            short = store.dashboard({"start": "2026-03-03", "end": "2026-03-05"})
            self.assertEqual(short["filters"]["selected_start"], "2026-03-01")
            self.assertEqual(short["summary"]["probable_cases"], 25)
            self.assertIsNone(short["summary"]["comparison_pct"])
            self.assertIn("Fixture sintética", bulletin(result))
            self.assertIn("Alfa", bulletin(selected))
            with self.assertRaises(ValueError):
                store.dashboard({"municipality": "999999"})
            with self.assertRaises(ValueError):
                store.dashboard({"start": "2026-04-01", "end": "2026-01-01"})

    def test_missing_dataset_and_population(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(DatasetStore(root).dashboard({})["meta"]["available"])
            data, _ = fixture(root)
            manifest_path = data / "snapshots" / "fixture" / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["municipalities"][0]["population"] = None
            manifest_path.write_text(json.dumps(manifest))
            self.assertIsNone(DatasetStore(data).dashboard({})["summary"]["incidence_per_100k"])

    def test_epi_year_boundaries(self):
        self.assertEqual(epi_label(date(2025, 12, 28)), "SE 53/2025")
        self.assertEqual(epi_label(date(2028, 12, 31)), "SE 01/2029")

    def test_file_update_does_not_create_zero_weeks_after_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            data, _ = fixture(Path(directory))
            path = data / "snapshots" / "fixture" / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["config"]["cutoff_date"] = "2026-03-14"
            manifest["source"]["updated_at"] = "2026-03-14"
            path.write_text(json.dumps(manifest))
            result = DatasetStore(data).dashboard({})
            self.assertEqual(result["filters"]["max_date"], "2026-03-07")
            self.assertEqual(result["series"][-1]["week_start"], "2026-03-01")
            self.assertEqual(result["summary"]["comparison_pct"], 66.7)

    def test_tampered_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            data, _ = fixture(Path(directory))
            gold = data / "snapshots" / "fixture" / "gold" / "weekly.parquet"
            frame = pd.read_parquet(gold)
            frame.loc[0, "probable_cases"] = 9999
            frame.to_parquet(gold, index=False)
            with self.assertRaisesRegex(ValueError, "Integridade"):
                DatasetStore(data).dashboard({})


class ActionTests(unittest.TestCase):
    def test_persistence_idempotency_validation_and_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actions.sqlite"
            store = ActionStore(path)
            towns = [{"code": "410001", "name": "Alfa"}]
            payload = {"municipality_code": "410001", "title": "Verificar a qualidade", "owner": "Equipe", "due_date": "2026-09-15", "description": "Sem dados pessoais."}
            key = "idempotency-test-123"
            result = store.create(payload, towns, key)
            self.assertEqual(store.create(payload, towns, key)["id"], result["id"])
            with self.assertRaises(ValueError):
                store.create({**payload, "title": "Outra providência"}, towns, key)
            with self.assertRaises(ValueError):
                store.create({**payload, "municipality_code": "999999"}, towns, "idempotency-test-456")
            store.update(result["id"], {"status": "concluida"})
            store.update(result["id"], {"status": "concluida"})
            self.assertEqual(ActionStore(path).list()[0]["status"], "concluida")
            with store.connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM action_events").fetchone()[0], 2)


class ServerTests(unittest.TestCase):
    def test_http_contracts_and_local_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data, _ = fixture(root)
            server = create_server(ROOT, 0, data, root / "runtime")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_port
            def request(method, url, value=None, headers=None):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                body = json.dumps(value).encode() if value is not None else None
                conn.request(method, url, body, headers or {})
                response = conn.getresponse()
                result = response.status, dict(response.getheaders()), response.read()
                conn.close()
                return result
            try:
                status, headers, body = request("GET", "/api/dashboard?municipality=410001")
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["summary"]["probable_cases"], 120)
                self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
                self.assertEqual(request("GET", "/data/bronze/input.csv")[0], 404)
                self.assertEqual(request("GET", "/api/actions", headers={"Host": "attacker.invalid"})[0], 403)
                payload = {"municipality_code": "410001", "title": "Revisar indicadores", "owner": "Equipe", "due_date": "2026-09-15", "description": "Teste"}
                safe = {"Content-Type": "application/json", "X-Vigia-Client": "local", "Idempotency-Key": "http-idempotency-1234"}
                self.assertEqual(request("POST", "/api/actions", payload)[0], 403)
                self.assertEqual(request("POST", "/api/actions", payload, {**safe, "Origin": "https://attacker.invalid"})[0], 403)
                status, _, body = request("POST", "/api/actions", payload, safe)
                self.assertEqual(status, 201)
                identifier = json.loads(body)["action"]["id"]
                self.assertEqual(request("PATCH", f"/api/actions/{identifier}", {"status": "concluida"}, safe)[0], 200)
                self.assertEqual(len(json.loads(request("GET", "/api/actions")[2])["actions"]), 1)
                self.assertEqual(request("GET", "/api/brief")[0], 200)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


class SourceTests(unittest.TestCase):
    def test_population_year_and_csv_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "population.json"
            path.write_text(json.dumps([{"D1C": "4106902", "D1N": "Curitiba - PR", "D3N": "2025", "V": "1830795"}]))
            self.assertEqual(read_population(path, "41", 2025)[0]["code"], "410690")
            with self.assertRaises(ValueError):
                read_population(path, "41", 2026)
            csv = root / "source.csv"
            csv.write_text("OTHER,NOT_ALLOWED\n1,2\n")
            with self.assertRaises(ValueError):
                inspect_csv(csv)


if __name__ == "__main__":
    unittest.main()
