# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de webhooks salientes (E11, ADR-018)."""

import socket
import uuid
from datetime import UTC, datetime

import pytest

from app.core.config import settings
from app.core.webhook_security import validate_webhook_target
from app.services.webhook_service import sign
from tests.conftest import USER

NOW = datetime.now(tz=UTC)


def test_sign_hmac_deterministic() -> None:
    body = b'{"event":"x"}'
    s1 = sign("secret", body)
    s2 = sign("secret", body)
    assert s1 == s2
    assert s1.startswith("sha256=")
    assert sign("otra", body) != s1


@pytest.mark.asyncio
async def test_create_webhook(client, db_conn) -> None:
    wid = uuid.uuid4()
    db_conn.fetchrow.return_value = {
        "id": wid,
        "url": "https://ext.example.com/hook",
        "event_types": ["document.radicado.created"],
        "active": True,
        "created_at": NOW,
    }
    resp = await client.post(
        "/api/v1/webhooks",
        json={
            "url": "https://ext.example.com/hook",
            "event_types": ["document.radicado.created"],
            "secret": "s3cr3t",
        },
        headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["url"] == "https://ext.example.com/hook"
    assert body["event_types"] == ["document.radicado.created"]
    # El secret no se expone en la respuesta.
    assert "secret" not in body


@pytest.mark.asyncio
async def test_create_webhook_sin_permiso_403(client, db_conn) -> None:
    """Sin USUA_PERM_ADMIN no se registra un webhook (fail-closed).

    Registrar una suscripción entrega CADA evento del tenant a una URL externa:
    sin este gate era un canal de exfiltración (y SSRF) abierto a cualquier
    usuario autenticado. El gate es de router → cubre create/list/delete.
    """
    db_conn.fetchval.return_value = None  # check_permission → False
    resp = await client.post(
        "/api/v1/webhooks",
        json={"url": "https://ext.example.com/hook", "event_types": ["document.radicado.created"]},
        headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_list_webhooks(client, db_conn) -> None:
    db_conn.fetch.return_value = [
        {
            "id": uuid.uuid4(),
            "url": "https://a.example/h",
            "event_types": [],
            "active": True,
            "created_at": NOW,
        },
    ]
    resp = await client.get("/api/v1/webhooks", headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)})
    assert resp.status_code == 200
    assert resp.headers["x-total-count"] == "1"
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_delete_webhook_not_found(client, db_conn) -> None:
    db_conn.fetchrow.return_value = None  # DELETE ... RETURNING no devolvió fila
    resp = await client.delete(
        f"/api/v1/webhooks/{uuid.uuid4()}", headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dispatch_no_subscriptions_is_noop(db_conn, monkeypatch) -> None:
    """dispatch sin suscripciones no falla y no entrega nada."""
    from app.services import webhook_service

    class _Acq:
        async def __aenter__(self):
            return db_conn

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(webhook_service, "acquire_tenant", lambda slug: _Acq())
    db_conn.fetch.return_value = []  # active_for_event → ninguna

    n = await webhook_service.dispatch(
        {"tenant_slug": "demo", "event_type": "document.radicado.created", "payload": {}}
    )
    assert n == 0


# ---------------------------------------------------------------------------
# Endurecimiento del destino (SSRF, hallazgo Media no bloqueante).
# ---------------------------------------------------------------------------


def _addrinfo(ip: str) -> list[tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


class TestValidateWebhookTargetUnit:
    """Unidad directa sobre `validate_webhook_target`, sin pasar por el schema."""

    def test_esquema_no_http_rechazado(self) -> None:
        with pytest.raises(ValueError, match="esquema"):
            validate_webhook_target("ftp://ext.example.com/hook", allow_private=False)

    def test_localhost_rechazado(self) -> None:
        with pytest.raises(ValueError, match="localhost"):
            validate_webhook_target("http://localhost:8080/hook", allow_private=False)

    def test_loopback_ip_literal_rechazado(self) -> None:
        with pytest.raises(ValueError, match="loopback"):
            validate_webhook_target("http://127.0.0.1/hook", allow_private=False)

    def test_loopback_ipv6_literal_rechazado(self) -> None:
        with pytest.raises(ValueError, match="loopback"):
            validate_webhook_target("http://[::1]/hook", allow_private=False)

    def test_link_local_ipv4_rechazado(self) -> None:
        """169.254.169.254 (metadata cloud) cae en link-local."""
        with pytest.raises(ValueError, match="link-local"):
            validate_webhook_target("http://169.254.169.254/hook", allow_private=False)

    def test_link_local_ipv6_rechazado(self) -> None:
        with pytest.raises(ValueError, match="link-local"):
            validate_webhook_target("http://[fe80::1]/hook", allow_private=False)

    @pytest.mark.parametrize("ip", ["10.0.0.5", "172.16.0.5", "192.168.1.5"])
    def test_rangos_privados_rfc1918_rechazados(self, ip: str) -> None:
        with pytest.raises(ValueError, match="privado"):
            validate_webhook_target(f"http://{ip}/hook", allow_private=False)

    def test_ula_ipv6_rechazado(self) -> None:
        """fc00::/7 — equivalente IPv6 de los rangos privados."""
        with pytest.raises(ValueError, match="privado"):
            validate_webhook_target("http://[fc00::1]/hook", allow_private=False)

    @pytest.mark.parametrize(
        "host",
        ["postgres", "keycloak", "document-service", "orpycamcp-postgres"],
    )
    def test_nombre_servicio_docker_rechazado(self, host: str) -> None:
        with pytest.raises(ValueError, match="red interna"):
            validate_webhook_target(f"http://{host}:8080/hook", allow_private=False)

    def test_destino_externo_valido_pasa_sin_resolucion(self) -> None:
        """resolve=False (validación de creación): no toca la red."""
        validate_webhook_target(
            "https://ext.example.com/hook", allow_private=False, resolve=False
        )

    def test_destino_externo_valido_pasa_con_resolucion(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "app.core.webhook_security.socket.getaddrinfo",
            lambda *a, **k: _addrinfo("8.8.8.8"),
        )
        validate_webhook_target(
            "https://ext.example.com/hook", allow_private=False, resolve=True
        )

    def test_rebinding_dns_a_ip_interna_rechazado(self, monkeypatch) -> None:
        """El dominio resuelve (ahora) a una IP interna: se cierra en la entrega."""
        monkeypatch.setattr(
            "app.core.webhook_security.socket.getaddrinfo",
            lambda *a, **k: _addrinfo("10.1.2.3"),
        )
        with pytest.raises(ValueError, match="privado"):
            validate_webhook_target(
                "https://rebindeado.example.com/hook", allow_private=False, resolve=True
            )

    def test_resolucion_dns_fallida_rechazada_con_motivo(self, monkeypatch) -> None:
        def _boom(*a, **k):
            raise socket.gaierror("Name or service not known")

        monkeypatch.setattr("app.core.webhook_security.socket.getaddrinfo", _boom)
        with pytest.raises(ValueError, match="no fue posible resolver"):
            validate_webhook_target(
                "https://no-existe.invalid/hook", allow_private=False, resolve=True
            )

    def test_allow_private_desactiva_las_restricciones(self) -> None:
        """Kill-switch de desarrollo: con allow_private=True solo se exige el esquema."""
        validate_webhook_target("http://postgres:5432/hook", allow_private=True)
        validate_webhook_target("http://127.0.0.1:9000/hook", allow_private=True)

    def test_allow_private_sigue_exigiendo_esquema_http(self) -> None:
        with pytest.raises(ValueError, match="esquema"):
            validate_webhook_target("file:///etc/passwd", allow_private=True)


@pytest.mark.asyncio
class TestCreateWebhookRechazaDestinoInterno:
    """Vía el endpoint real: `WebhookCreate` produce 422 (no 201) para destinos internos."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/hook",
            "http://localhost/hook",
            "http://postgres:5432/hook",
            "http://169.254.169.254/hook",
            "http://192.168.1.10/hook",
        ],
    )
    async def test_destino_interno_422(self, client, db_conn, url: str) -> None:
        resp = await client.post(
            "/api/v1/webhooks",
            json={"url": url, "event_types": []},
            headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)},
        )
        assert resp.status_code == 422
        # El detalle debe explicarle al administrador POR QUÉ se rechazó, no
        # un "URL inválida" genérico.
        detalle = str(resp.json())
        assert any(
            palabra in detalle
            for palabra in ("loopback", "link-local", "privado", "red interna")
        )
        # No se llegó a insertar nada en BD.
        db_conn.fetchrow.assert_not_called()

    async def test_destino_externo_sigue_funcionando(self, client, db_conn) -> None:
        wid = uuid.uuid4()
        db_conn.fetchrow.return_value = {
            "id": wid,
            "url": "https://ext.example.com/hook",
            "event_types": [],
            "active": True,
            "created_at": NOW,
        }
        resp = await client.post(
            "/api/v1/webhooks",
            json={"url": "https://ext.example.com/hook", "event_types": []},
            headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)},
        )
        assert resp.status_code == 201

    async def test_modo_desarrollo_permite_destino_interno(self, client, db_conn) -> None:
        """webhook_allow_private_targets=True (kill-switch de dev) deja pasar loopback."""
        wid = uuid.uuid4()
        db_conn.fetchrow.return_value = {
            "id": wid,
            "url": "http://127.0.0.1:4000/hook",
            "event_types": [],
            "active": True,
            "created_at": NOW,
        }
        previo = settings.webhook_allow_private_targets
        settings.webhook_allow_private_targets = True
        try:
            resp = await client.post(
                "/api/v1/webhooks",
                json={"url": "http://127.0.0.1:4000/hook", "event_types": []},
                headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)},
            )
        finally:
            settings.webhook_allow_private_targets = previo
        assert resp.status_code == 201


@pytest.mark.asyncio
async def test_dispatch_rechaza_entrega_a_destino_ahora_interno(
    db_conn, monkeypatch
) -> None:
    """Suscripción preexistente (o rebindeada) cuyo destino ya no es válido:

    no se entrega, no se reintenta, y NO se borra ni se desactiva — dispatch
    simplemente cuenta el intento (se buscó la suscripción) pero la entrega
    real queda en 0 éxitos; la suscripción sigue viva para que el
    administrador decida.
    """
    from app.services import webhook_service

    class _Acq:
        async def __aenter__(self):
            return db_conn

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(webhook_service, "acquire_tenant", lambda slug: _Acq())
    monkeypatch.setattr(
        "app.core.webhook_security.socket.getaddrinfo",
        lambda *a, **k: _addrinfo("10.0.0.5"),
    )
    db_conn.fetch.return_value = [
        {
            "id": uuid.uuid4(),
            "url": "http://reapuntado-interno.example.com/hook",
            "secret": None,
            "event_types": [],
        }
    ]

    n = await webhook_service.dispatch(
        {"tenant_slug": "demo", "event_type": "document.radicado.created", "payload": {}}
    )
    # Se intentó (había 1 suscripción activa) pero la entrega se rechazó por destino.
    assert n == 1


# ---------------------------------------------------------------------------
# OMI-03: el CRUD de webhooks deja asiento con actor y exige nivel 3 al escribir.
# ---------------------------------------------------------------------------


def _fila(url: str = "https://ext.example.com/hook") -> dict:
    return {
        "id": uuid.uuid4(),
        "url": url,
        "event_types": ["document.radicado.created"],
        "active": True,
        "created_at": NOW,
    }


@pytest.mark.asyncio
async def test_crear_webhook_deja_asiento_con_actor(client, db_conn, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    audit = AsyncMock()
    monkeypatch.setattr("app.routers.webhooks._audit.append", audit)
    db_conn.fetchrow.return_value = _fila()
    resp = await client.post(
        "/api/v1/webhooks",
        json={
            "url": "https://usuario:clave@ext.example.com/services/T0/B0/tokenpath?token=abc",
            "event_types": ["document.radicado.created"],
            "secret": "s3cr3t",
        },
        headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)},
    )
    assert resp.status_code == 201
    audit.assert_awaited_once()
    kw = audit.await_args.kwargs
    assert kw["action"] == "notification.webhook.created"
    assert kw["actor"] == str(USER)
    assert kw["object_ref"] == resp.json()["id"]
    # El asiento es inmutable: ni el secret ni credenciales/token de la URL.
    texto = str(kw["payload"])
    assert "s3cr3t" not in texto and "clave" not in texto and "abc" not in texto
    assert "tokenpath" not in texto
    assert kw["payload"]["url"] == "https://ext.example.com"
    assert len(kw["payload"]["url_sha256"]) == 64
    import hashlib

    # Huella del ORIGEN: la del URL completo permitiría confirmar un token adivinado.
    assert kw["payload"]["url_sha256"] == hashlib.sha256(b"https://ext.example.com").hexdigest()
    # El INSERT y el asiento comparten transacción.
    db_conn.transaction.assert_called()


@pytest.mark.asyncio
async def test_borrar_webhook_deja_asiento_con_actor(client, db_conn, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    audit = AsyncMock()
    monkeypatch.setattr("app.routers.webhooks._audit.append", audit)
    fila = _fila()
    db_conn.fetchrow.return_value = fila
    resp = await client.delete(
        f"/api/v1/webhooks/{fila['id']}",
        headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)},
    )
    assert resp.status_code == 204
    kw = audit.await_args.kwargs
    assert kw["action"] == "notification.webhook.deleted"
    assert kw["actor"] == str(USER)
    assert kw["object_ref"] == str(fila["id"])
    assert kw["payload"]["url"] == "https://ext.example.com"


@pytest.mark.asyncio
async def test_borrar_inexistente_no_deja_asiento(client, db_conn, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    audit = AsyncMock()
    monkeypatch.setattr("app.routers.webhooks._audit.append", audit)
    db_conn.fetchrow.return_value = None
    resp = await client.delete(
        f"/api/v1/webhooks/{uuid.uuid4()}",
        headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)},
    )
    assert resp.status_code == 404
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_escribir_exige_nivel_3_y_leer_nivel_1(client, db_conn, monkeypatch) -> None:
    """Crear/borrar piden crud>=3 (RF-SEG-03); listar basta con 1. El nivel viaja
    como 3.er argumento de la consulta de `check_permission`."""
    from unittest.mock import AsyncMock

    monkeypatch.setattr("app.routers.webhooks._audit.append", AsyncMock())
    db_conn.fetchrow.return_value = _fila()
    h = {"X-Tenant-Slug": "demo", "X-User-Id": str(USER)}

    def _niveles() -> set[int]:
        return {c.args[3] for c in db_conn.fetchval.call_args_list if len(c.args) >= 4}

    await client.post("/api/v1/webhooks", json={"url": "https://e.example.com/h"}, headers=h)
    assert _niveles() == {3}
    db_conn.fetchval.reset_mock()
    await client.delete(f"/api/v1/webhooks/{uuid.uuid4()}", headers=h)
    assert _niveles() == {3}
    db_conn.fetchval.reset_mock()
    await client.get("/api/v1/webhooks", headers=h)
    assert _niveles() == {1}


@pytest.mark.asyncio
async def test_crear_sin_identidad_401(client) -> None:
    resp = await client.post(
        "/api/v1/webhooks",
        json={"url": "https://e.example.com/h"},
        headers={"X-Tenant-Slug": "demo"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# AREA-10-11: la conexión sale hacia la IP que se validó (sin segunda resolución).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_la_entrega_conecta_a_la_ip_validada_no_a_la_que_resuelva_despues(
    monkeypatch,
) -> None:
    import httpx

    from app.services import webhook_service

    respuestas = iter([_addrinfo("93.184.216.34"), _addrinfo("10.0.0.5")])
    llamadas = []

    def _gai(*a, **k):
        llamadas.append(a)
        return next(respuestas)

    monkeypatch.setattr("app.core.webhook_security.socket.getaddrinfo", _gai)

    vistas: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        ok = await webhook_service._deliver_one(
            client,
            {"url": "https://hook.atacante.tld:8443/p?q=1", "secret": None},
            b"{}",
        )
    assert ok
    assert len(vistas) == 1, "no se envió nada: la prueba no mide el destino"
    req = vistas[0]
    assert req.url.host == "93.184.216.34"
    assert req.url.port == 8443
    assert req.url.path == "/p" and req.url.query == b"q=1"
    assert req.headers["Host"] == "hook.atacante.tld:8443"
    assert req.extensions["sni_hostname"] == "hook.atacante.tld"
    assert len(llamadas) == 1, "se resolvió el nombre más de una vez por intento"


def test_validate_devuelve_la_ip_a_fijar(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.webhook_security.socket.getaddrinfo",
        lambda *a, **k: _addrinfo("93.184.216.34"),
    )
    assert (
        validate_webhook_target("https://ext.example.com/h", allow_private=False)
        == "93.184.216.34"
    )
    assert validate_webhook_target("https://8.8.8.8/h", allow_private=False) == "8.8.8.8"
    assert validate_webhook_target("http://127.0.0.1/h", allow_private=True) is None


# ---------------------------------------------------------------------------
# Revisión testing-engineer (T5): huecos que las pruebas anteriores dejaban.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_una_redireccion_no_se_sigue_ni_con_un_cliente_que_las_sigue(monkeypatch) -> None:
    """`follow_redirects=False` va por petición: un 302 a una IP interna NO se sigue
    aunque el cliente compartido las siga (la validación SSRF solo cubre el 1.er salto)."""
    import httpx

    from app.services import webhook_service

    monkeypatch.setattr(
        "app.core.webhook_security.socket.getaddrinfo", lambda *a, **k: _addrinfo("93.184.216.34")
    )
    vistas: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return httpx.Response(302, headers={"Location": "http://10.0.0.5/admin"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_handler), follow_redirects=True
    ) as client:
        await webhook_service._deliver_one(
            client, {"url": "https://hook.example.com/p", "secret": None}, b"{}"
        )
    assert len(vistas) == 1, "no se envió nada o se siguió la redirección"
    assert vistas[0].url.host == "93.184.216.34"


def test_pinned_request_ipv6_y_puerto_por_defecto() -> None:
    from app.services.webhook_service import _pinned_request

    url, headers, ext = _pinned_request("https://hook.example.com/p?q=1", "2606:2800:220:1::1")
    assert url == "https://[2606:2800:220:1::1]/p?q=1"
    assert headers == {"Host": "hook.example.com"}  # sin puerto: no se inventa uno
    assert ext == {"sni_hostname": "hook.example.com"}
    # Sin IP que fijar (allow_private) no se toca nada.
    assert _pinned_request("http://localhost:8080/x", None) == ("http://localhost:8080/x", {}, {})


@pytest.mark.asyncio
async def test_borrar_webhook_sin_permiso_403_y_no_borra(client, db_conn) -> None:
    db_conn.fetchval.return_value = None  # check_permission → False
    resp = await client.delete(
        f"/api/v1/webhooks/{uuid.uuid4()}",
        headers={"X-Tenant-Slug": "demo", "X-User-Id": str(USER)},
    )
    assert resp.status_code == 403
    db_conn.fetchrow.assert_not_awaited()  # el DELETE ... RETURNING no llegó a correr


@pytest.mark.asyncio
async def test_sin_modulo_de_auditoria_no_se_abre_ni_se_cierra_el_canal(
    client, db_conn, monkeypatch
) -> None:
    """Fail-closed: sin `audit` el 503 sale ANTES de tocar la tabla."""
    monkeypatch.setattr("app.routers.webhooks._AUDIT_AVAILABLE", False)
    h = {"X-Tenant-Slug": "demo", "X-User-Id": str(USER)}
    r1 = await client.post("/api/v1/webhooks", json={"url": "https://e.example.com/h"}, headers=h)
    r2 = await client.delete(f"/api/v1/webhooks/{uuid.uuid4()}", headers=h)
    assert r1.status_code == r2.status_code == 503
    assert r1.json()["detail"]["error"] == "audit_unavailable"
    db_conn.fetchrow.assert_not_awaited()
