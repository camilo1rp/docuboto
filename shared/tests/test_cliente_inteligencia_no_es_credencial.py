# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La cuenta de servicio de inteligencia no es credencial de la API (ADR-045 §4, ADR-048 §3).

`init_tenant.py` registra por entidad una fila en `public.auth_service_accounts`
con la audiencia del proveedor y sin secreto: el token lo firma el propio
proceso y una fila sin `secret_hash` no sirve en `/oauth/token`. Un token con
esa audiencia y `principal_type=service` no entra al API de Docuboto (lo prueba
`services/docuboto/tests/auth/test_identidad.py`).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from tests._chain import CHAIN, create_table

RAIZ = Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "scripts" / "init_tenant.py"
TENANT = CHAIN / "tenant" / "800_intelligence_state.sql"
APP_ROLE = CHAIN / "global" / "020_app_role.sql"
GLOBAL_IDENTIDAD = CHAIN / "global" / "070_identity.sql"


def _modulo() -> Any:
    pytest.importorskip("asyncpg")
    spec = importlib.util.spec_from_file_location("init_tenant_inteligencia", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Conn:
    def __init__(self, *, existe: bool = False, seudonimo: bool = False) -> None:
        self.existe = existe
        self.seudonimo = seudonimo
        self.sql: list[str] = []
        self.args: list[tuple[Any, ...]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.sql.append(sql)
        self.args.append(args)
        return "OK"

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.sql.append(sql)
        self.args.append(args)
        return None if self.existe else args[0]

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.sql.append(sql)
        self.args.append(args)
        return {"purpose": "pseudonym"} if self.seudonimo else None


@pytest.mark.asyncio
async def test_la_cuenta_lleva_la_audiencia_del_proveedor_y_ningun_secreto() -> None:
    mod = _modulo()
    conn = _Conn()
    linea = await mod.aprovisionar_cuenta_inteligencia(
        conn, "demo", key=b"\x11" * 32, issuer="https://api.demo.test"
    )
    [(sql, args)] = [
        (q, a) for q, a in zip(conn.sql, conn.args) if "auth_service_accounts" in q
    ]
    assert "secret_hash" not in sql, "la cuenta de inteligencia no tiene secreto"
    assert "ON CONFLICT (client_id) DO NOTHING" in sql
    assert args == ("intelligence-demo", "demo", "pro" "boto-api")
    assert linea == (
        "[intelligence] cuenta de servicio — issuer=https://api.demo.test "
        "client_id=intelligence-demo"
    )


@pytest.mark.asyncio
async def test_reejecutar_no_duplica_y_lo_dice() -> None:
    mod = _modulo()
    conn = _Conn(existe=True, seudonimo=True)
    linea = await mod.aprovisionar_cuenta_inteligencia(
        conn, "demo", key=b"\x11" * 32, issuer="https://api.demo.test"
    )
    assert "ya existía" in linea
    assert not any("INSERT INTO intelligence_credentials" in q for q in conn.sql)


@pytest.mark.asyncio
async def test_si_falta_el_seudonimo_lo_sella() -> None:
    kek = pytest.importorskip("orpycamcp_common.kek")
    mod = _modulo()
    conn = _Conn(existe=True, seudonimo=False)
    await mod.aprovisionar_cuenta_inteligencia(
        conn, "demo", key=b"\x33" * 32, issuer="https://api.demo.test"
    )
    inserts = [
        args
        for sql, args in zip(conn.sql, conn.args)
        if "INSERT INTO intelligence_credentials" in sql
    ]
    assert len(inserts) == 1 and inserts[0][0] == "pseudonym"
    claro = kek.unseal(
        inserts[0][2],
        nonce=inserts[0][3],
        key=b"\x33" * 32,
        aad=mod.aad_credencial("demo", "pseudonym", 1),
    )
    assert len(claro) == 32
    # El search_path vuelve a public aunque la ranura se haya sellado.
    assert conn.sql[-1] == "SET search_path = public"


def test_la_tabla_de_cuentas_admite_cuentas_sin_secreto() -> None:
    sql = GLOBAL_IDENTIDAD.read_text(encoding="utf-8")
    assert "secret_hash text," in sql  # nullable: la de inteligencia no tiene
    assert "REFERENCES public.tenants(slug) ON DELETE CASCADE" in sql


def test_las_migraciones_dejan_local_y_el_libro_solo_acepta_inserciones() -> None:
    # El texto es el que imprime pg_dump (ADR-053).
    global_sql = create_table("tenants", "global")
    tenant_sql = TENANT.read_text(encoding="utf-8")
    assert "intelligence_provider text DEFAULT 'local'::text NOT NULL" in global_sql
    assert "CHECK ((intelligence_provider = ANY (ARRAY['local'::text, 'ppi'::text])))" in global_sql
    assert "intelligence_levels smallint[] DEFAULT '{1}'::smallint[] NOT NULL" in global_sql
    assert (
        "CHECK ((intelligence_levels <@ ARRAY[(1)::smallint, (2)::smallint, (3)::smallint]))"
        in global_sql
    )
    assert "intelligence_send_metadata boolean DEFAULT false NOT NULL" in global_sql
    assert "BEFORE DELETE OR UPDATE ON intelligence_send_log FOR EACH ROW" in tenant_sql
    assert "BEFORE TRUNCATE ON intelligence_send_log FOR EACH STATEMENT" in tenant_sql
    # El rol de aplicación no puede UPDATE/DELETE (se revoca) ni TRUNCATE (nunca se concede:
    # los privilegios por defecto son solo SELECT, INSERT, UPDATE, DELETE).
    assert "REVOKE UPDATE, DELETE ON TABLE intelligence_send_log FROM orpycamcp_app;" in tenant_sql
    app_role = APP_ROLE.read_text(encoding="utf-8")
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO orpycamcp_app" in app_role
    assert "TRUNCATE" not in app_role and "GRANT ALL" not in app_role
    assert "GRANT" not in tenant_sql
    assert "CHECK ((purpose = ANY (ARRAY['hmac_return'::text, 'pseudonym'::text])))" in tenant_sql
    assert "service_client" not in tenant_sql
    assert "send_suspended" in tenant_sql
    assert "backfill_cursor" in tenant_sql
    assert "secret text" not in tenant_sql
