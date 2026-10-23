# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-5 / C11 (Postgres REAL): el préstamo identificado, serializado y simétrico.

Lo que los mocks no pueden probar: que dos préstamos simultáneos no pasan los dos
(H4), que padre e hijo se excluyen aunque cada uno mire una unidad distinta, que el
clearance del PRESTATARIO está en el `WHERE`, y que el motor (triggers, índice parcial,
FK RESTRICT) defiende lo que el servicio no ve.

Mutaciones que deben ponerse en rojo (descritas, no ejecutadas):
- quitar `ux_prestamo_unidad_activo` Y el trigger `prestamo_guard_insert` ->
  `test_dos_prestamos_simultaneos_de_la_misma_unidad_solo_uno_gana`;
- bloquear por UNIDAD en vez de por RAÍZ (`unidad_lock_arbol`) ->
  `test_padre_e_hijo_simultaneos_solo_uno_gana`;
- quitar la condición de clearance del prestatario del `WHERE` ->
  `test_el_prestatario_necesita_clearance_sobre_el_contenido`;
- quitar `trg_transferencia_unidad_prestada` -> `test_no_se_transfiere_lo_que_esta_prestado`;
- volver a `ON DELETE CASCADE` -> `test_la_traza_de_custodia_no_se_borra`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

import asyncpg
import pytest
from fastapi import HTTPException

from app.core.database import tenant_schema
from app.schemas.fisico import ExpedienteUnidadLink, PrestamoCreate
from app.services.fisico_service import FisicoService

from ._auth_minimo import crear_tablas_auth, crear_usuario
from .conftest import DSN

pytestmark = pytest.mark.integration

ACTOR = uuid.uuid4()
MANANA = date.today() + timedelta(days=1)
EXTERNO = {
    "tipo": "externo",
    "nombre": "Ana Jurídica",
    "documento_tipo": "CC",
    "documento_numero": "1234567890",
}


def _cuerpo(prestatario: dict | None = None) -> PrestamoCreate:
    return PrestamoCreate(prestatario=prestatario or EXTERNO, fecha_devolucion_esperada=MANANA)


async def _unidad(conn, s: str, parent: uuid.UUID | None = None) -> uuid.UUID:
    return await conn.fetchval(
        f"INSERT INTO {s}.unidad_conservacion (tipo, codigo, signatura, parent_id) "
        "VALUES ('caja', $1, $2, $3) RETURNING id",
        f"C-{uuid.uuid4().hex[:6]}",
        f"SIG-{uuid.uuid4().hex[:8]}",
        parent,
    )


async def _expediente(conn, s: str, unidad: uuid.UUID | None = None, nivel: int = 1) -> uuid.UUID:
    exp = await conn.fetchval(
        f"INSERT INTO {s}.expedientes (code, name, nivel_seguridad) "
        "VALUES ($1, 'Expediente C11', $2) RETURNING id",
        f"EXP-{uuid.uuid4().hex[:8]}",
        nivel,
    )
    if unidad is not None:
        await conn.execute(
            f"INSERT INTO {s}.expediente_unidad (expediente_id, unidad_id) VALUES ($1, $2)",
            exp,
            unidad,
        )
    return exp


async def _armar(tenant_ctx):
    slug, conn = tenant_ctx
    await crear_tablas_auth(conn, slug)
    # Los INSERT directos de estos tests disparan triggers que resuelven por `search_path`
    # (como en producción, donde la conexión ya lo trae fijado por la dependencia).
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}"')
    return slug, conn, f'"{tenant_schema(slug)}"', FisicoService(conn, slug)


async def _prestar(svc: FisicoService, unidad: uuid.UUID, cuerpo: PrestamoCreate | None = None):
    return await svc.create_prestamo(unidad, cuerpo or _cuerpo(), str(ACTOR), actor=ACTOR)


async def _error(coro) -> str:
    with pytest.raises(HTTPException) as exc:
        await coro
    return exc.value.detail["error"]


# -- una sola unidad, dos prestamos a la vez -----------------------------


async def test_un_prestamo_deja_la_fila_identificada(tenant_ctx) -> None:
    """Control positivo de todo lo demás: sin él, un servicio que rechazara SIEMPRE
    pasaría las pruebas de rechazo."""
    slug, conn, s, svc = await _armar(tenant_ctx)
    unidad = await _unidad(conn, s)
    p = await _prestar(svc, unidad)
    assert p.prestatario_tipo == "externo" and p.prestado_por == ACTOR
    fila = await conn.fetchrow(f"SELECT * FROM {s}.prestamo WHERE id = $1", p.id)
    assert fila["tercero_documento_numero"] == "1234567890"  # la fila lleva el dato completo
    asiento = await conn.fetchval(
        "SELECT payload::text FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action = 'archive.prestamo_created'",
        slug,
    )
    assert "1234567890" not in asiento and "****7890" in asiento  # el asiento, enmascarado


async def test_dos_prestamos_simultaneos_de_la_misma_unidad_solo_uno_gana(tenant_ctx) -> None:
    slug, conn, s, svc = await _armar(tenant_ctx)
    unidad = await _unidad(conn, s)
    otra = await asyncpg.connect(dsn=DSN)
    try:
        resultados = await asyncio.gather(
            _prestar(svc, unidad),
            _prestar(FisicoService(otra, slug), unidad),
            return_exceptions=True,
        )
    finally:
        await otra.close()
    ganadores = [r for r in resultados if not isinstance(r, BaseException)]
    perdedores = [r for r in resultados if isinstance(r, HTTPException)]
    assert len(ganadores) == 1 and len(perdedores) == 1, resultados
    assert perdedores[0].detail["error"] == "unidad_ya_prestada"
    assert await conn.fetchval(f"SELECT count(*) FROM {s}.prestamo WHERE estado = 'prestado'") == 1


async def test_padre_e_hijo_simultaneos_solo_uno_gana(tenant_ctx) -> None:
    """Cada uno mira una unidad distinta: el `UNIQUE(unidad_id)` no los separa. Los
    separa el bloqueo por RAÍZ del árbol."""
    slug, conn, s, svc = await _armar(tenant_ctx)
    caja = await _unidad(conn, s)
    carpeta = await _unidad(conn, s, caja)
    otra = await asyncpg.connect(dsn=DSN)
    try:
        resultados = await asyncio.gather(
            _prestar(svc, caja),
            _prestar(FisicoService(otra, slug), carpeta),
            return_exceptions=True,
        )
    finally:
        await otra.close()
    assert sum(1 for r in resultados if not isinstance(r, BaseException)) == 1, resultados
    assert sum(1 for r in resultados if isinstance(r, HTTPException)) == 1, resultados


@pytest.mark.parametrize("primero", ["padre", "hijo"])
async def test_el_arbol_se_excluye_en_los_dos_sentidos(tenant_ctx, primero: str) -> None:
    slug, conn, s, svc = await _armar(tenant_ctx)
    caja = await _unidad(conn, s)
    carpeta = await _unidad(conn, s, caja)
    a, b = (caja, carpeta) if primero == "padre" else (carpeta, caja)
    await _prestar(svc, a)
    assert await _error(_prestar(svc, b)) == "unidad_ya_prestada"
    # una unidad ajena al árbol no se ve afectada
    ajena = await _unidad(conn, s)
    await _prestar(svc, ajena)


async def test_devolver_libera_el_arbol(tenant_ctx) -> None:
    slug, conn, s, svc = await _armar(tenant_ctx)
    unidad = await _unidad(conn, s)
    p = await _prestar(svc, unidad)
    d = await svc.devolver_prestamo(p.id, str(ACTOR), actor=ACTOR)
    assert d.estado == "devuelto" and d.devuelto_recibido_por == ACTOR
    await _prestar(svc, unidad)  # ahora sí


# -- clearance de quien recibe -------------------------------------------


async def test_el_prestatario_necesita_clearance_sobre_el_contenido(tenant_ctx) -> None:
    slug, conn, s, svc = await _armar(tenant_ctx)
    unidad = await _unidad(conn, s)
    await _expediente(conn, s, unidad, nivel=2)
    # quien presta es root (resuelve 3) para que el rechazo sea del PRESTATARIO
    root = await crear_usuario(conn, slug, root=True)
    bajo = await crear_usuario(conn, slug, nivel=1)
    suficiente = await crear_usuario(conn, slug, nivel=2)

    async def _presta(prestatario: dict):
        return await svc.create_prestamo(
            unidad, _cuerpo(prestatario), str(root), actor=root
        )

    assert await _error(_presta({"tipo": "interno", "user_id": str(bajo)})) == "prestatario_sin_acceso"
    assert await _error(_presta(EXTERNO)) == "prestatario_sin_acceso"  # externo: solo nivel 1
    p = await _presta({"tipo": "interno", "user_id": str(suficiente)})  # control positivo
    assert p.prestatario_user_id == suficiente


async def test_quien_presta_tambien_necesita_clearance(tenant_ctx) -> None:
    slug, conn, s, svc = await _armar(tenant_ctx)
    unidad = await _unidad(conn, s)
    await _expediente(conn, s, unidad, nivel=3)
    bajo = await crear_usuario(conn, slug, nivel=1)
    assert (
        await _error(svc.create_prestamo(unidad, _cuerpo(), str(bajo), actor=bajo))
        == "clearance_insuficiente"
    )


async def test_el_clearance_de_quien_presta_es_una_condicion_propia_del_where(tenant_ctx) -> None:
    """El prestatario SÍ alcanza el contenido; quien presta no. Con el gate del prestatario como
    único freno (el caso de arriba, externo), quitar el del prestador pasaba inadvertido: el
    diagnóstico elige el mensaje, no decide. Mutación: `gate_presta = TRUE` en
    `insert_prestamo` -> este test cae (el préstamo se crearía)."""
    slug, conn, s, svc = await _armar(tenant_ctx)
    unidad = await _unidad(conn, s)
    await _expediente(conn, s, unidad, nivel=3)
    presta = await crear_usuario(conn, slug, nivel=1)
    recibe = await crear_usuario(conn, slug, nivel=3)
    cuerpo = _cuerpo({"tipo": "interno", "user_id": str(recibe)})
    assert (
        await _error(svc.create_prestamo(unidad, cuerpo, str(presta), actor=presta))
        == "clearance_insuficiente"
    )
    assert await conn.fetchval(f"SELECT count(*) FROM {s}.prestamo") == 0


async def test_un_sub_de_keycloak_o_un_inactivo_no_es_prestatario(tenant_ctx) -> None:
    slug, conn, s, svc = await _armar(tenant_ctx)
    unidad = await _unidad(conn, s)
    inactivo = await crear_usuario(conn, slug, nivel=1, activo=False)
    for user_id in (uuid.uuid4(), inactivo):  # un UUID cualquiera, como un `sub`; y un inactivo
        assert (
            await _error(_prestar(svc, unidad, _cuerpo({"tipo": "interno", "user_id": str(user_id)})))
            == "prestatario_inexistente"
        )


async def test_el_contenido_de_un_hijo_cuenta_para_el_padre(tenant_ctx) -> None:
    slug, conn, s, svc = await _armar(tenant_ctx)
    caja = await _unidad(conn, s)
    carpeta = await _unidad(conn, s, caja)
    await _expediente(conn, s, carpeta, nivel=2)
    # la caja no tiene expedientes propios, pero se lleva la carpeta que sí. Presta un
    # root (alcanza el nivel 2) para que el rechazo sea del PRESTATARIO externo.
    root = await crear_usuario(conn, slug, root=True)
    assert (
        await _error(svc.create_prestamo(caja, _cuerpo(), str(root), actor=root))
        == "prestatario_sin_acceso"
    )


# -- simetría: lo que sale no se mueve, lo que se mueve no sale ---------


async def test_no_se_vincula_a_una_unidad_prestada(tenant_ctx) -> None:
    slug, conn, s, svc = await _armar(tenant_ctx)
    unidad = await _unidad(conn, s)
    exp = await _expediente(conn, s)
    await svc.link_expediente(unidad, ExpedienteUnidadLink(expediente_id=exp), actor=ACTOR)  # control
    otro = await _expediente(conn, s)
    await _prestar(svc, unidad)
    with pytest.raises(HTTPException) as exc:
        await svc.link_expediente(unidad, ExpedienteUnidadLink(expediente_id=otro), actor=ACTOR)
    assert exc.value.detail["error"] == "unidad_prestada"


async def _transferencia(conn, s: str, exp: uuid.UUID):
    return await conn.fetchval(
        f"INSERT INTO {s}.transferencias (tipo, expediente_id, creada_por, objeto) "
        "VALUES ('primaria', $1, 'itest', 'Transferencia de prueba C11') RETURNING id",
        exp,
    )


async def test_no_se_transfiere_lo_que_esta_prestado(tenant_ctx) -> None:
    slug, conn, s, svc = await _armar(tenant_ctx)
    unidad = await _unidad(conn, s)
    exp = await _expediente(conn, s, unidad)
    p = await _prestar(svc, unidad)
    with pytest.raises(asyncpg.CheckViolationError) as exc:
        await _transferencia(conn, s, exp)
    assert exc.value.constraint_name == "ck_transferencia_unidad_prestada"
    # control positivo: devuelta la unidad, la transferencia se crea
    await svc.devolver_prestamo(p.id, str(ACTOR), actor=ACTOR)
    assert await _transferencia(conn, s, exp) is not None


async def test_no_se_presta_lo_que_esta_saliendo_por_transferencia(tenant_ctx) -> None:
    slug, conn, s, svc = await _armar(tenant_ctx)
    unidad = await _unidad(conn, s)
    exp = await _expediente(conn, s, unidad)
    await _transferencia(conn, s, exp)
    assert await _error(_prestar(svc, unidad)) == "unidad_en_transferencia"


async def test_no_se_presta_lo_que_esta_en_un_lote_de_eliminacion(tenant_ctx) -> None:
    slug, conn, s, svc = await _armar(tenant_ctx)
    unidad = await _unidad(conn, s)
    exp = await _expediente(conn, s, unidad)
    lote = await conn.fetchval(
        f"INSERT INTO {s}.eliminacion_lotes (codigo, creado_por) VALUES ('L-C11', $1) RETURNING id",
        ACTOR,
    )
    await conn.execute(
        f"INSERT INTO {s}.eliminacion_lote_items (lote_id, expediente_id, expediente_code, "
        "disposition_code, fecha_vencimiento, nivel_seguridad) "
        "VALUES ($1, $2, 'EXP', 'E', now()::date, 1)",
        lote,
        exp,
    )
    assert await _error(_prestar(svc, unidad)) == "unidad_en_lote_eliminacion"
    # un lote ANULADO no bloquea (control: el predicado mira el estado)
    await conn.execute(
        f"UPDATE {s}.eliminacion_lotes SET estado = 'anulado', anulado_at = now(), "
        "anulado_por = $2, anulado_motivo = 'prueba de control C11' WHERE id = $1",
        lote,
        ACTOR,
    )
    await _prestar(svc, unidad)


async def test_no_se_borra_un_expediente_cuya_unidad_esta_prestada(tenant_ctx) -> None:
    slug, conn, s, svc = await _armar(tenant_ctx)
    unidad = await _unidad(conn, s)
    exp = await _expediente(conn, s, unidad)
    await _prestar(svc, unidad)
    with pytest.raises(asyncpg.CheckViolationError) as exc:
        await conn.execute(f"DELETE FROM {s}.expedientes WHERE id = $1", exp)
    assert exc.value.constraint_name == "ck_expediente_unidad_prestada"


# -- la traza de custodia es append-only ---------------------------------


async def test_la_traza_de_custodia_no_se_borra(tenant_ctx) -> None:
    slug, conn, s, svc = await _armar(tenant_ctx)
    unidad = await _unidad(conn, s)
    p = await _prestar(svc, unidad)
    with pytest.raises(asyncpg.ForeignKeyViolationError):  # RESTRICT, ya no CASCADE
        await conn.execute(f"DELETE FROM {s}.unidad_conservacion WHERE id = $1", unidad)
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(f"DELETE FROM {s}.prestamo WHERE id = $1", p.id)
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(f"TRUNCATE {s}.prestamo")


async def test_el_unico_update_admitido_es_devolver(tenant_ctx) -> None:
    slug, conn, s, svc = await _armar(tenant_ctx)
    unidad = await _unidad(conn, s)
    p = await _prestar(svc, unidad)
    for cambio in (
        "tercero_nombre = 'Otra Persona'",
        "unidad_id = gen_random_uuid()",
        f"prestado_por = '{uuid.uuid4()}'",
        "fecha_devolucion_esperada = fecha_devolucion_esperada + 30",
    ):
        with pytest.raises((asyncpg.CheckViolationError, asyncpg.ForeignKeyViolationError)):
            await conn.execute(f"UPDATE {s}.prestamo SET {cambio} WHERE id = $1", p.id)
    # control positivo: la devolución, por el camino real, sí pasa
    d = await svc.devolver_prestamo(p.id, str(ACTOR), actor=ACTOR)
    assert d.estado == "devuelto"
    # y un préstamo devuelto no se reabre
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            f"UPDATE {s}.prestamo SET estado = 'prestado', fecha_devolucion_real = NULL, "
            "devuelto_recibido_por = NULL WHERE id = $1",
            p.id,
        )


async def test_la_forma_del_prestatario_la_impone_la_base(tenant_ctx) -> None:
    """El CHECK no depende de Pydantic: un INSERT directo con un 'interno' sin user_id
    o un 'externo' sin documento no entra."""
    slug, conn, s, svc = await _armar(tenant_ctx)
    unidad = await _unidad(conn, s)
    base = (
        f"INSERT INTO {s}.prestamo (unidad_id, prestatario_tipo, prestatario_user_id, "
        "tercero_nombre, tercero_documento_tipo, tercero_documento_numero, prestado_por, "
        "fecha_devolucion_esperada) VALUES ($1, $2, $3, $4, $5, $6, $7, current_date)"
    )
    for args in (
        (unidad, "interno", None, None, None, None, ACTOR),
        (unidad, "externo", None, "Ana Pérez", None, None, ACTOR),
        (unidad, "externo", uuid.uuid4(), "Ana Pérez", "CC", "1234567", ACTOR),
    ):
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(base, *args)
    await conn.execute(base, unidad, "externo", None, "Ana Pérez", "CC", "1234567", ACTOR)  # control
