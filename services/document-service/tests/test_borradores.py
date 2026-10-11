# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de plantillas y borradores de Salida (paridad legado)."""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.clients import signature_client as signature_client_mod
from tests.conftest import TENANT_HEADERS, make_radicado_record


async def _sin_circuito(borrador_id: str, tenant_slug: str) -> dict:
    """Doble de `signature_client.get_estado_circuito`: no hay circuito abierto."""
    return {"pendiente": False, "completo": False, "solicitud_id": None}


@pytest.fixture(autouse=True)
def _circuito_sin_abrir(monkeypatch) -> None:
    """`radicar` (como `aprobar`) consulta a signature-service si hay un circuito
    abierto (E23, RF-PRO-09) y responde 503 `circuito_no_verificable` si no puede
    saberlo —fail-closed, correcto—. Sin este doble la consulta sale de verdad, no
    hay signature-service en el carril unitario y los tests de radicación recibían
    503 en vez de lo que miden. Los tests del gate (`test_circuito_ata.py`) y
    `test_aprobar_borrador` sobrescriben este valor por su cuenta."""
    monkeypatch.setattr(signature_client_mod, "get_estado_circuito", _sin_circuito)

@pytest.fixture(autouse=True)
def _datos_minimos_derivados(monkeypatch, request) -> None:
    """Ac. AGN 060/2001 §5.2 (AREA-09-02): `radicar` deriva tipo documental y
    dependencias del borrador con consultas propias. Los mocks de este módulo
    contestan `True` a cualquier `fetchval`, que no es un tipo documental: se
    sustituye SOLO esa derivación para que los tests midan lo suyo (el orden,
    el render, la tenencia). La derivación real se prueba en
    `test_datos_minimos_ac060.py` y contra Postgres en
    `integration/test_datos_minimos_integration.py`; el test que opta por la
    real lo marca con `@pytest.mark.derivacion_real`."""
    if request.node.get_closest_marker("derivacion_real"):
        return
    from app.services import borrador_service as svc

    async def _derivados(self, row, registered_by, datos=None):
        return "Oficio", 1, 1

    monkeypatch.setattr(svc.BorradorService, "_datos_minimos_del_borrador", _derivados)

    # Con tipo documental, `create` consulta la plantilla de metadatos con un
    # `fetchrow` más, que descuadraba las secuencias de estos mocks.
    from app.repositories.metadata_repository import MetadataRepository

    async def _sin_plantilla(self, doc_class):
        return None

    monkeypatch.setattr(MetadataRepository, "get_active_template", _sin_plantilla)


NOW = datetime.now(tz=UTC)


def _plantilla_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "nombre": "Oficio estándar",
        "tipo_documental": "Oficio",
        "cuerpo": "Cordial saludo, ...",
        "activo": True,
        "created_at": NOW,
        "es_global": True,
        "creado_por": None,
    }
    base.update(kw)
    return base


#: Fila que devuelve `resolve_caller_clearance`. Crear un borrador la consulta
#: SIEMPRE (E23): el contexto de combinación lee el radicado padre y sin
#: clearance esa lectura sería fail-open.
_CLEARANCE_ROW = {"is_root": False, "max_level": 3}


def _borrador_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "doc_type": "S",
        "subject": "Respuesta a solicitud",
        "dest_dept": "Jurídica",
        "dest_dept_code": None,
        "cuerpo": "Texto",
        "plantilla_id": None,
        "estado": "borrador",
        "radicado_id": None,
        "creado_por": None,
        "created_at": NOW,
        "updated_at": NOW,
        "radicado_padre_id": None,
        "documento_file_id": None,
        "documento_sha256": None,
        "documento_formato": None,
        "combinacion": None,
        # Columnas reales de `borrador` (migración 030). Faltaban aquí, y un
        # fixture que omite una columna que existe es una afirmación falsa sobre
        # el colaborador: los gates que la miran pasaban por caminos que en
        # producción no existen.
        "version": 1,
        "cuerpo_sha256": None,
        "faltantes": [],
        "nivel_seguridad": 1,
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_crear_plantilla(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = _plantilla_row()
    resp = await client.post(
        "/api/v1/plantillas",
        json={
            "nombre": "Oficio estándar",
            "cuerpo": "Cordial saludo, ...",
            "tipo_documental": "Oficio",
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["nombre"] == "Oficio estándar"


@pytest.mark.asyncio
async def test_crear_borrador_desde_plantilla(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    pid = uuid.uuid4()
    # clearance → get_plantilla (cuerpo) → insert_borrador
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _plantilla_row(id=pid, cuerpo="Cuerpo de plantilla"),
        {"codigo": None},  # dependencia productora del llamante (E23)
        _borrador_row(plantilla_id=pid, cuerpo="Cuerpo de plantilla"),
    ]
    resp = await client.post(
        "/api/v1/borradores",
        json={"doc_type": "S", "subject": "Resp.", "plantilla_id": str(pid)},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["estado"] == "borrador"


@pytest.mark.asyncio
async def test_editar_borrador_radicado_409(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = _borrador_row(estado="radicado")
    resp = await client.patch(
        f"/api/v1/borradores/{uuid.uuid4()}",
        json={"subject": "Nuevo"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "borrador_radicado"


@pytest.mark.asyncio
async def test_aprobar_borrador(client_with_conn, monkeypatch) -> None:
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    # Aprobar consulta a signature-service si hay un circuito abierto (E23,
    # RF-PRO-09). Sin este doble la consulta saldría de verdad y daría 503:
    # fail-closed, que es lo correcto, pero no es lo que mide este test.
    monkeypatch.setattr(
        signature_client_mod,
        "get_estado_circuito",
        _sin_circuito,
    )
    # _require_editable (get) → set_estado (RETURNING aprobado)
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _borrador_row(id=bid, estado="borrador"),
        _borrador_row(id=bid, estado="aprobado"),
    ]
    resp = await client.post(f"/api/v1/borradores/{bid}/aprobar", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["estado"] == "aprobado"


# ---------------------------------------------------------------------------
# D-11: comprobaciones de autorización (PERM_RADI / USUA_PERM_ADMIN)
# ---------------------------------------------------------------------------

_USER = str(uuid.uuid4())

# -- Plantillas: USUA_PERM_ADMIN (crear/borrar) y PERM_RADI (listar) --------


@pytest.mark.asyncio
async def test_crear_plantilla_sin_permiso_403(client_with_conn) -> None:
    """USUA_PERM_ADMIN requerido para crear plantilla; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        "/api/v1/plantillas",
        json={"nombre": "Oficio", "cuerpo": "...", "tipo_documental": "Oficio"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_crear_plantilla_con_permiso_201(client_with_conn) -> None:
    """USUA_PERM_ADMIN concedido → crear plantilla devuelve 201."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _plantilla_row()
    resp = await client.post(
        "/api/v1/plantillas",
        json={"nombre": "Oficio estándar", "cuerpo": "Cordial saludo", "tipo_documental": "Oficio"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_listar_plantillas_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI requerido para listar plantillas; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get(
        "/api/v1/plantillas",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_listar_plantillas_con_permiso_200(client_with_conn) -> None:
    """PERM_RADI concedido → listar plantillas devuelve 200."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    # Desde E23 Inc.5 el listado consulta primero las URD del llamante para
    # recortar por dependencia (RF-PRO-06).
    mock_conn.fetch.side_effect = [[{"depe_id": 100}], [_plantilla_row()]]
    resp = await client.get(
        "/api/v1/plantillas",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_borrar_plantilla_sin_permiso_403(client_with_conn) -> None:
    """USUA_PERM_ADMIN requerido para borrar plantilla; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.delete(
        f"/api/v1/plantillas/{uuid.uuid4()}",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_borrar_plantilla_con_permiso_204(client_with_conn) -> None:
    """USUA_PERM_ADMIN concedido → borrar plantilla devuelve 204."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.execute.return_value = "DELETE 1"
    resp = await client.delete(
        f"/api/v1/plantillas/{uuid.uuid4()}",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 204


# -- Borradores: PERM_RADI (todas las operaciones) ---------------------------


@pytest.mark.asyncio
async def test_crear_borrador_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI requerido para crear borrador; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        "/api/v1/borradores",
        json={"doc_type": "S", "subject": "Prueba"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_listar_borradores_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI requerido para listar borradores; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get(
        "/api/v1/borradores",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_get_borrador_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI requerido para consultar borrador; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.get(
        f"/api/v1/borradores/{uuid.uuid4()}",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_editar_borrador_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI requerido para editar borrador; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.patch(
        f"/api/v1/borradores/{uuid.uuid4()}",
        json={"subject": "Nuevo"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_aprobar_borrador_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI requerido para aprobar borrador; sin permiso → 403."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        f"/api/v1/borradores/{uuid.uuid4()}/aprobar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_radicar_borrador_sin_permiso_403(client_with_conn) -> None:
    """PERM_RADI requerido para radicar; sin permiso → 403 (sin asignar tracking_number)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.post(
        f"/api/v1/borradores/{uuid.uuid4()}/radicar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_radicar_borrador_con_permiso_201(client_with_conn) -> None:
    """PERM_RADI concedido → radicar borrador asigna tracking_number oficial y devuelve 201."""
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    rid = uuid.uuid4()
    radicado = make_radicado_record(doc_id=rid, doc_type="S", tracking_number="2024-DEMO-S-000001")
    # fetchval covers: check_permission (True) + next_sequence (True=1) + audit hash (True).
    mock_conn.fetchval.return_value = True
    mock_conn.fetch.return_value = []  # _get_anexos
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,                                            # resolve_caller_clearance
        _borrador_row(id=bid, doc_type="S", estado="aprobado", cuerpo=None),  # get_borrador
        {"code": "DEMO"},                                          # get_tenant_code
        radicado,                                                  # repo.create
        _borrador_row(id=bid, doc_type="S", estado="radicado", radicado_id=rid),  # set_estado
    ]
    resp = await client.post(
        f"/api/v1/borradores/{bid}/radicar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 201
    assert "tracking_number" in resp.json()


@pytest.mark.asyncio
async def test_radicar_un_borrador_sin_aprobar_es_409(client_with_conn) -> None:
    """La aprobación era un control que solo vivía en el navegador.

    El ciclo documentado es crear → editar → aprobar → radicar, y la pantalla de
    borradores lo respeta (`canRadicar = estado === "aprobado"`). El servicio, en
    cambio, solo comprobaba que no estuviera YA radicado: un `POST` directo a
    esta ruta saltaba la aprobación y consumía el número igualmente. Un control
    que vive solo en el cliente no es un control — el cliente es la parte hostil
    (D-05).

    Se comprueba que **no se asigna número**: el daño de este hueco no es el
    estado de una fila, es un número de radicado inmutable consumido por un
    documento que nadie aprobó.
    """
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    mock_conn.fetchval.return_value = True  # permiso concedido: no es lo que se prueba
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _borrador_row(id=bid, doc_type="S", estado="borrador"),
    ]

    resp = await client.post(
        f"/api/v1/borradores/{bid}/radicar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "borrador_sin_aprobar"
    # Nada de la secuencia de radicación llegó a ejecutarse: solo la resolución
    # del clearance y la lectura del borrador.
    assert mock_conn.fetchrow.await_count == 2


# ── E23 Inc.1: vocabulario, validación y combinación ────────────────────────


@pytest.mark.asyncio
async def test_el_vocabulario_se_puede_consultar(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    resp = await client.get("/api/v1/plantillas/campos", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    nombres = {c["nombre"] for c in resp.json()["campos"]}
    assert {"NOM_R", "RAD_E", "F_HOY", "RAD_S"} <= nombres


@pytest.mark.asyncio
async def test_campos_no_se_confunde_con_un_id_de_plantilla(client_with_conn) -> None:
    """FastAPI casa por orden de declaración.

    Si `/plantillas/campos` quedara declarada DESPUÉS de `/plantillas/{id}`,
    esta petición devolvería un 422 de UUID inválido en vez del vocabulario, y
    el fallo solo aparecería al abrir la pantalla.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    resp = await client.get("/api/v1/plantillas/campos", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert "campos" in resp.json()


@pytest.mark.asyncio
async def test_una_plantilla_con_marcador_inventado_no_se_guarda(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    resp = await client.post(
        "/api/v1/plantillas",
        json={"nombre": "Mala", "cuerpo": "Señor *NOM_INVENTADO*, ..."},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422
    detalle = resp.json()["detail"]
    assert detalle["error"] == "marcador_desconocido"
    # La LISTA, no un «no válido» a secas: sin ella hay que buscarlo a ojo en un
    # cuerpo de varias páginas, y el camino corto es borrar marcadores buenos.
    assert detalle["marcadores"] == ["NOM_INVENTADO"]
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_una_plantilla_con_marcadores_validos_si_se_guarda(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _plantilla_row(cuerpo="Señor *NOM_P*: *ASUNTO*")
    resp = await client.post(
        "/api/v1/plantillas",
        json={"nombre": "Buena", "cuerpo": "Señor *NOM_P*: *ASUNTO*"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_crear_desde_plantilla_combina_y_reporta_faltantes(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    pid = uuid.uuid4()
    padre = uuid.uuid4()
    registered = datetime(2026, 3, 4, tzinfo=UTC)
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _plantilla_row(id=pid, cuerpo="Señor *NOM_R*, sobre *RAD_E*. Tel: *TEL_R*"),
        {  # radicado padre
            "id": padre,
            "tracking_number": "2026-DEMO-E-000001",
            "subject": "Petición",
            "pages": 2,
            "registered_at": registered,
            "sender_name": None,
            "sender_entity": None,
        },
        {  # actor remitente (copia congelada), sin teléfono
            "nombre": "Ana Ruiz",
            "entidad": None,
            "email": None,
            "telefono": None,
            "direccion": "Calle 1",
            "municipio": "Bogotá",
            "departamento": "Bogotá D.C.",
            "numero_documento": "1000",
        },
        {"codigo": None},  # dependencia productora del llamante (E23)
        _borrador_row(plantilla_id=pid, cuerpo="Señor Ana Ruiz, sobre 2026-DEMO-E-000001. Tel: "),
    ]
    mock_conn.fetch.return_value = []
    resp = await client.post(
        "/api/v1/borradores",
        json={
            "doc_type": "S",
            "subject": "Resp.",
            "plantilla_id": str(pid),
            "radicado_padre_id": str(padre),
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    # El teléfono no estaba: se vacía en el texto Y se declara.
    assert resp.json()["faltantes"] == ["TEL_R"]
    # Y lo que se INSERTA es el cuerpo ya combinado, no la plantilla literal.
    insert = mock_conn.fetchrow.await_args_list[-1]
    assert "*NOM_R*" not in insert.args[4]
    assert "Ana Ruiz" in insert.args[4]


@pytest.mark.asyncio
async def test_el_padre_por_encima_del_clearance_no_se_puede_responder(client_with_conn) -> None:
    """El gate va en el WHERE, y su efecto es indistinguible de «no existe».

    Sin él, crear un borrador contra un radicado reservado escribiría su asunto
    dentro del cuerpo de un documento legible por cualquiera, y (AREA-01-N2) el
    borrador no tendría nivel que lo protegiera. Antes devolvía 201 con el
    contexto vacío; ahora es 404 neutro: un borrador sobre un padre que el
    creador no alcanza ni él mismo podría releerlo.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.side_effect = [{"is_root": False, "max_level": 1}]
    mock_conn.fetchval.side_effect = [True, None]  # permiso; el WHERE nivel <= 1 no devuelve fila
    resp = await client.post(
        "/api/v1/borradores",
        json={
            "doc_type": "S",
            "subject": "Resp.",
            "radicado_padre_id": str(uuid.uuid4()),
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "not_found"

    # Con un mock quien decide que no hay fila es el mock: se inspecciona el SQL
    # ENVIADO y su parámetro.
    consulta = mock_conn.fetchval.await_args_list[-1]
    assert "nivel_seguridad <= $2" in " ".join(consulta.args[0].split()), consulta.args[0]
    assert consulta.args[2] == 1


@pytest.mark.asyncio
async def test_el_borrador_con_padre_visible_hereda_su_nivel(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.side_effect = [
        {"is_root": False, "max_level": 3},
        _borrador_row(nivel_seguridad=3),
    ]
    mock_conn.fetchval.return_value = 3  # nivel del padre
    resp = await client.post(
        "/api/v1/borradores",
        json={"doc_type": "S", "subject": "Resp.", "radicado_padre_id": str(uuid.uuid4())},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    insert = mock_conn.fetchrow.await_args_list[-1]
    assert 3 in insert.args, "el INSERT debe llevar el nivel heredado del padre"
    assert resp.json()["nivel_seguridad"] == 3


@pytest.mark.asyncio
async def test_combinar_plantilla_devuelve_texto_resuelto(client_with_conn) -> None:
    """La pantalla pide el cuerpo YA combinado, no la plantilla cruda.

    Si la interfaz precargara la plantilla literal en el editor y la enviara
    tal cual, la combinación del servidor nunca se ejecutaría: el `cuerpo`
    vendría lleno y la rama que combina no se tocaría nunca. Es el patrón del
    dato cargado que nadie lee, y aquí acabaría con `*NOM_R*` impreso en el
    oficio del ciudadano.
    """
    client, mock_conn = client_with_conn
    pid = uuid.uuid4()
    padre = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _plantilla_row(id=pid, cuerpo="Señor *NOM_R*: *ASUNTO*. Tel *TEL_R*"),
        {
            "id": padre,
            "tracking_number": "2026-DEMO-E-000009",
            "subject": "Queja",
            "pages": 1,
            "registered_at": datetime(2026, 5, 5, tzinfo=UTC),
            "sender_name": None,
            "sender_entity": None,
        },
        {
            "nombre": "Luis Páez",
            "entidad": None,
            "email": None,
            "telefono": None,
            "direccion": None,
            "municipio": None,
            "departamento": None,
            "numero_documento": None,
        },
        {"codigo": None},  # dependencia productora del llamante (E23)
    ]
    mock_conn.fetch.return_value = []
    resp = await client.post(
        f"/api/v1/plantillas/{pid}/combinar",
        json={"radicado_padre_id": str(padre)},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    cuerpo = resp.json()["cuerpo"]
    assert cuerpo == "Señor Luis Páez: Queja. Tel "
    assert resp.json()["faltantes"] == ["TEL_R"]


@pytest.mark.asyncio
async def test_combinar_una_plantilla_inexistente_da_404(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.side_effect = [_CLEARANCE_ROW, None]
    resp = await client.post(
        f"/api/v1/plantillas/{uuid.uuid4()}/combinar", json={}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 404


# ── E23 Inc.3: el documento entra al archivo ────────────────────────────────


def _radicar_side_effects(bid, rid, radicado, *, cuerpo, combinacion=None):
    """Secuencia de `fetchrow` de una radicación desde borrador."""
    return [
        _CLEARANCE_ROW,
        _borrador_row(
            id=bid, doc_type="S", estado="aprobado", cuerpo=cuerpo, combinacion=combinacion
        ),
        {"code": "DEMO"},
        radicado,
        _borrador_row(id=bid, doc_type="S", estado="radicado", radicado_id=rid),
    ]


@pytest.fixture
def render_falso(monkeypatch):
    """Sustituye las dos llamadas a storage-service y REGISTRA el orden.

    Registrar el orden es el punto: lo que hay que probar de este incremento no
    es que se genere un PDF —eso lo prueba storage-service contra veraPDF real—
    sino que nada irreversible ocurra antes de que el documento exista.
    """
    from app.services import borrador_service as svc

    registro = {"renders": [], "subidas": 0, "fallo": None}

    async def _render(*, tenant_slug, cuerpo_html, pagina, perfil=None):
        registro["renders"].append({"html": cuerpo_html, "pagina": pagina})
        if registro["fallo"] is not None:
            raise registro["fallo"]
        return SimpleNamespace(
            contenido=b"%PDF-1.7 fake", formato="PDF/A-2B", herramienta="weasyprint"
        )

    async def _upload(*, tenant_slug, filename, mime_type, data):
        registro["subidas"] += 1
        return SimpleNamespace(file_id=uuid.uuid4(), sha256="a" * 64, file_size=len(data))

    monkeypatch.setattr(svc, "render_documento", _render)
    monkeypatch.setattr(svc, "upload_artefacto", _upload)
    return registro


@pytest.mark.asyncio
async def test_un_fallo_de_render_no_consume_numero(client_with_conn, render_falso) -> None:
    """El criterio de hecho del incremento.

    Un número de radicado es inmutable por ley: no se devuelve. Un hueco en la
    secuencia es permanente y hay que explicárselo a un auditor. Por eso el
    render va ANTES de pedir el número, y por eso este test mira la secuencia,
    no el código de estado.
    """
    from app.clients.storage_client import RenderNoDisponibleError

    client, mock_conn = client_with_conn
    bid, rid = uuid.uuid4(), uuid.uuid4()
    radicado = make_radicado_record(doc_id=rid, doc_type="S", tracking_number="2024-DEMO-S-000001")
    render_falso["fallo"] = RenderNoDisponibleError("sin motor")
    mock_conn.fetchval.return_value = True
    mock_conn.fetch.return_value = []
    mock_conn.fetchrow.side_effect = _radicar_side_effects(
        bid, rid, radicado, cuerpo="<p>Cuerpo</p>"
    )

    resp = await client.post(
        f"/api/v1/borradores/{bid}/radicar", headers={**TENANT_HEADERS, "X-User-Id": _USER}
    )

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "render_no_disponible"
    # Y esto es lo que importa: la secuencia NO se tocó. `next_sequence` usa
    # fetchval; que no se llamara al `get_tenant_code` (3.er fetchrow) prueba
    # que la creación del radicado ni empezó.
    assert mock_conn.fetchrow.await_count == 2
    assert render_falso["subidas"] == 0


@pytest.mark.asyncio
async def test_sin_marcadores_diferidos_se_renderiza_una_sola_vez(
    client_with_conn, render_falso
) -> None:
    # Renderizar dos veces cuando el cuerpo no lleva `*RAD_S*` sería trabajo
    # tirado, y el segundo render ocurriría YA con el número consumido: menos
    # pasos irreversibles es menos superficie de fallo.
    client, mock_conn = client_with_conn
    bid, rid = uuid.uuid4(), uuid.uuid4()
    radicado = make_radicado_record(doc_id=rid, doc_type="S", tracking_number="2024-DEMO-S-000001")
    mock_conn.fetchval.return_value = True
    mock_conn.fetch.return_value = []
    mock_conn.fetchrow.side_effect = _radicar_side_effects(
        bid, rid, radicado, cuerpo="<p>Sin numero propio</p>"
    )

    resp = await client.post(
        f"/api/v1/borradores/{bid}/radicar", headers={**TENANT_HEADERS, "X-User-Id": _USER}
    )
    assert resp.status_code == 201
    assert len(render_falso["renders"]) == 1
    assert render_falso["subidas"] == 1


@pytest.mark.asyncio
async def test_con_RAD_S_hay_segunda_pasada_con_el_numero(
    client_with_conn, render_falso
) -> None:
    """El precio de que el documento lleve su propio número impreso.

    El número no existe hasta que se consume, y se consume al final; así que
    `*RAD_S*` obliga a renderizar otra vez. La primera pasada lo deja INTACTO
    —si lo vaciara, la segunda no tendría qué sustituir—.
    """
    client, mock_conn = client_with_conn
    bid, rid = uuid.uuid4(), uuid.uuid4()
    radicado = make_radicado_record(doc_id=rid, doc_type="S", tracking_number="2024-DEMO-S-000001")
    mock_conn.fetchval.return_value = True
    mock_conn.fetch.return_value = []
    mock_conn.fetchrow.side_effect = _radicar_side_effects(
        bid, rid, radicado, cuerpo="<p>Radicado *RAD_S* del *F_RAD_S*</p>"
    )

    resp = await client.post(
        f"/api/v1/borradores/{bid}/radicar", headers={**TENANT_HEADERS, "X-User-Id": _USER}
    )
    assert resp.status_code == 201
    assert len(render_falso["renders"]) == 2
    primero, segundo = render_falso["renders"]
    assert "*RAD_S*" in primero["html"], "la 1.a pasada debe DEJAR el diferido intacto"
    assert "2024-DEMO-S-000001" in segundo["html"]
    assert segundo["pagina"]["radicado"] == "2024-DEMO-S-000001"


@pytest.mark.asyncio
async def test_el_documento_se_anexa_como_principal(client_with_conn, render_falso) -> None:
    # `es_principal` distingue el documento que ES la comunicación de los
    # adjuntos que la acompañan: el índice electrónico y el envío postal lo
    # necesitan para saber qué pieza despachar.
    client, mock_conn = client_with_conn
    bid, rid = uuid.uuid4(), uuid.uuid4()
    radicado = make_radicado_record(doc_id=rid, doc_type="S", tracking_number="2024-DEMO-S-000001")
    mock_conn.fetchval.return_value = True
    mock_conn.fetch.return_value = []
    mock_conn.fetchrow.side_effect = _radicar_side_effects(
        bid, rid, radicado, cuerpo="<p>Cuerpo</p>"
    )

    resp = await client.post(
        f"/api/v1/borradores/{bid}/radicar", headers={**TENANT_HEADERS, "X-User-Id": _USER}
    )
    assert resp.status_code == 201
    sentencias = [c.args[0] for c in mock_conn.execute.await_args_list]
    anexo = [q for q in sentencias if "INSERT INTO anexos" in q]
    assert anexo, "el documento producido no se anexó"
    assert "documento_principal" in anexo[0]
    congelado = [q for q in sentencias if "combinacion=$5::jsonb" in q]
    assert congelado, "el contexto de combinación no se congeló"


@pytest.mark.asyncio
async def test_el_contexto_congelado_manda_sobre_el_actual(
    client_with_conn, render_falso
) -> None:
    """Mismo principio que la dirección de un envío: el dato pertenece al ACTO.

    Si el borrador ya trae contexto congelado, se usa ESE y no se vuelve a
    resolver contra las fuentes vivas. Sin esta regla, cambiar mañana el nombre
    de la entidad reescribiría la explicación de un documento ya producido.
    """
    client, mock_conn = client_with_conn
    bid, rid = uuid.uuid4(), uuid.uuid4()
    radicado = make_radicado_record(doc_id=rid, doc_type="S", tracking_number="2024-DEMO-S-000001")
    mock_conn.fetchval.return_value = True
    mock_conn.fetch.return_value = []
    mock_conn.fetchrow.side_effect = _radicar_side_effects(
        bid,
        rid,
        radicado,
        cuerpo="<p>Para *NOM_P* de *NOM_E*</p>",
        combinacion=json.dumps({"NOM_P": "Ana Ruiz", "NOM_E": "Nombre de entonces"}),
    )

    resp = await client.post(
        f"/api/v1/borradores/{bid}/radicar", headers={**TENANT_HEADERS, "X-User-Id": _USER}
    )
    assert resp.status_code == 201
    html = render_falso["renders"][0]["html"]
    assert "Ana Ruiz" in html
    assert "Nombre de entonces" in html


@pytest.mark.asyncio
async def test_previsualizar_devuelve_el_pdf_y_declara_los_faltantes(
    client_with_conn, render_falso
) -> None:
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _borrador_row(
            id=bid,
            cuerpo="<p>Para *NOM_P*, tel *TEL_P*</p>",
            combinacion=json.dumps({"NOM_P": "Ana Ruiz"}),
        ),
    ]
    resp = await client.post(
        f"/api/v1/borradores/{bid}/previsualizar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    # Sin esta cabecera la pantalla enseñaría un documento con huecos y nadie
    # sabría que son huecos.
    assert resp.headers["X-Faltantes"] == "TEL_P"


@pytest.mark.asyncio
async def test_previsualizar_no_persiste_nada(client_with_conn, render_falso) -> None:
    # Es lo que permite ver ANTES de gastar el número: si escribiera, dejaría de
    # ser una previsualización.
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_CLEARANCE_ROW, _borrador_row(id=bid, cuerpo="<p>x</p>")]
    resp = await client.post(
        f"/api/v1/borradores/{bid}/previsualizar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    assert mock_conn.execute.await_count == 0
    assert render_falso["subidas"] == 0


@pytest.mark.asyncio
async def test_previsualizar_deja_RAD_S_a_la_vista(client_with_conn, render_falso) -> None:
    """Sustituirlo por un número de ejemplo enseñaría un documento que no es el
    que se va a producir — justo lo contrario de para qué sirve previsualizar."""
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _borrador_row(id=bid, cuerpo="<p>Radicado *RAD_S*</p>"),
    ]
    resp = await client.post(
        f"/api/v1/borradores/{bid}/previsualizar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    assert "*RAD_S*" in render_falso["renders"][0]["html"]
    assert "X-Faltantes" in resp.headers
    assert "RAD_S" not in resp.headers["X-Faltantes"].split(",")


@pytest.mark.asyncio
async def test_previsualizar_sin_renderizador_es_503(client_with_conn, render_falso) -> None:
    from app.clients.storage_client import RenderNoDisponibleError

    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    render_falso["fallo"] = RenderNoDisponibleError("sin motor")
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_CLEARANCE_ROW, _borrador_row(id=bid, cuerpo="<p>x</p>")]
    resp = await client.post(
        f"/api/v1/borradores/{bid}/previsualizar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "render_no_disponible"


# ── E23 Inc.5: alcance de plantillas por dependencia ────────────────────────


@pytest.mark.asyncio
async def test_ver_todas_las_plantillas_exige_administracion(client_with_conn) -> None:
    """`?todas=true` no basta: hace falta el permiso.

    Un recorte que el propio llamante puede apagar con un parámetro de consulta
    no es un recorte. El gate se comprueba en el handler y no subiendo el del
    endpoint, porque la lista normal la necesita cualquiera con `PERM_RADI`
    para redactar.
    """
    client, mock_conn = client_with_conn
    # 1.º fetchval: PERM_RADI del endpoint (concedido). 2.º: USUA_PERM_ADMIN (no).
    mock_conn.fetchval.side_effect = [True, None]
    resp = await client.get(
        "/api/v1/plantillas?todas=true",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    # Y no llegó a consultarse nada.
    mock_conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_un_administrador_ve_todas(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [True, True]
    mock_conn.fetch.return_value = [_plantilla_row()]
    resp = await client.get(
        "/api/v1/plantillas?todas=true",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    # Sin recorte: la consulta de URD del usuario NI SE HACE.
    consultas = [c.args[0] for c in mock_conn.fetch.await_args_list]
    assert not any("urd_usuaroldep" in q for q in consultas)


@pytest.mark.asyncio
async def test_el_listado_normal_SI_consulta_las_dependencias(client_with_conn) -> None:
    # Control positivo del anterior: si el recorte no se aplicara, el test de
    # arriba pasaría por casualidad.
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetch.side_effect = [[{"depe_id": 100}], [_plantilla_row()]]
    resp = await client.get(
        "/api/v1/plantillas",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    consultas = [c.args[0] for c in mock_conn.fetch.await_args_list]
    assert any("urd_usuaroldep" in q for q in consultas)
    # Y el SQL enviado lleva el recorte, no solo el parámetro.
    listado = [q for q in consultas if "FROM plantilla" in q]
    assert listado and "es_global OR EXISTS" in listado[0]


@pytest.mark.asyncio
async def test_fijar_alcance_es_de_administracion(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None
    resp = await client.put(
        f"/api/v1/plantillas/{uuid.uuid4()}/alcance",
        json={"es_global": False, "dependencias": [100]},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_poner_global_borra_las_dependencias(client_with_conn) -> None:
    # Mantenerlas guardadas dejaría una asignación invisible que reaparecería al
    # volver a restringir, y nadie recordaría de dónde salió.
    client, mock_conn = client_with_conn
    pid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _plantilla_row(id=pid, es_global=True)
    mock_conn.fetch.return_value = []
    resp = await client.put(
        f"/api/v1/plantillas/{pid}/alcance",
        json={"es_global": True, "dependencias": [100, 200]},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )
    assert resp.status_code == 200
    borrados = [
        c for c in mock_conn.execute.await_args_list
        if "DELETE FROM plantilla_dependencia" in c.args[0]
    ]
    assert borrados, "poner global tiene que limpiar la asignación"
    insertados = [
        c for c in mock_conn.execute.await_args_list
        if "INSERT INTO plantilla_dependencia" in c.args[0]
    ]
    assert not insertados, "ninguna dependencia debe insertarse si es global"


# --- Dependencia productora y funcionario (E23, revisión por navegador) -------


@pytest.mark.asyncio
async def test_la_dependencia_del_pie_es_la_que_produce_no_la_de_destino(
    client_with_deps,
) -> None:
    """`*DEPENDENCIA*` es la del funcionario que firma, no la del destinatario.

    Los tres marcadores del grupo Dependencia se resolvían desde
    `dest_dept_code` —la dependencia DESTINO del documento—, cuando el
    vocabulario los declara «dependencia productora». En una Salida eso es la
    unidad que recibe, así que el pie de firma del oficio salía con el nombre
    de otra dependencia. Un hueco se ve al releer; un valor equivocado no: el
    oficio se firma y se envía con él.
    """
    client, mock_conn, fake = client_with_deps
    pid = uuid.uuid4()
    fake.dependencias = {
        902: {"codigo": 902, "nombre": "Oficina Jurídica", "sigla": "JUR"},
        999: {"codigo": 999, "nombre": "Ventanilla Única", "sigla": "VUR"},
    }
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _plantilla_row(id=pid, cuerpo="Firma: *DEPENDENCIA* (*DEP_SIGLA*)"),
        {"codigo": 902},  # dependencia principal del llamante
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        f"/api/v1/plantillas/{pid}/combinar",
        json={"dest_dept_code": 999},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 200
    assert resp.json()["cuerpo"] == "Firma: Oficina Jurídica (JUR)"
    # Y la de destino no se consulta siquiera: si apareciera aquí, el valor
    # correcto de arriba podría estar saliendo por casualidad del orden.
    assert [codigo for _, codigo in fake.dep_calls] == [902]


@pytest.mark.asyncio
async def test_sin_dependencia_establecida_el_campo_se_declara_faltante(
    client_with_deps,
) -> None:
    """Anti-vacuidad: no se inventa una dependencia cuando no se sabe cuál es."""
    client, mock_conn, fake = client_with_deps
    pid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _plantilla_row(id=pid, cuerpo="Firma: *DEPENDENCIA*"),
        {"codigo": None},  # el usuario no tiene dependencia activa ni principal
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        f"/api/v1/plantillas/{pid}/combinar",
        json={"dest_dept_code": 999},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 200
    assert resp.json()["cuerpo"] == "Firma: "
    assert resp.json()["faltantes"] == ["DEPENDENCIA"]
    assert fake.dep_calls == []


@pytest.mark.asyncio
async def test_el_login_del_funcionario_llega_a_la_combinacion(client_with_conn) -> None:
    """`funcionario` era un parámetro que NINGÚN router pasaba.

    Estaba escrito en el servicio, con su test de unidad, y ningún camino
    —previsualizar, crear ni radicar— lo rellenaba: `*LOGIN*` salía vacío
    siempre. La cabecera la inyecta el gateway desde el token ya validado.
    """
    client, mock_conn = client_with_conn
    pid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _plantilla_row(id=pid, cuerpo="Proyectó: *LOGIN*"),
        {"codigo": None},
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        f"/api/v1/plantillas/{pid}/combinar",
        json={},
        headers={**TENANT_HEADERS, "X-User-Id": _USER, "X-Username": "JEFE"},
    )

    assert resp.status_code == 200
    assert resp.json()["cuerpo"] == "Proyectó: JEFE"
    assert resp.json()["faltantes"] == []


@pytest.mark.asyncio
async def test_la_previsualizacion_recuerda_los_huecos_de_la_combinacion(
    client_with_conn, render_falso
) -> None:
    """El cuerpo guardado ya está combinado: los huecos no son marcadores.

    `X-Faltantes` salía SIEMPRE vacía en la previsualización, y no por un fallo
    de la cabecera: el cuerpo se guarda ya combinado —donde no había dato hay
    una cadena vacía, no un `*NOM_R*`—, así que volver a combinarlo no encuentra
    nada que reportar. El aviso existía solo en la respuesta que creó el
    borrador y se perdía; a partir de ahí, el hueco del oficio era
    indistinguible de un espacio escrito a propósito.
    """
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _borrador_row(
            id=bid,
            cuerpo="<p>Señor(a) , tel *TEL_P*</p>",
            combinacion=json.dumps({"NOM_P": ""}),
            faltantes=["NOM_P", "ASUNTO"],
        ),
    ]

    resp = await client.post(
        f"/api/v1/borradores/{bid}/previsualizar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 200
    # Los guardados primero, y los que siguen sin resolver AHORA detrás: el
    # marcador que aún está en el texto también es un hueco.
    assert resp.headers["X-Faltantes"] == "NOM_P,ASUNTO,TEL_P"


@pytest.mark.asyncio
async def test_un_borrador_sin_huecos_no_inventa_el_aviso(client_with_conn, render_falso) -> None:
    # Anti-vacuidad: si la cabecera se llenara siempre, el test de arriba pasaría
    # sin probar que los guardados llegan.
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _borrador_row(id=bid, cuerpo="<p>Texto completo</p>", faltantes=[]),
    ]

    resp = await client.post(
        f"/api/v1/borradores/{bid}/previsualizar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 200
    assert resp.headers["X-Faltantes"] == ""


@pytest.mark.asyncio
async def test_los_huecos_se_calculan_aunque_la_pantalla_mande_el_cuerpo(
    client_with_conn,
) -> None:
    """La pantalla manda el cuerpo YA combinado, y aun así hay que declarar los huecos.

    La rama que combina solo corría con `cuerpo is None`. Pero la pantalla pide
    el texto resuelto con `/plantillas/{id}/combinar` para enseñarlo en el
    editor y lo envía tal cual: la rama no se ejecutaba nunca y la lista de
    campos sin dato se guardaba vacía. El aviso desaparecía justo antes de
    hacer falta.

    Y no vale aceptar la lista que mande el cliente: es un dato que el cliente
    elige y acaba impreso en un documento que da fe. El servidor rehace la misma
    combinación y saca los faltantes de ahí.
    """
    client, mock_conn = client_with_conn
    pid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _plantilla_row(id=pid, cuerpo="Señor *NOM_R*, sobre *ASUNTO*"),
        {"codigo": None},
        _borrador_row(plantilla_id=pid, cuerpo="Señor Pedro Gómez, sobre nómina"),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.post(
        "/api/v1/borradores",
        json={
            "doc_type": "S",
            "subject": "Resp.",
            "plantilla_id": str(pid),
            # El cuerpo llega puesto y EDITADO a mano: es lo que se guarda.
            "cuerpo": "Señor Pedro Gómez, sobre nómina",
        },
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 201
    assert set(resp.json()["faltantes"]) == {"NOM_R", "ASUNTO"}
    # Lo que se INSERTA es el cuerpo del cliente, no el recombinado: la persona
    # ya lo había rellenado a mano y pisarlo sería perder su trabajo.
    insert = mock_conn.fetchrow.await_args_list[-1]
    assert insert.args[4] == "Señor Pedro Gómez, sobre nómina"
    # Y los faltantes viajan al INSERT, que es lo que los hace sobrevivir.
    assert list(insert.args[8]) == ["NOM_R", "ASUNTO"]


@pytest.mark.asyncio
async def test_sin_plantilla_no_se_calculan_huecos(client_with_conn) -> None:
    # Anti-vacuidad: un borrador redactado a mano no tiene marcadores que
    # resolver, y no debe arrastrar avisos ni hacer lecturas de más.
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.side_effect = [_CLEARANCE_ROW, _borrador_row(cuerpo="Texto libre")]

    resp = await client.post(
        "/api/v1/borradores",
        json={"doc_type": "I", "subject": "Memo", "cuerpo": "Texto libre"},
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 201
    assert resp.json()["faltantes"] == []


# --- Devolver a borrador (E23, RF-PRO-10) -------------------------------------


@pytest.mark.asyncio
async def test_un_aprobado_vuelve_a_borrador_para_poder_corregirlo(client_with_conn) -> None:
    """El ciclo era de una sola dirección y el texto quedaba congelado al aprobar.

    `borrador → aprobado → radicado`, sin vuelta: la única salida de un
    aprobado era radicar. Eso dejaba inalcanzable la mitad del trámite que se
    pidió — un circuito de firmas «a veces se revierte por cambios en el
    documento y se necesitan de nuevo las firmas»— porque no había forma de
    introducir esos cambios.
    """
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _borrador_row(id=bid, estado="aprobado"),
        _borrador_row(id=bid, estado="borrador"),
    ]

    resp = await client.post(
        f"/api/v1/borradores/{bid}/devolver",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 200
    assert resp.json()["estado"] == "borrador"


@pytest.mark.asyncio
async def test_un_radicado_no_se_devuelve(client_with_conn) -> None:
    """Lo radicado tiene número inmutable: reabrirlo no es un acto que exista."""
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _borrador_row(id=bid, estado="radicado")

    resp = await client.post(
        f"/api/v1/borradores/{bid}/devolver",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "borrador_radicado"


@pytest.mark.asyncio
async def test_devolver_uno_que_ya_esta_en_borrador_es_409(client_with_conn) -> None:
    # Anti-vacuidad: si el endpoint pusiera 'borrador' sin mirar el estado, los
    # dos casos de arriba pasarían igual.
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _borrador_row(id=bid, estado="borrador")

    resp = await client.post(
        f"/api/v1/borradores/{bid}/devolver",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "estado_invalido"


@pytest.mark.asyncio
async def test_devolver_sin_permiso_403(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None

    resp = await client.post(
        f"/api/v1/borradores/{uuid.uuid4()}/devolver",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 403


# ---- AREA-09-02: datos mínimos del radicado (Ac. AGN 060/2001 §5.2) ----

@pytest.mark.asyncio
async def test_radicar_borrador_sin_datos_derivables_es_422_antes_del_numero(
    client_with_conn, monkeypatch
) -> None:
    """El cableado, no solo la pieza: `radicar` llama a la derivación y rechaza
    ANTES de `tracking_sequences` (el consecutivo no se devuelve)."""
    from app.services import borrador_service as svc

    async def _nada(self, row, registered_by, datos=None):
        return None, None, None

    monkeypatch.setattr(svc.BorradorService, "_datos_minimos_del_borrador", _nada)
    client, mock_conn = client_with_conn
    bid, rid = uuid.uuid4(), uuid.uuid4()
    mock_conn.fetchval.return_value = True
    mock_conn.fetch.return_value = []
    mock_conn.fetchrow.side_effect = _radicar_side_effects(
        bid, rid, make_radicado_record(doc_id=rid, doc_type="S"), cuerpo=None
    )
    resp = await client.post(
        f"/api/v1/borradores/{bid}/radicar", headers={**TENANT_HEADERS, "X-User-Id": _USER}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "datos_minimos_incompletos"


@pytest.mark.derivacion_real
@pytest.mark.asyncio
async def test_la_derivacion_del_borrador_usa_plantilla_dependencia_y_urd() -> None:
    from app.services.borrador_service import BorradorService

    padre = uuid.uuid4()
    conn = AsyncMock()

    async def _fetchval(query: str, *a: object):
        if "FROM plantilla" in query:
            return "Oficio"
        if "dest_dept_code FROM radicados" in query:
            return 20
        raise AssertionError(query)

    conn.fetchval.side_effect = _fetchval
    # Borrador anterior a la 046 (solo nombre): se resuelve por el criterio único.
    conn.fetchrow.return_value = {"codigo": 4, "candidatos": 1}
    conn.fetch.return_value = [{"depe_id": 9}, {"depe_id": 20}]
    svc = BorradorService(conn, "demo")
    row = {
        "doc_type": "S",
        "plantilla_id": uuid.uuid4(),
        "dest_dept": "Jurídica",
        "dest_dept_code": None,
        "radicado_padre_id": padre,
    }
    tipo, origen, destino = await svc._datos_minimos_del_borrador(row, uuid.uuid4())
    assert (tipo, origen, destino) == ("Oficio", 20, 4)  # el padre está en SU dependencia

    # Sin el padre en sus dependencias → la de menor código; sin URD → None.
    row["radicado_padre_id"] = None
    assert (await svc._datos_minimos_del_borrador(row, uuid.uuid4()))[1] == 9
    conn.fetch.return_value = []
    assert (await svc._datos_minimos_del_borrador(row, uuid.uuid4()))[1] is None
