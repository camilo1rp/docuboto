# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Gates de autorización de la pasarela de conocimiento (ADR-037).

La bandera de la entidad (``require_asistente_habilitado``) y el permiso
``PERM_CONOCIMIENTO`` (``require_permission``) se resuelven en la BD del tenant
(ADR-013: la BD autoriza). El clearance sobre cada radicado ya no se resuelve aquí:
lo aplica document-service al buscar como el usuario (ADR-046 §2).

La conexión debe tener el ``search_path`` ya fijado al schema del tenant (lo hace
``get_tenant_conn``). Política **fail-closed**.
"""

import logging
from collections.abc import Iterable

import asyncpg
from fastapi import Depends, HTTPException, Request
from orpycamcp_common.database import get_tenant_conn
from orpycamcp_common.tenant import (
    ERROR_ASISTENTE_DESHABILITADO,
    ERROR_ASISTENTE_NO_DISPONIBLE,
    asistente_habilitado,
)

try:
    from orpycamcp_common import audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "knowledge-service"
logger = logging.getLogger(__name__)

async def require_asistente_habilitado(
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    """Gate de la bandera por entidad (ADR-037): 403 si la entidad no activó el asistente.

    Va ANTES de ``require_permission`` (el 403 honesto «la entidad no lo tiene» gana
    al «usted no tiene el permiso»). Aplica también a ROOT: la bandera es una decisión
    de la entidad, no un permiso de la persona. Sin caché (apagar debe ser inmediato) y
    fail-closed: si el registro no responde, 503, no se sirve.
    """
    try:
        activado = await asistente_habilitado(conn, request.headers.get("X-Tenant-Slug", ""))
    except Exception:  # noqa: BLE001 — no poder saberlo se responde como «no»
        logger.warning("no se pudo leer asistente_habilitado", exc_info=True)
        status_code, error, detail = ERROR_ASISTENTE_NO_DISPONIBLE
        raise HTTPException(
            status_code=status_code,
            detail={"error": error, "detail": detail, "status": status_code},
        ) from None
    if not activado:
        status_code, error, detail = ERROR_ASISTENTE_DESHABILITADO
        raise HTTPException(
            status_code=status_code,
            detail={"error": error, "detail": detail, "status": status_code},
        )




def motivo_interruptor(*, bandera: bool, proveedor: str, suspendido: bool) -> str | None:
    """None si la entidad tiene el envío encendido. Si no, un motivo cerrado.

    El interruptor es la bandera, el proveedor ``ppi`` y el envío no suspendido.
    No mira el radicado: un borrado (anulación, purga, nivel ya no habilitado)
    también se queda en cero llamadas cuando el interruptor está apagado.
    """
    if not bandera:
        return "bandera"
    if proveedor != "ppi":
        return "local"
    if suspendido:
        return "suspendido"
    return None


def motivo_salida(
    *,
    bandera: bool,
    proveedor: str,
    niveles_habilitados: Iterable[int],
    suspendido: bool,
    nivel_vivo: int | None,
) -> str | None:
    """None si el registro puede salir ahora. Si no, un motivo cerrado.

    Fail-closed (ADR-045 §3 y §4). El interruptor apagado (bandera o ``local``),
    el envío suspendido, un radicado ausente y un nivel vivo fuera de los
    habilitados niegan. No mira el nivel que traía el evento: quien llama pasa
    el nivel que acaba de leer.
    """
    if (motivo := motivo_interruptor(
        bandera=bandera, proveedor=proveedor, suspendido=suspendido
    )) is not None:
        return motivo
    if nivel_vivo is None:
        return "ausente"
    try:
        nivel = int(nivel_vivo)
        habilitados = {int(n) for n in niveles_habilitados}
    except (TypeError, ValueError):
        return "nivel"
    if nivel not in (1, 2, 3) or nivel not in habilitados:
        return "nivel"
    return None


def puede_salir(
    *,
    bandera: bool,
    proveedor: str,
    niveles_habilitados: Iterable[int],
    suspendido: bool,
    nivel_vivo: int | None,
) -> bool:
    """¿Puede salir este registro hacia el proveedor, ahora?"""
    return (
        motivo_salida(
            bandera=bandera,
            proveedor=proveedor,
            niveles_habilitados=niveles_habilitados,
            suspendido=suspendido,
            nivel_vivo=nivel_vivo,
        )
        is None
    )


