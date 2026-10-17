# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""T13 — cumplimiento documental en document-service (unit, con dobles).

Cada bloque cita el hallazgo y la mutación que lo tumba (ver CHANGELOG):

- AREA-02-07: honeypot (quitar `_sin_honeypot` → los tests de honeypot caen) e
  `Idempotency-Key` (no pasar `submission_key` a `create` → cae el cableado).
- AREA-09-08: constancia de la autorización de tratamiento (quitar `Literal[True]` →
  el 422 sin autorización cae).
- AREA-09-07: `habilitada` (devolver siempre True → cae el caso sin dependencia).
- AREA-09-10: rotación del secreto (aceptar la ranura anterior sin mirar su caducidad
  → cae el caso caducado) y acuse obligatorio.
- AREA-09-16: `documento.contenido_cambiado` (quitar el `encolar`/`enviar` → cae).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.core.postal_webhook_auth import cifrar_secreto, verify_any, verify_signature
from app.schemas.postal import ShipmentStatusUpdate
from app.schemas.public import VentanillaRadicacion
from app.services.document_service import DocumentService, DuplicateSubmissionError
from tests.conftest import TENANT_HEADERS
from tests.test_ventanilla import (
    BASE,
    _cuerpo,
    _fetchrow_por_consulta,
    _fetchval_por_consulta,
    _multipart,
    _resultado_radicado,
)

KEY = "k" * 32


def _preparar(conn, tipo="Derecho de petición", codigo="cod-verif"):
    conn.fetchval.side_effect = _fetchval_por_consulta(tipo, codigo)
    conn.fetchrow.side_effect = _fetchrow_por_consulta()


# --------------------------------------------------------------------------- 02-07


@pytest.mark.asyncio
async def test_el_honeypot_relleno_es_422_y_no_se_toca_nada(client_with_conn) -> None:
    client, conn = client_with_conn
    _preparar(conn)
    with patch.object(DocumentService, "create", AsyncMock()) as crear:
        resp = await client.post(BASE, **_multipart(_cuerpo(sitio_web="http://spam.example")))
    assert resp.status_code == 422
    crear.assert_not_awaited()
    consultas = [str(c.args[0]) for c in conn.fetchval.await_args_list if c.args]
    assert not any("tracking_sequences" in q for q in consultas)
    # Genérico: no le dice al bot cuál es la trampa.
    assert "sitio_web" not in json.dumps(resp.json())


@pytest.mark.asyncio
async def test_el_honeypot_vacio_no_estorba(client_with_conn) -> None:
    client, conn = client_with_conn
    _preparar(conn)
    with patch.object(DocumentService, "create", AsyncMock(return_value=_resultado_radicado())):
        resp = await client.post(BASE, **_multipart(_cuerpo(sitio_web="")))
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_pqrs_tambien_tiene_honeypot(client_with_conn) -> None:
    client, conn = client_with_conn
    _preparar(conn)
    resp = await client.post(
        "/api/v1/public/demo/pqrs",
        json={
            "tipo": "peticion",
            "nombre": "X",
            "asunto": "A",
            "descripcion": "D",
            "acepta_tratamiento": True,
            "politica_version": "v1",
            "sitio_web": "x",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_una_clave_de_idempotencia_mal_formada_es_422(client_with_conn) -> None:
    client, conn = client_with_conn
    _preparar(conn)
    resp = await client.post(
        BASE, **_multipart(_cuerpo()), headers={"Idempotency-Key": "corta"}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "idempotency_key_invalida"


@pytest.mark.asyncio
async def test_la_clave_viaja_a_create_para_reclamarse_en_su_transaccion(client_with_conn) -> None:
    """El cableado, no la pieza: sin `submission_key` la reclamación no ocurre dentro
    de la transacción del radicado y la idempotencia sería una convención."""
    client, conn = client_with_conn
    _preparar(conn)
    with patch.object(
        DocumentService, "create", AsyncMock(return_value=_resultado_radicado())
    ) as crear:
        resp = await client.post(
            BASE, **_multipart(_cuerpo()), headers={"Idempotency-Key": KEY}
        )
    assert resp.status_code == 201, resp.text
    clave, huella = crear.await_args.kwargs["submission_key"]
    assert clave == KEY and len(huella) == 64


@pytest.mark.asyncio
async def test_un_reintento_con_la_misma_clave_devuelve_el_mismo_radicado(client_with_conn) -> None:
    client, conn = client_with_conn
    rid = uuid.uuid4()
    huella_guardada: dict = {}

    async def _fetchrow(sql, *a, **k):
        if "public_submission_keys" in sql:
            return (
                None
                if not huella_guardada
                else {
                    "huella": huella_guardada["h"],
                    "id": rid,
                    "tracking_number": "2026-DEMO-E-000007",
                    "verification_code": "codigo-guardado",
                    "dest_dept": "Ventanilla",
                }
            )
        return await _fetchrow_por_consulta()(sql, *a, **k)

    conn.fetchval.side_effect = _fetchval_por_consulta("Derecho de petición", "cod")
    conn.fetchrow.side_effect = _fetchrow

    # 1.er envío: crea, y «guarda» la huella con la que se reclamó.
    async def _crear(self, doc, registered_by=None, *, submission_key=None, procedencia=None):
        huella_guardada["h"] = submission_key[1]
        return SimpleNamespace(id=rid, tracking_number="2026-DEMO-E-000007")

    with patch.object(DocumentService, "create", _crear):
        r1 = await client.post(BASE, **_multipart(_cuerpo()), headers={"Idempotency-Key": KEY})
    assert r1.status_code == 201

    # 2.º envío: el camino barato lo resuelve SIN llamar a `create`.
    with patch.object(DocumentService, "create", AsyncMock()) as crear2:
        r2 = await client.post(BASE, **_multipart(_cuerpo()), headers={"Idempotency-Key": KEY})
    assert r2.status_code == 201
    crear2.assert_not_awaited()
    assert r2.json()["tracking_number"] == "2026-DEMO-E-000007"
    assert r2.json()["verification_code"] == "codigo-guardado"


@pytest.mark.asyncio
async def test_la_misma_clave_con_otro_contenido_es_409(client_with_conn) -> None:
    client, conn = client_with_conn

    async def _fetchrow(sql, *a, **k):
        if "public_submission_keys" in sql:
            return {
                "huella": "0" * 64,
                "id": uuid.uuid4(),
                "tracking_number": "x",
                "verification_code": "y",
                "dest_dept": None,
            }
        return await _fetchrow_por_consulta()(sql, *a, **k)

    conn.fetchval.side_effect = _fetchval_por_consulta("Derecho de petición", "cod")
    conn.fetchrow.side_effect = _fetchrow
    resp = await client.post(BASE, **_multipart(_cuerpo()), headers={"Idempotency-Key": KEY})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "idempotency_key_reutilizada"


@pytest.mark.asyncio
async def test_si_la_carrera_la_gana_otra_peticion_se_devuelve_su_radicado(client_with_conn) -> None:
    """`create` levanta el duplicado (la clave ya estaba reclamada y confirmada)."""
    client, conn = client_with_conn
    estado = {"ya": False}

    async def _fetchrow(sql, *a, **k):
        if "public_submission_keys" in sql:
            if not estado["ya"]:
                return None  # el camino barato no la ve todavía
            return {
                "huella": estado["h"],
                "id": uuid.uuid4(),
                "tracking_number": "2026-DEMO-E-000009",
                "verification_code": "ganador",
                "dest_dept": "V",
            }
        return await _fetchrow_por_consulta()(sql, *a, **k)

    conn.fetchval.side_effect = _fetchval_por_consulta("Derecho de petición", "cod")
    conn.fetchrow.side_effect = _fetchrow

    async def _crear(self, doc, registered_by=None, *, submission_key=None, procedencia=None):
        estado["ya"] = True
        estado["h"] = submission_key[1]
        raise DuplicateSubmissionError(submission_key[0])

    with patch.object(DocumentService, "create", _crear):
        resp = await client.post(BASE, **_multipart(_cuerpo()), headers={"Idempotency-Key": KEY})
    assert resp.status_code == 201
    assert resp.json()["tracking_number"] == "2026-DEMO-E-000009"


# --------------------------------------------------------------------------- 09-08


def test_sin_autorizacion_de_tratamiento_no_se_radica() -> None:
    base = {
        "tipo_documental": "DP",
        "nombre": "A",
        "asunto": "a",
        "descripcion": "d",
        "politica_version": "v1",
    }
    with pytest.raises(ValueError):
        VentanillaRadicacion(**base)  # falta
    with pytest.raises(ValueError):
        VentanillaRadicacion(**base, acepta_tratamiento=False)
    ok = VentanillaRadicacion(**base, acepta_tratamiento=True)
    assert ok.politica_version == "v1"


@pytest.mark.asyncio
async def test_la_constancia_de_consentimiento_viaja_con_version_instante_e_ip(
    client_with_conn, monkeypatch
) -> None:
    """Constancia v2 (B2, ADR-031): la IP es la que el GATEWAY firmó para esta ruta."""
    from orpycamcp_common import identidad

    monkeypatch.setenv("IDENTITY_SIGNING_SECRET", "secreto-de-prueba")
    client, conn = client_with_conn
    _preparar(conn)
    with patch.object(
        DocumentService, "create", AsyncMock(return_value=_resultado_radicado())
    ) as crear:
        resp = await client.post(
            BASE,
            **_multipart(_cuerpo(politica_version="2026-01")),
            headers=identidad.firmar_ip_publica("203.0.113.9", BASE),
        )
    assert resp.status_code == 201, resp.text
    c = crear.await_args.kwargs["procedencia"]["consentimiento"]
    assert c["politica_version"] == "2026-01"
    assert c["version_constancia"] == 2
    assert c["norma"] == "Ley 1581/2012"
    assert c["ip"] == "203.0.113.9"
    assert c["ip_verificada"] is True
    assert c["politica_url"] == "https://entidad.example/privacidad"
    assert len(c["politica_huella"]) == 64
    datetime.fromisoformat(c["instante"])


@pytest.mark.asyncio
async def test_el_consentimiento_queda_en_el_asiento_de_creacion(client_with_conn) -> None:
    """Se ejerce `create` REAL con la constancia por `procedencia` (la única vía: el
    cuerpo de un cliente ya no puede aportarla): el asiento la lleva."""
    from app.schemas.document import DocumentCreate
    from app.services.document_service import DocumentService

    _client, conn = client_with_conn
    conn.fetchrow.side_effect = [
        None,
        {"code": "DEMO"},
        __import__("tests.conftest", fromlist=["x"]).make_radicado_record(),
    ]
    conn.fetchval.return_value = 1
    conn.fetch.return_value = []
    doc = DocumentCreate(
        doc_type="E", subject="Con constancia", doc_class="Petición", dest_dept_code=1
    )
    from tests.conftest import FakeTenantClient

    svc = DocumentService(conn, "demo", FakeTenantClient())  # type: ignore[arg-type]
    await svc.create(
        doc,
        None,
        procedencia={"consentimiento": {"politica_version": "v9", "norma": "Ley 1581/2012"}},
    )
    asientos = [
        c.args
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == "document.radicado_created"
    ]
    assert len(asientos) == 1
    assert json.loads(asientos[0][8])["consentimiento"]["politica_version"] == "v9"


@pytest.mark.asyncio
async def test_el_cliente_no_puede_aportar_el_consentimiento(client_with_conn) -> None:
    """B6: `consentimiento` es una constancia con valor probatorio: la pone el
    sistema por `procedencia`; en el cuerpo de un cliente es 422."""
    client, conn = client_with_conn
    resp = await client.post(
        "/api/v1/documents",
        json={
            "doc_type": "E",
            "subject": "Con constancia",
            "doc_class": "Petición",
            "dest_dept_code": 1,
            "metadata": {"consentimiento": {"politica_version": "v9"}},
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    assert "consentimiento" in resp.text
    conn.execute.assert_not_called()


# --------------------------------------------------------------------------- 09-07


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("receptora", "tipos", "esperada"),
    [(True, 2, True), (False, 2, False), (True, 0, False), (False, 0, False)],
)
async def test_el_estado_de_la_ventanilla_dice_si_esta_habilitada(
    client_with_conn, receptora, tipos, esperada
) -> None:
    client, conn = client_with_conn

    async def _fetchval(sql, *a, **k):
        if "FROM dependencias" in sql:
            return receptora
        if "FROM tipos_documentales" in sql:
            return tipos
        return None

    conn.fetchval.side_effect = _fetchval
    resp = await client.get("/api/v1/public/demo/ventanilla/estado")
    assert resp.status_code == 200
    assert resp.json() == {
        "habilitada": esperada,
        "dependencia_receptora": receptora,
        "tipos_admitidos": tipos,
        "politica_publicada": True,  # el doble de tenant-service la trae vigente
    }


# --------------------------------------------------------------------------- 09-10


def _firma(secreto: str, cuerpo: bytes) -> str:
    import hashlib
    import hmac

    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def test_verify_any_acepta_cualquiera_de_las_ranuras_y_nada_si_no_hay() -> None:
    cuerpo = b"{}"
    assert verify_any(["nuevo", "viejo"], cuerpo, _firma("viejo", cuerpo)) is True
    assert verify_any(["nuevo", "viejo"], cuerpo, _firma("nuevo", cuerpo)) is True
    assert verify_any(["nuevo"], cuerpo, _firma("viejo", cuerpo)) is False
    assert verify_any([], cuerpo, _firma("nuevo", cuerpo)) is False
    assert verify_signature("", cuerpo, _firma("", cuerpo)) is False


_KEK = "A" * 43 + "="  # 32 bytes en base64


def _fila(vigente: str, anterior: str | None, caduca: datetime | None, slug="demo", op="4-72"):
    c1, n1 = cifrar_secreto(vigente, clave_b64=_KEK, tenant_slug=slug, operador=op)
    c2 = n2 = None
    if anterior is not None:
        c2, n2 = cifrar_secreto(anterior, clave_b64=_KEK, tenant_slug=slug, operador=op)
    return {
        "webhook_secret": None,
        "secreto_cifrado": c1,
        "secreto_nonce": n1,
        "anterior_cifrado": c2,
        "anterior_nonce": n2,
        "anterior_caduca_at": caduca,
    }


def test_la_ranura_anterior_vale_dentro_de_su_vigencia_y_no_despues(monkeypatch) -> None:
    from app.services.postal_service import PostalService

    monkeypatch.setattr(settings, "email_encryption_key", _KEK)
    svc = PostalService(AsyncMock(), tenant_slug="demo")
    ahora = datetime.now(UTC)
    vigente = _fila("nuevo-" + "n" * 30, "viejo-" + "v" * 30, ahora + timedelta(hours=1))
    assert len(svc._secretos_vigentes(vigente, "4-72", "demo", ahora)) == 2
    caducada = _fila("nuevo-" + "n" * 30, "viejo-" + "v" * 30, ahora - timedelta(seconds=1))
    solo = svc._secretos_vigentes(caducada, "4-72", "demo", ahora)
    assert solo == ["nuevo-" + "n" * 30]
    sin_gracia = _fila("nuevo-" + "n" * 30, "viejo-" + "v" * 30, None)
    assert len(svc._secretos_vigentes(sin_gracia, "4-72", "demo", ahora)) == 1


def test_un_ciphertext_de_otro_operador_o_entidad_no_descifra(monkeypatch) -> None:
    from app.services.postal_service import PostalService

    monkeypatch.setattr(settings, "email_encryption_key", _KEK)
    svc = PostalService(AsyncMock(), tenant_slug="demo")
    fila = _fila("s" * 40, None, None, slug="otra-entidad")
    assert svc._secretos_vigentes(fila, "4-72", "demo") == []
    fila = _fila("s" * 40, None, None, op="servientrega")
    assert svc._secretos_vigentes(fila, "4-72", "demo") == []


@pytest.mark.asyncio
async def test_el_callback_firmado_con_la_ranura_anterior_vigente_es_200_y_caducada_401(
    monkeypatch,
) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from tests.conftest import FakeTenantClient, _make_fake_redis, _make_mock_conn, _override_conn
    from tests.test_postal_callback import _body, _FakePool, _ship_row, _url

    monkeypatch.setattr(settings, "email_encryption_key", _KEK)
    ahora = datetime.now(UTC)
    nuevo, viejo = "nuevo-" + "n" * 30, "viejo-" + "v" * 30

    async def _correr(caduca: datetime) -> int:
        conn = _make_mock_conn()
        _override_conn(conn, FakeTenantClient())
        from app.core.database import get_redis

        app.dependency_overrides[get_redis] = lambda: _make_fake_redis()
        app.state.db_pool = _FakePool(conn)
        conn.fetchval.side_effect = [uuid.uuid4(), None]
        conn.fetchrow.side_effect = [
            _fila(nuevo, viejo, caduca),
            _ship_row(estado="registrado"),
            _ship_row(estado="en_transito"),
        ]
        cuerpo = _body(event_id=f"evt-{uuid.uuid4()}")
        from orpycamcp_common.tenant import AtencionPublica

        with patch(
            "app.core.database.atencion_publica",
            new=AsyncMock(return_value=AtencionPublica.COMPLETA),  # ADR-035: aquí no se prueba el estado
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.post(
                    _url(),
                    content=cuerpo,
                    headers={"Content-Type": "application/json", "X-OrpycaMCP-Signature": _firma(viejo, cuerpo)},
                )
        app.dependency_overrides.clear()
        return r.status_code

    assert await _correr(ahora + timedelta(hours=2)) == 200
    assert await _correr(ahora - timedelta(minutes=1)) == 401
    if hasattr(app.state, "db_pool"):
        delattr(app.state, "db_pool")


@pytest.mark.asyncio
async def test_rotar_la_credencial_exige_admin_nivel_5(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchval.return_value = None  # sin permiso
    resp = await client.put(
        "/api/v1/envios/operadores/4-72/credencial",
        json={"secreto": "s" * 40},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_rotar_la_credencial_cifra_asienta_y_no_devuelve_el_secreto(
    client_with_conn, monkeypatch
) -> None:
    client, conn = client_with_conn
    monkeypatch.setattr(settings, "email_encryption_key", _KEK)
    secreto = "secreto-que-no-debe-salir-" + "x" * 10
    async def _fetchval(sql, *a, **k):
        if "RETURNING updated_at" in sql:
            return datetime.now(UTC)
        if "SELECT hash" in sql:
            return None  # audit.append: sin hash previo
        return True  # permiso concedido

    conn.fetchval.side_effect = _fetchval
    conn.fetchrow.side_effect = [None]  # el FOR UPDATE no encuentra fila: primera vez
    resp = await client.put(
        "/api/v1/envios/operadores/4-72/credencial",
        json={"secreto": secreto, "gracia_horas": 12},
        headers={**TENANT_HEADERS, "X-User-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 200, resp.text
    assert secreto not in resp.text
    assert resp.json()["anterior_vigente_hasta"] is None  # primera vez: no hay anterior
    # Nada en claro: ni en los parámetros del upsert ni en el asiento.
    todo = repr(conn.fetchval.call_args_list) + repr(conn.execute.call_args_list)
    assert secreto not in todo
    asientos = [
        c.args
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == "document.postal_credencial_rotada"
    ]
    assert len(asientos) == 1


@pytest.mark.asyncio
async def test_sin_clave_maestra_la_rotacion_se_niega_y_no_guarda_en_claro(
    client_with_conn, monkeypatch
) -> None:
    client, conn = client_with_conn
    monkeypatch.setattr(settings, "email_encryption_key", "")
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = {"is_root": True}
    resp = await client.put(
        "/api/v1/envios/operadores/4-72/credencial",
        json={"secreto": "s" * 40},
        headers={**TENANT_HEADERS, "X-User-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "custodia_no_configurada"


def test_entregado_a_mano_exige_el_acuse_pero_los_demas_estados_no() -> None:
    with pytest.raises(ValueError, match="acuse"):
        ShipmentStatusUpdate(estado="entregado")
    with pytest.raises(ValueError):
        ShipmentStatusUpdate(estado="entregado", acuse_file_id=uuid.uuid4())
    ok = ShipmentStatusUpdate(estado="entregado", acuse_file_id=uuid.uuid4(), acuse_sha256="a" * 64)
    assert ok.acuse_sha256 == "a" * 64
    assert ShipmentStatusUpdate(estado="en_transito").estado == "en_transito"


#: Quien registra la entrega en estas pruebas (cabecera `X-User-Id`).
ACTOR = uuid.uuid4()
ACTOR_HEADERS = {**TENANT_HEADERS, "X-User-Id": str(ACTOR)}


def _storage_responde(
    monkeypatch,
    huella: str | None,
    *,
    cae: bool = False,
    autor: uuid.UUID | None = ACTOR,
    referencias: tuple[str, ...] = (),
    existe: bool | None = None,
) -> list:
    """Sustituye la consulta de procedencia a storage; devuelve las llamadas recibidas."""
    from app.clients import storage_client

    llamadas: list = []
    hay = (huella is not None) if existe is None else existe

    async def _f(*, tenant_slug, file_id):
        llamadas.append((tenant_slug, file_id))
        if cae:
            raise storage_client.StorageContentError("storage caído")
        if not hay:
            return None
        return storage_client.ProcedenciaArchivo(
            sha256=huella, uploaded_by=autor, referencias=list(referencias)
        )

    monkeypatch.setattr(storage_client, "fetch_file_procedencia", _f)
    return llamadas


async def _entregar(client_with_conn, *, sha: str, acuse_actual: uuid.UUID | None = None, fid=None):
    from tests.test_postal import _ship_row

    client, conn = client_with_conn
    sid = uuid.uuid4()
    fid = fid or uuid.uuid4()
    conn.fetchval.return_value = True
    conn.fetchrow.side_effect = [
        {"is_root": True},
        _ship_row(id=sid, estado="en_transito", acuse_file_id=acuse_actual),
        _ship_row(id=sid, estado="entregado", acuse_file_id=fid, acuse_sha256="c" * 64),
    ]
    conn.fetch.return_value = []
    resp = await client.post(
        f"/api/v1/envios/{sid}/estado",
        json={"estado": "entregado", "acuse_file_id": str(fid), "acuse_sha256": sha},
        headers=ACTOR_HEADERS,
    )
    return resp, conn


def _hubo_update_o_asiento(conn) -> bool:
    return any(
        "UPDATE postal_shipment" in str(c.args[0]) for c in conn.fetchrow.await_args_list
    ) or any(
        isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
        for c in conn.execute.call_args_list
    )


@pytest.mark.asyncio
async def test_un_acuse_que_storage_no_conoce_es_403_igual_que_uno_ajeno(
    client_with_conn, monkeypatch
) -> None:
    """AREA-09-10: un UUID inventado no es constancia. Se responde IGUAL que uno
    ajeno (403 acuse_ajeno): un 422 distinto revelaría qué file_id existen."""
    llamadas = _storage_responde(monkeypatch, None)
    resp, conn = await _entregar(client_with_conn, sha="c" * 64)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "acuse_ajeno"
    assert len(llamadas) == 1
    assert not _hubo_update_o_asiento(conn)


@pytest.mark.asyncio
async def test_un_acuse_con_otra_huella_es_422(client_with_conn, monkeypatch) -> None:
    _storage_responde(monkeypatch, "d" * 64)
    resp, conn = await _entregar(client_with_conn, sha="c" * 64)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "acuse_invalido"
    assert not _hubo_update_o_asiento(conn)


@pytest.mark.asyncio
async def test_storage_caido_al_cotejar_el_acuse_es_503_no_se_asienta_sin_cotejar(
    client_with_conn, monkeypatch
) -> None:
    _storage_responde(monkeypatch, None, cae=True)
    resp, conn = await _entregar(client_with_conn, sha="c" * 64)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "acuse_no_verificable"
    assert not _hubo_update_o_asiento(conn)


@pytest.mark.asyncio
async def test_un_acuse_subido_por_otra_persona_es_403_y_no_cambia_nada(
    client_with_conn, monkeypatch
) -> None:
    """B2: nadie presenta como acuse propio el fichero que subió otro."""
    _storage_responde(monkeypatch, "c" * 64, autor=uuid.uuid4())
    resp, conn = await _entregar(client_with_conn, sha="c" * 64)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "acuse_ajeno"
    assert not _hubo_update_o_asiento(conn)


@pytest.mark.asyncio
async def test_un_acuse_sin_autor_conocido_es_403(client_with_conn, monkeypatch) -> None:
    """Subida anónima o de sistema (`uploaded_by` NULL): no es de quien lo presenta."""
    _storage_responde(monkeypatch, "c" * 64, autor=None)
    resp, conn = await _entregar(client_with_conn, sha="c" * 64)
    assert resp.status_code == 403
    assert not _hubo_update_o_asiento(conn)


@pytest.mark.asyncio
async def test_el_autor_se_comprueba_antes_que_la_huella(client_with_conn, monkeypatch) -> None:
    """Con el orden inverso, el 422 de la huella distinta sería un oráculo sobre
    el contenido de un fichero ajeno."""
    _storage_responde(monkeypatch, "d" * 64, autor=uuid.uuid4())
    resp, _ = await _entregar(client_with_conn, sha="c" * 64)
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fuentes",
    [("anexos.file_id",), ("postal_shipment.acuse_file_id",), ("borrador.documento_file_id",),
     ("anexos(ausente)",)],
)
async def test_un_acuse_ya_referenciado_es_409(client_with_conn, monkeypatch, fuentes) -> None:
    """Un anexo (o el acuse de OTRO envío) no se reutiliza como constancia de entrega."""
    _storage_responde(monkeypatch, "c" * 64, referencias=fuentes)
    resp, conn = await _entregar(client_with_conn, sha="c" * 64)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "acuse_ya_referenciado"
    assert not _hubo_update_o_asiento(conn)


@pytest.mark.asyncio
async def test_reintento_del_mismo_envio_con_su_propio_acuse_es_idempotente(
    client_with_conn, monkeypatch
) -> None:
    fid = uuid.uuid4()
    _storage_responde(monkeypatch, "c" * 64, referencias=("postal_shipment.acuse_file_id",))
    resp, _ = await _entregar(client_with_conn, sha="c" * 64, acuse_actual=fid, fid=fid)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_reintento_no_exime_si_ademas_es_un_anexo(client_with_conn, monkeypatch) -> None:
    fid = uuid.uuid4()
    _storage_responde(
        monkeypatch, "c" * 64,
        referencias=("postal_shipment.acuse_file_id", "anexos.file_id"),
    )
    resp, _ = await _entregar(client_with_conn, sha="c" * 64, acuse_actual=fid, fid=fid)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_la_carrera_la_cierra_el_indice_unico_con_409(client_with_conn, monkeypatch) -> None:
    """Dos registros simultáneos pasan el cotejo; el índice 045 rechaza al segundo."""
    import asyncpg

    _storage_responde(monkeypatch, "c" * 64)
    client, conn = client_with_conn
    from tests.test_postal import _ship_row

    sid = uuid.uuid4()
    conn.fetchval.return_value = True

    async def _fetchrow(sql, *a, **k):
        if "UPDATE postal_shipment" in sql:
            raise asyncpg.UniqueViolationError("postal_shipment_acuse_file_id_uq")
        if "is_root" in sql:
            return {"is_root": True}
        return _ship_row(id=sid, estado="en_transito")

    conn.fetchrow.side_effect = _fetchrow
    conn.fetch.return_value = []
    resp = await client.post(
        f"/api/v1/envios/{sid}/estado",
        json={"estado": "entregado", "acuse_file_id": str(uuid.uuid4()), "acuse_sha256": "c" * 64},
        headers=ACTOR_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "acuse_ya_referenciado"


@pytest.mark.asyncio
async def test_se_asienta_la_huella_normalizada_que_devuelve_storage(
    client_with_conn, monkeypatch
) -> None:
    llamadas = _storage_responde(monkeypatch, "C" * 64)  # storage la devuelve en mayúsculas
    resp, conn = await _entregar(client_with_conn, sha="c" * 64)
    assert resp.status_code == 200, resp.text
    assert llamadas[0][0] == TENANT_HEADERS["X-Tenant-Slug"]
    upd = next(c.args for c in conn.fetchrow.await_args_list if "UPDATE postal_shipment" in str(c.args[0]))
    assert upd[7] == "c" * 64


@pytest.mark.asyncio
async def test_el_acuse_llega_al_update_y_al_asiento(client_with_conn, monkeypatch) -> None:
    from tests.test_postal import _ship_row

    _storage_responde(monkeypatch, "b" * 64)
    client, conn = client_with_conn
    sid, fid = uuid.uuid4(), uuid.uuid4()
    conn.fetchval.return_value = True
    conn.fetchrow.side_effect = [
        {"is_root": True},
        _ship_row(id=sid, estado="en_transito"),
        _ship_row(id=sid, estado="entregado", acuse_file_id=fid, acuse_sha256="b" * 64),
    ]
    conn.fetch.return_value = []
    resp = await client.post(
        f"/api/v1/envios/{sid}/estado",
        json={"estado": "entregado", "acuse_file_id": str(fid), "acuse_sha256": "B" * 64},
        headers=ACTOR_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["acuse_file_id"] == str(fid)
    upd = next(c.args for c in conn.fetchrow.await_args_list if "UPDATE postal_shipment" in str(c.args[0]))
    assert upd[6] == fid and upd[7] == "b" * 64  # normalizado a minúsculas
    asiento = next(
        c.args
        for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == "document.envio_estado"
    )
    assert json.loads(asiento[8])["acuse_file_id"] == str(fid)


@pytest.mark.asyncio
async def test_entregado_sin_acuse_es_422_por_la_api(client_with_conn) -> None:
    client, conn = client_with_conn
    sid = uuid.uuid4()
    conn.fetchval.return_value = True
    conn.fetchrow.return_value = {"is_root": True}
    resp = await client.post(
        f"/api/v1/envios/{sid}/estado", json={"estado": "entregado"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- 09-16


@pytest.mark.asyncio
async def test_editar_el_texto_emite_contenido_cambiado_con_el_nuevo_hash() -> None:
    from app.schemas.borrador import BorradorUpdate
    from app.services.borrador_service import BorradorService

    bid = uuid.uuid4()
    conn = AsyncMock()
    conn.transaction = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(
        return_value=AsyncMock()
    )
    redis = AsyncMock()
    redis.xadd.return_value = b"1-0"
    sig = AsyncMock()
    svc = BorradorService(conn, "demo", signature_client=sig, redis=redis)
    antes = {"estado": "borrador", "radicado_padre_id": None, "cuerpo_sha256": "a" * 64}
    despues = {
        "id": bid,
        "version": 2,
        "radicado_padre_id": None,
        "nivel_seguridad": 2,
        "cuerpo_sha256": "b" * 64,
    }
    svc._require_editable = AsyncMock(return_value=antes)
    svc._repo = AsyncMock()
    svc._repo.update_borrador.return_value = despues
    with patch("app.services.borrador_service._bo", lambda r: r):
        await svc.editar(bid, BorradorUpdate(cuerpo="<p>nuevo</p>"), max_clearance=3, actor=uuid.uuid4())

    redis.xadd.assert_awaited_once()
    campos = redis.xadd.await_args.args[1]
    assert campos[b"event_type" if b"event_type" in campos else "event_type"] in (
        b"documento.contenido_cambiado",
        "documento.contenido_cambiado",
    )
    assert "b" * 64 in repr(campos)
    assert '"nivel_seguridad": 2' in repr(campos)  # declara el nivel (webhook de notification)
    sig.invalidar_circuitos.assert_awaited_once()  # la garantía síncrona sigue en pie


@pytest.mark.asyncio
async def test_cambiar_solo_el_asunto_no_emite_ni_invalida() -> None:
    from app.schemas.borrador import BorradorUpdate
    from app.services.borrador_service import BorradorService

    bid = uuid.uuid4()
    conn = AsyncMock()
    conn.transaction = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(
        return_value=AsyncMock()
    )
    redis = AsyncMock()
    sig = AsyncMock()
    svc = BorradorService(conn, "demo", signature_client=sig, redis=redis)
    mismo = "a" * 64
    svc._require_editable = AsyncMock(
        return_value={"estado": "borrador", "radicado_padre_id": None, "cuerpo_sha256": mismo}
    )
    svc._repo = AsyncMock()
    svc._repo.update_borrador.return_value = {
        "id": bid,
        "version": 2,
        "radicado_padre_id": None,
        "nivel_seguridad": 1,
        "cuerpo_sha256": mismo,
    }
    with patch("app.services.borrador_service._bo", lambda r: r):
        await svc.editar(bid, BorradorUpdate(subject="Otro asunto"), max_clearance=3, actor=None)
    redis.xadd.assert_not_awaited()
    sig.invalidar_circuitos.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_rele_sustituye_el_nivel_por_el_vigente() -> None:
    """Un reclassified rancio no debe llevar su nivel original al reenviarse."""
    from app.services import outbox

    conn = AsyncMock()
    conn.fetchval.return_value = 3
    out = await outbox._con_nivel_vivo(conn, {"tracking_number": "T-1", "nivel_nuevo": 1})
    assert out["nivel_nuevo"] == 3
    assert "FROM radicados" in conn.fetchval.await_args.args[0]
    # radicado desconocido: el payload sale tal cual
    conn.fetchval.return_value = None
    assert (await outbox._con_nivel_vivo(conn, {"tracking_number": "X", "nivel_nuevo": 1}))["nivel_nuevo"] == 1
