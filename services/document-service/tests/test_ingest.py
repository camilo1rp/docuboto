# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de ingesta de correo (E19, F2)."""

from email.message import EmailMessage as PyEmail
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.clients.imap_client import EmailMessage, parse_email
from app.core.config import settings
from app.main import app
from app.routers.ingest import get_imap_client
from app.services.document_service import DocumentService
from app.services.email_ingest import EmailIngestor
from tests.conftest import TENANT_HEADERS


def _raw_email() -> bytes:
    msg = PyEmail()
    msg["From"] = "Juan Pérez <juan@ciudadano.co>"
    msg["Subject"] = "Solicitud de información"
    msg.set_content("Cuerpo del correo con la petición.")
    msg.add_attachment(
        b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="anexo.pdf"
    )
    return msg.as_bytes()


def test_parse_email() -> None:
    em = parse_email("42", _raw_email())
    assert em.uid == "42"
    assert em.from_addr == "juan@ciudadano.co"
    assert em.from_name == "Juan Pérez"
    assert em.subject == "Solicitud de información"
    assert "petición" in em.body
    assert em.attachments == ["anexo.pdf"]


@pytest.mark.asyncio
async def test_ingestor_maps_to_entrada() -> None:
    fake_service = SimpleNamespace(
        create=AsyncMock(return_value=SimpleNamespace(tracking_number="2024-DEMO-E-000007"))
    )
    ingestor = EmailIngestor(fake_service)  # type: ignore[arg-type]
    em = EmailMessage(
        uid="1",
        from_addr="a@b.co",
        from_name="Ana",
        subject="Hola",
        body="texto",
        attachments=["x.pdf"],
    )
    tn = await ingestor.ingest(em)
    assert tn == "2024-DEMO-E-000007"
    payload = fake_service.create.call_args[0][0]
    assert payload.doc_type == "E"
    assert payload.sender_name == "Ana"
    assert payload.metadata["source"] == "email"
    assert payload.metadata["sender_email"] == "a@b.co"
    assert payload.metadata["attachments"] == ["x.pdf"]


@pytest.mark.asyncio
async def test_poll_not_configured(client_with_conn) -> None:
    client, _ = client_with_conn
    app.dependency_overrides[get_imap_client] = lambda: None
    try:
        resp = await client.post("/api/v1/ingest/email/poll", headers=TENANT_HEADERS)
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "email_not_configured"
    finally:
        app.dependency_overrides.pop(get_imap_client, None)


@pytest.fixture
def buzon_de_esta_entidad():
    """El buzón declara pertenecer al tenant de las cabeceras de test.

    Desde 11.9 el poll exige que el dueño del buzón coincida con la entidad que
    lo dispara; sin declararlo, responde 503 aunque IMAP esté configurado.
    """
    anterior = settings.imap_tenant_slug
    settings.imap_tenant_slug = TENANT_HEADERS["X-Tenant-Slug"]
    yield
    settings.imap_tenant_slug = anterior


@pytest.mark.asyncio
async def test_poll_ingests_messages(client_with_conn, buzon_de_esta_entidad) -> None:
    client, _ = client_with_conn
    messages = [
        EmailMessage(uid="1", from_addr="a@b.co", from_name="Ana", subject="Uno", body="x"),
        EmailMessage(uid="2", from_addr="c@d.co", from_name="Carlos", subject="Dos", body="y"),
    ]
    fake_imap = SimpleNamespace(
        fetch_unseen=AsyncMock(return_value=messages),
        mark_seen=AsyncMock(),
    )
    app.dependency_overrides[get_imap_client] = lambda: fake_imap
    fake_create = AsyncMock(
        side_effect=[
            SimpleNamespace(tracking_number="2024-DEMO-E-000001"),
            SimpleNamespace(tracking_number="2024-DEMO-E-000002"),
        ]
    )
    try:
        with patch.object(DocumentService, "create", fake_create):
            resp = await client.post("/api/v1/ingest/email/poll", headers=TENANT_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ingested"] == 2
        assert body["tracking_numbers"] == ["2024-DEMO-E-000001", "2024-DEMO-E-000002"]
        assert fake_imap.mark_seen.await_count == 2
    finally:
        app.dependency_overrides.pop(get_imap_client, None)


# ── 11.9: el poll solo exigía un JWT válido ──────────────────────────────────


@pytest.mark.asyncio
async def test_poll_exige_permiso_administrativo(client_with_conn) -> None:
    """Radicar en masa es un acto administrativo, no algo que pueda hacer
    cualquier usuario con sesión abierta."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # sin USUA_PERM_ADMIN

    app.dependency_overrides[get_imap_client] = lambda: SimpleNamespace(
        fetch_unseen=AsyncMock(return_value=[]), mark_seen=AsyncMock()
    )
    try:
        resp = await client.post(
            "/api/v1/ingest/email/poll",
            headers={**TENANT_HEADERS, "X-User-Id": "11111111-1111-1111-1111-111111111111"},
        )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(get_imap_client, None)


@pytest.mark.asyncio
async def test_poll_desde_otra_entidad_no_se_lleva_el_buzon(client_with_conn) -> None:
    """El defecto de fondo: el proceso tiene UN buzón y el tenant venía de la
    cabecera.

    Un usuario de la entidad A disparaba la ingesta y los correos —que pueden ir
    dirigidos a la B— acababan radicados en A. Y como el poll los marca como
    leídos, no vuelven a aparecer: no es una lectura indebida, es una
    apropiación irreversible.
    """
    client, _ = client_with_conn
    fake_imap = SimpleNamespace(fetch_unseen=AsyncMock(return_value=[]), mark_seen=AsyncMock())
    app.dependency_overrides[get_imap_client] = lambda: fake_imap
    anterior = settings.imap_tenant_slug
    settings.imap_tenant_slug = "otra-entidad"
    try:
        resp = await client.post("/api/v1/ingest/email/poll", headers=TENANT_HEADERS)
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "imap_tenant_mismatch"
        # El mensaje no dice de quién ES el buzón: el nombre de las demás
        # instituciones del despliegue no es asunto de esta entidad.
        assert "otra-entidad" not in resp.text
        # Y sobre todo: no se tocó el buzón.
        assert fake_imap.fetch_unseen.await_count == 0
    finally:
        settings.imap_tenant_slug = anterior
        app.dependency_overrides.pop(get_imap_client, None)


@pytest.mark.asyncio
async def test_poll_sin_dueno_declarado_es_fail_closed(client_with_conn) -> None:
    """Un buzón sin dueño no tiene forma de saber en qué entidad radicar, y
    adivinarlo por la cabecera es exactamente el defecto."""
    client, _ = client_with_conn
    fake_imap = SimpleNamespace(fetch_unseen=AsyncMock(return_value=[]), mark_seen=AsyncMock())
    app.dependency_overrides[get_imap_client] = lambda: fake_imap
    anterior = settings.imap_tenant_slug
    settings.imap_tenant_slug = ""
    try:
        resp = await client.post("/api/v1/ingest/email/poll", headers=TENANT_HEADERS)
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "imap_tenant_not_configured"
        assert fake_imap.fetch_unseen.await_count == 0
    finally:
        settings.imap_tenant_slug = anterior
        app.dependency_overrides.pop(get_imap_client, None)


@pytest.mark.asyncio
async def test_poll_deja_asiento_del_acto(client_with_conn, buzon_de_esta_entidad) -> None:
    """La ingesta CREA radicados oficiales y no quedaba registro de quién la
    disparó. Un asiento por poll — los radicados ya dejan el suyo."""
    client, mock_conn = client_with_conn
    fake_imap = SimpleNamespace(
        fetch_unseen=AsyncMock(
            return_value=[
                EmailMessage(uid="1", from_addr="a@b.co", from_name="Ana", subject="Uno", body="x")
            ]
        ),
        mark_seen=AsyncMock(),
    )
    app.dependency_overrides[get_imap_client] = lambda: fake_imap
    fake_create = AsyncMock(return_value=SimpleNamespace(tracking_number="2024-DEMO-E-000001"))
    try:
        with patch.object(DocumentService, "create", fake_create):
            resp = await client.post(
                "/api/v1/ingest/email/poll",
                headers={**TENANT_HEADERS, "X-User-Id": "11111111-1111-1111-1111-111111111111"},
            )
        assert resp.status_code == 200
        ejecutadas = " ".join(str(c) for c in mock_conn.execute.call_args_list)
        assert "audit_log" in ejecutadas, "el acto de ingesta no dejó asiento"
        assert "ingest.email.poll" in ejecutadas
    finally:
        app.dependency_overrides.pop(get_imap_client, None)


# ── Estado de la ingesta (sin efectos) ────────────────────────────────────────
#
# El poll era la única puerta: para saber si la ingesta estaba configurada había
# que ejecutarla, y ejecutarla crea radicados y marca correos como leídos. Estos
# tests cubren la consulta que faltaba y, sobre todo, las dos cosas que puede
# hacer mal: revelar el buzón de otra entidad, y desmentir al poll.

ESTADO_URL = "/api/v1/ingest/email/estado"


@pytest.mark.asyncio
async def test_estado_sin_imap_no_es_un_error(client_with_conn) -> None:
    """"No hay ingesta" es una respuesta, no una avería: 200, no 503."""
    client, _ = client_with_conn
    app.dependency_overrides[get_imap_client] = lambda: None
    try:
        resp = await client.get(ESTADO_URL, headers=TENANT_HEADERS)
        assert resp.status_code == 200
        cuerpo = resp.json()
        assert cuerpo["habilitada"] is False
        assert cuerpo["motivo"] == "imap_no_configurado"
    finally:
        app.dependency_overrides.pop(get_imap_client, None)


@pytest.mark.asyncio
async def test_estado_habilitado_describe_el_buzon(client_with_conn, buzon_de_esta_entidad) -> None:
    client, _ = client_with_conn
    app.dependency_overrides[get_imap_client] = lambda: SimpleNamespace()
    previos = (settings.imap_host, settings.imap_mailbox, settings.imap_user)
    settings.imap_host, settings.imap_mailbox, settings.imap_user = (
        "imap.entidad.gov.co",
        "CORRESPONDENCIA",
        "correspondencia@entidad.gov.co",
    )
    try:
        cuerpo = (await client.get(ESTADO_URL, headers=TENANT_HEADERS)).json()
        assert cuerpo["habilitada"] is True
        assert cuerpo["motivo"] is None
        assert cuerpo["servidor"] == "imap.entidad.gov.co"
        assert cuerpo["buzon"] == "CORRESPONDENCIA"
        assert cuerpo["cuenta"] == "correspondencia@entidad.gov.co"
    finally:
        settings.imap_host, settings.imap_mailbox, settings.imap_user = previos
        app.dependency_overrides.pop(get_imap_client, None)


@pytest.mark.asyncio
async def test_estado_de_otra_entidad_no_describe_el_buzon(client_with_conn) -> None:
    """El punto delicado del endpoint.

    El 403 del poll está redactado a propósito para no decir de quién ES el
    buzón. Un estado que devolviera servidor y cuenta cuando el buzón es de otra
    entidad tiraría ese cuidado por la ventana desde la puerta de al lado: la
    dirección de correspondencia y el servidor de correo de otra institución del
    despliegue no son asunto de quien pregunta.
    """
    client, _ = client_with_conn
    app.dependency_overrides[get_imap_client] = lambda: SimpleNamespace()
    previos = (settings.imap_tenant_slug, settings.imap_host, settings.imap_user)
    settings.imap_tenant_slug = "otra-entidad"
    settings.imap_host = "imap.otra-entidad.gov.co"
    settings.imap_user = "correspondencia@otra-entidad.gov.co"
    try:
        cuerpo = (await client.get(ESTADO_URL, headers=TENANT_HEADERS)).json()
        assert cuerpo["habilitada"] is False
        assert cuerpo["motivo"] == "buzon_de_otra_entidad"
        assert cuerpo["servidor"] is None
        assert cuerpo["buzon"] is None
        assert cuerpo["cuenta"] is None
        # Ni el nombre de la entidad dueña se filtra por el motivo.
        assert "otra-entidad" not in resp_texto(cuerpo)
    finally:
        (settings.imap_tenant_slug, settings.imap_host, settings.imap_user) = previos
        app.dependency_overrides.pop(get_imap_client, None)


def resp_texto(cuerpo: dict) -> str:
    return " ".join(str(v) for v in cuerpo.values())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dueno, hay_imap",
    [
        ("", True),  # entidad no declarada
        ("otra-entidad", True),  # buzón de otra entidad
        (TENANT_HEADERS["X-Tenant-Slug"], False),  # sin cliente IMAP
        (TENANT_HEADERS["X-Tenant-Slug"], True),  # disponible
    ],
)
async def test_estado_y_poll_no_pueden_discrepar(client_with_conn, dueno, hay_imap) -> None:
    """El estado promete exactamente lo que el poll cumple.

    Una pantalla que anuncia "lista para traer correo" sobre un buzón que el
    poll va a rechazar es peor que no tener pantalla. Ambas puertas consultan
    `diagnosticar_buzon`; este test es el que se pondría rojo si alguien vuelve
    a escribir la condición dos veces.
    """
    client, _ = client_with_conn
    falso_imap = SimpleNamespace(fetch_unseen=AsyncMock(return_value=[]), mark_seen=AsyncMock())
    app.dependency_overrides[get_imap_client] = lambda: (falso_imap if hay_imap else None)
    anterior = settings.imap_tenant_slug
    settings.imap_tenant_slug = dueno
    try:
        habilitada = (await client.get(ESTADO_URL, headers=TENANT_HEADERS)).json()["habilitada"]
        poll = await client.post("/api/v1/ingest/email/poll", headers=TENANT_HEADERS)
        acepto = poll.status_code == 200
        assert habilitada == acepto, (
            f"el estado dice habilitada={habilitada} y el poll respondió {poll.status_code}"
        )
    finally:
        settings.imap_tenant_slug = anterior
        app.dependency_overrides.pop(get_imap_client, None)


# ── Fecha de recibo (E19 Increment 2) ─────────────────────────────────────────


def test_la_fecha_de_recibo_sale_del_buzon_no_del_remitente() -> None:
    """`INTERNALDATE` y no la cabecera `Date`.

    `Date` la pone el cliente del REMITENTE: depende de su reloj, puede faltar
    —el servidor de pruebas lo demostró: llegó vacía— y sobre todo **la controla
    él**, así que podría fecharse en el pasado para ganar plazo en un derecho de
    petición. El `INTERNALDATE` lo pone el servidor de la ENTIDAD cuando el
    mensaje entra en el buzón, y es literalmente la «fecha y hora de recibo» de
    la que la Circular Externa AGN 001/2020 exige dejar constancia.
    """
    from app.clients.imap_adaptador import _internaldate

    respuesta = b'1 (UID 7 INTERNALDATE "20-Aug-2026 09:15:42 +0000" BODY[] {793}'
    recibido = _internaldate(respuesta)
    assert recibido is not None
    assert (recibido.year, recibido.month, recibido.day) == (2026, 8, 20)
    assert (recibido.hour, recibido.minute) == (9, 15)


def test_un_huso_horario_no_desplaza_la_fecha_de_recibo() -> None:
    """La misma marca en otro huso es el mismo instante, no otro día.

    Interpretar la tupla como hora local desplazaría el recibo tantas horas como
    el huso del servidor — cinco en Colombia, suficientes para cambiar el día en
    que vence un plazo.
    """
    from app.clients.imap_adaptador import _internaldate

    utc = _internaldate(b'1 (INTERNALDATE "20-Aug-2026 09:15:42 +0000" BODY[] {1}')
    bogota = _internaldate(b'1 (INTERNALDATE "20-Aug-2026 04:15:42 -0500" BODY[] {1}')
    assert utc is not None and bogota is not None
    assert utc == bogota


def test_sin_internaldate_no_se_inventa_una_fecha() -> None:
    """Una fecha de recibo inventada es peor que ninguna: nadie la revisaría."""
    from app.clients.imap_adaptador import _internaldate

    assert _internaldate(b"1 (UID 7 BODY[] {793}") is None


# ── UIDVALIDITY: la comprobación que impide radicar el correo equivocado ──────


def test_un_uidvalidity_distinto_impide_operar() -> None:
    """El defecto que apareció usando el sistema, no en ningún test.

    Un `uid` solo identifica un mensaje mientras el `UIDVALIDITY` de la carpeta
    no cambie (RFC 3501 §2.3.1.1). Cuando cambia —restauración del buzón,
    migración de servidor— los `uid` se reparten de nuevo desde 1: la bandeja
    seguiría apuntando al «mensaje 7» y traería el que ahora ocupa esa posición.
    Otro correo, de otro remitente, **radicado bajo el asunto del primero**.

    El modelo guardaba el `uidvalidity` desde el principio; lo que faltaba era
    comprobarlo antes de traer o mover. Se descubrió al reiniciarse el servidor
    de pruebas, que asignó uno nuevo.
    """
    from app.clients.imap_adaptador import Conexion, ErrorDeCorreo, ImapAdaptador

    adaptador = ImapAdaptador(
        Conexion(
            host="h", puerto=993, usa_ssl=True, direccion="a@b.co",
            auth_tipo="password", secreto="x", login="a@b.co",
        )
    )
    with pytest.raises(ErrorDeCorreo) as exc:
        adaptador._exigir_uidvalidity(999, 111, "INBOX")
    assert exc.value.codigo == "uidvalidity_cambiado"
    # El mensaje dice qué hacer, no solo qué pasó.
    assert "vuelva a consultar el buzón" in str(exc.value).lower()

    # Coincidiendo, no estorba.
    adaptador._exigir_uidvalidity(111, 111, "INBOX")
    # Y sin expectativa declarada tampoco: es el caso de la propia
    # sincronización, que acaba de leerlo y no tiene con qué contrastar.
    adaptador._exigir_uidvalidity(111, None, "INBOX")
