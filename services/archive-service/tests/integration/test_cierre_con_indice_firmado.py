# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Un expediente con el índice ya firmado SE PUEDE cerrar.

El defecto se encontró cerrando un expediente real desde el navegador. Firmar el
índice electrónico con el expediente todavía abierto es un camino que la propia
interfaz ofrece (pestaña «Índice electrónico» → firmar). Quien lo usaba se
quedaba con un expediente **imposible de cerrar**:

  1. `close_expediente` transiciona el expediente a `closed`.
  2. Llama a `IndexService.regenerate` para emitir el índice definitivo.
  3. `regenerate` ve un índice vigente en estado `firmado` y lanza 409.
  4. La transacción revierte ÍNTEGRA — transición, evento y todo.

El expediente se quedaba `open` para siempre, y el único rastro era un asiento
`archive.expediente_close_failed` cuyo motivo, `expediente_closed`, decía justo
lo contrario de su estado real (`status_actual: open`). Cerrar es el acto
central del ciclo de vida de un expediente (Ley 594/2000): quedarse sin poder
ejecutarlo bloquea también la transferencia y la disposición final.

Lo más caro del hallazgo es que **el código ya describía el problema**: el
comentario del llamante decía «el hueco que este parche cierra» sobre un parche
que no lo cerraba — `assert_open=False` levanta el guard del estado del
expediente, no el del índice firmado, que son guards distintos.

Va al tier de integración porque lo que falla es una TRANSACCIÓN que revierte
entera: con conexiones simuladas no hay rollback que observar, y el estado
final del expediente —lo único que le importa a quien lo cierra— no existe.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.schemas.archive import ExpedienteCreate
from app.services.archive_service import ArchiveService
from app.services.index_service import IndexService

pytestmark = pytest.mark.asyncio


async def _firmar_indice_vigente(conn: asyncpg.Connection, expediente_id: uuid.UUID) -> None:
    """Deja el índice vigente en `firmado`, como tras sellarlo desde la UI."""
    await conn.execute(
        """
        UPDATE expediente_indice
           SET estado = 'firmado'
         WHERE expediente_id = $1
           AND version = (SELECT MAX(version) FROM expediente_indice WHERE expediente_id = $1)
        """,
        expediente_id,
    )


async def test_se_puede_cerrar_un_expediente_con_el_indice_firmado(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El caso exacto del navegador: firmar antes de cerrar no bloquea el cierre."""
    slug, conn = tenant_ctx
    service = ArchiveService(conn, slug)

    exp = await service.create_expediente(
        payload=ExpedienteCreate(name="itest cierre con indice firmado", nivel_seguridad=1),
        opened_by=None,
    )
    # El índice se genera al crear/vincular; se sella como haría un firmante.
    await IndexService(conn, slug).regenerate(exp.id, generado_por="itest")
    await _firmar_indice_vigente(conn, exp.id)

    await service.close_expediente(expediente_id=exp.id, actor_id=None, firmante=None)

    estado = await conn.fetchval("SELECT status FROM expedientes WHERE id = $1", exp.id)
    assert estado == "closed", (
        "el expediente debía quedar cerrado; si sigue 'open', el guard del índice "
        "firmado volvió a revertir la transacción entera"
    )


async def test_el_cierre_deja_su_asiento_y_no_el_de_fallo(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Gemelo: el rastro que queda tiene que ser el del acto, no el del fallo.

    Con el defecto, `audit_log` recibía `archive.expediente_close_failed` con un
    motivo que contradecía el estado real. Un asiento que describe mal lo que
    pasó es peor que ninguno: es lo que se consulta cuando alguien pregunta por
    qué el expediente sigue abierto.
    """
    slug, conn = tenant_ctx
    service = ArchiveService(conn, slug)

    exp = await service.create_expediente(
        payload=ExpedienteCreate(name="itest asiento de cierre", nivel_seguridad=1),
        opened_by=None,
    )
    await IndexService(conn, slug).regenerate(exp.id, generado_por="itest")
    await _firmar_indice_vigente(conn, exp.id)

    await service.close_expediente(expediente_id=exp.id, actor_id=None, firmante=None)

    acciones = await conn.fetch(
        "SELECT action FROM public.audit_log WHERE tenant_slug = $1 AND object_ref = $2",
        slug,
        exp.code,
    )
    nombres = {r["action"] for r in acciones}
    assert "archive.expediente_closed" in nombres
    assert "archive.expediente_close_failed" not in nombres


async def test_una_regeneracion_normal_SIGUE_rechazando_el_indice_firmado(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Control negativo: la excepción es SOLO para el cierre.

    Un índice firmado es inmutable, y levantar el guard para todo el mundo
    convertiría la corrección en una puerta abierta: cualquier rebuild ad-hoc
    podría pisar una versión ya sellada. Fuera del cierre, el 409 se mantiene.
    """
    from fastapi import HTTPException

    slug, conn = tenant_ctx
    service = ArchiveService(conn, slug)
    exp = await service.create_expediente(
        payload=ExpedienteCreate(name="itest guard sigue vivo", nivel_seguridad=1),
        opened_by=None,
    )
    index = IndexService(conn, slug)
    await index.regenerate(exp.id, generado_por="itest")
    await _firmar_indice_vigente(conn, exp.id)

    with pytest.raises(HTTPException) as exc:
        await index.regenerate(exp.id, generado_por="itest")
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "indice_firmado_inmutable"
