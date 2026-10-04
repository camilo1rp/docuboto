# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: AGPL v3 o posterior. Se entrega SIN GARANTÍA ALGUNA.

"""Contrato productor <-> notification: todo evento de radicado declara su nivel.

`notification-service.recortar_si_clasificado` decide qué sale por un webhook mirando
`nivel_seguridad`/`nivel_nuevo`/`nivel_anterior` del payload; un evento `document.*` o
`workflow.*` que no declara ninguno sale MÍNIMO (fail-closed). Es lo correcto, pero
deja al productor la tarea de declararlo: uno nuevo que se olvide deja a los
integradores externos sin el contenido que sí podían recibir (y, si el consumidor
cambiara de criterio, filtraría un reservado). OMI-01.

Qué vigila: el censo se DERIVA con `ast` de cada `events.emit(...)` en document-service
y workflow-service **y de cada `outbox.encolar(...)`** (AREA-09-05: desde T13 document-service
encola sus eventos en el outbox transaccional en vez de emitirlos tras el commit; el
guardián tiene que seguirlos o el censo se vaciaría en silencio). Los que no declaran nivel están en `SIN_NIVEL_DECLARADOS` (declarados,
no aprobados): la lista solo puede BAJAR. Un emisor nuevo sin nivel rompe el test.
"""

from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = ("document-service", "workflow-service")
CLAVES_DE_NIVEL = frozenset({"nivel_seguridad", "nivel_nuevo", "nivel_anterior"})

#: Emisores de `document.*` que hoy salen mínimos por el webhook. purged/annulled
#: llevan solo id+tracking (el consumidor conserva el id); los envíos postales
#: pierden guía/estado en el webhook (el tipo del evento ya dice el estado).
SIN_NIVEL_DECLARADOS = frozenset({
    "document-service::anulacion_service.py::'document.radicado.annulled'",
    "document-service::postal_service.py::_STREAM_EVENT[estado]",
    "document-service::purga_service.py::'document.radicado.purged'",
})


def _declara_nivel(payload: ast.expr) -> bool:
    if not isinstance(payload, ast.Dict):
        return True  # no analizable: no se acusa en falso (el censo exige literal abajo)
    for k, v in zip(payload.keys, payload.values, strict=True):
        if k is None:  # `**expr`
            if "nivel" in ast.unparse(v):
                return True
        elif isinstance(k, ast.Constant) and k.value in CLAVES_DE_NIVEL:
            return True
    return False


def _es_llamada(n: ast.AST, objeto: str, metodo: str) -> bool:
    return (
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == metodo
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == objeto
    )


def _censo() -> dict[str, bool]:
    censo: dict[str, bool] = {}
    for svc in SERVICIOS:
        for f in sorted((RAIZ / "services" / svc / "app").rglob("*.py")):
            for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                # Outbox: `outbox.encolar(conn, stream=, event_type=, ..., payload=)`.
                if _es_llamada(n, "outbox", "encolar"):
                    tipo = next((k.value for k in n.keywords if k.arg == "event_type"), None)
                    payload = next((k.value for k in n.keywords if k.arg == "payload"), None)
                    if tipo is not None and payload is not None:
                        censo[f"{svc}::{f.name}::{ast.unparse(tipo)}"] = _declara_nivel(payload)
                    continue
                if not (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "emit"
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "events"
                    and len(n.args) >= 3
                ):
                    continue
                payload = next((k.value for k in n.keywords if k.arg == "payload"), None)
                if payload is None:
                    continue
                censo[f"{svc}::{f.name}::{ast.unparse(n.args[2])}"] = _declara_nivel(payload)
    return censo


def test_el_censo_ve_los_emisores() -> None:
    censo = _censo()
    assert len(censo) >= 8, f"debería ver >=8 emisores: {sorted(censo)}"
    assert any(censo.values()) and not all(censo.values())


def test_el_censo_ve_los_emisores_de_workflow_con_nivel() -> None:
    """Control positivo: los de workflow SÍ lo declaran (vía `**await self._nivel_payload`)."""
    censo = _censo()
    wf = {k: v for k, v in censo.items() if k.startswith("workflow-service::")}
    assert len(wf) >= 3 and all(wf.values()), wf


def test_todo_emisor_de_radicado_declara_su_nivel() -> None:
    sin_nivel = {k for k, ok in _censo().items() if not ok}
    nuevos = sorted(sin_nivel - SIN_NIVEL_DECLARADOS)
    assert not nuevos, (
        "Emisores de document.*/workflow.* SIN nivel en el payload (el webhook los "
        f"envía mínimos): {nuevos}. Declara `nivel_seguridad` en el payload."
    )


def test_las_excepciones_siguen_siendo_excepciones() -> None:
    censo = _censo()
    arregladas = sorted(k for k in SIN_NIVEL_DECLARADOS if censo.get(k) is True)
    desaparecidas = sorted(k for k in SIN_NIVEL_DECLARADOS if k not in censo)
    assert not arregladas, f"ya declaran nivel: quítalas -> {arregladas}"
    assert not desaparecidas, f"ya no existen: quítalas -> {desaparecidas}"
