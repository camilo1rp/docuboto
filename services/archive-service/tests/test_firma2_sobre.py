# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""FIRMA-2, ronda 3 — sobre de revelación del fundamento (ADR-030, migración 064).

Cada prueba apunta a una mutación concreta:

* sobre sin WORM — no insertar la fila `sobre` de bookkeeping / no protegerla post-commit.
* sobre sin sello — escribir la fila aunque el sello del sobre falle (o consumir el TOTP antes).
* sobre servible sin admin 3 — `min_crud=2` (o sin gate) en `GET .../sobre`.
* sobre sin asiento de lectura — quitar `_asentar_lectura_fundamento` del camino de lectura.
* revocar devolviendo fundamento — quitar el `None` de `fundamento`/`fundamento_sal` en la respuesta.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.clients.signature_client import SignOutcome
from app.core.actor import Actor
from app.services.constancia_firma_excepcion import (
    HuellaFundamento,
    build_sobre_xml,
    compromiso_fundamento,
    huella_de_fundamento,
    leer_sobre,
    sha256_c14n,
)
from app.services.transferencia_service import TransferenciaService
from tests.conftest import TENANT_HEADERS
from tests.test_acta_firma_inobtenible import (
    ADMIN,
    COMPROMISO,
    FUNDAMENTO,
    FUNDAMENTO_REVOCACION,
    SAL,
    TENANT,
    _asientos,
    _conn,
    _excepcion_row,
    _externos,
    _fila_revocacion,
    _insert_excepcion,
    _payload,
    _revocar_payload,
    _secuencia,
    _sello_ok,
    _texto_asientos,
)

_KEY_SOBRE = "file-sobre/sobre-firmado.xml"


def _con_sobre(tid, **kw) -> dict:
    return _excepcion_row(
        tid,
        sobre_firma_id=uuid.uuid4(),
        sobre_minio_key=_KEY_SOBRE,
        sobre_sha256="s" * 64,
        sobre_xades_level="XAdES-T",
        worm_sobre_protegido=True,
        **kw,
    )


# --- el XML del sobre ---------------------------------------------------------------------


def test_el_sobre_lleva_texto_sal_y_compromiso_y_se_lee_de_vuelta() -> None:
    h = huella_de_fundamento(FUNDAMENTO)
    acto_id = uuid.uuid4()
    xml = build_sobre_xml(acto_id=acto_id, acto="constancia", texto=FUNDAMENTO, huella=h)
    assert "urn:orpycamcp:acta-firma-excepcion-sobre:v1" in xml
    assert leer_sobre(xml) == {
        "id": str(acto_id), "acto": "constancia", "texto": FUNDAMENTO,
        "sal_hex": h.sal.hex(), "compromiso": h.compromiso, "algoritmo": "SHA-256-salted",
    }
    # el compromiso del sobre es el MISMO que el del XML principal: SHA-256(sal || texto)
    assert h.compromiso == compromiso_fundamento(h.sal, FUNDAMENTO)
    assert sha256_c14n(xml) == sha256_c14n(xml)  # canónico e idempotente


def test_un_retorno_de_carro_interno_sobrevive_al_sobre() -> None:
    """El compromiso cubre los bytes EXACTOS: un parser normalizaría `\\r\\n` a `\\n` y rompería la
    verificación por un tercero."""
    texto = "Linea uno del fundamento.\r\nLinea dos del fundamento, mas larga."
    h = HuellaFundamento(sal=SAL, compromiso=compromiso_fundamento(SAL, texto))
    xml = build_sobre_xml(acto_id=uuid.uuid4(), acto="revocacion", texto=texto, huella=h)
    assert leer_sobre(xml)["texto"] == texto


def test_un_caracter_no_admitido_por_xml_no_produce_un_sobre_a_medias() -> None:
    h = huella_de_fundamento("x")
    with pytest.raises(ValueError):
        build_sobre_xml(acto_id=uuid.uuid4(), acto="constancia", texto="malo\x00texto", huella=h)


# --- declarar: el sobre se sella, se registra y se protege ---------------------------------


@pytest.mark.asyncio
async def test_declarar_sella_el_sobre_lo_registra_y_lo_protege_worm() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None, _con_sobre(tid)])

    with _externos() as (sign_xml, sign_pers, worm):
        resp = await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
            tid, Actor(id=ADMIN), _payload()
        )

    # dos sellos: la constancia y, aparte, el sobre (con otro objeto_id)
    assert sign_xml.await_count == 2
    principal, sobre = (c.kwargs for c in sign_xml.await_args_list)
    assert "urn:orpycamcp:acta-firma-excepcion-sobre:v1" in sobre["payload_xml"]
    assert "urn:orpycamcp:acta-firma-excepcion:v3" in principal["payload_xml"]
    assert sobre["objeto_tipo"] == "acta_transferencia" and sobre["objeto_id"] != principal["objeto_id"]
    campos = leer_sobre(sobre["payload_xml"])
    insert = _insert_excepcion(conn)
    sal = next(a for a in insert.args if isinstance(a, bytes))
    assert campos["texto"] == FUNDAMENTO and campos["sal_hex"] == sal.hex()
    assert campos["compromiso"] == compromiso_fundamento(sal, FUNDAMENTO)
    assert campos["id"] == principal["payload_xml"].split("<constanciaId>")[1].split("<")[0]
    # el texto y la sal NO van en la constancia (solo en el sobre)
    assert FUNDAMENTO not in principal["payload_xml"] and sal.hex() not in principal["payload_xml"]
    # la fila guarda la referencia al objeto sellado y su huella, no el XML del sobre
    assert sha256_c14n(sobre["payload_xml"]) in insert.args
    assert "sobre_minio_key" in insert.args[0] and "sobre_sha256" in insert.args[0]
    # mutación «sobre sin WORM»: bookkeeping en la MISMA tx y protección post-commit
    worm_inserts = [
        c for c in conn.execute.call_args_list
        if c.args and "INSERT INTO transferencia_acta_firma_excepcion_worm" in str(c.args[0])
    ]
    assert [c.args[2] for c in worm_inserts] == ["sello", "sobre"]
    assert [c.args[1] for c in worm.await_args_list] == ["sello", "sobre"]
    assert resp.sobre is True and resp.worm_sobre_protegido is True
    # el asiento lleva la huella del sobre, nunca el texto ni la sal
    texto = _texto_asientos(conn)
    assert "sobre_sha256" in texto and "defuncion" not in texto and sal.hex() not in texto
    sign_pers.assert_not_awaited()


@pytest.mark.asyncio
async def test_sin_sello_del_sobre_no_se_declara_nada() -> None:
    """Mutación: escribir la fila aunque el sobre no se selle."""
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None, _excepcion_row(tid)])

    with _externos() as (sign_xml, _sp, _w):
        sign_xml.side_effect = [_sello_ok(), SignOutcome(signed=None, transient=True)]
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload()
            )

    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "sellado_sobre_no_obtenido"
    assert not _asientos(conn)
    assert not any(
        "INSERT INTO transferencia_acta_firma_excepcion " in c.args[0]
        for c in conn.fetchrow.call_args_list
    )


def _sobre_ok(file_id: uuid.UUID) -> SignOutcome:
    ok = _sello_ok()
    return SignOutcome(
        signed=replace(ok.signed, minio_key_firmado=f"{file_id}/acta-transferencia-sobre.xml")
    )


@pytest.mark.asyncio
async def test_con_totp_invalido_el_sobre_ni_se_sella_ni_se_sube() -> None:
    """Mutación: sellar el sobre ANTES de validar la firma personal -> una copia del texto en el
    almacén por cada intento fallido."""
    from app.clients.signature_client import SignPersonalOutcome

    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None, _excepcion_row(tid)])
    with _externos(personal=SignPersonalOutcome(signed=None, rejected=(401, {"error": "totp_incorrecto"}))) as (
        sign_xml, sign_pers, _w
    ), patch("app.clients.storage_client.purgar_huerfanos_internal", new=AsyncMock()) as purga:
        with pytest.raises(HTTPException):
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload(totp_code="000000")
            )
    sign_pers.assert_awaited_once()
    assert sign_xml.await_count == 1  # solo el sello de la constancia: el sobre no se subió
    assert "urn:orpycamcp:acta-firma-excepcion:v3" in sign_xml.await_args.kwargs["payload_xml"]
    purga.assert_not_awaited()


async def _declarar_con_fallo(final_extra, file_id: uuid.UUID, purga_side=None):
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None, final_extra])
    with _externos() as (sign_xml, _sp, _w), patch(
        "app.clients.storage_client.purgar_huerfanos_internal",
        new=AsyncMock(side_effect=purga_side, return_value=[{"resultado": "purgado"}]),
    ) as purga:
        sign_xml.side_effect = [_sello_ok(), _sobre_ok(file_id)]
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload()
            )
    return exc, purga, conn


@pytest.mark.asyncio
async def test_si_el_motor_rechaza_la_fila_el_sobre_subido_se_purga() -> None:
    """Mutación: no llamar a `_purgar_sobre_huerfano` en el `except` -> el texto queda
    huérfano en el almacén."""
    file_id = uuid.uuid4()
    motor = asyncpg.CheckViolationError.new(
        {"M": "x", "C": "23514", "n": "ck_firma_excepcion_declarante_es_parte"}
    )
    exc, purga, _c = await _declarar_con_fallo(motor, file_id)
    assert exc.value.status_code == 403
    purga.assert_awaited_once()
    kw = purga.await_args.kwargs
    assert kw["file_ids"] == [file_id] and kw["tenant_slug"] == TENANT
    assert kw["subido_desde"].tzinfo is not None


@pytest.mark.asyncio
async def test_si_se_pierde_la_carrera_el_sobre_subido_se_purga() -> None:
    file_id = uuid.uuid4()
    exc, purga, _c = await _declarar_con_fallo(None, file_id)
    assert exc.value.status_code == 409
    assert purga.await_args.kwargs["file_ids"] == [file_id]


@pytest.mark.asyncio
async def test_si_la_purga_falla_el_acto_igual_responde_su_error_y_el_barrido_lo_recoge() -> None:
    file_id = uuid.uuid4()
    exc, purga, _c = await _declarar_con_fallo(None, file_id, purga_side=RuntimeError("storage caído"))
    assert exc.value.status_code == 409  # el fallo de la purga no lo enmascara
    purga.assert_awaited_once()


@pytest.mark.asyncio
async def test_en_el_camino_feliz_no_se_purga_nada() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None, _con_sobre(tid)])
    with _externos(), patch(
        "app.clients.storage_client.purgar_huerfanos_internal", new=AsyncMock()
    ) as purga:
        await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
            tid, Actor(id=ADMIN), _payload()
        )
    purga.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_barrido_purga_solo_lo_que_el_repositorio_dice_sin_referencia() -> None:
    from datetime import UTC, datetime

    f1, f2 = uuid.uuid4(), uuid.uuid4()
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    svc = TransferenciaService(_conn(), TENANT)
    with (
        patch.object(
            svc._repo, "list_sobres_huerfanos",
            new=AsyncMock(return_value=[{"id": f1, "uploaded_at": t0}, {"id": f2, "uploaded_at": t0}]),
        ),
        patch(
            "app.clients.storage_client.purgar_huerfanos_internal",
            new=AsyncMock(return_value=[
                {"file_id": str(f1), "resultado": "purgado"},
                {"file_id": str(f2), "resultado": "error"},
            ]),
        ) as purga,
    ):
        res = await svc.purgar_sobres_huerfanos()
    assert res == {"candidatos": 2, "purgados": 1, "fallidos": 1}
    assert purga.await_args.kwargs["file_ids"] == [f1, f2]
    assert purga.await_args.kwargs["subido_desde"] == t0


# --- revocar -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revocar_sella_el_sobre_y_no_devuelve_el_fundamento_de_la_constancia_original() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    previa = _excepcion_row(tid)  # lleva el texto y la sal de la constancia original
    revocacion = _fila_revocacion(
        previa, sobre_minio_key=_KEY_SOBRE, sobre_sha256="r" * 64,
    )
    _secuencia(conn, tid, exp_id, final=[previa, revocacion])

    with _externos() as (sign_xml, _p, _w):
        resp = await TransferenciaService(conn, TENANT).revocar_firma_inobtenible(
            tid, Actor(id=ADMIN), _revocar_payload()
        )

    # item 2 (Baja): ni el texto ni la sal de la constancia ORIGINAL (de otro declarante) salen
    # en esta respuesta; la vista completa queda en GET /acta, que sí asienta la lectura.
    assert resp.fundamento is None and resp.fundamento_sal is None
    assert FUNDAMENTO not in resp.model_dump_json() and SAL.hex() not in resp.model_dump_json()
    assert resp.fundamento_compromiso == COMPROMISO  # el compromiso sí es público
    # lo que acaba de escribir quien revoca se mantiene
    assert resp.revocacion_fundamento == FUNDAMENTO_REVOCACION
    # el sobre de la revocación: sellado aparte, con su bookkeeping WORM
    assert sign_xml.await_count == 2
    sobre = sign_xml.await_args_list[1].kwargs
    campos = leer_sobre(sobre["payload_xml"])
    assert campos["acto"] == "revocacion" and campos["texto"] == FUNDAMENTO_REVOCACION
    assert campos["id"] != str(previa["id"])  # el id del acto es el de la REVOCACIÓN
    worm_inserts = [
        c for c in conn.execute.call_args_list
        if c.args and "INSERT INTO transferencia_acta_firma_excepcion_revocacion_worm" in str(c.args[0])
    ]
    assert [c.args[2] for c in worm_inserts] == ["sello", "sobre"]
    assert resp.revocacion_sobre is True and resp.revocacion_worm_sobre_protegido is True


@pytest.mark.asyncio
async def test_sin_sello_del_sobre_no_se_revoca_nada() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[_excepcion_row(tid)])
    with _externos() as (sign_xml, _p, _w):
        sign_xml.side_effect = [_sello_ok(), SignOutcome(signed=None, seal_absent=True)]
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).revocar_firma_inobtenible(
                tid, Actor(id=ADMIN), _revocar_payload()
            )
    assert exc.value.status_code == 503
    assert not _asientos(conn)


def test_no_se_redeclara_si_el_sobre_de_la_revocacion_no_esta_protegido() -> None:
    base = {
        "revocada_en": object(), "revocacion_firma_id": uuid.uuid4(),
        "revocacion_worm_protegido": True, "revocacion_firma_personal_id": None,
        "revocacion_sobre_minio_key": _KEY_SOBRE,
    }
    assert TransferenciaService._revocacion_protegida({**base, "revocacion_worm_sobre_protegido": False}) is False
    assert TransferenciaService._revocacion_protegida({**base, "revocacion_worm_sobre_protegido": True}) is True
    # una revocación anterior a la 064 (sin sobre) no queda bloqueada
    legada = {**base, "revocacion_sobre_minio_key": None, "revocacion_worm_sobre_protegido": None}
    assert TransferenciaService._revocacion_protegida(legada) is True


# --- lectura mediada ---------------------------------------------------------------------


async def _leer(*, fila: dict | None, xml: str | None, acto: str = "constancia"):
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[fila])
    with patch(
        "app.clients.storage_client.download_signed_xml", new=AsyncMock(return_value=xml)
    ) as descarga:
        try:
            res = await TransferenciaService(conn, TENANT).leer_sobre_fundamento(
                tid, fila["id"] if fila else uuid.uuid4(), Actor(id=ADMIN), acto=acto
            )
        except HTTPException as exc:
            return conn, descarga, exc
    return conn, descarga, res


@pytest.mark.asyncio
async def test_leer_el_sobre_deja_asiento_de_lectura_via_sobre_sin_texto_ni_sal() -> None:
    tid = uuid.uuid4()
    fila = _con_sobre(tid)
    conn, descarga, res = await _leer(fila=fila, xml="<sobre-firmado/>")
    assert res.xml_firmado == "<sobre-firmado/>" and res.worm_protegido is True
    assert res.sobre_sha256 == "s" * 64 and res.acto == "constancia"
    descarga.assert_awaited_once()
    assert descarga.await_args.kwargs["minio_key_firmado"] == _KEY_SOBRE
    # mutación «sobre sin asiento de lectura»
    asientos = _asientos(conn)
    assert len(asientos) == 1
    texto = _texto_asientos(conn)
    compacto = texto.replace("\\", "").replace(" ", "")
    assert "transferencia.acta_fundamento_leido" in texto and '"via":"sobre"' in compacto
    assert str(ADMIN) in texto and str(fila["id"]) in texto
    assert FUNDAMENTO not in texto and SAL.hex() not in texto


@pytest.mark.asyncio
async def test_leer_el_sobre_de_la_revocacion_usa_su_propia_clave_y_asienta() -> None:
    tid = uuid.uuid4()
    fila = _excepcion_row(
        tid, revocacion_id=uuid.uuid4(), revocacion_fundamento=FUNDAMENTO_REVOCACION,
        revocacion_sobre_minio_key="file-rev/sobre-rev.xml", revocacion_sobre_sha256="q" * 64,
        revocacion_sobre_xades_level="XAdES-T", revocacion_worm_sobre_protegido=False,
    )
    conn, descarga, res = await _leer(fila=fila, xml="<x/>", acto="revocacion")
    assert descarga.await_args.kwargs["minio_key_firmado"] == "file-rev/sobre-rev.xml"
    assert res.acto == "revocacion" and res.worm_protegido is False
    assert len(_asientos(conn)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("acto", ["constancia", "revocacion"])
async def test_un_acto_sin_sobre_es_404_y_no_deja_asiento(acto: str) -> None:
    """Constancias v2/legadas (y las anteriores a la 064): sin sobre, 404 neutro."""
    fila = _excepcion_row(uuid.uuid4())  # sin sobre_minio_key
    conn, descarga, exc = await _leer(fila=fila, xml="<x/>", acto=acto)
    assert isinstance(exc, HTTPException) and exc.status_code == 404
    assert exc.detail["error"] == "sobre_not_found"
    descarga.assert_not_awaited()
    assert not _asientos(conn)


@pytest.mark.asyncio
async def test_si_el_almacen_no_entrega_el_sobre_es_503_y_no_se_asienta_una_lectura_que_no_ocurrio() -> None:
    conn, _d, exc = await _leer(fila=_con_sobre(uuid.uuid4()), xml=None)
    assert isinstance(exc, HTTPException) and exc.status_code == 503
    assert exc.detail["error"] == "sobre_no_disponible"
    assert not _asientos(conn)


@pytest.mark.asyncio
async def test_si_el_asiento_de_lectura_falla_el_sobre_no_sale() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[_con_sobre(tid)])
    with (
        patch("app.clients.storage_client.download_signed_xml", new=AsyncMock(return_value="<x/>")),
        patch(
            "app.services.transferencia_service.audit.append",
            new=AsyncMock(side_effect=RuntimeError("audit caído")),
        ),
        pytest.raises(RuntimeError),
    ):
        await TransferenciaService(conn, TENANT).leer_sobre_fundamento(
            tid, uuid.uuid4(), Actor(id=ADMIN), acto="constancia"
        )


@pytest.mark.asyncio
async def test_el_sobre_exige_administracion_nivel_3(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Mutación «sobre servible sin admin 3»: `min_crud=2` o sin gate."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    resp = await client.get(
        f"/api/v1/transferencias/{uuid.uuid4()}/acta/firma-inobtenible/{uuid.uuid4()}/sobre",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    consulta = next(c for c in mock_conn.fetchval.call_args_list if "auth_permissions" in c.args[0])
    assert consulta.args[2:] == ("USUA_PERM_ADMIN", 3)


# --- verificación: cuarta copia ------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("campos_alterados", "esperado"),
    [({}, True), ({"texto": "otro texto distinto"}, False), ({"sal_hex": "00" * 32}, False)],
)
async def test_el_sobre_se_coteja_contra_la_fila(campos_alterados: dict, esperado: bool) -> None:
    tid = uuid.uuid4()
    fila = _con_sobre(tid)
    h = HuellaFundamento(sal=SAL, compromiso=COMPROMISO)
    xml = build_sobre_xml(acto_id=fila["id"], acto="constancia", texto=FUNDAMENTO, huella=h)
    if campos_alterados.get("texto"):
        xml = xml.replace(FUNDAMENTO, campos_alterados["texto"])
    if campos_alterados.get("sal_hex"):
        xml = xml.replace(SAL.hex(), campos_alterados["sal_hex"])
    with patch("app.clients.storage_client.download_signed_xml", new=AsyncMock(return_value=xml)):
        ok = await TransferenciaService(_conn(), TENANT)._cotejar_sobre(
            _KEY_SOBRE, fila, acto="constancia"
        )
    assert ok is esperado


@pytest.mark.asyncio
async def test_un_sobre_que_no_se_puede_leer_no_cuenta_como_coincidencia() -> None:
    with patch("app.clients.storage_client.download_signed_xml", new=AsyncMock(return_value=None)):
        ok = await TransferenciaService(_conn(), TENANT)._cotejar_sobre(
            _KEY_SOBRE, _con_sobre(uuid.uuid4()), acto="constancia"
        )
    assert ok is None


# --- tipos de storage -----------------------------------------------------------------------


@pytest.mark.parametrize("acto", ["excepcion", "revocacion"])
@pytest.mark.parametrize("rol", ["elabora", "entrega", "recibe"])
def test_el_tipo_del_sobre_en_storage(acto: str, rol: str) -> None:
    from app.services.index_service import _TIPO_FIRMA_EXCEPCION, _TIPO_FIRMA_REVOCACION

    esperado = f"acta_firma_{acto}_sobre_{rol}"
    assert TransferenciaService._tipo_worm_excepcion("sobre", rol, acto) == esperado
    tabla = _TIPO_FIRMA_EXCEPCION if acto == "excepcion" else _TIPO_FIRMA_REVOCACION
    assert tabla[("sobre", rol)] == esperado


# --- lectura a petición del fundamento (sin sal) ---------------------------------------------


async def _leer_fundamento(*, fila, acto="constancia", conn=None):
    conn = conn or _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[fila])
    try:
        res = await TransferenciaService(conn, TENANT).leer_fundamento(
            tid, fila["id"] if fila else uuid.uuid4(), Actor(id=ADMIN), acto=acto
        )
    except HTTPException as exc:
        return conn, exc
    return conn, res


@pytest.mark.asyncio
async def test_leer_el_fundamento_asienta_via_lectura_y_no_devuelve_la_sal() -> None:
    """Mutaciones: sin `_asentar_lectura_fundamento` -> rojo; sal en la respuesta -> rojo."""
    fila = _excepcion_row(uuid.uuid4())
    conn, res = await _leer_fundamento(fila=fila)
    assert res.fundamento == FUNDAMENTO and res.acto == "constancia"
    assert SAL.hex() not in res.model_dump_json() and "sal" not in res.model_dump()
    texto = _texto_asientos(conn)
    assert len(_asientos(conn)) == 1
    compacto = texto.replace("\\", "").replace(" ", "")
    assert "transferencia.acta_fundamento_leido" in texto and '"via":"lectura"' in compacto
    assert FUNDAMENTO not in texto and SAL.hex() not in texto


@pytest.mark.asyncio
async def test_leer_el_fundamento_de_la_revocacion_y_404_si_no_existe() -> None:
    fila = _excepcion_row(
        uuid.uuid4(), revocacion_id=uuid.uuid4(), revocacion_fundamento=FUNDAMENTO_REVOCACION
    )
    _c, res = await _leer_fundamento(fila=fila, acto="revocacion")
    assert res.fundamento == FUNDAMENTO_REVOCACION
    conn, exc = await _leer_fundamento(fila=_excepcion_row(uuid.uuid4()), acto="revocacion")
    assert exc.status_code == 404 and exc.detail["error"] == "fundamento_not_found"
    assert _asientos(conn) == []
    conn, exc = await _leer_fundamento(fila=None)
    assert exc.status_code == 404 and _asientos(conn) == []


@pytest.mark.asyncio
async def test_si_el_asiento_de_lectura_falla_el_fundamento_no_sale() -> None:
    with (
        patch(
            "app.services.transferencia_service.audit.append",
            new=AsyncMock(side_effect=RuntimeError("audit caído")),
        ),
        pytest.raises(RuntimeError),
    ):
        await _leer_fundamento(fila=_excepcion_row(uuid.uuid4()))


@pytest.mark.asyncio
async def test_el_fundamento_exige_administracion_nivel_3(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Mutación: `min_crud=2` o sin gate."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    resp = await client.get(
        f"/api/v1/transferencias/{uuid.uuid4()}/acta/firma-inobtenible/{uuid.uuid4()}/fundamento",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    consulta = next(c for c in mock_conn.fetchval.call_args_list if "auth_permissions" in c.args[0])
    assert consulta.args[2:] == ("USUA_PERM_ADMIN", 3)


@pytest.mark.asyncio
async def test_ninguna_vista_de_declarar_ni_de_revocar_devuelve_la_sal() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None, _con_sobre(tid)])
    with _externos():
        resp = await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
            tid, Actor(id=ADMIN), _payload()
        )
    assert resp.fundamento_sal is None and SAL.hex() not in resp.model_dump_json()
