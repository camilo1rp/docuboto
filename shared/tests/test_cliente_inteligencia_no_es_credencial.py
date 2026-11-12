# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El cliente de servicio de inteligencia no es credencial de la API (ADR-045 §4).

`init_tenant.py` crea un cliente confidencial, solo `client_credentials`, por
entidad. Su token se presenta al proveedor. Meterlo en `ALLOWED_AZP` lo
volvería credencial de toda la API de Docuboto. El secreto se guarda cifrado
con un AAD de entidad y finalidad; este paso no lo imprime.
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
CONFIG = (RAIZ / "services/auth-service/auth/core/config.py").read_text(encoding="utf-8")


def _modulo() -> Any:
    pytest.importorskip("asyncpg")
    spec = importlib.util.spec_from_file_location("init_tenant_inteligencia", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _admitidos() -> set[str]:
    import re

    m = re.search(r'^\s*allowed_azp:\s*str\s*=\s*"([^"]*)"', CONFIG, re.M)
    assert m, "no se encontró el default de allowed_azp"
    return {c.strip() for c in m.group(1).split(",") if c.strip()}


_MISMO = object()


class _Conn:
    def __init__(
        self, fila: dict[str, Any] | None, *, seudonimo: Any = _MISMO
    ) -> None:
        self.fila = fila
        self.seudonimo = seudonimo
        self.sql: list[str] = []
        self.args: list[tuple[Any, ...]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.sql.append(sql)
        self.args.append(args)
        return "OK"

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.sql.append(sql)
        self.args.append(args)
        if args and args[0] == "pseudonym" and self.seudonimo is not _MISMO:
            return self.seudonimo
        return self.fila


class _Http:
    """Responde el alta de un cliente y guarda el secreto que se le pidió crear."""

    def __init__(
        self, *, existente: dict[str, Any] | None = None, secreto: str = "secreto-guardado"
    ) -> None:
        self.existente = existente
        self.secreto = secreto
        self.llamadas: list[tuple[str, str]] = []
        self.cuerpo_alta: dict[str, Any] | None = None
        self.cuerpo_mapper: dict[str, Any] | None = None
        self.cuerpo_alcance: dict[str, Any] | None = None
        self.mappers: list[dict[str, Any]] = []
        self.alcances: list[dict[str, Any]] = []
        self.alcances_asignados: list[dict[str, Any]] = []

    async def request(self, metodo: str, url: str, **kwargs: Any) -> Any:
        self.llamadas.append((metodo, url))
        if url.endswith("/protocol/openid-connect/token"):
            return _Resp(200, {"access_token": "admin-token"})
        if url.endswith("/clients") and metodo == "GET":
            return _Resp(200, [] if self.existente is None else [self.existente])
        if url.endswith("/clients") and metodo == "POST":
            self.cuerpo_alta = kwargs.get("json")
            self.existente = {"id": "uuid-1", "clientId": kwargs["json"]["clientId"]}
            return _Resp(201, None)
        if url.endswith("/client-scopes") and metodo == "GET":
            return _Resp(200, list(self.alcances))
        if url.endswith("/client-scopes") and metodo == "POST":
            self.cuerpo_alcance = kwargs.get("json")
            fila = dict(kwargs.get("json") or {})
            fila.setdefault("id", "scope-1")
            self.alcances.append(fila)
            return _Resp(201, None)
        if url.endswith("/default-client-scopes") and metodo == "GET":
            return _Resp(200, list(self.alcances_asignados))
        if url.endswith("/protocol-mappers/models") and metodo == "GET":
            return _Resp(200, list(self.mappers))
        if url.endswith("/protocol-mappers/models") and metodo == "POST":
            self.cuerpo_mapper = kwargs.get("json")
            fila = dict(kwargs.get("json") or {})
            fila.setdefault("id", "mapper-1")
            self.mappers.append(fila)
            return _Resp(201, None)
        if "/protocol-mappers/models/" in url and metodo == "PUT":
            self.cuerpo_mapper = kwargs.get("json")
            return _Resp(204, None)
        if url.endswith("/client-secret") and metodo == "GET":
            return _Resp(200, {"type": "secret", "value": self.secreto})
        if metodo == "PUT":
            return _Resp(204, None)
        raise AssertionError(f"llamada no prevista: {metodo} {url}")


class _Resp:
    def __init__(self, status: int, payload: Any) -> None:
        self.status_code = status
        self._payload = payload
        self.content = b"" if payload is None else b"1"

    def json(self) -> Any:
        return self._payload


def test_el_cliente_no_entra_en_allowed_azp() -> None:
    mod = _modulo()
    admitidos = _admitidos()
    assert "intelligence-demo" not in admitidos
    assert mod.id_cliente_inteligencia("demo") not in admitidos
    assert not any(c.startswith("intelligence-") for c in admitidos)
    assert "allowed_azp" not in SCRIPT.read_text(encoding="utf-8")


def test_el_cuerpo_es_solo_client_credentials() -> None:
    mod = _modulo()
    cuerpo = mod.cuerpo_cliente_servicio("intelligence-demo", "s3cret")
    assert cuerpo["publicClient"] is False
    assert cuerpo["standardFlowEnabled"] is False
    assert cuerpo["implicitFlowEnabled"] is False
    assert cuerpo["directAccessGrantsEnabled"] is False
    assert cuerpo["serviceAccountsEnabled"] is True
    assert cuerpo["clientId"] == "intelligence-demo"
    mapeadores = cuerpo["protocolMappers"]
    assert mapeadores[0]["protocolMapper"] == "oidc-audience-mapper"
    assert mapeadores[0]["config"]["included.custom.audience"] == "pro" "boto-api"
    assert mapeadores[0]["config"]["access.token.claim"] == "true"
    alcance = mod.cuerpo_alcance_audiencia()
    assert alcance["name"] == "audiencia-proveedor"
    assert alcance["protocolMappers"][0]["config"]["included.custom.audience"] == "pro" "boto-api"


def _cliente_existente() -> dict[str, Any]:
    return {"id": "uuid-1", "clientId": "intelligence-demo"}


@pytest.mark.asyncio
async def test_reejecutar_actualiza_keycloak_sin_rotar_el_secreto() -> None:
    mod = _modulo()
    conn = _Conn({"client_id": "intelligence-demo"})
    http = _Http(existente=_cliente_existente())
    linea = await mod.aprovisionar_cliente_inteligencia(
        conn,
        "demo",
        key=b"\x11" * 32,
        keycloak_url="http://keycloak:8080",
        realm="orpycamcp",
        admin_user="admin",
        admin_password="no-se-usa",
        issuer="http://localhost:19180/realms/orpycamcp",
        http=http,
    )
    assert any(url.endswith("/protocol/openid-connect/token") for _, url in http.llamadas)
    assert any("/protocol-mappers/models" in url for _, url in http.llamadas)
    assert not any(url.endswith("/client-secret") for _, url in http.llamadas)
    assert http.cuerpo_mapper is not None
    assert http.cuerpo_mapper["config"]["included.custom.audience"] == "pro" "boto-api"
    assert http.cuerpo_alcance is not None
    assert http.cuerpo_alcance["name"] == "audiencia-proveedor"
    assert any(
        metodo == "PUT" and "/default-client-scopes/" in url
        for metodo, url in http.llamadas
    )
    assert "issuer=http://localhost:19180/realms/orpycamcp" in linea
    assert "client_id=intelligence-demo" in linea
    assert not any("INSERT" in sql for sql in conn.sql)

    http.llamadas.clear()
    await mod.aprovisionar_cliente_inteligencia(
        conn,
        "demo",
        key=b"\x11" * 32,
        keycloak_url="http://keycloak:8080",
        realm="orpycamcp",
        admin_user="admin",
        admin_password="no-se-usa",
        issuer="http://localhost:19180/realms/orpycamcp",
        http=http,
    )
    assert any("/protocol-mappers/models" in url for metodo, url in http.llamadas)
    assert not any(url.endswith("/client-secret") for _, url in http.llamadas)
    assert not any("INSERT INTO intelligence_credentials" in sql for sql in conn.sql)


@pytest.mark.asyncio
async def test_reejecutar_cambia_audiencia_de_cliente_a_personalizada() -> None:
    mod = _modulo()
    conn = _Conn({"client_id": "intelligence-demo"})
    http = _Http(existente=_cliente_existente())
    http.mappers = [
        {
            "id": "mapper-viejo",
            "protocolMapper": "oidc-audience-mapper",
            "config": {"included.client.audience": "pro" "boto-api"},
        }
    ]
    await mod.aprovisionar_cliente_inteligencia(
        conn,
        "demo",
        key=b"\x11" * 32,
        keycloak_url="http://keycloak:8080",
        realm="orpycamcp",
        admin_user="admin",
        admin_password="no-se-usa",
        issuer="http://localhost:19180/realms/orpycamcp",
        http=http,
    )
    assert http.cuerpo_mapper is not None
    assert http.cuerpo_mapper["config"]["included.custom.audience"] == "pro" "boto-api"
    assert "included.client.audience" not in http.cuerpo_mapper["config"]
    assert any(
        metodo == "PUT" and url.endswith("/protocol-mappers/models/mapper-viejo")
        for metodo, url in http.llamadas
    )
    assert any(
        metodo == "PUT" and "/default-client-scopes/" in url
        for metodo, url in http.llamadas
    )


@pytest.mark.asyncio
async def test_si_falta_el_seudonimo_lo_sella_sin_rotar_el_secreto() -> None:
    kek = pytest.importorskip("orpycamcp_common.kek")
    mod = _modulo()
    conn = _Conn({"client_id": "intelligence-demo"}, seudonimo=None)
    http = _Http(existente=_cliente_existente())
    await mod.aprovisionar_cliente_inteligencia(
        conn,
        "demo",
        key=b"\x33" * 32,
        keycloak_url="http://keycloak:8080",
        realm="orpycamcp",
        admin_user="admin",
        admin_password="no-se-usa",
        issuer="http://localhost:19180/realms/orpycamcp",
        http=http,
    )
    assert not any(url.endswith("/client-secret") for _, url in http.llamadas)
    inserts = [
        args
        for sql, args in zip(conn.sql, conn.args)
        if "INSERT INTO intelligence_credentials" in sql
    ]
    assert len(inserts) == 1
    assert inserts[0][0] == "pseudonym"
    claro = kek.unseal(
        inserts[0][2],
        nonce=inserts[0][3],
        key=b"\x33" * 32,
        aad=mod.aad_credencial("demo", "pseudonym", 1),
    )
    assert len(claro) == 32


@pytest.mark.asyncio
async def test_el_secreto_se_guarda_cifrado_y_no_sale_en_la_linea() -> None:
    kek = pytest.importorskip("orpycamcp_common.kek")
    mod = _modulo()
    conn = _Conn(None)
    http = _Http(secreto="secreto-que-no-se-imprime")
    linea = await mod.aprovisionar_cliente_inteligencia(
        conn,
        "demo",
        key=b"\x22" * 32,
        keycloak_url="http://keycloak:8080",
        realm="orpycamcp",
        admin_user="admin",
        admin_password="admin-pass",
        issuer="http://localhost:19180/realms/orpycamcp",
        http=http,
    )
    assert "secreto-que-no-se-imprime" not in linea
    assert http.cuerpo_alta is not None
    assert http.cuerpo_alta["standardFlowEnabled"] is False
    assert http.cuerpo_alta["directAccessGrantsEnabled"] is False
    inserts = [
        args
        for sql, args in zip(conn.sql, conn.args)
        if "INSERT INTO intelligence_credentials" in sql
    ]
    assert [fila[0] for fila in inserts] == ["service_client", "pseudonym"]
    args = inserts[0]
    assert args[2] == "intelligence-demo"
    claro = kek.unseal(
        args[3],
        nonce=args[4],
        key=b"\x22" * 32,
        aad=mod.aad_credencial("demo", "service_client", 1),
    )
    assert claro == b"secreto-que-no-se-imprime"
    clave = inserts[1]
    claro_clave = kek.unseal(
        clave[2],
        nonce=clave[3],
        key=b"\x22" * 32,
        aad=mod.aad_credencial("demo", "pseudonym", 1),
    )
    assert len(claro_clave) == 32
    with pytest.raises(Exception):
        kek.unseal(
            args[3],
            nonce=args[4],
            key=b"\x22" * 32,
            aad=mod.aad_credencial("otra", "service_client", 1),
        )


@pytest.mark.asyncio
async def test_el_error_de_keycloak_no_incluye_el_secreto() -> None:
    httpx = pytest.importorskip("httpx")
    mod = _modulo()
    secreto = "super-secret-value-no-loguear"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/clients") and request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(500, text=secreto)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(RuntimeError) as exc:
            await mod.asegurar_cliente_servicio(
                http,
                keycloak_url="http://keycloak:8080",
                realm="orpycamcp",
                token="t",
                client_id="intelligence-demo",
                secret_nuevo=secreto,
            )
    assert secreto not in str(exc.value)
    assert "500" in str(exc.value)


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
    assert (
        "CHECK ((purpose = ANY (ARRAY['service_client'::text, 'hmac_return'::text, 'pseudonym'::text])))"
        in tenant_sql
    )
    assert "send_suspended" in tenant_sql
    assert "backfill_cursor" in tenant_sql
    assert "secret text" not in tenant_sql
