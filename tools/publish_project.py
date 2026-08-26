#!/usr/bin/env python3
"""Prepara evidencia para publicar un proyecto SINCAL sin modificar el sitio.

El proceso resuelve la memoria en la carpeta sincronizada de Google Drive, extrae
campos rastreables y localiza la etiqueta ``VISTA ISOMETRICA`` en el DWG. La
generación del DXF recortado se incorpora después de validar la ventana de cada
vista; por diseño, este MVP nunca publica ni sobrescribe activos web.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


MEMORY_EXTENSIONS = {".docx", ".pdf"}
DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
CAD_TITLE = "VISTA ISOMETRICA"


class PublishError(RuntimeError):
    """Error that must stop the publication workflow."""


@contextmanager
def best_effort_tempdir(prefix: str) -> Iterable[Path]:
    """Remove our AutoCAD profile when possible, without masking CAD results."""
    temporary = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield temporary
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.replace("\u00a0", " "))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value).strip().upper()


def slugify(value: str) -> str:
    value = normalized(value).lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def modified_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class DrawingCode:
    filename: str
    volume: str
    structure: str
    revision: str | None


@dataclass(frozen=True)
class DrawingCandidate:
    path: Path
    revision: str | None
    score: int
    modified_at: float


def parse_drawing_code(dwg: Path) -> DrawingCode:
    tokens = dwg.stem.upper().split("-")
    volume_index = next((index for index, token in enumerate(tokens) if re.fullmatch(r"V\d+T\d+", token)), None)
    if volume_index is None:
        revision = tokens[-1] if re.fullmatch(r"[A-ZÑ0]", tokens[-1]) else None
        # Los proyectos antiguos usan códigos VxxTxx; los más recientes pueden
        # usar el formato G45-PT-PD-PLA-0900-O. La memoria se resuelve entonces
        # por el nombre de la estructura.
        return DrawingCode(dwg.name, "", "", revision)

    structure = next(
        (token for token in tokens[volume_index + 1 :] if re.fullmatch(r"(?:PS|PI|PVI)[A-Z0-9]+", token)),
        None,
    )
    if structure is None:
        raise PublishError(f"No se encontró el código de estructura (PS/PI/PVI) en {dwg.name}.")

    revision = tokens[-1] if re.fullmatch(r"[A-ZÑ0]", tokens[-1]) else None
    return DrawingCode(dwg.name, tokens[volume_index], structure, revision)


@dataclass(frozen=True)
class MemoryCandidate:
    path: Path
    revision: str | None
    score: int
    modified_at: float


def revision_value(revision: str | None) -> int:
    if revision == "0":
        return 28
    if revision == "Ñ":
        return 15
    if revision and re.fullmatch(r"[A-Z]", revision):
        return ord(revision) - ord("A") + 1 + (1 if revision > "N" else 0)
    return 0


def revisions_in_path(path: Path) -> Iterable[str]:
    for part in (path.name, *[parent.name for parent in path.parents]):
        revision_text = unicodedata.normalize("NFC", part).upper()
        match = re.search(r"(?:^|[-_ .])REV(?:ISION)?\s*[-_. ]*([A-ZÑ0])(?:$|[-_. ])", revision_text)
        if match:
            yield match.group(1)
    name_match = re.search(r"-([A-ZÑ0])\.(?:DOCX|PDF)$", unicodedata.normalize("NFC", path.name).upper())
    if name_match:
        yield name_match.group(1)


def find_order_folder(drive_root: Path, work_order: str) -> Path:
    work_order_normalized = normalized(work_order).replace(" ", "")
    matches = [
        entry
        for entry in drive_root.iterdir()
        if entry.is_dir() and normalized(entry.name).replace(" ", "").startswith(work_order_normalized)
    ]
    if not matches:
        raise PublishError(f"No existe una carpeta que comience con {work_order} en {drive_root}.")
    if len(matches) > 1:
        names = ", ".join(entry.name for entry in matches)
        raise PublishError(f"La OT {work_order} es ambigua: {names}.")
    return matches[0]


def is_project_definitive(path: Path) -> bool:
    return any("PROYECTO DEFINITIVO" in normalized(part) for part in path.parts)


def is_native_plan_directory(path: Path) -> bool:
    """Native files may be directly in Planos/Nativos or a nested subfolder."""
    return normalized(path.name) == "NATIVOS" and any(
        normalized(parent.name) == "PLANOS" for parent in path.parents
    )


def structure_tokens(structure_name: str) -> list[str]:
    generic_terms = {"PUENTE", "PASO", "SUPERIOR", "INFERIOR", "VIADUCTO", "EL", "LA", "LOS", "LAS", "DE"}
    tokens = [
        token for token in re.findall(r"[A-Z0-9]{2,}", normalized(structure_name))
        if token not in generic_terms
    ]
    if not tokens:
        raise PublishError("El nombre de la estructura no contiene términos buscables.")
    return tokens


def drawing_score(path: Path, structure_name: str, tokens: list[str]) -> int | None:
    if path.suffix.lower() != ".dwg" or not is_project_definitive(path) or not is_native_plan_directory(path.parent):
        return None

    route = normalized(str(path.parent))
    if "PUENTE" in normalized(structure_name) and "MURO" in route:
        return None
    haystack = f"{normalized(path.stem)} {route}"
    if not all(re.search(rf"(?:^|[^A-Z0-9]){re.escape(token)}(?:$|[^A-Z0-9])", haystack) for token in tokens):
        return None

    score = len(tokens) * 10
    if normalized(path.parent.parent.name) == "PLANOS":
        score += 30
    score += 10
    return score


def find_plan_one(drive_root: Path, work_order: str, structure_name: str) -> tuple[Path, list[DrawingCandidate]]:
    """Find the newest unambiguous Plan 1, strictly inside Proyecto Definitivo."""
    order_folder = find_order_folder(drive_root, work_order)
    tokens = structure_tokens(structure_name)
    candidates: list[DrawingCandidate] = []
    for directory, _, filenames in os.walk(order_folder):
        native_directory = Path(directory)
        if not is_native_plan_directory(native_directory):
            continue
        drawing_paths = sorted(
            (native_directory / filename for filename in filenames if filename.lower().endswith(".dwg")),
            key=lambda path: normalized(path.name),
        )
        if not drawing_paths:
            continue
        # Por definición operativa del usuario, el primer DWG ordenado de
        # NATIVOS corresponde al Plano 1 de esa estructura y revisión.
        path = drawing_paths[0]
        score = drawing_score(path, structure_name, tokens)
        if score is None:
            continue
        revision = max(revisions_in_path(path), key=revision_value, default=None)
        candidates.append(DrawingCandidate(path, revision, score, path.stat().st_mtime))

    if not candidates:
        raise PublishError(
            f"No se encontró el primer DWG de PLANOS/NATIVOS para '{structure_name}' bajo Proyecto Definitivo "
            f"en {order_folder}. No se consideran archivos de Anteproyectos u otras etapas."
        )

    candidates.sort(
        key=lambda candidate: (revision_value(candidate.revision), candidate.modified_at, candidate.score),
        reverse=True,
    )
    best = candidates[0]
    ties = [
        candidate for candidate in candidates
        if (
            revision_value(candidate.revision),
            candidate.modified_at,
            candidate.score,
        ) == (
            revision_value(best.revision),
            best.modified_at,
            best.score,
        )
    ]
    if len(ties) > 1:
        paths = "\n".join(f"- {candidate.path}" for candidate in ties)
        raise PublishError(
            "Hay varios Planos 1 igualmente válidos en la revisión más reciente; se requiere selección manual:\n"
            f"{paths}"
        )
    return best.path, candidates


def memory_score(path: Path, code: DrawingCode, structure_name: str | None = None) -> int | None:
    name = normalized(path.name)
    route = normalized(str(path.parent))
    if structure_name and "PUENTE" in normalized(structure_name) and "MURO" in route:
        return None
    if code.volume and code.structure:
        if code.volume not in name or code.structure not in name:
            return None
    elif structure_name:
        tokens = structure_tokens(structure_name)
        if not all(re.search(rf"(?:^|[^A-Z0-9]){re.escape(token)}(?:$|[^A-Z0-9])", f"{name} {route}") for token in tokens):
            return None
    else:
        raise PublishError(
            "El plano no contiene código VxxTxx; entregue --structure para poder buscar su memoria."
        )
    if "-ME-" not in name and "MEMORIA" not in route:
        return None

    score = 100
    if "-ME-" in name:
        score += 30
    if "-ES-" in name:
        score += 15
    if "MEMORIA" in route:
        score += 10
    if path.suffix.lower() == ".docx":
        score += 3
    return score


def find_memory(
    drive_root: Path, work_order: str, code: DrawingCode, structure_name: str | None = None
) -> tuple[Path, list[MemoryCandidate]]:
    order_folder = find_order_folder(drive_root, work_order)
    candidates: list[MemoryCandidate] = []
    for directory, _, filenames in os.walk(order_folder):
        for filename in filenames:
            path = Path(directory, filename)
            if path.suffix.lower() not in MEMORY_EXTENSIONS:
                continue
            score = memory_score(path, code, structure_name)
            if score is None:
                continue
            revision = max(revisions_in_path(path), key=revision_value, default=None)
            candidates.append(MemoryCandidate(path, revision, score, path.stat().st_mtime))

    if not candidates:
        raise PublishError(
            f"No se encontró memoria para {structure_name or f'{code.volume}/{code.structure}'} dentro de {order_folder}."
        )

    candidates.sort(
        key=lambda candidate: (
            candidate.score,
            revision_value(candidate.revision),
            candidate.modified_at,
            candidate.path.suffix.lower() == ".docx",
        ),
        reverse=True,
    )
    best = candidates[0]
    ties = [
        candidate for candidate in candidates
        if (
            candidate.score,
            revision_value(candidate.revision),
            candidate.modified_at,
        ) == (
            best.score,
            revision_value(best.revision),
            best.modified_at,
        )
    ]
    if len(ties) > 1:
        paths = "\n".join(f"- {candidate.path}" for candidate in ties)
        raise PublishError(f"Hay memorias igualmente válidas; se requiere selección manual:\n{paths}")
    return best.path, candidates


def extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    paragraphs = []
    for paragraph in root.findall(".//w:p", DOCX_NS):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", DOCX_NS)).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def extract_pdf_text(path: Path) -> str:
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError as error:
        raise PublishError("Para leer memorias PDF instale PyMuPDF: pip install pymupdf") from error
    document = fitz.open(path)
    return "\n".join(page.get_text() for page in document)


def first_match(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else None


def named_project(text: str) -> str | None:
    matches = re.findall(r"MEMORIA\s+PROYECTO\s*[–-]\s*([^\n]+)", text, flags=re.IGNORECASE | re.MULTILINE)
    clean = [re.sub(r"\s+", " ", match).strip() for match in matches]
    # El índice suele añadir el número de página al primer resultado; la versión
    # posterior es el encabezado real de la sección.
    return next((match for match in reversed(clean) if not re.search(r"\d+$", match)), clean[-1] if clean else None)


def extract_metadata(text: str) -> dict[str, str]:
    fields = {
        "nombre": named_project(text),
        "longitud": first_match(text, r"(?:tramo|longitud)\s+de\s+(\d+(?:[,.]\d+)?)\s*m\b"),
        "ancho_tablero": first_match(text, r"ancho\s+total\s+de\s+(\d+(?:[,.]\d+)?)\s*m\b"),
        "pistas": first_match(text, r"(?:cabida\s+a|considera)\s+(\d+)\s+pistas"),
        "tipologia": first_match(text, r"tipología\s+general\s+de\s+esta\s+estructura\s+es\s+de\s+([^\.]+)"),
    }
    return {name: value for name, value in fields.items() if value}


def find_accoreconsole(explicit_path: str | None) -> Path | None:
    candidates = [Path(explicit_path)] if explicit_path else []
    candidates.extend(
        Path(rf"C:\Program Files\Autodesk\AutoCAD {version}\accoreconsole.exe")
        for version in (2027, 2026, 2025, 2024)
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def inspect_isometric(dwg: Path, accoreconsole: Path, lisp: Path) -> dict[str, Any]:
    with best_effort_tempdir(prefix="sincal-cad-") as temporary:
        # AutoCAD considera confiable la raíz Temp del usuario, pero no sus
        # subcarpetas efímeras en todas las instalaciones.
        temporary_path = Path(tempfile.gettempdir())
        run_id = uuid.uuid4().hex
        report = temporary_path / f"sincal-iso-{run_id}.txt"
        script = temporary_path / f"sincal-iso-{run_id}.scr"
        temporary_lisp = temporary_path / f"sincal-iso-{run_id}.lsp"
        console_log = temporary_path / f"sincal-iso-{run_id}.log"
        isolated_profile = temporary / "autocad-user-data"
        isolated_profile.mkdir()
        shutil.copyfile(lisp, temporary_lisp)
        report_lisp_path = report.as_posix().replace("\\", "/")
        lisp_path = temporary_lisp.as_posix().replace("\\", "/")
        script.write_text(
            f'(load "{lisp_path}")\n(sincal:inspect "{report_lisp_path}")\n',
            encoding="ascii",
        )
        # El Core Console no siempre termina tras `quit`, por lo que esperamos
        # únicamente el reporte de lectura y cerramos este proceso auxiliar que
        # nosotros mismos iniciamos. El perfil aislado evita que rutinas locales
        # interfieran con la inspección de solo lectura.
        with console_log.open("wb") as log_stream:
            process = subprocess.Popen(
                [
                    str(accoreconsole), "/isolate", "sincal-web-publisher", str(isolated_profile),
                    "/i", str(dwg), "/s", str(script), "/readonly", "/l", "en-US",
                ],
                stdout=log_stream,
                stderr=subprocess.STDOUT,
            )
            def report_complete() -> bool:
                try:
                    return report.exists() and "DONE\n" in report.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return False

            deadline = time.monotonic() + 120
            while not report_complete() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.25)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        if not report_complete():
            raw_log = console_log.read_bytes() if console_log.exists() else b""
            log_tail = raw_log.decode("utf-16", errors="replace")[-1500:] if raw_log.startswith(b"\xff\xfe") else raw_log.decode("utf-8", errors="replace")[-1500:]
            raise PublishError(f"AutoCAD no generó el reporte de isométrica.\n{log_tail}")
        matches = []
        for line in report.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("MATCH|"):
                _, entity_type, tag, value, block, handle, layer, layout, insertion = line.split("|", maxsplit=8)
                matches.append({
                    "entity_type": entity_type,
                    "tag": tag,
                    "value": value,
                    "block": block,
                    "handle": handle,
                    "layer": layer,
                    "layout": layout,
                    "insertion": insertion,
                })
        for artifact in (report, script, temporary_lisp, console_log):
            artifact.unlink(missing_ok=True)
        return {"title": CAD_TITLE, "matches": matches, "count": len(matches), "status": "ok"}


def preview_isometric(
    dwg: Path, accoreconsole: Path, lisp: Path, insertion: tuple[float, float], output: Path
) -> None:
    """Render a temporary PNG or export an SVG around the detected title."""
    output.unlink(missing_ok=True)
    with best_effort_tempdir(prefix="sincal-cad-preview-") as temporary:
        temporary_path = Path(tempfile.gettempdir())
        run_id = uuid.uuid4().hex
        script = temporary_path / f"sincal-preview-{run_id}.scr"
        temporary_lisp = temporary_path / f"sincal-preview-{run_id}.lsp"
        console_log = temporary_path / f"sincal-preview-{run_id}.log"
        isolated_profile = temporary / "autocad-user-data"
        isolated_profile.mkdir()
        shutil.copyfile(lisp, temporary_lisp)
        lisp_path = temporary_lisp.as_posix().replace("\\", "/")
        output_path = output.resolve().as_posix().replace("\\", "/")
        suffix = output.suffix.lower()
        cad_function = {
            ".svg": "sincal:export-isometric-svg",
            ".dwg": "sincal:export-isometric-dwg",
            ".dxf": "sincal:export-isometric-dxf",
            ".txt": "sincal:inspect-isometric-crop",
            ".xrefs": "sincal:inspect-xrefs",
            ".coords": "sincal:inspect-title-coordinate",
            ".near": "sincal:inspect-near-title",
            ".labels": "sincal:inspect-isometric-labels",
        }.get(suffix, "sincal:preview-isometric")
        script.write_text(
            f'(load "{lisp_path}")\n'
            f'({cad_function} "{output_path}" {insertion[0]} {insertion[1]})\n',
            encoding="ascii",
        )
        with console_log.open("wb") as log_stream:
            process = subprocess.Popen(
                [
                    str(accoreconsole), "/isolate", "sincal-web-preview", str(isolated_profile),
                    "/i", str(dwg), "/s", str(script), "/readonly", "/l", "en-US",
                ],
                stdout=log_stream,
                stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + 120
            while (
                (not output.exists() or output.stat().st_size == 0)
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.25)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        if not output.is_file() or output.stat().st_size == 0:
            raw_log = console_log.read_bytes() if console_log.exists() else b""
            log_tail = (
                raw_log.decode("utf-16", errors="replace")[-2000:]
                if raw_log.startswith(b"\xff\xfe")
                else raw_log.decode("utf-8", errors="replace")[-2000:]
            )
            raise PublishError(f"AutoCAD no generó la exportación temporal de la isométrica.\n{log_tail}")
        for artifact in (script, temporary_lisp, console_log):
            try:
                artifact.unlink(missing_ok=True)
            except PermissionError:
                pass


def run(args: argparse.Namespace) -> dict[str, Any]:
    drive_root = Path(args.drive_root).expanduser().resolve()
    if not drive_root.is_dir():
        raise PublishError(f"No existe la carpeta raíz: {drive_root}")

    drawing_candidates: list[DrawingCandidate] = []
    if args.dwg:
        dwg = Path(args.dwg).expanduser().resolve()
        if not dwg.is_file():
            raise PublishError(f"No existe el DWG: {dwg}")
        drawing_origin = "explicit"
    else:
        dwg, drawing_candidates = find_plan_one(drive_root, args.ot, args.structure)
        drawing_origin = "discovered"

    code = parse_drawing_code(dwg)
    memory, candidates = find_memory(drive_root, args.ot, code, args.structure)
    memory_text = extract_docx_text(memory) if memory.suffix.lower() == ".docx" else extract_pdf_text(memory)

    cad: dict[str, Any]
    if args.skip_cad:
        cad = {"status": "skipped"}
    else:
        executable = find_accoreconsole(args.accoreconsole)
        if executable is None:
            cad = {"status": "unavailable", "reason": "No se encontró accoreconsole.exe"}
        else:
            cad = inspect_isometric(dwg, executable, Path(__file__).parent / "cad" / "inspect_isometric.lsp")
            if cad["count"] != 1:
                raise PublishError(f"Se esperaban exactamente 1 coincidencia de {CAD_TITLE}; se obtuvieron {cad['count']}.")

    source = {
        "work_order": args.ot,
        "drawing": {
            "path": str(dwg),
            "sha256": sha256(dwg),
            "modified_at": modified_at(dwg),
            "origin": drawing_origin,
            **asdict(code),
        },
        "memory": {
            "path": str(memory),
            "sha256": sha256(memory),
            "modified_at": modified_at(memory),
            "extension": memory.suffix.lower(),
        },
    }
    return {
        "status": "ready_for_crop_review",
        "source": source,
        "memory_candidates": [
            {
                "path": str(candidate.path),
                "revision": candidate.revision,
                "score": candidate.score,
                "modified_at": modified_at(candidate.path),
            }
            for candidate in candidates
        ],
        "drawing_candidates": [
            {
                "path": str(candidate.path),
                "revision": candidate.revision,
                "score": candidate.score,
                "modified_at": modified_at(candidate.path),
            }
            for candidate in drawing_candidates
        ],
        "metadata": extract_metadata(memory_text),
        "isometric_detection": cad,
        "next_step": "Definir y validar el marco geométrico de la vista antes de exportar el DXF recortado.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ot", required=True, help="Orden de trabajo, por ejemplo G-130.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dwg", help="Plano 1 DWG ya identificado.")
    source.add_argument("--structure", help="Nombre de la estructura para descubrir automáticamente su Plano 1.")
    parser.add_argument("--drive-root", required=True, help="Carpeta sincronizada 'Proyectos Sincal'.")
    parser.add_argument("--output", default="build/project-intake.json", help="Informe JSON generado; no publica el sitio.")
    parser.add_argument("--accoreconsole", help="Ruta opcional de accoreconsole.exe.")
    parser.add_argument("--skip-cad", action="store_true", help="Omite la inspección de la etiqueta CAD.")
    args = parser.parse_args()
    try:
        report = run(args)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Informe generado: {output}")
        return 0
    except (PublishError, OSError, subprocess.TimeoutExpired) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
