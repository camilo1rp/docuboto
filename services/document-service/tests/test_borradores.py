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

import pytest

from tests.conftest import TENANT_HEADERS, make_radicado_record

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
async def test_aprobar_borrador(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    # _require_editable (get) → set_estado (RETURNING aprobado)
    mock_conn.fetchrow.side_effect = [
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
async def test_el_padre_por_encima_del_clearance_no_aporta_contexto(client_with_conn) -> None:
    """El gate va en el WHERE, y su efecto es indistinguible de «no existe».

    Sin él, crear un borrador contra un radicado reservado escribiría su asunto
    dentro del cuerpo de un documento legible por cualquiera: la combinación
    sería una vía de exfiltración.
    """
    client, mock_conn = client_with_conn
    pid = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        {"is_root": False, "max_level": 1},
        _plantilla_row(id=pid, cuerpo="Sobre *RAD_E*: *ASUNTO*"),
        None,  # el WHERE nivel_seguridad <= 1 no devuelve fila
        _borrador_row(plantilla_id=pid, cuerpo="Sobre : "),
    ]
    resp = await client.post(
        "/api/v1/borradores",
        json={
            "doc_type": "S",
            "subject": "Resp.",
            "plantilla_id": str(pid),
            "radicado_padre_id": str(uuid.uuid4()),
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert set(resp.json()["faltantes"]) == {"RAD_E", "ASUNTO"}
    cuerpo = mock_conn.fetchrow.await_args_list[-1].args[4]
    assert "Petición" not in cuerpo

    # Y esto es lo que de verdad prueba el gate. Con un mock, quien decide que
    # no hay fila es el mock, no el `WHERE`: borrar el filtro dejaría el test en
    # verde. Así que se inspecciona el SQL **enviado** y su parámetro.
    consulta_padre = mock_conn.fetchrow.await_args_list[2]
    sql = " ".join(consulta_padre.args[0].split())
    assert "nivel_seguridad <= $2" in sql, sql
    assert consulta_padre.args[2] == 1


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
