# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Los borradores respetan el no-read-up y su edición deja asiento (AREA-01-N2).

Postgres REAL: el gate vive en el `WHERE` y un mock lo decidiría él mismo.
Hallazgo: cualquier `PERM_RADI` de nivel 1 listaba, leía y editaba los borradores
de todo el tenant, incluidos los que responden a radicados reservados.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
from fastapi import HTTPException

from app.core.database import tenant_schema

pytestmark = pytest.mark.integration

#: Ac. AGN 060/2001 §5.2: el borrador no guarda tipo ni dependencias; se aportan al radicar.
from app.schemas.borrador import BorradorRadicar  # noqa: E402

_DATOS = BorradorRadicar(tipo_documental="Oficio", origin_dept_code=1, dest_dept_code=1)

# Quien radicó el padre y creó los borradores: tiene el radicado (sin pasos de
# flujo, la tenencia cae en `registered_by`) y por tanto puede editarlos.
AUTOR = uuid.uuid4()


@pytest.fixture
def tenant_ctx(tenant_flujo):
    """Este archivo ejerce el predicado de tenencia: necesita las tablas del flujo."""
    return tenant_flujo


async def _escenario(tenant_ctx: tuple[str, asyncpg.Connection]):
    from app.schemas.borrador import BorradorCreate
    from app.schemas.document import DocumentCreate
    from app.services.borrador_service import BorradorService
    from app.services.document_service import DocumentService

    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    padre = await DocumentService(conn, slug).create(
        DocumentCreate(doc_type="E",doc_class="Petición",dest_dept_code=1, subject="Queja reservada", sender_name="x"),
        registered_by=AUTOR,
    )
    await conn.execute("UPDATE radicados SET nivel_seguridad = 3, clasificacion_fundamento = 'Ley 1712/2014 art. 19 (prueba)' WHERE id = $1", padre.id)
    servicio = BorradorService(conn, slug)
    reservado = await servicio.crear(
        BorradorCreate(
            doc_type="S", subject="Respuesta a la queja", radicado_padre_id=padre.id
        ),
        AUTOR,
        max_clearance=3,
    )
    publico = await servicio.crear(
        BorradorCreate(doc_type="S", subject="Oficio suelto sin padre"),
        AUTOR,
        max_clearance=1,
    )
    return slug, conn, servicio, padre, reservado, publico


@pytest.mark.asyncio
async def test_el_borrador_hereda_el_nivel_del_padre(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    _, _, _, _, reservado, publico = await _escenario(tenant_ctx)
    assert reservado.nivel_seguridad == 3
    assert publico.nivel_seguridad == 1


@pytest.mark.asyncio
async def test_clearance_1_no_lista_ni_lee_ni_edita_el_reservado(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch
) -> None:
    from app.schemas.borrador import BorradorUpdate

    _, conn, servicio, _, reservado, publico = await _escenario(tenant_ctx)
    antes = await conn.fetchval("SELECT updated_at FROM borrador WHERE id = $1", reservado.id)

    ids = {b.id for b in await servicio.listar(None, max_clearance=1)}
    assert ids == {publico.id}, "el listado de nivel 1 no puede incluir el borrador reservado"

    with pytest.raises(HTTPException) as exc:
        await servicio.get(reservado.id, max_clearance=1)
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        await servicio.editar(
            reservado.id, BorradorUpdate(subject="Reescrito por quien no debe"),
            max_clearance=1, actor=uuid.uuid4(),
        )
    assert exc.value.status_code == 404
    assert (
        await conn.fetchval("SELECT updated_at FROM borrador WHERE id = $1", reservado.id)
    ) == antes, "el PATCH denegado no puede tocar la fila"
    assert await conn.fetchval("SELECT subject FROM borrador WHERE id = $1", reservado.id) == (
        "Respuesta a la queja"
    )

    with pytest.raises(HTTPException) as exc:
        await servicio.aprobar(reservado.id, max_clearance=1)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_clearance_3_lo_ve_y_lo_edita_dejando_asiento(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Control positivo: sin él, un gate que niega a TODOS pasaría igual."""
    from app.schemas.borrador import BorradorUpdate

    slug, conn, servicio, _, reservado, publico = await _escenario(tenant_ctx)
    ids = {b.id for b in await servicio.listar(None, max_clearance=3)}
    assert ids == {reservado.id, publico.id}

    actor = AUTOR
    editado = await servicio.editar(
        reservado.id, BorradorUpdate(subject="Respuesta corregida"), max_clearance=3, actor=actor
    )
    assert editado.subject == "Respuesta corregida"
    asientos = await conn.fetch(
        "SELECT actor, payload FROM public.audit_log "
        "WHERE tenant_slug = $1 AND action = 'borrador.editado' AND object_ref = $2",
        slug,
        str(reservado.id),
    )
    assert len(asientos) == 1
    assert asientos[0]["actor"] == str(actor)


@pytest.mark.asyncio
async def test_reclasificar_el_padre_cierra_tambien_el_borrador(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """La instantánea del hijo no puede quedarse más abierta que el padre."""
    _, conn, servicio, padre, _, _ = await _escenario(tenant_ctx)
    from app.schemas.borrador import BorradorCreate

    # Padre público, borrador nivel 1; después el padre sube a 2.
    await conn.execute("UPDATE radicados SET nivel_seguridad = 1 WHERE id = $1", padre.id)
    b = await servicio.crear(
        BorradorCreate(doc_type="S", subject="Respuesta posterior", radicado_padre_id=padre.id),
        None,
        max_clearance=1,
    )
    assert b.nivel_seguridad == 1
    await conn.execute("UPDATE radicados SET nivel_seguridad = 2, clasificacion_fundamento = 'Ley 1712/2014 art. 19 (prueba)' WHERE id = $1", padre.id)
    assert b.id not in {x.id for x in await servicio.listar(None, max_clearance=1)}
    with pytest.raises(HTTPException):
        await servicio.get(b.id, max_clearance=1)


@pytest.mark.asyncio
async def test_no_se_crea_un_borrador_sobre_un_padre_que_no_se_alcanza(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    from app.schemas.borrador import BorradorCreate

    _, _, servicio, padre, _, _ = await _escenario(tenant_ctx)
    with pytest.raises(HTTPException) as exc:
        await servicio.crear(
            BorradorCreate(doc_type="S", subject="Intento sobre reservado", radicado_padre_id=padre.id),
            None,
            max_clearance=1,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_el_repositorio_gatea_cada_escritura_por_si_mismo(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Defensa en profundidad: el UPDATE y el cambio de estado llevan SU gate.

    El servicio ya cortó en el `get`; si alguien reordena el servicio, el
    repositorio no puede quedarse abierto.
    """
    from app.repositories.borrador_repository import BorradorRepository

    _, conn, _, _, reservado, _ = await _escenario(tenant_ctx)
    repo = BorradorRepository(conn)
    assert await repo.update_borrador(
        reservado.id, {"subject": "colado"}, 1, actor=AUTOR, con_padre=True
    ) is None
    assert await repo.set_estado(reservado.id, "aprobado", max_clearance=1) is None
    assert await conn.fetchval("SELECT subject FROM borrador WHERE id = $1", reservado.id) == (
        "Respuesta a la queja"
    )
    assert await conn.fetchval("SELECT estado FROM borrador WHERE id = $1", reservado.id) == (
        "borrador"
    )
    assert await repo.update_borrador(
        reservado.id, {"subject": "legítimo"}, 3, actor=AUTOR, con_padre=True
    ) is not None


class _SinCircuito:
    """Doble de signature-service: sin circuito abierto."""

    @staticmethod
    async def get_estado_circuito(borrador_id: str, tenant_slug: str) -> dict:
        return {"pendiente": False, "completo": False}


def _clearance_3(monkeypatch) -> None:
    """El radicador tiene clearance 3 (el no-write-up de `create` lo resuelve por actor)."""

    async def _c(conn, user_id):
        return 3

    monkeypatch.setattr("app.services.document_service.resolve_caller_clearance", _c)

    # Sin render ni storage reales: aquí se prueba la clasificación, no el PDF.
    async def _sin_documento(self, *a, **k):
        return None

    monkeypatch.setattr(
        "app.services.borrador_service.BorradorService._producir", _sin_documento
    )


@pytest.mark.asyncio
async def test_radicar_hereda_el_nivel_del_padre_y_el_de_clearance_1_no_lo_ve(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch
) -> None:
    """El acto irreversible no puede publicar lo que el gate del borrador protegía.

    Antes el `DocumentCreate` salía con el default (nivel 1) y `observations` =
    cuerpo que arrastra datos del padre CLASIFICADO.
    """
    from app.schemas.borrador import BorradorCreate
    from app.services.borrador_service import BorradorService
    from app.services.document_service import DocumentService

    _clearance_3(monkeypatch)
    slug, conn, _, padre, _, _ = await _escenario(tenant_ctx)
    await conn.execute(
        "UPDATE radicados SET clasificacion_fundamento = 'Ley 1712 art. 18', "
        "clasificacion_causal = 'intimidad', clasificacion_plazo_meses = 60 WHERE id = $1",
        padre.id,
    )
    servicio = BorradorService(conn, slug, signature_client=_SinCircuito)
    b = await servicio.crear(
        BorradorCreate(
            doc_type="S", subject="Respuesta", cuerpo="Datos del padre reservado",
            radicado_padre_id=padre.id,
        ),
        None,
        max_clearance=3,
    )
    await conn.execute("UPDATE borrador SET estado = 'aprobado' WHERE id = $1", b.id)

    docs = DocumentService(conn, slug)
    nuevo = await servicio.radicar(b.id, docs, AUTOR, max_clearance=3, datos=_DATOS)

    fila = await conn.fetchrow(
        "SELECT nivel_seguridad, clasificacion_fundamento, clasificacion_causal, "
        "clasificacion_plazo_meses, responde_a FROM radicados WHERE id = $1",
        nuevo.id,
    )
    assert fila["nivel_seguridad"] == 3
    assert fila["clasificacion_fundamento"] == "Ley 1712 art. 18"
    assert fila["clasificacion_causal"] == "intimidad"
    assert fila["clasificacion_plazo_meses"] == 60
    assert fila["responde_a"] == padre.id
    # Quien tiene clearance 1 no lo alcanza por la vía del documento...
    assert await docs._repo.get_by_id(nuevo.id, 1) is None
    # ...y el de clearance 3 sí (control positivo).
    assert await docs._repo.get_by_id(nuevo.id, 3) is not None


@pytest.mark.asyncio
async def test_radicar_con_padre_reservado_sin_fundamento_no_cae_a_nivel_1(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch
) -> None:
    """Un padre antiguo sin fundamento no puede dejar al hijo en PUBLICA."""
    from app.schemas.borrador import BorradorCreate
    from app.services.borrador_service import BorradorService
    from app.services.document_service import DocumentService

    _clearance_3(monkeypatch)
    slug, conn, _, padre, _, _ = await _escenario(tenant_ctx)
    # El padre «antiguo»: la restricción 041 impide ya CREAR uno así, de modo que se
    # simula el heredado quitándola (en producción la migración los rellenó). El código
    # de herencia sigue siendo la defensa para lo que pudiera quedar sin motivar.
    await conn.execute("ALTER TABLE radicados DROP CONSTRAINT radicados_clasificado_con_fundamento")
    await conn.execute("UPDATE radicados SET clasificacion_fundamento = NULL WHERE id = $1", padre.id)
    servicio = BorradorService(conn, slug, signature_client=_SinCircuito)
    b = await servicio.crear(
        BorradorCreate(doc_type="S", subject="Respuesta", radicado_padre_id=padre.id),
        None,
        max_clearance=3,
    )
    await conn.execute("UPDATE borrador SET estado = 'aprobado' WHERE id = $1", b.id)
    nuevo = await servicio.radicar(
        b.id, DocumentService(conn, slug), AUTOR, max_clearance=3, datos=_DATOS
    )
    fila = await conn.fetchrow(
        "SELECT nivel_seguridad, clasificacion_fundamento FROM radicados WHERE id = $1",
        nuevo.id,
    )
    assert fila["nivel_seguridad"] == 3
    assert "Heredada" in fila["clasificacion_fundamento"]


@pytest.mark.asyncio
async def test_aprobar_y_devolver_dejan_asiento_atomico(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    from app.services.borrador_service import BorradorService

    slug, conn, _, _, _, publico = await _escenario(tenant_ctx)
    servicio = BorradorService(conn, slug, signature_client=_SinCircuito)
    actor = uuid.uuid4()
    await servicio.aprobar(publico.id, max_clearance=1, actor=actor)
    await servicio.devolver_a_borrador(publico.id, max_clearance=1, actor=actor)
    filas = await conn.fetch(
        "SELECT action, actor, payload FROM public.audit_log "
        "WHERE tenant_slug = $1 AND object_ref = $2 "
        "AND action IN ('borrador.aprobado', 'borrador.devuelto') ORDER BY id",
        slug,
        str(publico.id),
    )
    assert [f["action"] for f in filas] == ["borrador.aprobado", "borrador.devuelto"]
    assert all(f["actor"] == str(actor) for f in filas)
