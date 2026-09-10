from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from vigia.pipeline import build_dataset, epidemiological_week  # noqa: E402


MUNICIPALITIES = [
    {"code": "410001", "name": "Alfa", "population": 1000, "population_year": 2025},
    {"code": "410002", "name": "Beta", "population": None, "population_year": None},
]
CONFIG = {
    "uf": "41", "year": 2026, "source_updated_at": "2026-03-31T12:00:00Z",
    "case_definition": "Definição operacional pendente de validação técnica.",
    "separator": ";", "encoding": "utf-8",
}
SOURCE = {"name": "fixture sintética", "url": "https://example.invalid/sinan"}


class PipelineTests(unittest.TestCase):
    def run_fixture(self, rows, chunksize=2):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path = root / "input.csv"
            pd.DataFrame(rows).to_csv(csv_path, sep=";", index=False)
            output = root / "snapshot"
            manifest = build_dataset(csv_path, output, MUNICIPALITIES, {**CONFIG, "chunksize": chunksize}, SOURCE)
            return manifest, pd.read_parquet(output / "gold" / "weekly.parquet")

    def test_reconciliation_and_eligibility(self):
        rows = [
            {"ID_MN_RESI": "410001", "DT_SIN_PRI": "04/01/2026", "CLASSI_FIN": "10", "EVOLUCAO": "2", "CRITERIO": "1"},
            {"ID_MN_RESI": "410001", "DT_SIN_PRI": "05/01/2026", "CLASSI_FIN": "5", "EVOLUCAO": "2", "CRITERIO": "1"},
            {"ID_MN_RESI": "410002", "DT_SIN_PRI": "06/01/2026", "CLASSI_FIN": "13", "EVOLUCAO": "2", "CRITERIO": "2"},
            {"ID_MN_RESI": "410002", "DT_SIN_PRI": "06/01/2026", "CLASSI_FIN": "", "EVOLUCAO": "", "CRITERIO": ""},
            {"ID_MN_RESI": "410002", "DT_SIN_PRI": "06/01/2026", "CLASSI_FIN": "11", "EVOLUCAO": "2", "CRITERIO": "9"},
        ]
        manifest, weekly = self.run_fixture(rows)
        quality = manifest["quality"]
        self.assertEqual(quality["valid_rows"], 5)
        self.assertEqual(quality["unknown_classification"], 1)
        self.assertEqual(quality["discarded_or_other_aggravation"], 2)
        self.assertEqual(int(weekly["notifications"].sum()), 5)
        self.assertEqual(int(weekly["probable_cases"].sum()), 3)
        self.assertEqual(int(weekly["confirmed_cases"].sum()), 2)
        self.assertEqual(int(weekly["deaths"].sum()), 1)
        self.assertTrue(all(item["status"] == "passed" for item in quality["checks"]))

    def test_week_boundaries_are_sunday_to_saturday(self):
        self.assertEqual(epidemiological_week(date(2026, 1, 1)), (date(2025, 12, 28), 53, 2025))
        self.assertEqual(epidemiological_week(date(2026, 1, 4)), (date(2026, 1, 4), 1, 2026))
        self.assertEqual(epidemiological_week(date(2026, 12, 31))[1:], (52, 2026))
        self.assertEqual(epidemiological_week(date(2028, 12, 31)), (date(2028, 12, 31), 1, 2029))

    def test_iso_onset_date_is_not_reinterpreted_by_locale_or_timezone(self):
        rows = [{"ID_MN_RESI": "410001", "DT_SIN_PRI": "2026-01-04", "CLASSI_FIN": "10", "EVOLUCAO": "2", "CRITERIO": "1"}]
        manifest, weekly = self.run_fixture(rows)
        self.assertEqual(manifest["quality"]["valid_rows"], 1)
        self.assertEqual(weekly.loc[0, "week_start"], "2026-01-04")
        self.assertEqual(weekly.loc[0, "week_label"], "SE 01/2026")

    def test_manifest_keeps_observed_event_end_separate_from_later_cutoff(self):
        rows = [
            {"ID_MN_RESI": "410001", "DT_SIN_PRI": "04/01/2026", "CLASSI_FIN": "10", "EVOLUCAO": "2", "CRITERIO": "1"},
            {"ID_MN_RESI": "410002", "DT_SIN_PRI": "10/01/2026", "CLASSI_FIN": "11", "EVOLUCAO": "1", "CRITERIO": "1"},
        ]
        manifest, weekly = self.run_fixture(rows)
        self.assertEqual(manifest["config"]["cutoff_date"], "2026-03-31")
        self.assertEqual(manifest["event_start"], "2026-01-04")
        self.assertEqual(manifest["event_end"], "2026-01-10")
        self.assertEqual(manifest["quality"]["valid_onset_min"], "2026-01-04")
        self.assertEqual(manifest["quality"]["valid_onset_max"], "2026-01-10")
        self.assertEqual(set(weekly["week_start"]), {"2026-01-04"})

    def test_missing_invalid_and_out_of_scope_are_counted(self):
        rows = [
            {"ID_MN_RESI": "", "DT_SIN_PRI": "04/01/2026", "CLASSI_FIN": "10", "EVOLUCAO": "2", "CRITERIO": "1"},
            {"ID_MN_RESI": "410001", "DT_SIN_PRI": "", "CLASSI_FIN": "10", "EVOLUCAO": "2", "CRITERIO": "1"},
            {"ID_MN_RESI": "410001", "DT_SIN_PRI": "invalida", "CLASSI_FIN": "10", "EVOLUCAO": "2", "CRITERIO": "1"},
            {"ID_MN_RESI": "410001", "DT_SIN_PRI": "01/01/2025", "CLASSI_FIN": "10", "EVOLUCAO": "2", "CRITERIO": "1"},
            {"ID_MN_RESI": "410001", "DT_SIN_PRI": "01/04/2026", "CLASSI_FIN": "10", "EVOLUCAO": "2", "CRITERIO": "1"},
            {"ID_MN_RESI": "420001", "DT_SIN_PRI": "04/01/2026", "CLASSI_FIN": "10", "EVOLUCAO": "2", "CRITERIO": "1"},
            {"ID_MN_RESI": "419999", "DT_SIN_PRI": "04/01/2026", "CLASSI_FIN": "10", "EVOLUCAO": "2", "CRITERIO": "1"},
        ]
        manifest, weekly = self.run_fixture(rows)
        quality = manifest["quality"]
        self.assertEqual(quality["missing_municipality"], 1)
        self.assertEqual(quality["missing_onset"], 1)
        self.assertEqual(quality["invalid_dates"], 1)
        self.assertEqual(quality["onset_outside_year"], 1)
        self.assertEqual(quality["after_cutoff"], 1)
        self.assertEqual(quality["outside_uf"], 1)
        self.assertEqual(quality["unknown_municipality"], 1)
        self.assertTrue(weekly.empty)

    def test_chunking_is_invariant_and_output_has_no_source_columns(self):
        rows = [
            {"ID_MN_RESI": "410001", "DT_SIN_PRI": "04/01/2026", "CLASSI_FIN": "11", "EVOLUCAO": "1", "CRITERIO": "1"},
            {"ID_MN_RESI": "410001", "DT_SIN_PRI": "05/01/2026", "CLASSI_FIN": "5", "EVOLUCAO": "2", "CRITERIO": "1"},
            {"ID_MN_RESI": "410002", "DT_SIN_PRI": "06/01/2026", "CLASSI_FIN": "desconhecido", "EVOLUCAO": "2", "CRITERIO": "2"},
        ]
        first_manifest, first = self.run_fixture(rows, chunksize=1)
        second_manifest, second = self.run_fixture(rows, chunksize=100)
        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(first_manifest["quality"]["unknown_classification"], 1)
        self.assertEqual(second_manifest["quality"]["rows_read"], 3)
        self.assertFalse({"ID_MN_RESI", "DT_SIN_PRI", "CLASSI_FIN", "EVOLUCAO", "CRITERIO", "cpf"} & set(first.columns))

    def test_existing_snapshot_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path, output = root / "input.csv", root / "snapshot"
            pd.DataFrame([{"ID_MN_RESI": "410001", "DT_SIN_PRI": "04/01/2026", "CLASSI_FIN": "10", "EVOLUCAO": "2", "CRITERIO": "1"}]).to_csv(csv_path, sep=";", index=False)
            output.mkdir()
            (output / "existing.txt").write_text("preserve", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                build_dataset(csv_path, output, MUNICIPALITIES, CONFIG, SOURCE)
            self.assertEqual((output / "existing.txt").read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
