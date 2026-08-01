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
    db_conn.execute.return_value = "DELETE 0"
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
