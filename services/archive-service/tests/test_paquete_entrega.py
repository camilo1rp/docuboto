# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""B4 / ADR-030 §5 — paquete de entrega de la transferencia secundaria.

Cada prueba apunta a una mutación concreta:

* asentar tras servir — mover los asientos fuera de la tx / después de empaquetar.
* quitar el 409 — `componentes_sin_object_lock` (o descargar antes de comprobar el lock).
* servir una primaria o no recibida — quitar `paquete_solo_secundaria_recibida`.
* no-read-up — quitar el 404 neutro del expediente (o su `max_clearance`).
* sobre «presente» por defecto — la nota debe decir `ausente` para v2/legadas.
* OAI/EAD/CMIS leyendo las tablas de excepción — guardián por fuente.
"""

from __future__ import annotations

import hashlib
import io
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.core.actor import Actor
from app.services.paquete_entrega import NS_NOTA
from app.services.transferencia_service import TransferenciaService
from tests.conftest import TENANT_HEADERS
from tests.test_acta_firma_inobtenible import ADMIN, TENANT, _excepcion_row, _revocada

NOW = datetime.now(tz=UTC)


class _Tx:
    def __init__(self, eventos: list) -> None:
        self._e = eventos

    async def __aenter__(self) -> None:
        self._e.append("tx_in")

    async def __aexit__(self, *a) -> None:
        self._e.append("tx_out")


def _con_sobre(tid, **kw) -> dict:
    base = {"sobre_minio_key": "f-sobre/sobre.xml", "worm_sobre_protegido": True, "worm_protegido": True}
    return _excepcion_row(tid, **{**base, **kw})


def _svc(
    *,
    tipo: str = "secundaria",
    estado: str = "recibida",
    excepciones: list | None = None,
    visible: bool = True,
    sello_lock: bool = True,
    personales: list | None = None,
    indice_lock: bool = True,
    aip_lock: bool = True,
):
    eventos: list = []
    tid = uuid.uuid4()
    conn = MagicMock()
    conn.transaction = MagicMock(side_effect=lambda: _Tx(eventos))
    svc = TransferenciaService(conn, TENANT)
    repo = MagicMock()
    repo.get = AsyncMock(
        return_value={"id": tid, "tipo": tipo, "estado": estado, "expediente_id": uuid.uuid4()}
    )
    repo.get_acta = AsyncMock(
        return_value={
            "acta_fuid_xml": "<fuid>acta</fuid>",
            "acta_fuid_sha256": hashlib.sha256(b"<fuid>acta</fuid>").hexdigest(),
            "indice_version": 1,
        }
    )
    repo.get_acta_firma_worm = AsyncMock(
        return_value={
            "estado": "firmado",
            "minio_key_firmado": "f-acta/acta.xml",
            "worm_protegido_at": NOW if sello_lock else None,
        }
    )
    repo.list_acta_firma_personal = AsyncMock(return_value=personales or [])
    repo.get_firma_personal_worm = AsyncMock(return_value={"worm_protegido_at": NOW})
    repo.list_acta_firma_excepciones = AsyncMock(return_value=excepciones or [])
    svc._repo = repo
    svc._index_repo = MagicMock()
    svc._index_repo.get_version = AsyncMock(
        return_value={"id": uuid.uuid4(), "estado": "firmado", "minio_key_firmado": "f-idx/indice.xml"}
    )
    svc._index_repo.get_by_id = AsyncMock(
        return_value={"worm_protegido_at": NOW if indice_lock else None}
    )
    svc._aip_repo = MagicMock()
    svc._aip_repo.get_empaquetado = AsyncMock(
        return_value={"estado": "empaquetado", "aip_worm_object_id": uuid.uuid4()}
        if aip_lock
        else {"estado": "pendiente", "aip_worm_object_id": None}
    )
    archive = MagicMock()
    archive.caller_clearance = AsyncMock(return_value=2)
    archive.get_expediente_by_id = AsyncMock(
        return_value=SimpleNamespace(code="EXP-1") if visible else None
    )
    svc._archive = archive
    return svc, tid, eventos, archive


ACTA = b"<fuid>acta</fuid>"
AIP = {
    "bucket": "orpycamcp-t-documents",
    "object_key": "aip/EXP-1.zip",
    "aip_sha256": "b" * 64,
    "aip_bytes": 123,
    "formato": "bagit-1.0",
    "retain_until": "2040-01-01T00:00:00Z",
    "retention_mode": "COMPLIANCE",
    "num_documentos": 3,
    "indice_sha256": None,
}


def _externos(
    eventos: list,
    *,
    audit_error: Exception | None = None,
    contenidos: dict | None = None,
    capturas: dict | None = None,
    aip: dict | None | bool = True,
):
    """`contenidos` cambia lo que ENTREGA el almacén (por file_id); `capturas` cambia la huella que
    el almacén registró al subir (por file_id; `None` = sin huella). Sin cambios coinciden."""
    contenidos = contenidos or {}

    def _original(fid: str) -> bytes:
        return ACTA if fid == "f-acta" else f"<c k='{fid}'/>".encode()

    def _bytes(fid: str) -> bytes:
        return contenidos.get(fid, _original(fid))

    async def _dl(*, tenant_slug, minio_key_firmado):
        eventos.append(("descarga", minio_key_firmado))
        return _bytes(minio_key_firmado.split("/", 1)[0])

    async def _audit(conn, **kw):
        if audit_error is not None:
            raise audit_error
        eventos.append(("audit", kw["action"], kw["payload"]))

    async def _hashes(*, tenant_slug, file_ids):
        res = {fid: hashlib.sha256(_original(fid)).hexdigest() for fid in file_ids}
        res.update(capturas or {})
        return {k: v for k, v in res.items() if v is not None}

    async def _get_aip(*, tenant_slug, expediente_id):
        return AIP if aip is True else aip

    dl = AsyncMock(side_effect=_dl)
    return (
        patch("app.clients.storage_client.download_signed_bytes", new=dl),
        _Pila(
            patch("app.services.transferencia_service.audit.append", new=AsyncMock(side_effect=_audit)),
            patch("app.clients.storage_client.fetch_file_hashes_internal", new=AsyncMock(side_effect=_hashes)),
            patch("app.clients.storage_client.get_aip", new=AsyncMock(side_effect=_get_aip)),
        ),
        dl,
    )


class _Pila:
    """Varios `patch` como un único contexto."""

    def __init__(self, *ps) -> None:
        self._ps = ps

    def __enter__(self) -> None:
        for p in self._ps:
            p.__enter__()

    def __exit__(self, *a) -> None:
        for p in reversed(self._ps):
            p.__exit__(*a)


def _abrir(paquete) -> dict[str, bytes]:
    paquete.seek(0)
    with zipfile.ZipFile(io.BytesIO(paquete.read())) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


@pytest.mark.asyncio
async def test_el_paquete_trae_acta_cadena_completa_y_nota_con_huellas() -> None:
    svc, tid, eventos, _ = _svc()
    revocada = _revocada(tid, protegida=True)
    revocada.update(
        sobre_minio_key="f-sobrec/c.xml", worm_sobre_protegido=True, worm_protegido=True,
        revocacion_sobre_minio_key="f-sobrer/r.xml", revocacion_worm_sobre_protegido=True,
        rol="recibe", ordinal=1,
    )
    vigente = _con_sobre(tid, rol="recibe", ordinal=2)
    svc._repo.list_acta_firma_excepciones.return_value = [revocada, vigente]
    dl, au, dlm = _externos(eventos)
    with dl, au:
        paquete, tamano = await svc.paquete_entrega(tid, Actor(id=ADMIN))
    ficheros = _abrir(paquete)
    paquete.close()

    assert tamano > 0
    assert set(ficheros) == {
        "acta/acta-fuid.xml",
        "acta/acta-fuid.xades.xml",
        "indice/indice-electronico.xades.xml",
        "clasificado/constancias/recibe-1.xml",
        "clasificado/revocaciones/recibe-1.xml",
        "clasificado/sobres/recibe-1-constancia.xml",
        "clasificado/sobres/recibe-1-revocacion.xml",
        "clasificado/constancias/recibe-2.xml",
        "clasificado/sobres/recibe-2-constancia.xml",
        "nota-entrega.xml",
        "checksums.txt",
    }
    # el acta viaja TAL CUAL; la nota la referencia por su huella y no la reescribe
    assert ficheros["acta/acta-fuid.xml"] == b"<fuid>acta</fuid>"
    raiz = ET.fromstring(ficheros["nota-entrega.xml"])
    assert raiz.find(f"{{{NS_NOTA}}}acta").get("acta_fuid_sha256") == hashlib.sha256(ACTA).hexdigest()
    # el AIP se REFERENCIA (bucket, clave, huella), no viaja dentro
    aip = raiz.find(f"{{{NS_NOTA}}}aip")
    assert (aip.get("object_key"), aip.get("sha256"), aip.get("incluido_en_paquete")) == (
        "aip/EXP-1.zip", "b" * 64, "no",
    )
    assert not any(n.endswith(".zip") for n in ficheros)
    comps = {
        c.get("ruta"): c.attrib for c in raiz.find(f"{{{NS_NOTA}}}componentes")
    }
    for ruta, attrs in comps.items():
        assert attrs["sha256"] == hashlib.sha256(ficheros[ruta]).hexdigest()
    cl = comps["clasificado/sobres/recibe-1-constancia.xml"]
    assert cl["clasificacion"] == "clasificada"
    assert cl["fundamento"] == "Ley 1712/2014 art. 18 lit. a"
    assert cl["acceso"] == "restringido a la entidad receptora"
    assert comps["acta/acta-fuid.xml"]["clasificacion"] == "ninguna"
    # checksums.txt cubre todo salvo a sí mismo
    lineas = ficheros["checksums.txt"].decode().strip().splitlines()
    assert len(lineas) == len(ficheros) - 1
    for ln in lineas:
        h, ruta = re.split(r"\s+", ln, maxsplit=1)
        assert hashlib.sha256(ficheros[ruta]).hexdigest() == h


@pytest.mark.asyncio
async def test_sobre_ausente_se_declara_en_la_nota_y_no_se_inventa() -> None:
    svc, tid, eventos, _ = _svc()
    legada = _excepcion_row(tid, worm_protegido=True, sobre_minio_key=None)
    svc._repo.list_acta_firma_excepciones.return_value = [legada]
    dl, au, _m = _externos(eventos)
    with dl, au:
        paquete, _ = await svc.paquete_entrega(tid, Actor(id=ADMIN))
    ficheros = _abrir(paquete)
    paquete.close()
    assert not any(r.startswith("clasificado/sobres/") for r in ficheros)
    comp = next(
        c
        for c in ET.fromstring(ficheros["nota-entrega.xml"]).iter(f"{{{NS_NOTA}}}componente")
        if c.get("ruta") == "clasificado/constancias/recibe-1.xml"
    )
    assert comp.get("sobre") == "ausente"
    # sin sobre no se revela fundamento: no hay asiento de lectura
    assert not [e for e in eventos if e[0] == "audit" and e[1] == "transferencia.acta_fundamento_leido"]


@pytest.mark.asyncio
async def test_los_asientos_van_en_una_tx_y_antes_de_servir() -> None:
    """Mutación «asentar tras servir»: al volver el método, los asientos ya están escritos,
    dentro de UNA tx, y el de generación lleva la huella real de la nota."""
    svc, tid, eventos, _ = _svc()
    svc._repo.list_acta_firma_excepciones.return_value = [_con_sobre(tid)]
    dl, au, _m = _externos(eventos)
    with dl, au:
        paquete, _ = await svc.paquete_entrega(tid, Actor(id=ADMIN))
    ficheros = _abrir(paquete)
    paquete.close()

    marcas = [e if isinstance(e, str) else e[0] + ":" + e[1] if e[0] == "audit" else e[0] for e in eventos]
    assert marcas.count("tx_in") == marcas.count("tx_out") == 1
    dentro = marcas[marcas.index("tx_in") : marcas.index("tx_out")]
    assert dentro == [
        "tx_in",
        "audit:transferencia.acta_fundamento_leido",
        "audit:transferencia.paquete_entrega_generado",
    ]
    leido = next(e for e in eventos if e[0] == "audit" and e[1] == "transferencia.acta_fundamento_leido")
    assert leido[2]["via"] == "paquete"
    gen = next(e for e in eventos if e[0] == "audit" and e[1] == "transferencia.paquete_entrega_generado")
    assert gen[2]["nota_sha256"] == hashlib.sha256(ficheros["nota-entrega.xml"]).hexdigest()
    assert gen[2]["componentes"]["acta/acta-fuid.xml"] == hashlib.sha256(b"<fuid>acta</fuid>").hexdigest()


@pytest.mark.asyncio
async def test_sin_asiento_no_hay_paquete() -> None:
    svc, tid, eventos, _ = _svc()
    svc._repo.list_acta_firma_excepciones.return_value = [_con_sobre(tid)]
    dl, au, _m = _externos(eventos, audit_error=RuntimeError("audit caído"))
    with dl, au, pytest.raises(RuntimeError):
        await svc.paquete_entrega(tid, Actor(id=ADMIN))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutar",
    [
        pytest.param(lambda s, t: setattr(s._repo.get_acta_firma_worm, "return_value",
                     {"estado": "firmado", "minio_key_firmado": "k", "worm_protegido_at": None}),
                     id="sello-del-acta-sin-lock"),
        pytest.param(lambda s, t: setattr(s._repo.get_acta_firma_worm, "return_value",
                     {"estado": "pendiente_firma", "minio_key_firmado": None, "worm_protegido_at": None}),
                     id="acta-sin-sellar"),
        pytest.param(lambda s, t: setattr(s._repo.list_acta_firma_excepciones, "return_value",
                     [_con_sobre(t, worm_protegido=False)]), id="constancia-sin-lock"),
        pytest.param(lambda s, t: setattr(s._repo.list_acta_firma_excepciones, "return_value",
                     [_con_sobre(t, worm_sobre_protegido=False)]), id="sobre-sin-lock"),
        pytest.param(lambda s, t: setattr(s._repo.list_acta_firma_excepciones, "return_value",
                     [_con_sobre(t, worm_sobre_protegido=None)]), id="sobre-sin-fila-worm"),
        pytest.param(lambda s, t: setattr(s._repo.list_acta_firma_excepciones, "return_value",
                     [_revocada(t, protegida=False, worm_protegido=True)]), id="revocacion-sin-lock"),
        pytest.param(lambda s, t: setattr(s._index_repo.get_by_id, "return_value",
                     {"worm_protegido_at": None}), id="indice-firmado-sin-lock"),
        pytest.param(lambda s, t: setattr(s._index_repo.get_version, "return_value",
                     {"id": uuid.uuid4(), "estado": "pendiente_firma", "minio_key_firmado": None}),
                     id="indice-sin-firmar"),
        pytest.param(lambda s, t: setattr(s._aip_repo.get_empaquetado, "return_value",
                     {"estado": "pendiente", "aip_worm_object_id": None}), id="aip-sin-empaquetar"),
        pytest.param(lambda s, t: setattr(s._aip_repo.get_empaquetado, "return_value",
                     {"estado": "omitido", "aip_worm_object_id": None}), id="aip-omitido"),
    ],
)
async def test_409_si_algun_componente_no_esta_bajo_object_lock(mutar) -> None:
    """Mutación «quitar el 409»: nada se descarga ni se asienta."""
    svc, tid, eventos, _ = _svc()
    svc._repo.list_acta_firma_excepciones.return_value = [_con_sobre(tid)]
    mutar(svc, tid)
    dl, au, descargas = _externos(eventos)
    with dl, au, pytest.raises(HTTPException) as exc:
        await svc.paquete_entrega(tid, Actor(id=ADMIN))
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "componentes_sin_object_lock"
    assert exc.value.detail["componentes"] or exc.value.detail["componentes_clasificados"]
    descargas.assert_not_awaited()
    assert not [e for e in eventos if isinstance(e, tuple) and e[0] == "audit"]


@pytest.mark.asyncio
async def test_aip_sin_object_lock_en_el_almacen_es_409() -> None:
    """El AIP se referencia por clave/huella: si el almacén no confirma retención, no hay paquete."""
    for aip in (None, {**AIP, "retention_mode": None}, {**AIP, "object_key": None}):
        svc, tid, eventos, _ = _svc()
        dl, au, descargas = _externos(eventos, aip=aip)
        with dl, au, pytest.raises(HTTPException) as exc:
            await svc.paquete_entrega(tid, Actor(id=ADMIN))
        assert exc.value.status_code == 409
        assert "aip" in exc.value.detail["componentes"]
        descargas.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_409_agrega_los_clasificados_y_no_enumera_sus_rutas() -> None:
    """Mutación: volver a listar `clasificado/...` en `componentes` (revela cuántas excepciones hay)."""
    svc, tid, eventos, _ = _svc()
    svc._repo.list_acta_firma_excepciones.return_value = [
        _con_sobre(tid, rol="recibe", ordinal=1, worm_protegido=False),
        _con_sobre(tid, rol="recibe", ordinal=2, worm_protegido=False),
    ]
    svc._index_repo.get_by_id.return_value = {"worm_protegido_at": None}
    dl, au, _m = _externos(eventos)
    with dl, au, pytest.raises(HTTPException) as exc:
        await svc.paquete_entrega(tid, Actor(id=ADMIN))
    d = exc.value.detail
    assert d["componentes"] == ["indice/indice-electronico.xades.xml"]
    assert d["componentes_clasificados"] == 2
    assert "2 componentes clasificados" in d["detail"]
    assert not any("clasificado/" in str(v) for v in d["componentes"])


def _incidentes(eventos: list) -> list:
    return [e for e in eventos if isinstance(e, tuple) and e[0] == "audit" and e[1].endswith("incidente")]


@pytest.mark.asyncio
async def test_acta_alterada_en_la_base_es_409_con_asiento_de_incidente() -> None:
    """Mutación: quitar el recotejo de `acta_fuid_xml` contra `acta_fuid_sha256`."""
    svc, tid, eventos, _ = _svc()
    svc._repo.get_acta.return_value = {
        "acta_fuid_xml": "<fuid>acta ALTERADA</fuid>",
        "acta_fuid_sha256": hashlib.sha256(ACTA).hexdigest(),
        "indice_version": 1,
    }
    dl, au, descargas = _externos(eventos)
    with dl, au, pytest.raises(HTTPException) as exc:
        await svc.paquete_entrega(tid, Actor(id=ADMIN))
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "componente_integridad_discrepante"
    [inc] = _incidentes(eventos)
    assert inc[1] == "transferencia.paquete_entrega_incidente"
    assert inc[2]["motivo"] == "acta_fuid_xml_distinta_de_su_huella"
    descargas.assert_not_awaited()
    assert not [e for e in eventos if isinstance(e, tuple) and e[1:2] == ("transferencia.paquete_entrega_generado",)]


@pytest.mark.asyncio
async def test_sello_del_acta_que_no_firma_el_acta_congelada_es_incidente() -> None:
    """Copia WORM del acta distinta del acta de la base (mutación: quitar el cotejo con el sello)."""
    svc, tid, eventos, _ = _svc()
    otro = b"<fuid>otra acta</fuid>"
    dl, au, _m = _externos(
        eventos,
        contenidos={"f-acta": otro},
        capturas={"f-acta": hashlib.sha256(otro).hexdigest()},  # la captura ya es la del otro
    )
    with dl, au, pytest.raises(HTTPException) as exc:
        await svc.paquete_entrega(tid, Actor(id=ADMIN))
    assert exc.value.status_code == 409
    [inc] = _incidentes(eventos)
    assert inc[2]["motivo"] == "sello_del_acta_no_firma_el_acta_congelada"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("contenidos", "capturas", "motivo"),
    [
        pytest.param({"f-idx": b"<alterado/>"}, None, "contenido_distinto_de_la_captura", id="bytes-distintos"),
        pytest.param(None, {"f-idx": None}, "huella_de_captura_ausente", id="sin-huella-de-captura"),
        pytest.param(None, {"f-idx": "0" * 64}, "contenido_distinto_de_la_captura", id="captura-distinta"),
    ],
)
async def test_componente_que_no_reproduce_su_huella_de_captura_es_incidente(
    contenidos, capturas, motivo
) -> None:
    """Mutación: comparar contra lo descargado (checksums autoconsistentes) o no comparar."""
    svc, tid, eventos, _ = _svc()
    dl, au, _m = _externos(eventos, contenidos=contenidos, capturas=capturas)
    with dl, au, pytest.raises(HTTPException) as exc:
        await svc.paquete_entrega(tid, Actor(id=ADMIN))
    assert exc.value.status_code == 409
    [inc] = _incidentes(eventos)
    assert inc[2]["motivo"] == motivo
    assert inc[2]["ruta"] == "indice/indice-electronico.xades.xml"
    assert not [e for e in eventos if isinstance(e, tuple) and e[1:2] == ("transferencia.paquete_entrega_generado",)]


@pytest.mark.asyncio
async def test_un_componente_clasificado_discrepante_no_se_nombra_en_la_respuesta() -> None:
    svc, tid, eventos, _ = _svc()
    svc._repo.list_acta_firma_excepciones.return_value = [_con_sobre(tid)]
    dl, au, _m = _externos(eventos, contenidos={"f-sobre": b"<manipulado/>"})
    with dl, au, pytest.raises(HTTPException) as exc:
        await svc.paquete_entrega(tid, Actor(id=ADMIN))
    assert exc.value.detail["componente"] == "clasificado"
    assert _incidentes(eventos)[0][2]["ruta"].startswith("clasificado/")


@pytest.mark.asyncio
async def test_los_componentes_viajan_como_bytes_sin_decodificar() -> None:
    """Mutación: volver a `.text` (un XML en latin-1 cambiaría de bytes y de huella)."""
    svc, tid, eventos, _ = _svc()
    latin = "<c>año</c>".encode("latin-1")
    dl, au, _m = _externos(
        eventos, contenidos={"f-idx": latin}, capturas={"f-idx": hashlib.sha256(latin).hexdigest()}
    )
    with dl, au:
        paquete, _ = await svc.paquete_entrega(tid, Actor(id=ADMIN))
    assert _abrir(paquete)["indice/indice-electronico.xades.xml"] == latin
    paquete.close()


@pytest.mark.asyncio
async def test_sin_huellas_de_captura_es_503() -> None:
    svc, tid, eventos, _ = _svc()
    dl, au, _m = _externos(eventos)
    with dl, au, patch(
        "app.clients.storage_client.fetch_file_hashes_internal", new=AsyncMock(return_value={})
    ), pytest.raises(HTTPException) as exc:
        await svc.paquete_entrega(tid, Actor(id=ADMIN))
    assert exc.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(("tipo", "estado"), [("primaria", "recibida"), ("secundaria", "enviada")])
async def test_solo_secundaria_recibida(tipo: str, estado: str) -> None:
    svc, tid, eventos, _ = _svc(tipo=tipo, estado=estado)
    dl, au, descargas = _externos(eventos)
    with dl, au, pytest.raises(HTTPException) as exc:
        await svc.paquete_entrega(tid, Actor(id=ADMIN))
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "paquete_solo_secundaria_recibida"
    descargas.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_read_up_es_404_neutro_antes_de_cualquier_409() -> None:
    """Un expediente que el llamante no puede leer es 404 aunque la transferencia sea primaria:
    el 409 no es un oráculo. El clearance viaja a la consulta del expediente."""
    svc, tid, eventos, archive = _svc(visible=False, tipo="primaria")
    dl, au, _m = _externos(eventos)
    with dl, au, pytest.raises(HTTPException) as exc:
        await svc.paquete_entrega(tid, Actor(id=ADMIN))
    assert exc.value.status_code == 404
    assert archive.get_expediente_by_id.await_args.kwargs["max_clearance"] == 2


@pytest.mark.asyncio
async def test_componente_que_el_almacen_no_entrega_es_503_sin_asientos() -> None:
    svc, tid, eventos, _ = _svc()
    dl, au, descargas = _externos(eventos)
    descargas.side_effect = None
    descargas.return_value = None
    with dl, au, pytest.raises(HTTPException) as exc:
        await svc.paquete_entrega(tid, Actor(id=ADMIN))
    assert exc.value.status_code == 503
    assert not [e for e in eventos if isinstance(e, tuple) and e[0] == "audit"]


@pytest.mark.asyncio
async def test_el_endpoint_exige_administracion_nivel_3(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Mutación: `min_crud=2` o sin gate."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    resp = await client.post(
        f"/api/v1/transferencias/{uuid.uuid4()}/paquete-entrega", headers=TENANT_HEADERS
    )
    assert resp.status_code == 403
    consulta = next(c for c in mock_conn.fetchval.call_args_list if "auth_permissions" in c.args[0])
    assert consulta.args[2:] == ("USUA_PERM_ADMIN", 3)


@pytest.mark.asyncio
async def test_el_endpoint_sirve_un_zip_con_su_longitud(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid = uuid.uuid4()
    svc, _t, eventos, _a = _svc()
    dl, au, _m = _externos(eventos)
    original = TransferenciaService.paquete_entrega

    async def _delegado(self, transferencia_id, actor):
        return await original(svc, tid, actor)

    with dl, au, patch(
        "app.routers.transferencias.TransferenciaService.paquete_entrega", new=_delegado
    ):
        resp = await client.post(f"/api/v1/transferencias/{tid}/paquete-entrega", headers=TENANT_HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    assert int(resp.headers["content-length"]) == len(resp.content)
    assert "nota-entrega.xml" in zipfile.ZipFile(io.BytesIO(resp.content)).namelist()


# --- guardián: ni OAI, ni EAD, ni CMIS ven la cadena de excepciones ---------------------------

_APP = Path(__file__).resolve().parents[1] / "app"
_PROHIBIDO = re.compile(r"transferencia_acta_firma_excepcion|paquete_entrega|nota-entrega|sobre_minio_key")


def _interop() -> list[Path]:
    return [
        p
        for p in _APP.rglob("*.py")
        if re.search(r"oai|ead|cmis|public", p.name) and "paquete" not in p.name
    ]


def test_guardian_interoperabilidad_no_toca_la_cadena_de_excepciones() -> None:
    interop = _interop()
    assert {p.name for p in interop} >= {"oai.py", "oai_service.py", "oai_repository.py"}  # control positivo
    ofensores = [p.name for p in interop if _PROHIBIDO.search(p.read_text(encoding="utf-8"))]
    assert not ofensores, ofensores
    # control: el guardián SÍ ve la tabla donde corresponde
    assert _PROHIBIDO.search((_APP / "repositories" / "transferencia_repository.py").read_text())
