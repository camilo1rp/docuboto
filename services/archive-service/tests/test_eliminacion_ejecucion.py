# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La ejecución del lote — RF-TRD-07, fase 5 de 5. **Aquí sí se destruye.**

Es el único camino del sistema que borra documentos de archivo. Lo que se prueba
es lo que impide que destruya cuando no debe, y lo que impide que MIENTA sobre
lo que destruyó:

* **Las cuatro precondiciones, una por una**: lote avalado, acta existente, acta
  FIRMADA e inventario congelado. Exigir la firma puede bloquear la ejecución si
  signature-service está caído, y es deliberado: el último acto irreversible es
  exactamente el momento de no seguir con una constancia a medias.
* **Los documentos se destruyen ANTES que el expediente.** Al revés, los
  vínculos a radicados caerían en cascada y ya no habría forma de saber qué
  documentos había que destruir: quedarían vivos para siempre y sin nadie que
  supiera que debían morir.
* **Si la purga falla, el expediente queda INTACTO y sin lápida.** Una lápida
  sobre bytes vivos afirma para siempre una destrucción que no ocurrió.
* **El lote no se marca `ejecutado` con expedientes pendientes**, y los
  recuentos son los REALES, no los pedidos.
"""

import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.clients.document_client import DocumentPurgaError
from tests.conftest import TENANT_HEADERS, USER_ID

LOTE = uuid.uuid4()
RUTA = f"/api/v1/eliminacion/lotes/{LOTE}/ejecutar"
EXP_A = uuid.uuid4()
EXP_B = uuid.uuid4()


def _estado(estado="avalado", *, avalado_por=None) -> dict:
    return {
        "estado": estado,
        "publicado_hasta": date(2026, 8, 15),
        "publicidad_abierta": False,
        "creado_por": uuid.uuid4(),
        # Distinto del llamante de los tests (`USER_ID`): la separación de
        # funciones exige que quien ejecuta no sea quien avaló.
        "avalado_por": avalado_por or uuid.uuid4(),
    }


def _acta(firma_estado="firmado", last_error=None, *, worm=True) -> dict:
    return {
        "lote_id": LOTE,
        "acta_xml": "<actaEliminacion/>",
        "acta_sha256": "c" * 64,
        "metodo_borrado": "Borrado seguro y picado de soportes",
        "observaciones": None,
        "generado_por": uuid.uuid4(),
        "generado_at": datetime(2026, 8, 29, tzinfo=UTC),
        "firma_estado": firma_estado,
        "firma_id": uuid.uuid4() if firma_estado == "firmado" else None,
        "minio_key_firmado": "k",
        "xades_level": "XAdES-B",
        "acreditado": False,
        "last_error": last_error,
        "firmado_at": datetime(2026, 8, 29, tzinfo=UTC),
        # AREA-06-02: `ejecutar` exige el candado WORM del acta.
        "worm_protegido_at": datetime(2026, 8, 29, tzinfo=UTC) if worm else None,
        "worm_last_error": None,
    }


def _lote_row() -> dict:
    return {
        "id": LOTE,
        "codigo": "LOTE-2026-001",
        "estado": "avalado",
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


def _item(exp_id, code="EXP-2020-0001") -> dict:
    return {
        "expediente_id": exp_id,
        "expediente_code": code,
        "expediente_nombre": "Contratos 2020",
        "trd_serie_code": "DEM-100",
        "disposition_code": "E",
        "fecha_vencimiento": date(2026, 1, 1),
        "fecha_extrema_inicial": date(2020, 1, 1),
        "fecha_extrema_final": date(2020, 12, 31),
        "nivel_seguridad": 1,
        "radicados_incluidos": 12,
    }


def _purga(recuento=None, error=None):
    mock = AsyncMock(
        side_effect=error,
        return_value=recuento or {"radicados_eliminados": 12, "anexos_eliminados": 30},
    )
    return patch("app.clients.document_client.purgar_radicados_internal", mock)


def _entorno(mock_conn, *, items, acta=None, estado="avalado"):
    """Encadena las lecturas del ejecutor en su orden real."""
    mock_conn.fetchval.return_value = 1
    mock_conn.execute.return_value = "DELETE 1"
    mock_conn.fetchrow.side_effect = [
        _estado(estado),
        acta if acta is not None else _acta(),
        _lote_row(),
        # una `insert_lapida` + un `marcar_lote_ejecutado` por expediente
        *[{"expediente_id": i["expediente_id"]} for i in items],
        _lote_row(),
    ]
    # inventario; luego, por expediente: constancias que citan un anexo sin baja avalada (C14),
    # radicados y bajas de soporte marcadas como destruidas -- todas vacías por defecto.
    mock_conn.fetch.side_effect = [items] + [[] for _ in range(3 * len(items))]


# ── Las cuatro precondiciones ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_un_lote_sin_aval_no_se_ejecuta(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.return_value = _estado("publicado")

    resp = await client.post(RUTA, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "lote_no_ejecutable"


@pytest.mark.asyncio
async def test_NO_se_destruye_sin_acta(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.side_effect = [_estado(), None]

    resp = await client.post(RUTA, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "acta_ausente"


@pytest.mark.asyncio
async def test_NO_se_destruye_con_el_acta_sin_firmar_y_el_error_DICE_por_que(
    client_with_conn,
) -> None:
    """Puede bloquear la ejecución si signature-service está caído, y es
    deliberado. El mensaje trae el motivo del último fallo para que se sepa qué
    desbloquear."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.side_effect = [
        _estado(),
        _acta(firma_estado="pendiente_firma", last_error="sello_ausente"),
    ]

    resp = await client.post(RUTA, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    detalle = resp.json()["detail"]
    assert detalle["error"] == "acta_sin_firmar"
    assert "sello_ausente" in detalle["detail"]


@pytest.mark.asyncio
async def test_un_lote_inexistente_da_404(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.return_value = None

    resp = await client.post(RUTA, headers=TENANT_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ejecutar_exige_nivel_4_y_no_basta_el_3(client_with_conn) -> None:
    """AREA-06-01: ejecutar y avalar piden crud 4 (el más alto que el catálogo
    reserva a los actos irreversibles), no el 3 de las fases reversibles. El
    doble de la consulta RBAC responde según el `min_crud` que de verdad se le
    pasa, así que un gate bajado a 3 deja de dar 403 y este test lo ve."""
    client, mock_conn = client_with_conn

    async def _rbac(sql, *args):
        if "gp.crud >= $3" in sql:
            return args[2] <= 3  # el llamante tiene nivel 3
        return 1

    mock_conn.fetchval.side_effect = _rbac

    resp = await client.post(RUTA, headers=TENANT_HEADERS)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_NO_ejecuta_quien_avalo_y_NO_se_destruye_nada(client_with_conn) -> None:
    """AREA-06-01: separación de funciones. La guarda va ANTES del bucle de
    destrucción; con la guarda movida después, el spy contaría una purga y el
    expediente ya estaría borrado cuando se dijera que no."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.side_effect = [_estado(avalado_por=USER_ID)]

    with _purga() as purga:
        resp = await client.post(RUTA, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "separacion_de_funciones"
    purga.assert_not_awaited()
    sql = " ".join(str(c.args[0]) for c in mock_conn.execute.call_args_list)
    assert "DELETE FROM expedientes" not in sql
    assert "INSERT INTO expediente_lapida" not in sql


@pytest.mark.asyncio
async def test_NO_se_destruye_con_el_acta_firmada_pero_sin_candado_WORM(
    client_with_conn,
) -> None:
    """AREA-06-02: el acta es lo único que sobrevive a lo eliminado; sin
    candado, el objeto firmado podría borrarse del almacén justo después."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetchrow.side_effect = [_estado(), _acta(worm=False)]

    with _purga() as purga:
        resp = await client.post(RUTA, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "acta_sin_worm"
    purga.assert_not_awaited()


# ── La destrucción ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ejecutar_destruye_escribe_la_lapida_y_deja_asiento(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    _entorno(mock_conn, items=[_item(EXP_A)])

    with _purga():
        resp = await client.post(RUTA, headers=TENANT_HEADERS)

    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["estado"] == "ejecutado"
    assert cuerpo["expedientes_eliminados"] == 1
    assert cuerpo["radicados_eliminados"] == 12
    assert cuerpo["anexos_eliminados"] == 30
    acciones = [
        c.args[5]
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert "archive.expediente_eliminado_trd" in acciones
    assert "archive.eliminacion_lote_ejecutado" in acciones


@pytest.mark.asyncio
async def test_los_documentos_se_destruyen_ANTES_que_el_expediente(client_with_conn) -> None:
    """Al revés, los vínculos a radicados caerían en cascada y ya no habría
    forma de saber qué documentos había que destruir."""
    client, mock_conn = client_with_conn
    _entorno(mock_conn, items=[_item(EXP_A)])
    orden: list[str] = []

    async def _purgar(**kwargs):
        orden.append("purga")
        return {"radicados_eliminados": 1, "anexos_eliminados": 2}

    async def _execute(*args, **kwargs):
        # Registra Y devuelve el tag: `delete_expediente` lo parsea, así que un
        # doble que devolviera `None` rompería por una razón ajena a lo que
        # este test mide.
        orden.append("sql:" + str(args[0])[:34])
        return "DELETE 1"

    # La lápida se escribe con `fetchrow` (INSERT ... RETURNING), no con
    # `execute`: si solo se observara `execute`, este test no vería la mitad de
    # la secuencia que mide.
    preparadas = list(mock_conn.fetchrow.side_effect)

    async def _fetchrow(*args, **kwargs):
        orden.append("sql:" + str(args[0]).strip()[:34])
        return preparadas.pop(0)

    mock_conn.execute.side_effect = _execute
    mock_conn.fetchrow.side_effect = _fetchrow

    with patch("app.clients.document_client.purgar_radicados_internal", _purgar):
        await client.post(RUTA, headers=TENANT_HEADERS)

    # Lo que importa es el ORDEN RELATIVO, no la primera línea: los
    # `SET search_path` de cada consulta van por delante de todo y no dicen
    # nada sobre la secuencia que este test mide.
    purga = orden.index("purga")
    borrado = next(i for i, o in enumerate(orden) if o.startswith("sql:DELETE FROM expedientes"))
    lapida = next(i for i, o in enumerate(orden) if "INSERT INTO expediente_lapida" in o)  # noqa: E501
    assert purga < borrado, orden
    # Y la lápida antes del borrado: al revés, un fallo entre ambos dejaría el
    # expediente destruido y sin lápida.
    assert lapida < borrado, orden


@pytest.mark.asyncio
async def test_si_la_purga_falla_el_expediente_queda_INTACTO_y_sin_lapida(
    client_with_conn,
) -> None:
    """Una lápida sobre bytes vivos afirma para siempre una destrucción que no
    ocurrió."""
    client, mock_conn = client_with_conn
    _entorno(mock_conn, items=[_item(EXP_A)])

    with _purga(error=DocumentPurgaError("storage-service inalcanzable")):
        resp = await client.post(RUTA, headers=TENANT_HEADERS)

    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["expedientes_eliminados"] == 0
    assert len(cuerpo["fallidos"]) == 1
    # Con su CÓDIGO, no solo el UUID: quien reintenta necesita saber cuál es.
    assert cuerpo["fallidos"][0]["expediente_code"] == "EXP-2020-0001"
    sql = " ".join(str(c.args[0]) for c in mock_conn.execute.call_args_list)
    assert "INSERT INTO expediente_lapida" not in sql
    assert "DELETE FROM expedientes" not in sql


@pytest.mark.asyncio
async def test_con_un_expediente_fallido_el_lote_NO_se_marca_ejecutado(
    client_with_conn,
) -> None:
    """Un lote «ejecutado» con expedientes vivos dentro mentiría sobre lo único
    que ese estado significa."""
    client, mock_conn = client_with_conn
    _entorno(mock_conn, items=[_item(EXP_A), _item(EXP_B, code="EXP-2020-0002")])

    llamadas = {"n": 0}

    async def _purgar(**kwargs):
        llamadas["n"] += 1
        if llamadas["n"] == 2:
            raise DocumentPurgaError("bytes vivos")
        return {"radicados_eliminados": 3, "anexos_eliminados": 4}

    with patch("app.clients.document_client.purgar_radicados_internal", _purgar):
        resp = await client.post(RUTA, headers=TENANT_HEADERS)

    cuerpo = resp.json()
    assert cuerpo["estado"] != "ejecutado"
    assert cuerpo["expedientes_eliminados"] == 1
    assert len(cuerpo["fallidos"]) == 1
    sql = " ".join(str(c.args[0]) for c in mock_conn.execute.call_args_list)
    assert "SET estado = 'ejecutado'" not in sql


@pytest.mark.asyncio
async def test_los_recuentos_son_los_REALES_no_los_pedidos(client_with_conn) -> None:
    """El número que la lápida afirma para siempre sale de lo que de verdad se
    borró — el inventario dice 12 radicados y la purga devuelve 9."""
    client, mock_conn = client_with_conn
    _entorno(mock_conn, items=[_item(EXP_A)])

    with _purga(recuento={"radicados_eliminados": 9, "anexos_eliminados": 11}):
        cuerpo = (await client.post(RUTA, headers=TENANT_HEADERS)).json()

    assert cuerpo["radicados_eliminados"] == 9
    assert cuerpo["anexos_eliminados"] == 11


@pytest.mark.asyncio
async def test_la_ejecucion_no_acepta_ningun_parametro_del_cliente(client_with_conn) -> None:
    """No hay `dry_run`, ni `forzar`, ni `omitir_worm`: un acto irreversible no
    debe tener parámetros que cambien lo que hace."""
    client, mock_conn = client_with_conn
    _entorno(mock_conn, items=[_item(EXP_A)])

    with _purga() as purgar:
        await client.post(RUTA, json={"forzar": True, "dry_run": True}, headers=TENANT_HEADERS)

    # El cuerpo se ignora por completo: la llamada aguas abajo es idéntica.
    kwargs = purgar.await_args.kwargs
    assert set(kwargs) == {"tenant_slug", "radicado_ids", "expediente_id", "motivo", "actor"}
    assert "Resolución 512 de 2026" in kwargs["motivo"]


# ── C14 (ADR-043): el anexo citado por una constancia vigente ────────────────────────────


@pytest.mark.asyncio
async def test_una_constancia_vigente_sin_baja_avalada_bloquea_el_expediente_ANTES_de_purgar(
    client_with_conn,
) -> None:
    """Si document-service/storage se negaran a mitad, el borrado quedaría PARCIAL. La comprobación
    previa (vista `v_anexo_citado_vigente`) frena ANTES de tocar un byte.

    Mutación: quitar la comprobación previa de `_eliminar_expediente` -> `purgar` se llama y el
    fallo ya no lleva el código `soporte_citado_no_avalado`."""
    client, mock_conn = client_with_conn
    _entorno(mock_conn, items=[_item(EXP_A)])
    tid = uuid.uuid4()
    mock_conn.fetch.side_effect = [
        [_item(EXP_A)],
        [{"excepcion_id": uuid.uuid4(), "transferencia_id": tid, "rol": "recibe", "ordinal": 1}],
    ]

    with _purga() as purgar:
        resp = await client.post(RUTA, headers=TENANT_HEADERS)

    cuerpo = resp.json()
    assert cuerpo["estado"] != "ejecutado" and cuerpo["expedientes_eliminados"] == 0
    (fallo,) = cuerpo["fallidos"]
    assert fallo["motivo"] == "soporte_citado_no_avalado"
    assert str(tid) in fallo["detalle"] and fallo["expediente_code"] == "EXP-2020-0001"
    purgar.assert_not_called()
    sql = " ".join(str(c.args[0]) for c in mock_conn.execute.call_args_list)
    assert "INSERT INTO expediente_lapida" not in sql and "DELETE FROM expedientes" not in sql


@pytest.mark.asyncio
async def test_el_rechazo_de_storage_por_anexo_citado_tambien_se_etiqueta(client_with_conn) -> None:
    """Red final: la constancia se declaró DESPUÉS de la comprobación y storage (que sí bloquea la
    fila) se niega: el fallo se etiqueta igual."""
    client, mock_conn = client_with_conn
    _entorno(mock_conn, items=[_item(EXP_A)])

    with _purga(error=DocumentPurgaError("storage → 409 anexo_citado_por_constancia")):
        cuerpo = (await client.post(RUTA, headers=TENANT_HEADERS)).json()

    assert cuerpo["fallidos"][0]["motivo"] == "soporte_citado_no_avalado"


@pytest.mark.asyncio
async def test_otros_fallos_de_purga_no_llevan_el_codigo_de_soporte(client_with_conn) -> None:
    """Control negativo del etiquetado."""
    client, mock_conn = client_with_conn
    _entorno(mock_conn, items=[_item(EXP_A)])
    with _purga(error=DocumentPurgaError("storage-service inalcanzable")):
        cuerpo = (await client.post(RUTA, headers=TENANT_HEADERS)).json()
    assert cuerpo["fallidos"][0]["motivo"] is None


@pytest.mark.asyncio
async def test_con_la_baja_avalada_se_purga_y_el_soporte_queda_destruido_con_asiento(
    client_with_conn,
) -> None:
    """Control positivo: sin bloqueantes (la vista ya no cuenta la cita porque el lote avalado
    trae su baja) la purga procede, se fija `soporte_eliminado_at` y se asienta cada constancia."""
    client, mock_conn = client_with_conn
    _entorno(mock_conn, items=[_item(EXP_A)])
    tid = uuid.uuid4()
    mock_conn.fetch.side_effect = [
        [_item(EXP_A)],  # inventario
        [],  # bloqueantes: ninguno
        [],  # radicados
        [{"excepcion_id": uuid.uuid4(), "transferencia_id": tid, "rol": "entrega",
          "ordinal": 2, "file_id": uuid.uuid4()}],  # bajas marcadas como destruidas
    ]

    with _purga():
        resp = await client.post(RUTA, headers=TENANT_HEADERS)

    assert resp.json()["estado"] == "ejecutado"
    acciones = [
        c.args[5]
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert "archive.constancia_soporte_eliminado_trd" in acciones
    sql = " ".join(str(c.args[0]) for c in mock_conn.fetch.call_args_list)
    assert "UPDATE eliminacion_lote_soporte_citado" in sql and "soporte_eliminado_at" in sql
