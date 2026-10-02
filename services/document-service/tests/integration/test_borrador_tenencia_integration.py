# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Editar un borrador basta con TENER el radicado (AREA-01-N2, complemento).

Postgres REAL: el predicado de tenencia (ADR-028) vive en el `WHERE` del UPDATE y un
doble lo decidiría él mismo. Reglas:
  - sin padre: su creador;
  - con padre y paso activo: asignado al actor, o sin asignar y de su dependencia;
  - con padre sin paso activo: quien poseía el último paso, o quien radicó el padre
    si no hubo ningún paso (R16);
  - nadie más: el MISMO 404 neutro que un borrador inexistente, sin tocar la fila.
`aprobar`/`devolver_a_borrador` NO exigen tenencia (actos de revisión).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.core.database import tenant_schema

pytestmark = pytest.mark.integration

AUTOR = uuid.uuid4()  # radicó el padre y creó el borrador sin padre


@pytest.fixture
def tenant_ctx(tenant_flujo):
    return tenant_flujo


class _SinCircuito:
    @staticmethod
    async def get_estado_circuito(borrador_id: str, tenant_slug: str) -> dict:
        return {"pendiente": False, "completo": False}


async def _padre_y_borrador(tenant_ctx, *, con_padre: bool = True):
    from app.schemas.borrador import BorradorCreate
    from app.schemas.document import DocumentCreate
    from app.services.borrador_service import BorradorService
    from app.services.document_service import DocumentService

    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    padre = await DocumentService(conn, slug).create(
        DocumentCreate(doc_type="E", subject="Solicitud", sender_name="x"),
        registered_by=AUTOR,
    )
    servicio = BorradorService(conn, slug, signature_client=_SinCircuito)
    b = await servicio.crear(
        BorradorCreate(
            doc_type="S",
            subject="Respuesta",
            radicado_padre_id=padre.id if con_padre else None,
        ),
        AUTOR,
        max_clearance=3,
    )
    return slug, conn, servicio, padre, b


async def _paso(conn, padre_id, n, status, *, asignado=None, dept="Jurídica") -> None:
    await conn.execute(
        "INSERT INTO flow_steps (radicado_id, step_number, to_dept, assigned_to, status) "
        "VALUES ($1, $2, $3, $4, $5)",
        padre_id, n, dept, asignado, status,
    )


async def _puede_editar(servicio, conn, b, actor) -> bool:
    from app.schemas.borrador import BorradorUpdate

    antes = await conn.fetchval("SELECT updated_at FROM borrador WHERE id = $1", b.id)
    try:
        r = await servicio.editar(
            b.id, BorradorUpdate(subject=f"editado por {actor}"), max_clearance=3, actor=actor
        )
    except HTTPException as exc:
        assert exc.status_code == 404, "el no poseedor recibe el 404 neutro, no un 403"
        assert exc.detail["error"] == "not_found" or "no encontrado" in str(exc.detail).lower()
        assert (
            await conn.fetchval("SELECT updated_at FROM borrador WHERE id = $1", b.id)
        ) == antes, "una edición denegada no puede tocar la fila"
        return False
    assert r.subject.startswith("editado por")
    return True


@pytest.mark.asyncio
async def test_sin_padre_solo_edita_su_creador(tenant_ctx) -> None:
    _, conn, servicio, _, b = await _padre_y_borrador(tenant_ctx, con_padre=False)
    assert not await _puede_editar(servicio, conn, b, uuid.uuid4())
    assert not await _puede_editar(servicio, conn, b, None), "sin identidad: fail-closed"
    assert await _puede_editar(servicio, conn, b, AUTOR)  # control positivo


@pytest.mark.asyncio
async def test_con_paso_activo_asignado_edita_el_asignado_y_no_quien_radico(tenant_ctx) -> None:
    _, conn, servicio, padre, b = await _padre_y_borrador(tenant_ctx)
    asignado = uuid.uuid4()
    await _paso(conn, padre.id, 1, "pending", asignado=asignado)
    assert not await _puede_editar(servicio, conn, b, uuid.uuid4())
    assert not await _puede_editar(servicio, conn, b, AUTOR), (
        "quien radicó pero ya repartió el radicado deja de tenerlo"
    )
    assert await _puede_editar(servicio, conn, b, asignado)


@pytest.mark.asyncio
async def test_paso_sin_dueno_edita_cualquiera_de_la_dependencia(tenant_ctx) -> None:
    _, conn, servicio, padre, b = await _padre_y_borrador(tenant_ctx)
    miembro, ajeno, inactivo = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await conn.execute("INSERT INTO dependencias (codigo, nombre) VALUES (410, 'Jurídica')")
    await conn.execute("INSERT INTO dependencias (codigo, nombre) VALUES (420, 'Contratación')")
    await conn.execute(
        "INSERT INTO urd_usuaroldep (user_id, depe_id) VALUES ($1, 410), ($2, 420)",
        miembro, ajeno,
    )
    await conn.execute(
        "INSERT INTO urd_usuaroldep (user_id, depe_id, is_active) VALUES ($1, 410, false)",
        inactivo,
    )
    await _paso(conn, padre.id, 1, "pending", asignado=None, dept="Jurídica")
    assert not await _puede_editar(servicio, conn, b, ajeno)
    assert not await _puede_editar(servicio, conn, b, inactivo), "URD inactiva no cuenta"
    assert await _puede_editar(servicio, conn, b, miembro)


@pytest.mark.asyncio
async def test_sin_paso_activo_edita_quien_lo_tuvo_por_ultima_vez(tenant_ctx) -> None:
    _, conn, servicio, padre, b = await _padre_y_borrador(tenant_ctx)
    primero, ultimo = uuid.uuid4(), uuid.uuid4()
    await _paso(conn, padre.id, 1, "completed", asignado=primero)
    await _paso(conn, padre.id, 2, "completed", asignado=ultimo)
    assert not await _puede_editar(servicio, conn, b, primero)
    assert not await _puede_editar(servicio, conn, b, AUTOR), "hay historial: no vale registered_by"
    assert await _puede_editar(servicio, conn, b, ultimo)


@pytest.mark.asyncio
async def test_un_ultimo_paso_cancelado_no_da_tenencia(tenant_ctx) -> None:
    """Solo quien COMPLETÓ el último paso lo tuvo «por última vez» (R16)."""
    _, conn, servicio, padre, b = await _padre_y_borrador(tenant_ctx)
    quien = uuid.uuid4()
    await _paso(conn, padre.id, 1, "cancelled", asignado=quien)
    assert not await _puede_editar(servicio, conn, b, quien)
    assert not await _puede_editar(servicio, conn, b, AUTOR)


@pytest.mark.asyncio
async def test_sin_ningun_paso_edita_quien_radico_el_padre(tenant_ctx) -> None:
    _, conn, servicio, _, b = await _padre_y_borrador(tenant_ctx)
    assert not await _puede_editar(servicio, conn, b, uuid.uuid4())
    assert await _puede_editar(servicio, conn, b, AUTOR)


@pytest.mark.asyncio
async def test_aprobar_y_devolver_no_exigen_tenencia(tenant_ctx) -> None:
    """Son actos de revisión: aprobar suele ser de OTRA persona (separación de
    funciones). Siguen con PERM_RADI + clearance + circuito, como antes."""
    _, conn, servicio, padre, b = await _padre_y_borrador(tenant_ctx)
    await _paso(conn, padre.id, 1, "pending", asignado=uuid.uuid4())
    revisor = uuid.uuid4()
    assert not await _puede_editar(servicio, conn, b, revisor)
    aprobado = await servicio.aprobar(b.id, max_clearance=3, actor=revisor)
    assert aprobado.estado == "aprobado"
    devuelto = await servicio.devolver_a_borrador(b.id, max_clearance=3, actor=revisor)
    assert devuelto.estado == "borrador"


# ── radicar (acto IRREVERSIBLE) exige la misma tenencia; PATCH no edita un radicado ──


async def _aprobar_directo(conn, b) -> None:
    await conn.execute("UPDATE borrador SET estado = 'aprobado' WHERE id = $1", b.id)


async def _intenta_radicar(slug, conn, servicio, b, actor) -> bool:
    """`True` si radicó; si no, exige el 404 neutro y que NADA haya cambiado
    (ni número consumido ni borrador tocado)."""
    from app.services.document_service import DocumentService

    radicados = await conn.fetchval("SELECT count(*) FROM radicados")
    try:
        await servicio.radicar(b.id, DocumentService(conn, slug), actor, max_clearance=3)
    except HTTPException as exc:
        assert exc.status_code == 404, "el no poseedor recibe el 404 neutro, no un 403"
        assert await conn.fetchval("SELECT count(*) FROM radicados") == radicados, (
            "un radicar denegado consumió un número"
        )
        assert await conn.fetchval("SELECT estado FROM borrador WHERE id = $1", b.id) == "aprobado"
        return False
    return True


@pytest.mark.asyncio
async def test_radicar_exige_tenencia_con_padre(tenant_ctx) -> None:
    slug, conn, servicio, padre, b = await _padre_y_borrador(tenant_ctx)
    asignado = uuid.uuid4()
    await _paso(conn, padre.id, 1, "pending", asignado=asignado)
    await _aprobar_directo(conn, b)
    assert not await _intenta_radicar(slug, conn, servicio, b, uuid.uuid4())
    assert not await _intenta_radicar(slug, conn, servicio, b, None), "sin identidad: fail-closed"
    assert not await _intenta_radicar(slug, conn, servicio, b, AUTOR), (
        "quien radicó el padre pero ya lo repartió no tiene el borrador"
    )
    assert await _intenta_radicar(slug, conn, servicio, b, asignado)  # control positivo
    assert await conn.fetchval("SELECT estado FROM borrador WHERE id = $1", b.id) == "radicado"


@pytest.mark.asyncio
async def test_radicar_sin_padre_solo_su_creador(tenant_ctx) -> None:
    slug, conn, servicio, _, b = await _padre_y_borrador(tenant_ctx, con_padre=False)
    await _aprobar_directo(conn, b)
    assert not await _intenta_radicar(slug, conn, servicio, b, uuid.uuid4())
    assert await _intenta_radicar(slug, conn, servicio, b, AUTOR)


@pytest.mark.asyncio
async def test_editar_no_toca_un_borrador_que_se_radico_entre_la_guarda_y_el_update(
    tenant_ctx, monkeypatch
) -> None:
    """TOCTOU: `_require_editable` lo vio editable; antes del UPDATE se radicó. El
    UPDATE lleva `estado <> 'radicado'` y la respuesta es el 409, no una edición."""
    from app.schemas.borrador import BorradorUpdate

    _, conn, servicio, _, b = await _padre_y_borrador(tenant_ctx, con_padre=False)
    visto_antes = await servicio._repo.get_borrador(b.id, 3)
    await conn.execute("UPDATE borrador SET estado = 'radicado' WHERE id = $1", b.id)

    async def _rancio(*_a, **_k):
        return visto_antes  # la lectura previa, ya vieja

    monkeypatch.setattr(servicio, "_require_editable", _rancio)
    with pytest.raises(HTTPException) as exc:
        await servicio.editar(b.id, BorradorUpdate(subject="colado tras radicar"), max_clearance=3, actor=AUTOR)
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "borrador_radicado"
    assert await conn.fetchval("SELECT subject FROM borrador WHERE id = $1", b.id) == "Respuesta"
