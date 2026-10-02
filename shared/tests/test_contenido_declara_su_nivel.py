# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Todo esquema que lleve contenido de un radicado declara su `nivel_seguridad`.

La barrera de residencia del asistente (`mcp-server/app/services/assistant.py`,
`_recortar_por_residencia`) recorta el contenido de un objeto SOLO si el propio
objeto trae `nivel_seguridad >= 2`. Es la decisión correcta (no se recorta lo
que no es documento), pero deja el control en manos del PRODUCTOR: un esquema
que lleva `subject` y no declara el nivel saca el asunto de un radicado
RESERVADO al proveedor externo sin que nada lo note (ADR-006, «sin excepciones»).
Pasó con `RespuestaItem` y con `RadicadoDeTercero` (AREA-11-X1/X2).

**Qué vigila.** El censo se DERIVA de `services/*/app/schemas/*.py` con `ast`:
toda clase con un campo `subject`, `observations` o `asunto` debe tener también
`nivel_seguridad`. Las que hoy no lo tienen están en `EXCEPCIONES` —declaradas,
no aprobadas—: la lista solo puede BAJAR. Un esquema nuevo con contenido y sin
nivel rompe este test; arreglar una excepción obliga a quitarla de la lista
(`test_las_excepciones_siguen_siendo_excepciones`).
"""

from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"

CAMPOS_DE_CONTENIDO = frozenset({"subject", "observations", "asunto"})

#: "<servicio>::<archivo>::<Clase>". Entradas (Create/Update) que no devuelven
#: nada, o respuestas que aún no declaran el nivel. SearchHit y RadicadoMetaItem
#: son de LECTURA y siguen fuera de la barrera: deuda real, no un descuido del
#: censo.
EXCEPCIONES = frozenset({
    "archive-service::fisico.py::FuidRow",
    "document-service::anulacion.py::AnulacionPendiente",
    "document-service::batch.py::BatchDocumentCreate",
    "document-service::borrador.py::BorradorCreate",
    "document-service::borrador.py::BorradorUpdate",
    "document-service::document.py::DocumentUpdate",
    "document-service::internal.py::RadicadoMetaItem",
    "document-service::public.py::PqrsCreate",
    "document-service::public.py::VentanillaRadicacion",
    "document-service::respuesta.py::RespuestaRapidaCreate",
    "document-service::search.py::SearchHit",
    "notification-service::notification.py::NotificationResponse",
    "notification-service::notification.py::NotificationSend",
    "workflow-service::rules.py::RuleEvaluationRequest",
})


def _censo() -> dict[str, bool]:
    """`clave de clase -> declara nivel_seguridad`, solo clases con contenido."""
    censo: dict[str, bool] = {}
    for f in sorted(SERVICIOS.glob("*/app/schemas/*.py")):
        for c in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if not isinstance(c, ast.ClassDef):
                continue
            campos = {
                s.target.id
                for s in c.body
                if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
            }
            if campos & CAMPOS_DE_CONTENIDO:
                censo[f"{f.parts[-4]}::{f.name}::{c.name}"] = "nivel_seguridad" in campos
    return censo


def _niveles_con_default() -> set[str]:
    """Respuestas con contenido cuyo `nivel_seguridad` tiene valor por defecto.

    Un default (`= 1`, `= None`) es fail-OPEN: si el SELECT que llena el esquema
    olvida la columna, la respuesta sale con nivel 1 y la barrera de residencia
    del asistente deja pasar el texto de un reservado, sin ningún error. Las
    entradas (Create/Update/Request) sí pueden tener default: no devuelven datos.
    """
    con_default: set[str] = set()
    for f in sorted(SERVICIOS.glob("*/app/schemas/*.py")):
        for c in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if not isinstance(c, ast.ClassDef) or c.name.endswith(("Create", "Update", "Request")):
                continue
            campos = {
                s.target.id
                for s in c.body
                if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
            }
            if not campos & CAMPOS_DE_CONTENIDO:
                continue
            for st in c.body:
                if (
                    isinstance(st, ast.AnnAssign)
                    and isinstance(st.target, ast.Name)
                    and st.target.id == "nivel_seguridad"
                    and st.value is not None
                ):
                    con_default.add(f"{f.parts[-4]}::{f.name}::{c.name}")
    return con_default


def test_ninguna_respuesta_da_default_al_nivel() -> None:
    con_default = _niveles_con_default()
    assert not con_default, (
        "Respuestas con contenido cuyo nivel_seguridad tiene default (fail-open: un "
        f"SELECT que lo omita sale como nivel 1) -> {sorted(con_default)}"
    )


def test_el_censo_no_es_vacio() -> None:
    censo = _censo()
    assert len(censo) >= 10, f"el censo debería ver >=10 modelos con contenido: {len(censo)}"
    assert any(censo.values()), "ningún modelo declara nivel: el censo no está leyendo bien"


def test_todo_contenido_declara_su_nivel() -> None:
    sin_nivel = {k for k, ok in _censo().items() if not ok}
    nuevos = sorted(sin_nivel - EXCEPCIONES)
    assert not nuevos, (
        "Esquemas con subject/observations/asunto SIN nivel_seguridad: la barrera de "
        f"residencia del asistente no los recorta -> {nuevos}. Declara el campo "
        "(y añádelo al SELECT que lo llena)."
    )


def test_las_excepciones_siguen_siendo_excepciones() -> None:
    censo = _censo()
    arregladas = sorted(k for k in EXCEPCIONES if censo.get(k) is True)
    desaparecidas = sorted(k for k in EXCEPCIONES if k not in censo)
    assert not arregladas, f"ya declaran el nivel: quítalas de EXCEPCIONES -> {arregladas}"
    assert not desaparecidas, f"ya no existen: quítalas de EXCEPCIONES -> {desaparecidas}"
