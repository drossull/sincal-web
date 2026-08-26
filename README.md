# sincal-web

## Ingreso de proyectos terminados (MVP)

El publicador local encuentra el Plano 1 desde la OT y el nombre de la
estructura, restringiendo la búsqueda a una carpeta que contenga
`Proyecto Definitivo` (por ejemplo, `E-2 Proyecto Definitivo`). Prioriza las
revisiones de `A` a `Z`, con `0` como la más nueva. En
`PLANOS\NATIVOS`, el primer DWG en orden alfabético es el Plano 1. Después
resuelve la memoria asociada, extrae metadatos verificables y detecta la única
etiqueta `VISTA ISOMETRICA` del DWG. Genera un informe JSON; no modifica
`proyectos.json` ni publica activos hasta que se valide el marco geométrico
de la vista.

Entre candidatos de la misma revisión, la fecha de modificación más reciente
decide la selección y queda registrada junto con los hashes de origen.

```powershell
python tools/publish_project.py `
  --ot G-45 `
  --structure "Puente El Azul" `
  --drive-root "H:\.shortcut-targets-by-id\...\Proyectos Sincal"
```

También se acepta `--dwg "C:\ruta\plano-01.dwg"` cuando el Plano 1 ya fue
identificado. El modo automático nunca usa archivos de `Anteproyectos`.
La búsqueda de memoria considera `.docx` y `.pdf` que coincidan con el
volumen y código de estructura del plano, y conserva la ruta, revisión y hash
del documento seleccionado. Para PDF se requiere `pymupdf`; DOCX se procesa
sin dependencias adicionales.
