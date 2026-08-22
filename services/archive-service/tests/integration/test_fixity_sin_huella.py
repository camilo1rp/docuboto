# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Un documento SIN huella no es un documento alterado.

Encontrado recibiendo una transferencia real. La verificación de fixity resolvía
tres situaciones distintas con un solo booleano:

  1. Las dos huellas existen y coinciden      → íntegro.
  2. Existen y difieren (o falta una)         → discrepancia real: bloquea.
  3. NINGUNA existe                           → el documento no tiene contenido
                                                binario que resumir.

El tercer caso contaba como fallo. Un radicado sin anexos —un registro de
metadatos, que es lo normal en ventanilla— no tiene `content_hash`, así que
**cualquier expediente formado por radicados sin adjuntos era intransferible**,
y el motivo que se devolvía era «la verificación de fixity del acervo falló»:
una corrupción del acervo que no había ocurrido.

La corrección no consiste en dejar pasar: consiste en distinguir y **declarar**.
La fixity se predica de los objetos que tienen huella, y de cuántos no se está
dando fe se dice explícitamente, en la respuesta y en el asiento (ISO 16363;
Ley 594/2000 art. 16). Un `documentos_ok: true` que callara cuántos documentos
no se verificaron se leería como «se comprobaron todos», y eso sí sería falso.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.clearance import CLEARANCE_SIN_RESTRICCION
from app.schemas.archive import ExpedienteCreate, RadicadoLink
from app.services.archive_service import ArchiveService
from app.services.index_service import IndexService

pytestmark = pytest.mark.asyncio


async def _expediente_con_radicado(
    conn: asyncpg.Connection, slug: str, *, content_hash: str | None
):
    """Expediente con un radicado vinculado, con o sin huella declarada."""
    service = ArchiveService(conn, slug)
    exp = await service.create_expediente(
        payload=ExpedienteCreate(name=f"itest fixity {content_hash or 'sin huella'}", nivel_seguridad=1),
        opened_by=None,
    )
    radicado_id = uuid.uuid4()
    await service.link_radicado(
        expediente_id=exp.id,
        payload=RadicadoLink(
            radicado_id=radicado_id,
            tracking_number=f"2026-ITEST-E-{str(radicado_id.int)[:6]}",
            content_hash=content_hash,
        ),
        user_id=None,
    )
    return exp, radicado_id


async def test_un_radicado_sin_anexos_no_rompe_la_fixity(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """El caso del navegador: expediente de ventanilla, sin adjuntos."""
    slug, conn = tenant_ctx
    exp, _ = await _expediente_con_radicado(conn, slug, content_hash=None)

    fx = await IndexService(conn, slug).fixity(
        exp.id, version=None, max_clearance=CLEARANCE_SIN_RESTRICCION
    )

    assert fx.documentos_ok is True, (
        "un documento sin huella no es un documento alterado; contarlo como "
        "fallo hacía intransferible todo expediente sin adjuntos"
    )
    assert fx.documentos_sin_huella == 1


async def test_de_lo_que_no_se_verifica_se_deja_constancia(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Gemelo: no basta con no bloquear — hay que decir de qué no se responde.

    Un `documentos_ok: true` sin este número se leería como «se comprobaron
    todos los documentos», que es exactamente la afirmación que no se puede
    hacer.
    """
    slug, conn = tenant_ctx
    exp, _ = await _expediente_con_radicado(conn, slug, content_hash=None)

    fx = await IndexService(conn, slug).fixity(
        exp.id, version=None, max_clearance=CLEARANCE_SIN_RESTRICCION
    )

    assert fx.documentos_sin_huella == len(fx.documentos)
    assert all(d.sin_huella_declarada for d in fx.documentos)


async def test_una_huella_QUE_CAMBIA_sigue_bloqueando(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Control negativo: la alteración real se detecta igual que antes.

    Es la mitad que no puede perderse al corregir: si el documento tenía huella
    y ahora es otra, el acervo cambió y la recepción no puede seguir.
    """
    slug, conn = tenant_ctx
    exp, radicado_id = await _expediente_con_radicado(conn, slug, content_hash="a" * 64)

    # Alguien cambia el contenido: la huella actual ya no es la del índice.
    await conn.execute(
        "UPDATE expediente_radicados SET content_hash = $2 "
        "WHERE expediente_id = $1 AND radicado_id = $3",
        exp.id,
        "b" * 64,
        radicado_id,
    )

    fx = await IndexService(conn, slug).fixity(
        exp.id, version=None, max_clearance=CLEARANCE_SIN_RESTRICCION
    )

    assert fx.documentos_ok is False
    assert fx.documentos_sin_huella == 0
