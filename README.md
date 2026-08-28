# sincal-web

## Acceso interno Cadence

La navegación pública no muestra **Acceso trabajadores**. Mientras Cadence no
tenga un backend HTTPS publicado, Apache devuelve `404` para `/trabajadores` y
`/trabajadores.html`; el formulario se conserva únicamente para pruebas locales.

En desarrollo local, el navegador envía las credenciales directamente a
Cadence; el hosting estático no las persiste ni actúa como intermediario. La
publicación futura debe coordinarse con DNS, TLS, proxy inverso, autenticación
obligatoria y `CADENCE_LOGIN_ORIGINS` en el servicio Cadence.
La recuperación no es autónoma: la página indica que una contraseña olvidada
debe ser regenerada por el administrador de Sincal.

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
