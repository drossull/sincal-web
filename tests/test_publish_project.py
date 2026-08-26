import os
import tempfile
import unittest
from pathlib import Path

from tools.publish_project import PublishError, find_plan_one


class FindPlanOneTests(unittest.TestCase):
    def make_dwg(self, root: Path, relative_path: str) -> Path:
        drawing = root / relative_path
        drawing.parent.mkdir(parents=True, exist_ok=True)
        drawing.touch()
        return drawing

    def test_prefers_revision_zero_over_lettered_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            older = self.make_dwg(
                root,
                "G-45 Ruta/E-2 Proyecto Definitivo/Rev Z/09 Azul/Planos/Nativos/G45-PT-PD-PLA-0900-Z.dwg",
            )
            newest = self.make_dwg(
                root,
                "G-45 Ruta/E-2 Proyecto Definitivo/Rev 0/09 Azul/Planos/Nativos/G45-PT-PD-PLA-0900-0.dwg",
            )
            self.make_dwg(
                root,
                "G-45 Ruta/E-2 Proyecto Definitivo/Rev 0/09 Azul/Planos/Nativos/G45-PT-PD-PLA-0901-0.dwg",
            )
            newer_copy = self.make_dwg(
                root,
                "G-45 Ruta/E-2 Proyecto Definitivo/Rev 0/09 Azul/Copia/Planos/Nativos/G45-PT-PD-PLA-0900-0.dwg",
            )
            os.utime(newest, (100, 100))
            os.utime(newer_copy, (200, 200))

            drawing, candidates = find_plan_one(root, "G-45", "Puente El Azul")

            self.assertEqual(newer_copy, drawing)
            self.assertEqual({"Z", "0"}, {candidate.revision for candidate in candidates})
            self.assertNotEqual(older, drawing)

    def test_never_falls_back_to_anteproyectos(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_dwg(root, "G-45 Ruta/Anteproyectos/09 Azul/Planos/Nativos/G45-PT-AP-PLA-0900-A.dwg")

            with self.assertRaisesRegex(PublishError, "Proyecto Definitivo"):
                find_plan_one(root, "G-45", "Puente El Azul")


if __name__ == "__main__":
    unittest.main()
