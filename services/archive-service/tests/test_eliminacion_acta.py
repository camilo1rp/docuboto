# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El acta de eliminación — RF-TRD-07, fase 4 de 5.

**El acta es lo que sobrevive a lo eliminado.** Cuando la fase 5 borre los
bytes, será —junto con la lápida y el `audit_log`— la única constancia de qué se
destruyó. Lo que se prueba es lo que hace que sirva para eso:

* **El inventario va COMPLETO**, sin recortar por clearance: un acta a la que le
  faltan los clasificados no da fe de nada. Lo que se acota es **quién la lee**,
  y el umbral se DERIVA del contenido (el nivel más alto del inventario), nunca
  de un campo declarado.
* **Se genera una sola vez**: regenerarla produciría otro instante y otro SHA
  para una eliminación que ya tenía el suyo.
* **Su SHA-256 va al `audit_log`**, que es indeleble: un acta alterada
  saltándose el trigger seguiría delatándose contra ese número.
* **El sellado no bloquea** — y cuando falla, el acta queda con el motivo a la
  vista en vez de con un estado mudo.
* **El XML es canónico y determinista**: dos construcciones con los mismos datos
  dan la misma cadena byte a byte, o el SHA no significa nada.
"""

import hashlib
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.services.eliminacion_acta import construir_acta_xml
from tests.conftest import TENANT_HEADERS

LOTE = uuid.uuid4()
BASE = f"/api/v1/eliminacion/lotes/{LOTE}"
CUERPO = {"metodo_borrado": "Borrado seguro de los objetos y picado de soportes"}
_CLEARANCE = {"is_root": False, "max_level": 3}


def _lote_row(estado="avalado") -> dict:
    return {
        "id": LOTE,
        "codigo": "LOTE-2026-001",
        "estado": estado,
        "observaciones": None,
        "creado_por": uuid.uuid4(),
        "creado_at": datetime(2026, 8, 1, tzinfo=UTC),
        "publicado_at": datetime(2026, 8, 2, tzinfo=UTC),
        "publicado_hasta": date(2026, 8, 15),
        "acta_comite": "Acta 07 del Comité",
        "fecha_aval": date(2026, 8, 20),
        "acto_administrativo": "Resolución 512 de 2026",
        "avalado_por": uuid.uuid4(),
        "ejecutado_at": None,
        "anulado_motivo": None,
    }


def _item(*, code="EXP-2020-0001", nivel=1) -> dict:
    return {
        "expediente_id": uuid.uuid4(),
        "expediente_code": code,
        "expediente_nombre": "Contratos 2020",
        "trd_serie_code": "DEM-100",
        "disposition_code": "E",
        "fecha_vencimiento": date(2026, 1, 1),
        "fecha_extrema_inicial": date(2020, 1, 1),
        "fecha_extrema_final": date(2020, 12, 31),
        "nivel_seguridad": nivel,
        "radicados_incluidos": 12,
    }


def _acta_row(*, firma_estado="pendiente_firma", last_error=None) -> dict:
    xml = construir_acta_xml(
        lote=_lote_row(),
        items=[_item()],
        tenant_slug="demo",
        metodo_borrado=CUERPO["metodo_borrado"],
        observaciones=None,
        generado_por=str(uuid.uuid4()),
        generado_ts=datetime(2026, 8, 29, tzinfo=UTC),
    )
    return {
        "lote_id": LOTE,
        "acta_xml": xml,
        "acta_sha256": hashlib.sha256(xml.encode("utf-8")).hexdigest(),
        "metodo_borrado": CUERPO["metodo_borrado"],
        "observaciones": None,
        "generado_por": uuid.uuid4(),
        "generado_at": datetime(2026, 8, 29, tzinfo=UTC),
        "firma_estado": firma_estado,
        "firma_id": uuid.uuid4() if firma_estado == "firmado" else None,
        "minio_key_firmado": "k" if firma_estado == "firmado" else None,
        "xades_level": "XAdES-B" if firma_estado == "firmado" else None,
        "acreditado": False,
        "last_error": last_error,
        "firmado_at": datetime(2026, 8, 29, tzinfo=UTC) if firma_estado == "firmado" else None,
    }


class _Firmada:
    firma_id = uuid.uuid4()
    minio_key_firmado = "acta-eliminacion-x-firmado.xml"
    xades_level = "XAdES-B"
    acreditado = False


class _Outcome:
    def __init__(self, *, signed=None, seal_absent=False):
        self.signed = signed
        self.seal_absent = seal_absent


def _sellado(signed=None, seal_absent=False):
    """Doble del sellado. `sign_xml_outcome` vive en `app.clients.
    signature_client` y se importa DENTRO del método, así que se parchea ahí."""
    return patch(
        "app.clients.signature_client.sign_xml_outcome",
        AsyncMock(return_value=_Outcome(signed=signed, seal_absent=seal_absent)),
    )


# ── El XML, que es lo que se firma ───────────────────────────────────────────


def test_el_xml_es_determinista_byte_a_byte() -> None:
    """Dos construcciones con los mismos datos dan la MISMA cadena. Sin esto el
    SHA anclado en la auditoría no significaría nada."""
    args = dict(
        lote=_lote_row(),
        items=[_item(code="B"), _item(code="A")],
        tenant_slug="demo",
        metodo_borrado="Borrado seguro completo",
        observaciones=None,
        generado_por="u-1",
        generado_ts=datetime(2026, 8, 29, tzinfo=UTC),
    )
    assert construir_acta_xml(**args) == construir_acta_xml(**args)


def test_el_inventario_se_ordena_por_codigo_no_por_como_venga() -> None:
    """El orden de la consulta no puede cambiar el documento firmado."""
    lote = _lote_row()
    desordenado = construir_acta_xml(
        lote=lote,
        items=[_item(code="EXP-B"), _item(code="EXP-A")],
        tenant_slug="demo",
        metodo_borrado="Borrado seguro completo",
        observaciones=None,
        generado_por="u-1",
        generado_ts=datetime(2026, 8, 29, tzinfo=UTC),
    )
    ordenado = construir_acta_xml(
        lote=lote,
        items=[_item(code="EXP-A"), _item(code="EXP-B")],
        tenant_slug="demo",
        metodo_borrado="Borrado seguro completo",
        observaciones=None,
        generado_por="u-1",
        generado_ts=datetime(2026, 8, 29, tzinfo=UTC),
    )
    assert desordenado == ordenado
    assert desordenado.index("EXP-A") < desordenado.index("EXP-B")


def test_el_acta_lleva_el_fundamento_y_la_publicidad_previa() -> None:
    """Sin el aval, el acto administrativo y el plazo de publicidad, el acta
    sería un inventario: no acreditaría ni quién autorizó ni que hubo ocasión
    de oponerse."""
    xml = construir_acta_xml(
        lote=_lote_row(),
        items=[_item()],
        tenant_slug="demo",
        metodo_borrado="Borrado seguro completo",
        observaciones=None,
        generado_por="u-1",
        generado_ts=datetime(2026, 8, 29, tzinfo=UTC),
    )
    assert "Acta 07 del Comité" in xml
    assert "Resolución 512 de 2026" in xml
    assert "2026-08-15" in xml  # plazo de oposición
    assert "Borrado seguro completo" in xml


def test_el_inventario_del_acta_NO_se_recorta_por_clasificacion() -> None:
    """Un acta a la que le faltan los expedientes clasificados no da fe de lo
    que se destruyó. Lo que se acota es quién la lee, no lo que dice."""
    xml = construir_acta_xml(
        lote=_lote_row(),
        items=[_item(code="EXP-PUBLICO", nivel=1), _item(code="EXP-RESERVADO", nivel=3)],
        tenant_slug="demo",
        metodo_borrado="Borrado seguro completo",
        observaciones=None,
        generado_por="u-1",
        generado_ts=datetime(2026, 8, 29, tzinfo=UTC),
    )
    assert "EXP-RESERVADO" in xml
    assert "Contratos 2020" in xml
    assert 'total="2"' in xml


# ── Generar el acta ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generar_congela_el_acta_y_ancla_su_sha_en_la_auditoria(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    # `mark_acta_eliminacion_firmada` PARSEA el tag del comando para saber
    # cuántas filas tocó -- es así como se detecta la carrera con la
    # reconciliación, así que el doble tiene que devolverlo de verdad.
    mock_conn.execute.return_value = "UPDATE 1"
    # Un solo valor y no una secuencia: `fetchval` lo usan TRES cosas aquí --
    # el gate de permiso, el MAX(nivel) del inventario y el encadenado de
    # `audit_log`-- y una lista se agota en cuanto cambia el orden interno.
    # `1` sirve a las tres: gate concedido, nivel PÚBLICA, hash previo.
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.side_effect = [
        {"estado": "avalado", "publicado_hasta": date(2026, 8, 15), "publicidad_abierta": False},
        _lote_row(),
        {"id": LOTE},  # INSERT ... RETURNING
        _acta_row(firma_estado="firmado"),  # lectura final
        _CLEARANCE,
    ]
    mock_conn.fetch.return_value = [_item()]

    with _sellado(signed=_Firmada()):
        resp = await client.post(f"{BASE}/acta", json=CUERPO, headers=TENANT_HEADERS)

    assert resp.status_code == 201
    asientos = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == "archive.eliminacion_acta_generada"
    ]
    assert asientos
    # El SHA viaja en el asiento INDELEBLE, no solo en la fila append-only.
    assert "acta_sha256" in asientos[0][8]


@pytest.mark.asyncio
async def test_un_lote_SIN_aval_no_tiene_nada_que_documentar(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = {
        "estado": "publicado",
        "publicado_hasta": date(2026, 9, 30),
        "publicidad_abierta": True,
    }

    resp = await client.post(f"{BASE}/acta", json=CUERPO, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "lote_sin_aval"
    assert "publicado" in resp.json()["detail"]["detail"]


@pytest.mark.asyncio
async def test_el_acta_no_se_regenera(client_with_conn) -> None:
    """`ON CONFLICT DO NOTHING` devuelve None: regenerarla produciría otro
    instante y otro SHA para una eliminación que ya tenía el suyo."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        {"estado": "avalado", "publicado_hasta": date(2026, 8, 15), "publicidad_abierta": False},
        _lote_row(),
        None,  # el INSERT no creó nada
    ]
    mock_conn.fetch.return_value = [_item()]

    resp = await client.post(f"{BASE}/acta", json=CUERPO, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "acta_ya_existe"


@pytest.mark.asyncio
async def test_el_metodo_de_borrado_no_lo_inventa_el_sistema(client_with_conn) -> None:
    """Sin `metodo_borrado` no hay acta: una frase por defecto sería una
    afirmación que nadie hizo sobre un acto irreversible."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post(f"{BASE}/acta", json={}, headers=TENANT_HEADERS)
    assert resp.status_code == 422

    resp = await client.post(
        f"{BASE}/acta", json={"metodo_borrado": "borrar"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_generar_el_acta_exige_nivel_3(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False

    resp = await client.post(f"{BASE}/acta", json=CUERPO, headers=TENANT_HEADERS)
    assert resp.status_code == 403


# ── El sellado, que no bloquea ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_si_no_hay_sello_el_acta_QUEDA_igual_con_el_motivo_a_la_vista(
    client_with_conn,
) -> None:
    """Un signature-service sin sello no puede costar el acta: ya quedó íntegra
    y su SHA anclado. Lo que sí tiene que quedar es POR QUÉ no se firmó."""
    client, mock_conn = client_with_conn
    # `mark_acta_eliminacion_firmada` PARSEA el tag del comando para saber
    # cuántas filas tocó -- es así como se detecta la carrera con la
    # reconciliación, así que el doble tiene que devolverlo de verdad.
    mock_conn.execute.return_value = "UPDATE 1"
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.side_effect = [
        {"estado": "avalado", "publicado_hasta": date(2026, 8, 15), "publicidad_abierta": False},
        _lote_row(),
        {"id": LOTE},
        _acta_row(last_error="sello_ausente"),
        _CLEARANCE,
    ]
    mock_conn.fetch.return_value = [_item()]

    with _sellado(seal_absent=True):
        resp = await client.post(f"{BASE}/acta", json=CUERPO, headers=TENANT_HEADERS)

    assert resp.status_code == 201
    cuerpo = resp.json()
    assert cuerpo["firma_estado"] == "pendiente_firma"
    assert cuerpo["last_error"] == "sello_ausente"


@pytest.mark.asyncio
async def test_el_acta_se_sella_como_acta_eliminacion_y_sobre_SU_xml(
    client_with_conn,
) -> None:
    """El `objeto_tipo` es el que decide si sale XAdES real o un HMAC: si
    llegara otro, el acta quedaría marcada «firmada» siendo estructuralmente
    más débil, sin error y sin diferencia visible."""
    client, mock_conn = client_with_conn
    # `mark_acta_eliminacion_firmada` PARSEA el tag del comando para saber
    # cuántas filas tocó -- es así como se detecta la carrera con la
    # reconciliación, así que el doble tiene que devolverlo de verdad.
    mock_conn.execute.return_value = "UPDATE 1"
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.side_effect = [
        {"estado": "avalado", "publicado_hasta": date(2026, 8, 15), "publicidad_abierta": False},
        _lote_row(),
        {"id": LOTE},
        _acta_row(firma_estado="firmado"),
        _CLEARANCE,
    ]
    mock_conn.fetch.return_value = [_item()]

    with _sellado(signed=_Firmada()) as firmar:
        await client.post(f"{BASE}/acta", json=CUERPO, headers=TENANT_HEADERS)

    kwargs = firmar.await_args.kwargs
    assert kwargs["objeto_tipo"] == "acta_eliminacion"
    assert kwargs["objeto_id"] == str(LOTE)
    # Y firma EL acta, no un resumen ni otra cosa.
    assert "actaEliminacion" in kwargs["payload_xml"]
    assert "Resolución 512 de 2026" in kwargs["payload_xml"]


@pytest.mark.asyncio
async def test_reintentar_una_ya_firmada_no_es_un_error(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    # `mark_acta_eliminacion_firmada` PARSEA el tag del comando para saber
    # cuántas filas tocó -- es así como se detecta la carrera con la
    # reconciliación, así que el doble tiene que devolverlo de verdad.
    mock_conn.execute.return_value = "UPDATE 1"
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.side_effect = [
        _acta_row(firma_estado="firmado"),  # el retry lee el acta
        _acta_row(firma_estado="firmado"),  # y la relee para responder
        _CLEARANCE,
    ]

    resp = await client.post(f"{BASE}/acta/firmar", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert resp.json()["firma_estado"] == "firmado"


# ── Leerla: entera o nada ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_el_acta_se_sirve_entera_a_quien_despeja_su_nivel_MAS_ALTO(
    client_with_conn,
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 3  # gate concedido y MAX(nivel) = 3
    mock_conn.fetchrow.side_effect = [_acta_row(), _CLEARANCE]

    resp = await client.get(f"{BASE}/acta", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert "actaEliminacion" in resp.json()["acta_xml"]


@pytest.mark.asyncio
async def test_quien_no_despeja_el_nivel_del_acta_no_la_lee_recortada(
    client_with_conn,
) -> None:
    """403 y no 404: quien pregunta ya veía el lote, así que no se le confirma
    nada nuevo — lo que necesita es entender POR QUÉ, no creer que no existe.
    Y no se sirve recortada: romper la firma sería peor que negarla."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 3  # el acta cubre un CLASIFICADA
    mock_conn.fetchrow.side_effect = [_acta_row(), {"is_root": False, "max_level": 1}]

    resp = await client.get(f"{BASE}/acta", headers=TENANT_HEADERS)

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "acta_sobre_su_clearance"


@pytest.mark.asyncio
async def test_un_lote_sin_acta_todavia_da_404(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [None]

    resp = await client.get(f"{BASE}/acta", headers=TENANT_HEADERS)

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "acta_no_encontrada"


@pytest.mark.asyncio
async def test_una_firma_SIN_artefacto_no_se_marca_firmada(client_with_conn) -> None:
    """Lo destapó mypy, no un test: `minio_key_firmado` es `str | None` y el
    código lo daba por presente. Un acta «firmada» cuyo XML firmado no está en
    ninguna parte no se podría verificar nunca más — y el CHECK de la 048 la
    rechazaría con un error opaco. Se trata como fallo del sellado, con motivo.
    """

    class _SinArtefacto:
        firma_id = uuid.uuid4()
        minio_key_firmado = None
        xades_level = "XAdES-B"
        acreditado = False

    client, mock_conn = client_with_conn
    mock_conn.execute.return_value = "UPDATE 1"
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.side_effect = [
        {"estado": "avalado", "publicado_hasta": date(2026, 8, 15), "publicidad_abierta": False},
        _lote_row(),
        {"id": LOTE},
        _acta_row(last_error="sellado_sin_artefacto"),
        _CLEARANCE,
    ]
    mock_conn.fetch.return_value = [_item()]

    with _sellado(signed=_SinArtefacto()):
        resp = await client.post(f"{BASE}/acta", json=CUERPO, headers=TENANT_HEADERS)

    assert resp.status_code == 201
    cuerpo = resp.json()
    assert cuerpo["firma_estado"] == "pendiente_firma"
    assert cuerpo["last_error"] == "sellado_sin_artefacto"
    asientos = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == "archive.eliminacion_acta_firma_pendiente"
    ]
    assert asientos
