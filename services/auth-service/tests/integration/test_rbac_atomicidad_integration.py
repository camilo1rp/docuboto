# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Atomicidad del asiento al RETIRAR el segundo factor (AREA-04-04) y el gate de
clearance de la membresía (AREA-01-N3), contra Postgres REAL.

Un mock de `asyncpg` no puede decir si la baja y el asiento comparten
transacción, ni si el `WHERE` del `INSERT` filtra de verdad: ambas cosas son
propiedades de la base. Se verifica por MUTACIÓN: la misma operación con el
asiento caído (o con el nivel subido) tiene que dar el resultado opuesto.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.repositories.clearance_repository import ClearanceRepository
from app.repositories.rbac_repository import RBACRepository
from app.services import rbac_service, totp_service
from app.services.totp_service import TotpService
from tests.integration.conftest import RUN_INTEGRATION

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not RUN_INTEGRATION, reason="requiere Postgres real (RUN_INTEGRATION=1)"),
]


async def _user(conn: asyncpg.Connection, username: str) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO auth_users (username) VALUES ($1) RETURNING id", username
    )


async def _totp_activo(conn: asyncpg.Connection, user_id: uuid.UUID) -> uuid.UUID:
    totp_id = await conn.fetchval(
        "INSERT INTO auth_user_totp (user_id, secret_cifrado, secret_nonce, estado) "
        "VALUES ($1, '\\x00', '\\x00', 'activo') RETURNING id",
        user_id,
    )
    await conn.execute(
        "INSERT INTO auth_user_totp_recovery (totp_id, user_id, code_hash, code_salt) "
        "VALUES ($1, $2, '\\x01', '\\x02')",
        totp_id,
        user_id,
    )
    return totp_id


async def _estado_y_codigos(conn: asyncpg.Connection, totp_id: uuid.UUID) -> tuple[str, int]:
    estado = await conn.fetchval("SELECT estado FROM auth_user_totp WHERE id = $1", totp_id)
    codigos = await conn.fetchval(
        "SELECT count(*) FROM auth_user_totp_recovery WHERE totp_id = $1", totp_id
    )
    return estado, codigos


async def test_restablecer_por_admin_con_el_asiento_caido_no_quita_el_factor(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    victima = await _user(conn, "VICTIMA")
    admin = await _user(conn, "ADMIN")
    totp_id = await _totp_activo(conn, victima)

    async def _falla(*_a: object, **_k: object) -> None:
        raise RuntimeError("audit caído")

    monkeypatch.setattr(totp_service._audit, "append", _falla)

    with pytest.raises(RuntimeError):
        await TotpService(conn).revoke_as_admin(
            target_user_id=victima, admin_user_id=admin, tenant_slug=slug, motivo="perdió el equipo"
        )

    assert await _estado_y_codigos(conn, totp_id) == ("activo", 1)


async def test_desenrolar_sin_segundo_factor_activo_con_el_asiento_caido_revierte(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`revoke_self` sobre una credencial pendiente (sin step-up) también es una baja."""
    slug, conn = tenant_ctx
    user = await _user(conn, "PENDIENTE")
    totp_id = await _totp_activo(conn, user)
    await conn.execute("UPDATE auth_user_totp SET estado = 'pendiente_activacion' WHERE id = $1", totp_id)

    async def _falla(*_a: object, **_k: object) -> None:
        raise RuntimeError("audit caído")

    monkeypatch.setattr(totp_service._audit, "append", _falla)

    with pytest.raises(RuntimeError):
        await TotpService(conn).revoke_self(user, slug, None)

    assert await _estado_y_codigos(conn, totp_id) == ("pendiente_activacion", 1)


async def test_control_positivo_con_asiento_vivo_la_baja_se_confirma(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    victima = await _user(conn, "VICTIMA2")
    admin = await _user(conn, "ADMIN2")
    totp_id = await _totp_activo(conn, victima)
    llamadas: list[str] = []

    async def _ok(*_a: object, **k: object) -> str:
        llamadas.append(str(k["action"]))
        return "h"

    monkeypatch.setattr(totp_service._audit, "append", _ok)

    await TotpService(conn).revoke_as_admin(
        target_user_id=victima, admin_user_id=admin, tenant_slug=slug, motivo="perdió el equipo"
    )

    assert await _estado_y_codigos(conn, totp_id) == ("revocado", 0)
    assert llamadas == ["totp.restablecido_por_admin"]


# --- N3: no-write-up en la membresía -----------------------------------------
async def _grupo(conn: asyncpg.Connection, nombre: str, techo: int | None) -> uuid.UUID:
    gid = await conn.fetchval("INSERT INTO auth_groups (name) VALUES ($1) RETURNING id", nombre)
    if techo is not None:
        await conn.execute(
            "INSERT INTO role_clearance (group_id, max_level) VALUES ($1, $2)", gid, techo
        )
    return gid


async def test_un_admin_no_root_no_entra_en_un_grupo_de_techo_mayor(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    _, conn = tenant_ctx
    admin = await _user(conn, "ADMIN_N1")
    g_alto = await _grupo(conn, "ALTO", 3)
    repo, clearance = RBACRepository(conn), ClearanceRepository(conn)
    assert await clearance.caller_max_level(admin) == 1

    outcome = await repo.add_membership_guarded(
        user_id=admin, group_id=g_alto, caller_is_root=False, caller_level=1
    )

    assert outcome == "denied"
    assert await conn.fetchval("SELECT count(*) FROM auth_memberships WHERE user_id = $1", admin) == 0
    assert await clearance.caller_max_level(admin) == 1


async def test_el_root_si_entra_y_un_grupo_sin_techo_cuenta_como_publico(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """Controles positivos: ROOT entra al grupo alto; el admin N1 entra a uno sin
    `role_clearance` (cuenta como 1) y a uno de su mismo techo."""
    _, conn = tenant_ctx
    admin = await _user(conn, "ADMIN_N1B")
    g_alto = await _grupo(conn, "ALTO2", 3)
    g_sin = await _grupo(conn, "SIN_TECHO", None)
    g_igual = await _grupo(conn, "IGUAL", 1)
    repo = RBACRepository(conn)

    assert await repo.add_membership_guarded(
        user_id=admin, group_id=g_alto, caller_is_root=True, caller_level=3
    ) == "added"
    assert await repo.add_membership_guarded(
        user_id=admin, group_id=g_sin, caller_is_root=False, caller_level=1
    ) == "added"
    assert await repo.add_membership_guarded(
        user_id=admin, group_id=g_igual, caller_is_root=False, caller_level=1
    ) == "added"
    # ya era miembro: no es un rechazo
    assert await repo.add_membership_guarded(
        user_id=admin, group_id=g_sin, caller_is_root=False, caller_level=1
    ) == "exists"


async def test_grupo_inexistente_da_violacion_de_fk(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    _, conn = tenant_ctx
    admin = await _user(conn, "ADMIN_FK")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await RBACRepository(conn).add_membership_guarded(
            user_id=admin, group_id=uuid.uuid4(), caller_is_root=False, caller_level=1
        )


# --- 01-15: borrado de grupo ---------------------------------------------------
async def test_delete_group_solo_si_no_tiene_miembros_ni_urd(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    _, conn = tenant_ctx
    u = await _user(conn, "MIEMBRO")
    con_miembro = await _grupo(conn, "CON_MIEMBRO", None)
    vacio = await _grupo(conn, "VACIO", None)
    await RBACRepository(conn).add_membership(user_id=u, group_id=con_miembro)
    repo = RBACRepository(conn)

    assert await repo.delete_group_if_unused(con_miembro) == "in_use"
    assert await repo.delete_group_if_unused(vacio) == "deleted"
    assert await repo.delete_group_if_unused(vacio) == "missing"
    assert await repo.get_group(con_miembro) is not None


async def test_delete_group_con_urd_pero_sin_membresia_da_in_use(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """La rama URD del NOT EXISTS por sí sola: sin ella el FK de `rol_id` daría 500."""
    _, conn = tenant_ctx
    u = await _user(conn, "CON_URD")
    g = await _grupo(conn, "SOLO_URD", None)
    await conn.execute(
        "INSERT INTO urd_usuaroldep (user_id, depe_id, rol_id) VALUES ($1, 100, $2)", u, g
    )

    assert await RBACRepository(conn).delete_group_if_unused(g) == "in_use"
    assert await RBACRepository(conn).get_group(g) is not None


# --- 04-04: las dos bajas con step-up también revierten si el asiento cae -------
async def test_desenrolar_con_codigo_totp_y_el_asiento_caido_revierte(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    slug, conn = tenant_ctx
    user = await _user(conn, "STEPUP_TOTP")
    totp_id = await _totp_activo(conn, user)

    monkeypatch.setattr(TotpService, "_decrypt", lambda *_a, **_k: b"secreto")
    monkeypatch.setattr(
        totp_service,
        "classify_totp_attempt",
        lambda *_a, **_k: SimpleNamespace(outcome="valid", consumed_timestep=7),
    )

    async def _falla(*_a: object, **_k: object) -> None:
        raise RuntimeError("audit caído")

    monkeypatch.setattr(totp_service._audit, "append", _falla)

    with pytest.raises(RuntimeError):
        await TotpService(conn).revoke_self(user, slug, "123456")

    assert await _estado_y_codigos(conn, totp_id) == ("activo", 1)
    # el timestep consumido por el step-up también se revierte (no hay sobreconsumo)
    assert await conn.fetchval("SELECT last_timestep FROM auth_user_totp WHERE id = $1", totp_id) != 7


async def test_desenrolar_con_codigo_de_recuperacion_y_el_asiento_caido_revierte(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    user = await _user(conn, "STEPUP_REC")
    totp_id = await _totp_activo(conn, user)

    async def _consume(self: object, uid: uuid.UUID, code: str | None) -> bool:
        # consumo REAL en la misma conexión/transacción que la baja
        await conn.execute(
            "UPDATE auth_user_totp_recovery SET used_at = now() WHERE user_id = $1", uid
        )
        return True

    monkeypatch.setattr(TotpService, "_consume_recovery_code", _consume)

    async def _falla(*_a: object, **_k: object) -> None:
        raise RuntimeError("audit caído")

    monkeypatch.setattr(totp_service._audit, "append", _falla)

    with pytest.raises(RuntimeError):
        await TotpService(conn).revoke_self(user, slug, None, "ABCD-EFGH")

    assert await _estado_y_codigos(conn, totp_id) == ("activo", 1)
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM auth_user_totp_recovery WHERE user_id = $1 AND used_at IS NULL",
            user,
        )
        == 1
    )


# --- el asiento de RBAC viaja en la MISMA transacción que el cambio ------------
async def _audit_caido(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _falla(*_a: object, **_k: object) -> None:
        raise RuntimeError("audit caído")

    monkeypatch.setattr(rbac_service._audit, "append", _falla)


async def test_borrar_grupo_con_el_asiento_caido_no_lo_borra(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    g = await _grupo(conn, "A_BORRAR", None)
    await _audit_caido(monkeypatch)

    with pytest.raises(RuntimeError):
        await rbac_service.RBACService(conn).delete_group(
            group_id=g, tenant_slug=slug, actor="actor"
        )

    assert await RBACRepository(conn).get_group(g) is not None


async def test_actualizar_grupo_con_el_asiento_caido_no_lo_cambia(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    g = await _grupo(conn, "NOMBRE_VIEJO", None)
    await _audit_caido(monkeypatch)

    with pytest.raises(RuntimeError):
        await rbac_service.RBACService(conn).update_group(
            group_id=g, fields={"name": "NOMBRE_NUEVO"}, tenant_slug=slug, actor="actor"
        )

    assert await conn.fetchval("SELECT name FROM auth_groups WHERE id = $1", g) == "NOMBRE_VIEJO"


async def test_asientos_de_grupo_conservan_el_valor_anterior(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    g = await _grupo(conn, "ARCHIVO", None)
    asientos: list[dict] = []

    async def _captura(*_a: object, **k: object) -> None:
        asientos.append(dict(k))

    monkeypatch.setattr(rbac_service._audit, "append", _captura)
    svc = rbac_service.RBACService(conn)
    await svc.update_group(
        group_id=g, fields={"name": "VENTANILLA"}, tenant_slug=slug, actor="actor"
    )
    assert await svc.delete_group(group_id=g, tenant_slug=slug, actor="actor") == "deleted"

    assert asientos[0]["payload"]["antes"] == {"name": "ARCHIVO"}
    assert asientos[0]["payload"]["despues"] == {"name": "VENTANILLA"}
    assert asientos[1]["payload"]["name"] == "VENTANILLA"


async def test_agregar_miembro_con_el_asiento_caido_no_deja_la_membresia(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    u = await _user(conn, "SIN_TRAZA_M")
    g = await _grupo(conn, "G_M", None)
    await _audit_caido(monkeypatch)

    with pytest.raises(RuntimeError):
        await rbac_service.RBACService(conn).add_member(
            group_id=g,
            user_id=u,
            tenant_slug=slug,
            actor="actor",
            caller_is_root=True,
            caller_level=3,
        )

    assert await conn.fetchval("SELECT count(*) FROM auth_memberships WHERE user_id = $1", u) == 0


async def test_servicio_add_member_denegado_no_inserta_ni_asienta(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cableado servicio -> repositorio contra BD real: `denied` es 403 sin filas ni asiento."""
    from fastapi import HTTPException

    slug, conn = tenant_ctx
    u = await _user(conn, "ADMIN_SVC")
    g = await _grupo(conn, "G_ALTO_SVC", 3)
    asientos: list[str] = []

    async def _ok(*_a: object, **k: object) -> str:
        asientos.append(str(k["action"]))
        return "h"

    monkeypatch.setattr(rbac_service._audit, "append", _ok)

    with pytest.raises(HTTPException) as exc:
        await rbac_service.RBACService(conn).add_member(
            group_id=g,
            user_id=u,
            tenant_slug=slug,
            actor="actor",
            caller_is_root=False,
            caller_level=1,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "no_write_up"
    assert asientos == []
    assert await conn.fetchval("SELECT count(*) FROM auth_memberships WHERE user_id = $1", u) == 0
