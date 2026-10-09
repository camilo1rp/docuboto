# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""FIRMA-2, ronda de correcciones de auditoría (A1-A7, S4, S5).

Cada prueba apunta a una mutación concreta (ver el docstring): la que se cae al romper el
código es la prueba de que el control existe.

* A2 — enlaces de la cadena: sustituyeA / revocacionSha256 / constanciaSha256 / huella guardada /
  artefacto firmado. Mutación: `_cotejar_enlaces` que devuelve siempre «sin motivos».
* A3 — ancla común de retención y alineación de los eslabones anteriores. Mutación: no llamar a
  `_alinear_cadena`, o anclar en la fecha del propio acto.
* A4 — la salida de `agotado` exige administración nivel 3. Mutación: `min_crud=2`.
* A5 — `incapacidad` no viaja en claro en el XML ni en el asiento. Mutación: `motivo` crudo.
* S5 — leer el fundamento en claro deja asiento de lectura. Mutación: quitar la llamada.
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.clients.storage_client import WormProtectionOutcome, WormRenewalOutcome
from app.core.actor import Actor
from app.schemas.transferencia import RevocarFirmaInobtenibleRequest
from app.services.constancia_firma_excepcion import (
    Identidad,
    build_constancia_xml,
    build_revocacion_xml,
    huella_de_fundamento,
    sha256_c14n,
)
from app.services.transferencia_service import TransferenciaService
from tests.conftest import TENANT_HEADERS, USER_ID, make_expediente_record
from tests.test_acta_firma_inobtenible import (
    ADMIN,
    ANEXO,
    FUNDAMENTO,
    FUNDAMENTO_REVOCACION,
    NUMERO_RAD,
    PARTE,
    RAD,
    SHA_ANEXO,
    TENANT,
    _asientos,
    _conn,
    _excepcion_row,
    _externos,
    _insert_excepcion,
    _payload,
    _secuencia,
    _texto_asientos,
    _verificar,
)
from tests.test_constancia_worm import _clave, _fila, _svc
from tests.test_constancia_worm import _conn as _conn_worm
from tests.test_transferencias import _acta_row, _transfer_row

_PARTE = Identidad(id=PARTE, nombre="parte.user", cargo=None, fuente="usuario")
_DECL = Identidad(id=ADMIN, nombre="admin.user", cargo=None, fuente="usuario")
_SIG = (
    '<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#"><ds:SignatureValue>zz'
    "</ds:SignatureValue></ds:Signature>"
)


def _firmado(xml: str) -> str:
    """El XML con una firma envolvente (lo que guarda storage tras el XAdES)."""
    i = xml.rindex("</")
    return xml[:i] + _SIG + xml[i:]


# --- A2: los enlaces de la cadena se cotejan --------------------------------------------------


def _cadena(
    tid: uuid.UUID,
    *,
    sustituye_en_xml: uuid.UUID | None = None,
    rev_sha_en_xml: str | None = None,
    constancia_sha_en_revocacion: str | None = None,
    revocacion_sin_worm: bool = False,
):
    """Dos eslabones coherentes (el 1.º revocado, el 2.º vigente) con sus XML REALES; los
    parámetros rompen UN enlace dejando consistentes las demás huellas."""
    exp = uuid.uuid4()
    c1, c2, r1 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    h1, hr = huella_de_fundamento(FUNDAMENTO), huella_de_fundamento(FUNDAMENTO_REVOCACION)
    h2 = huella_de_fundamento(FUNDAMENTO)
    ahora = datetime.now(tz=UTC)
    comunes = dict(
        transferencia_id=tid, expediente_id=exp, acta_fuid_sha256="a" * 64, rol="recibe",
        parte=_PARTE, motivo="fallecimiento", referencia_radicado_id=RAD,
        referencia_radicado_numero=NUMERO_RAD, referencia_anexo_id=ANEXO,
        referencia_anexo_sha256=SHA_ANEXO, declarante=_DECL, declarada_en=ahora,
    )
    xml1 = build_constancia_xml(
        constancia_id=c1, ordinal=1, sustituye_a=None, revocacion_sustituida_sha256=None,
        fundamento=h1, **comunes,
    )
    sha1 = sha256_c14n(xml1)
    rxml = build_revocacion_xml(
        revocacion_id=r1, constancia_id=c1,
        constancia_sha256=constancia_sha_en_revocacion or sha1, transferencia_id=tid,
        expediente_id=exp, acta_fuid_sha256="a" * 64, ordinal=1, rol="recibe", parte_id=PARTE,
        causa="error_de_declaracion", fundamento=hr, revocante=_DECL, revocada_en=ahora,
    )
    rsha = sha256_c14n(rxml)
    xml2 = build_constancia_xml(
        constancia_id=c2, ordinal=2, sustituye_a=sustituye_en_xml or c1,
        revocacion_sustituida_sha256=rev_sha_en_xml or rsha, fundamento=h2, **comunes,
    )
    sha2 = sha256_c14n(xml2)
    f1 = _excepcion_row(
        tid, id=c1, ordinal=1, constancia_sha256=sha1, fundamento_sal=h1.sal,
        fundamento_compromiso=h1.compromiso, minio_key_firmado="k/c1.xml",
        revocada_en=ahora, revocacion_id=r1, revocacion_version=3, revocacion_sha256=rsha,
        revocacion_causa="error_de_declaracion", revocacion_fundamento_sal=hr.sal,
        revocacion_fundamento_compromiso=hr.compromiso, revocacion_firma_id=uuid.uuid4(),
        revocacion_minio_key_firmado="k/r1.xml", revocante_fuente="usuario",
        revocacion_worm_protegido=not revocacion_sin_worm,
    )
    f2 = _excepcion_row(
        tid, id=c2, ordinal=2, sustituye_a=c1, constancia_sha256=sha2, fundamento_sal=h2.sal,
        fundamento_compromiso=h2.compromiso, minio_key_firmado="k/c2.xml",
    )
    xml_cadena = [
        {"id": c1, "constancia_xml": xml1, "revocacion_xml": rxml},
        {"id": c2, "constancia_xml": xml2, "revocacion_xml": None},
    ]
    firmados = {"k/c1.xml": _firmado(xml1), "k/r1.xml": _firmado(rxml), "k/c2.xml": _firmado(xml2)}
    return [f1, f2], xml_cadena, firmados


def _descarga(firmados: dict[str, str]) -> AsyncMock:
    async def _d(*, tenant_slug: str, minio_key_firmado: str) -> str:
        return firmados.get(minio_key_firmado, "<xml/>")

    return AsyncMock(side_effect=_d)


async def _verificar_cadena(**kw):
    tid = uuid.uuid4()
    filas, xml_cadena, firmados = _cadena(tid, **kw)
    return await _verificar(
        [], [], filas=filas, xml_cadena=xml_cadena, descarga=_descarga(firmados)
    )


@pytest.mark.asyncio
async def test_una_cadena_coherente_cuadra_y_reporta_el_worm_de_cada_eslabon() -> None:
    res = await _verificar_cadena()
    assert res.cadena_motivos == [] and res.valida is True
    assert [e.enlace_valido for e in res.excepciones] == [True, True]
    assert [e.enlace_motivo for e in res.excepciones] == [None, None]
    # el estado WORM de cada eslabón viaja con él (revocación del 1.º protegida)
    assert res.excepciones[0].revocacion_worm_protegido is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rotura", "motivo"),
    [
        ({"sustituye_en_xml": uuid.uuid4()}, "sustituyeA_no_coincide"),
        ({"rev_sha_en_xml": "9" * 64}, "revocacionSha256_no_coincide"),
        ({"constancia_sha_en_revocacion": "8" * 64}, "constanciaSha256_no_coincide"),
        ({"revocacion_sin_worm": True}, "revocacion_sustituida_sin_worm"),
    ],
)
async def test_un_enlace_roto_invalida_el_acta_con_su_motivo(rotura: dict, motivo: str) -> None:
    """Mutación A2: un cotejo que no compara (devuelve siempre «sin motivos») deja `valida=True`."""
    res = await _verificar_cadena(**rotura)
    assert res.valida is False
    assert any(motivo in m for m in res.cadena_motivos), res.cadena_motivos
    assert any(e.enlace_valido is False and motivo in (e.enlace_motivo or "") for e in res.excepciones)


@pytest.mark.asyncio
async def test_un_xml_guardado_alterado_no_coincide_con_su_huella() -> None:
    tid = uuid.uuid4()
    filas, xml_cadena, firmados = _cadena(tid)
    xml_cadena[1]["constancia_xml"] = xml_cadena[1]["constancia_xml"].replace("recibe", "entrega")
    res = await _verificar([], [], filas=filas, xml_cadena=xml_cadena, descarga=_descarga(firmados))
    assert res.valida is False
    assert any("constancia_huella_distinta" in m for m in res.cadena_motivos)


@pytest.mark.asyncio
async def test_un_artefacto_firmado_sobre_otro_contenido_invalida_el_acta() -> None:
    tid = uuid.uuid4()
    filas, xml_cadena, firmados = _cadena(tid)
    firmados["k/c2.xml"] = _firmado(firmados["k/c1.xml"])  # el sello del 2.º firma OTRO XML
    res = await _verificar([], [], filas=filas, xml_cadena=xml_cadena, descarga=_descarga(firmados))
    assert res.valida is False
    assert any("artefacto_sello_no_firma_el_xml_guardado" in m for m in res.cadena_motivos)


@pytest.mark.asyncio
async def test_una_constancia_anterior_a_la_v3_no_es_comprobable_y_no_rompe_la_cadena() -> None:
    tid = uuid.uuid4()
    res = await _verificar(
        [], [], filas=[_excepcion_row(tid, constancia_version=2)],
        xml_cadena=[{"id": uuid.uuid4(), "constancia_xml": "<x/>", "revocacion_xml": None}],
    )
    assert res.excepciones[0].enlace_valido is None and res.cadena_motivos == []


# --- A3: ancla común de retención ------------------------------------------------------------


@pytest.mark.asyncio
async def test_la_retencion_se_ancla_al_ultimo_acto_de_la_cadena_no_a_la_fecha_propia() -> None:
    """Mutación A3: anclar en `actuada_en` deja al eslabón 1 vencer antes que su revocación."""
    conn = _conn_worm()
    fila = _fila("sello", actuada_en=datetime(2026, 10, 1, tzinfo=UTC))
    conn.fetchrow.return_value = fila
    ultimo = datetime(2027, 3, 1, tzinfo=UTC)
    conn.fetchval.return_value = ultimo  # GREATEST(MAX(declarada_en), MAX(revocada_en))
    conn.execute.return_value = "UPDATE 1"
    svc = _svc(conn)
    ok = WormProtectionOutcome(
        ok=True, worm_object_id=uuid.uuid4(), retain_until=datetime(2053, 1, 1, tzinfo=UTC)
    )
    with patch("app.clients.storage_client.proteger_artefacto_worm_internal", new=AsyncMock(return_value=ok)):
        await svc.proteger_worm_firma_excepcion(fila["id"], "sello", actor_id=None, origen="reconciliation")
    assert svc._resolve_worm_retention_acta.await_args.kwargs["firmado_at"] == ultimo


@pytest.mark.asyncio
async def test_al_proteger_un_eslabon_nuevo_se_renuevan_los_anteriores_a_la_misma_ancla() -> None:
    """Mutación A3: sin `_alinear_cadena` el eslabón anterior queda con su retención original."""
    conn = _conn_worm()
    fila = _fila("sello", actuada_en=datetime(2027, 3, 1, tzinfo=UTC))
    conn.fetchrow.return_value = fila
    conn.execute.return_value = "UPDATE 1"
    previo_id, previo_clave = uuid.uuid4(), _clave()
    nueva = datetime(2053, 1, 1, tzinfo=UTC)
    conn.fetch.return_value = [
        # el eslabón anterior, con retención más corta: se alinea
        {"acto": "revocacion", "acto_id": previo_id, "artefacto": "sello", "rol": "recibe",
         "minio_key_firmado": previo_clave, "worm_retain_until": datetime(2052, 1, 1, tzinfo=UTC),
         "worm_renovacion_intentos": 0},
        # otro que ya llega igual de lejos: no se toca
        {"acto": "excepcion", "acto_id": uuid.uuid4(), "artefacto": "sello", "rol": "recibe",
         "minio_key_firmado": _clave(), "worm_retain_until": nueva, "worm_renovacion_intentos": 0},
    ]
    svc = _svc(conn, retencion=(25, date(2027, 3, 1)))
    ok = WormProtectionOutcome(ok=True, worm_object_id=uuid.uuid4(), retain_until=nueva)
    with (
        patch("app.clients.storage_client.proteger_artefacto_worm", new=AsyncMock(return_value=ok)),
        patch(
            "app.clients.storage_client.renovar_retencion_worm_internal",
            new=AsyncMock(return_value=WormRenewalOutcome(ok=True, retain_until=nueva, renovado=True)),
        ) as renovar,
    ):
        res = await svc.proteger_worm_firma_excepcion(fila["id"], "sello", actor_id="u-1", origen="close")
    assert res == "protegido"
    renovar.assert_awaited_once()  # solo el anterior que se quedaba corto
    kw = renovar.await_args.kwargs
    assert kw["tipo"] == "acta_firma_revocacion_recibe"
    assert kw["file_id"] == previo_clave.split("/", 1)[0]
    assert kw["fecha_inicio"] == date(2027, 3, 1) and kw["retencion_anios"] == 25
    # la extensión real deja su asiento, a nombre de quien provocó el acto
    asientos = [c for c in conn.execute.call_args_list if c.args and "INSERT INTO public.audit_log" in str(c.args[0])]
    renovadas = [a for a in asientos if a.args[5].endswith("_retencion_renovada")]
    assert len(renovadas) == 1
    assert renovadas[0].args[3] == "u-1" and "alineacion_cadena" in renovadas[0].args[8]


# --- A4: salida de `agotado` solo para administración nivel 3 -----------------------------------

_URL = "/api/v1/transferencias/{}/acta/firma-inobtenible/{}"


@pytest.mark.asyncio
@pytest.mark.parametrize("sufijo", ["/worm/reintentar", "/revocacion/worm/reintentar"])
async def test_reintentar_el_worm_exige_administracion_nivel_3(
    client_with_conn: tuple[AsyncClient, AsyncMock], sufijo: str
) -> None:
    """Mutación A4: `min_crud=2` (o sin el gate) -> el reintento lo haría cualquier administrador."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    resp = await client.post(
        _URL.format(uuid.uuid4(), uuid.uuid4()) + sufijo, headers=TENANT_HEADERS
    )
    assert resp.status_code == 403
    consulta = next(c for c in mock_conn.fetchval.call_args_list if "auth_permissions" in c.args[0])
    assert consulta.args[2:] == ("USUA_PERM_ADMIN", 3)


# --- A5: el motivo `incapacidad` no viaja en claro --------------------------------------------


@pytest.mark.asyncio
async def test_incapacidad_va_en_la_base_pero_neutra_en_el_xml_y_en_el_asiento() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None, _excepcion_row(tid, motivo="incapacidad")])
    with _externos() as (sign_xml, _p, _w):
        await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
            tid, Actor(id=ADMIN), _payload(motivo="incapacidad")
        )
    xml = sign_xml.await_args_list[0].kwargs["payload_xml"]
    assert "impedimento_sobreviniente" in xml and "incapacidad" not in xml
    texto = _texto_asientos(conn)
    assert "impedimento_sobreviniente" in texto and "incapacidad" not in texto
    assert "incapacidad" in _insert_excepcion(conn).args  # la categoría exacta, en la base


# --- A6: la causa de la revocación es obligatoria y de vocabulario cerrado ---------------------


def test_la_causa_de_revocacion_es_obligatoria_y_cerrada() -> None:
    base = {"rol": "recibe", "fundamento": FUNDAMENTO_REVOCACION}
    with pytest.raises(ValueError):
        RevocarFirmaInobtenibleRequest(**base)
    with pytest.raises(ValueError):
        RevocarFirmaInobtenibleRequest(**base, causa="porque si")
    for causa in ("error_de_declaracion", "parte_puede_firmar", "otro"):
        assert RevocarFirmaInobtenibleRequest(**base, causa=causa).causa == causa


# --- S5: leer el fundamento en claro deja asiento -----------------------------------------------


async def _leer_acta(puede_ver: bool):
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,
    ]
    conn.fetch.side_effect = [[], [_excepcion_row(tid)]]
    with (
        patch(
            "app.services.transferencia_service.check_permission",
            new=AsyncMock(return_value=puede_ver),
        ),
        patch(
            "app.clients.document_client.fetch_radicado_metadata_internal",
            new=AsyncMock(return_value={str(RAD): {"tracking_number": NUMERO_RAD}}),
        ),
    ):
        res = await TransferenciaService(conn, TENANT).acta(tid, USER_ID)
    return conn, res


@pytest.mark.asyncio
@pytest.mark.parametrize("puede_ver", [True, False])
async def test_visitar_el_acta_no_devuelve_el_fundamento_ni_deja_asiento_de_lectura(puede_ver: bool) -> None:
    """Contrato (ronda 3): `GET /acta` ya no sirve el texto ni la sal -> no hay lectura que asentar.
    La lectura a petición y su asiento viven en `GET .../fundamento` (tests/test_firma2_sobre.py)."""
    conn, res = await _leer_acta(puede_ver)
    e = res.excepciones_firma[0]
    assert e.fundamento is None and e.fundamento_sal is None
    assert _asientos(conn) == []


# --- A5: los artefactos de firma inobtenible no se publican por OAI-PMH ni CMIS ----------------


def test_oai_y_cmis_no_leen_las_constancias_de_firma_inobtenible() -> None:
    """Dato de salud/fallecimiento (Ley 1581 art. 5) y clasificado (Ley 1712 art. 18 lit. a): ningún
    módulo de interoperabilidad de archive-service toca estas tablas ni su servicio."""
    raiz = Path(__file__).resolve().parents[1] / "app"
    prohibidos = ("firma_excepcion", "constancia_firma_excepcion", "fundamento")
    revisados = 0
    for ruta in raiz.rglob("*.py"):
        nombre = ruta.name.lower()
        if not ("oai" in nombre or "cmis" in nombre or "ead" in nombre or "interop" in nombre):
            continue
        revisados += 1
        ast.parse(ruta.read_text(encoding="utf-8"))
        texto = ruta.read_text(encoding="utf-8")
        assert not any(p in texto for p in prohibidos), ruta
    assert revisados >= 1
