# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""C-6 (ADR-042): reempaquetado del AIP desde el bag anterior (unitario, sin Postgres ni S3).

El bag anterior se fabrica con el `build_bag` REAL y el servicio lo relee de un almacén en
memoria: lo que se mide es el contenido del ZIP nuevo, no llamadas a mocks.
"""

from __future__ import annotations

import hashlib
import io
import re
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from lxml import etree

import app.core.config as config_module
from app.schemas.preservacion import AipReempaquetarRequest
from app.services.aip_builder import (
    _PREMIS_NS,
    AipDocMaterial,
    BagAnteriorCorruptoError,
    build_bag,
    read_bag_anterior,
)
from app.services.aip_service import (
    AipAnteriorFixityMismatchError,
    AipNoEncontradoError,
    AipService,
    AipYaSustituidoError,
    IndiceNoEsRenovacionError,
)
from app.services.pdfa.types import PdfaResult
from app.services.retention_policy import InvalidRetentionError
from tests.conftest import INTERNAL_HEADERS, TENANT_HEADERS, TENANT_SLUG

EXP = "EXP-1"
_DOC = b"contenido real del oficio"
_IDX0 = b"<indice v='0'/>"
_IDX2 = b"<indice v='2'/>"
NOW = datetime.now(tz=UTC)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _doc() -> AipDocMaterial:
    return AipDocMaterial("R-1", "oficio.pdf", _DOC, _sha(_DOC), "application/pdf")


def _indice(content: bytes) -> AipDocMaterial:
    return AipDocMaterial(f"indice:{EXP}", "indice-firmado.xml", content, _sha(content), "application/xml")


def _bag_v1(*, docs=None, pdfa=None) -> bytes:
    docs = [_doc()] if docs is None else docs
    return build_bag(
        expediente_id=EXP,
        tenant_slug=TENANT_SLUG,
        docs=docs,
        indice=_indice(_IDX0),
        generado=NOW,
        actor="u1",
        pdfa_results=pdfa,
    ).zip_bytes


class _Mundo:
    """Un almacén y unos repositorios en memoria, parcheados sobre un `AipService` real."""

    def __init__(
        self,
        *,
        bag: bytes | None = None,
        retain_anterior: datetime | None = None,
        retain_max: datetime | None = None,
        indice_tipo: str = "firma_lta_renovada",
        indice_cadena_ok: bool = True,
        sucesor: dict | None = None,
        indice_expediente: str = EXP,
    ) -> None:
        self.bag = bag if bag is not None else _bag_v1()
        self.aip_ant_id = uuid.uuid4()
        self.worm_ant_id = uuid.uuid4()
        self.idx_file_id = uuid.uuid4()
        self.retain_anterior = retain_anterior or NOW + timedelta(days=1000)
        self.retain_max = retain_max or self.retain_anterior
        self.eventos: list[dict] = []
        self.worm_inserts: list[dict] = []
        self.aip_inserts: list[dict] = []
        anterior_sha = _sha(self.bag)
        self.anterior = {
            "id": self.aip_ant_id, "expediente_id": EXP, "premis_xml": "<premis/>",
            "aip_sha256": anterior_sha, "indice_sha256": _sha(_IDX0), "num_documentos": 1,
            "estado": "empaquetado", "created_at": NOW, "bucket": "b", "object_key": "k.zip",
            "aip_bytes": len(self.bag), "formato": "application/zip",
            "retain_until": self.retain_anterior, "retention_mode": "COMPLIANCE",
            "legal_hold": False, "version": 1, "sustituye_a": None, "motivo": "ingesta",
        }
        self.sucesor = sucesor
        self.fila_ant = {
            "id": self.worm_ant_id, "expediente_id": EXP, "tipo": "aip", "bucket": "b",
            "object_key": "k.zip", "version_id": "v-ant", "sha256": anterior_sha,
            "retain_until": self.retain_anterior, "retention_mode": "COMPLIANCE",
            "legal_hold": False, "objeto_tipo": "expediente", "file_id": None,
        }
        idx_worm = uuid.uuid4()
        root = {"id": uuid.uuid4(), "tipo": "indice", "expediente_id": EXP, "sha256": _sha(_IDX0)}
        self.fila_indice = {
            "id": idx_worm, "tipo": indice_tipo, "expediente_id": indice_expediente,
            "sha256": _sha(_IDX2),
        }
        self.cadena = [self.fila_indice, root] if indice_cadena_ok else [self.fila_indice, {**root, "sha256": "0" * 64}]
        self.minio = MagicMock()
        self.minio.bucket_exists = AsyncMock(return_value=True)
        self.minio.get_object_lock_config = AsyncMock(return_value=MagicMock())
        self.puts: list[tuple] = []
        self._ret = None

        def _put(bucket, key, stream, **kw):
            self.puts.append((bucket, key, stream.read(), kw))
            self._ret = kw["retention"]
            return MagicMock(version_id="v-nuevo")

        self.minio.put_object = AsyncMock(side_effect=_put)
        self.minio.copy_object = AsyncMock()
        self.minio.set_object_retention = AsyncMock()
        self.minio.remove_object = AsyncMock()

        async def _ret_read(*_a, **_k):
            return self._ret

        self.minio.get_object_retention = AsyncMock(side_effect=_ret_read)

        async def _get(bucket, key, **kw):
            data = self.bag if key == "k.zip" else _IDX2
            obj = MagicMock()
            obj.read = AsyncMock(return_value=data)
            obj.close = MagicMock()
            return obj

        self.minio.get_object = AsyncMock(side_effect=_get)
        self.retain_max_value = self.retain_max

    def servicio(self) -> AipService:
        m = self
        conn = AsyncMock()
        conn.transaction = MagicMock(return_value=AsyncMock())
        svc = AipService(conn, TENANT_SLUG)
        svc._evt_repo = SimpleNamespace(
            get_aip=AsyncMock(return_value=m.anterior),
            get_aip_sucesor=AsyncMock(return_value=m.sucesor),
            current_num_copias=AsyncMock(return_value=1),
            insert_aip=AsyncMock(side_effect=lambda d: (m.aip_inserts.append(d) or {**m.anterior, **{
                k: d[k] for k in d if k in m.anterior}, "created_at": NOW, "estado": "empaquetado"})),
            insert_evento=AsyncMock(side_effect=lambda e, agente=None: m.eventos.append(e)),
        )
        svc._worm_repo = SimpleNamespace(
            get_by_file_id=AsyncMock(return_value=m.fila_indice),
            cadena_ascendente=AsyncMock(return_value=m.cadena),
            get_aip_por_sha256=AsyncMock(return_value=m.fila_ant),
            retain_until_maximo_aip=AsyncMock(return_value=m.retain_max_value),
            holds_activos=AsyncMock(return_value=[]),  # C-5: sin hold activo del expediente
            insert=AsyncMock(side_effect=lambda **kw: m.worm_inserts.append(kw)),
        )
        svc._file_repo = SimpleNamespace(
            get_by_id_any_state=AsyncMock(return_value=SimpleNamespace(
                sha256=_sha(_IDX2), bucket="bi", object_key="idx.xml", mime_type="application/xml")),
        )
        return svc

    def req(self, **kw) -> AipReempaquetarRequest:
        base = dict(
            expediente_id=EXP, aip_anterior_id=self.aip_ant_id, indice_file_id=self.idx_file_id,
            indice_xml_sha256=_sha(_IDX2), firma_id=uuid.uuid4(), ordinal=2,
        )
        base.update(kw)
        return AipReempaquetarRequest(**base)


@pytest.fixture(autouse=True)
def _habilitado(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config_module.settings, "preservation_enabled", True)
    monkeypatch.setattr(config_module.settings, "preservation_replica_enabled", False)
    with patch("app.services.aip_service.get_minio") as gm, patch(
        "app.services.aip_service.audit.append", new=AsyncMock()
    ):
        gm.return_value = None  # se pisa por test
        yield gm


def _zip(data: bytes) -> dict[str, bytes]:
    z = zipfile.ZipFile(io.BytesIO(data))
    return {n: z.read(n) for n in z.namelist()}


@pytest.mark.asyncio
async def test_payload_identico_y_solo_cambia_el_indice(_habilitado) -> None:
    m = _Mundo()
    _habilitado.return_value = m.minio
    resp, creado = await m.servicio().reempaquetar_aip(m.req(), actor="sys")
    assert creado and resp.version == 2 and resp.motivo == "renovacion_lta"
    nuevo, viejo = _zip(m.puts[0][2]), _zip(m.bag)
    assert nuevo["data/oficio.pdf"] == viejo["data/oficio.pdf"] == _DOC
    assert nuevo["indice-firmado.xml"] == _IDX2 and viejo["indice-firmado.xml"] == _IDX0
    # manifest y tagmanifest coherentes con el nuevo contenido
    assert _sha(_DOC) in nuevo["manifest-sha256.txt"].decode()
    tm = nuevo["tagmanifest-sha256.txt"].decode()
    assert _sha(_IDX2) in tm and _sha(_IDX0) not in tm
    # el AIP anterior no se toca: ni borrado ni retención reescrita
    m.minio.remove_object.assert_not_called()
    m.minio.set_object_retention.assert_not_called()


@pytest.mark.asyncio
async def test_premis_declara_derivacion_y_packing_y_valida_xsd(_habilitado) -> None:
    m = _Mundo()
    _habilitado.return_value = m.minio
    await m.servicio().reempaquetar_aip(m.req(), actor="sys")
    premis = _zip(m.puts[0][2])["premis.xml"]  # build_bag ya validó contra el XSD LoC
    root = etree.fromstring(premis)
    ns = {"p": _PREMIS_NS}
    assert root.findtext(".//p:relationshipType", namespaces=ns) == "derivation"
    assert root.findtext(".//p:relationshipSubType", namespaces=ns) == "has source"
    assert root.findtext(".//p:relatedObjectIdentifierValue", namespaces=ns) == str(m.aip_ant_id)
    tipos = [e.text for e in root.findall(".//p:eventType", ns)]
    assert "packing" in tipos and "ingestion" in tipos
    roles = {r.text for r in root.findall(".//p:linkingObjectRole", ns)}
    assert roles == {"source", "outcome"}
    detail = [d.text for d in root.findall(".//p:eventDetail", ns) if "reempaquetado" in (d.text or "")]
    assert detail and "ordinal 2" in detail[0]


@pytest.mark.asyncio
async def test_veredictos_pdfa_se_reutilizan_sin_revalidar(_habilitado) -> None:
    pdfa = {"R-1": PdfaResult("conformant", "2b", "veraPDF 1.2", {})}
    m = _Mundo(bag=_bag_v1(pdfa=pdfa))
    _habilitado.return_value = m.minio
    await m.servicio().reempaquetar_aip(m.req(), actor="sys")
    root = etree.fromstring(_zip(m.puts[0][2])["premis.xml"])
    ns = {"p": _PREMIS_NS}
    vals = [e for e in root.findall("p:event", ns) if e.findtext("p:eventType", namespaces=ns) == "validation"]
    assert len(vals) == 1
    assert "outcome=conformant" in vals[0].findtext(".//p:eventOutcomeDetailNote", namespaces=ns)


@pytest.mark.asyncio
async def test_indice_only_mantiene_el_indice_en_data(_habilitado) -> None:
    m = _Mundo(bag=_bag_v1(docs=[]))
    _habilitado.return_value = m.minio
    await m.servicio().reempaquetar_aip(m.req(), actor="sys")
    nuevo = _zip(m.puts[0][2])
    assert nuevo["data/indice-firmado.xml"] == _IDX2
    assert "indice-firmado.xml" not in nuevo  # nunca duplicado como tag file


@pytest.mark.asyncio
async def test_retencion_nunca_menor_hereda_el_mayor_de_la_cadena(_habilitado) -> None:
    mayor = NOW + timedelta(days=5000)
    m = _Mundo(retain_anterior=NOW + timedelta(days=1000), retain_max=mayor)
    _habilitado.return_value = m.minio
    resp, _ = await m.servicio().reempaquetar_aip(m.req(), actor="sys")
    assert m.aip_inserts[0]["retain_until"] == mayor
    assert m.worm_inserts[0]["retain_until"] == mayor
    assert m.worm_inserts[0]["retain_until"] >= m.retain_anterior
    # liga WORM al anterior (cascada de renovación) y al AIP anterior (versión)
    assert m.worm_inserts[0]["renueva_a"] == m.worm_ant_id and m.worm_inserts[0]["tipo"] == "aip"
    assert m.aip_inserts[0]["sustituye_a"] == m.aip_ant_id and m.aip_inserts[0]["version"] == 2
    # eventos: FIJACION ok y RENOVACION con la clave de idempotencia
    tipos = {e["tipo"] for e in m.eventos}
    assert {"FIJACION", "RENOVACION"} <= tipos
    ren = next(e for e in m.eventos if e["tipo"] == "RENOVACION")
    assert ren["detalle"]["subtipo"] == "aip_reempaquetado"
    assert re.fullmatch(r"aip:EXP-1:[0-9a-f-]{36}:2", ren["detalle"]["dedup_key"])


@pytest.mark.asyncio
async def test_retencion_vencida_no_reempaqueta(_habilitado) -> None:
    m = _Mundo(retain_anterior=NOW - timedelta(days=1), retain_max=NOW - timedelta(days=1))
    _habilitado.return_value = m.minio
    with pytest.raises(InvalidRetentionError):
        await m.servicio().reempaquetar_aip(m.req(), actor="sys")
    m.minio.put_object.assert_not_called()


@pytest.mark.asyncio
async def test_reintento_con_el_mismo_indice_es_idempotente(_habilitado) -> None:
    m = _Mundo()
    m.sucesor = {**m.anterior, "id": uuid.uuid4(), "version": 2, "sustituye_a": m.aip_ant_id,
                 "motivo": "renovacion_lta", "indice_sha256": _sha(_IDX2)}
    _habilitado.return_value = m.minio
    resp, creado = await m.servicio().reempaquetar_aip(m.req(), actor="sys")
    assert creado is False and resp.id == m.sucesor["id"]
    m.minio.put_object.assert_not_called()


@pytest.mark.asyncio
async def test_sucesor_con_otro_indice_es_409(_habilitado) -> None:
    m = _Mundo()
    m.sucesor = {**m.anterior, "id": uuid.uuid4(), "version": 2, "sustituye_a": m.aip_ant_id,
                 "motivo": "renovacion_lta", "indice_sha256": "a" * 64}
    _habilitado.return_value = m.minio
    with pytest.raises(AipYaSustituidoError):
        await m.servicio().reempaquetar_aip(m.req(), actor="sys")
    m.minio.put_object.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kw",
    [
        {"indice_tipo": "indice"},  # no es una renovación
        {"indice_cadena_ok": False},  # no desciende del embebido
        {"indice_expediente": "OTRO"},  # de otro expediente
    ],
)
async def test_indice_que_no_es_renovacion_del_embebido_es_422(_habilitado, kw) -> None:
    m = _Mundo(**kw)
    _habilitado.return_value = m.minio
    with pytest.raises(IndiceNoEsRenovacionError):
        await m.servicio().reempaquetar_aip(m.req(), actor="sys")
    m.minio.put_object.assert_not_called()


@pytest.mark.asyncio
async def test_aip_anterior_de_otro_expediente_es_404(_habilitado) -> None:
    m = _Mundo()
    _habilitado.return_value = m.minio
    with pytest.raises(AipNoEncontradoError):
        await m.servicio().reempaquetar_aip(m.req(expediente_id="OTRO"), actor="sys")


@pytest.mark.asyncio
async def test_bag_anterior_corrupto_es_409_con_fixity_fallo(_habilitado) -> None:
    buenos = _bag_v1()
    z = _zip(buenos)
    z["data/oficio.pdf"] = b"adulterado"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, c in z.items():
            zf.writestr(n, c)
    m = _Mundo(bag=buf.getvalue())
    # la huella del ZIP registrada es la del ZIP corrupto: lo que falla es el manifest interno
    _habilitado.return_value = m.minio
    with pytest.raises(AipAnteriorFixityMismatchError):
        await m.servicio().reempaquetar_aip(m.req(), actor="sys")
    assert any(e["tipo"] == "FIJACION" and e["resultado"] == "fallo" for e in m.eventos)
    m.minio.put_object.assert_not_called()


@pytest.mark.asyncio
async def test_zip_con_huella_distinta_a_la_registrada_es_409(_habilitado) -> None:
    m = _Mundo()
    m.anterior["aip_sha256"] = "b" * 64
    _habilitado.return_value = m.minio
    with pytest.raises(AipAnteriorFixityMismatchError):
        await m.servicio().reempaquetar_aip(m.req(), actor="sys")
    assert any(e["tipo"] == "FIJACION" and e["resultado"] == "fallo" for e in m.eventos)


def test_read_bag_anterior_rechaza_un_zip_ilegible() -> None:
    with pytest.raises(BagAnteriorCorruptoError):
        read_bag_anterior(b"no es un zip", expediente_id=EXP)


# -- HTTP ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ruta_exige_token_interno(client_with_conn) -> None:
    client, _conn, _minio = client_with_conn
    body = {
        "expediente_id": EXP, "aip_anterior_id": str(uuid.uuid4()),
        "indice_file_id": str(uuid.uuid4()), "indice_xml_sha256": "a" * 64,
        "firma_id": str(uuid.uuid4()), "ordinal": 1,
    }
    r = await client.post("/api/v1/preservacion/aip-reempaquetar-internal", json=body, headers=TENANT_HEADERS)
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc_name", "status", "code"),
    [
        ("AipNoEncontradoError", 404, "aip_not_found"),
        ("AipYaSustituidoError", 409, "aip_ya_sustituido"),
        ("AipAnteriorFixityMismatchError", 409, "aip_anterior_fixity_mismatch"),
        ("IndiceNoEsRenovacionError", 422, "indice_no_es_renovacion_del_embebido"),
    ],
)
async def test_errores_http(client_with_conn, monkeypatch, exc_name, status, code) -> None:
    import app.services.aip_service as mod

    client, _c, _m = client_with_conn
    monkeypatch.setattr(
        AipService, "reempaquetar_aip", AsyncMock(side_effect=getattr(mod, exc_name)("x"))
    )
    body = {
        "expediente_id": EXP, "aip_anterior_id": str(uuid.uuid4()),
        "indice_file_id": str(uuid.uuid4()), "indice_xml_sha256": "a" * 64,
        "firma_id": str(uuid.uuid4()), "ordinal": 1,
    }
    r = await client.post(
        "/api/v1/preservacion/aip-reempaquetar-internal", json=body, headers=INTERNAL_HEADERS
    )
    assert r.status_code == status and r.json()["detail"]["error"] == code


@pytest.mark.asyncio
async def test_201_si_crea_y_200_si_ya_existia(client_with_conn, monkeypatch) -> None:
    from app.schemas.preservacion import AipResponse

    client, _c, _m = client_with_conn
    resp = AipResponse(
        id=uuid.uuid4(), expediente_id=EXP, aip_sha256="c" * 64, indice_sha256="d" * 64,
        num_documentos=1, estado="empaquetado", created_at=NOW, premis_xml="<p/>", bucket="b",
        object_key="k", aip_bytes=1, formato="application/zip", retain_until=NOW,
        retention_mode="COMPLIANCE", legal_hold=False, version=2, motivo="renovacion_lta",
        sustituye_a=uuid.uuid4(),
    )
    body = {
        "expediente_id": EXP, "aip_anterior_id": str(uuid.uuid4()),
        "indice_file_id": str(uuid.uuid4()), "indice_xml_sha256": "a" * 64,
        "firma_id": str(uuid.uuid4()), "ordinal": 1,
    }
    url = "/api/v1/preservacion/aip-reempaquetar-internal"
    monkeypatch.setattr(AipService, "reempaquetar_aip", AsyncMock(return_value=(resp, True)))
    assert (await client.post(url, json=body, headers=INTERNAL_HEADERS)).status_code == 201
    monkeypatch.setattr(AipService, "reempaquetar_aip", AsyncMock(return_value=(resp, False)))
    r = await client.post(url, json=body, headers=INTERNAL_HEADERS)
    assert r.status_code == 200 and r.json()["version"] == 2


def test_ruta_de_conservacion_admitida_y_solo_esa() -> None:
    from app.core.conservacion import es_ruta_de_conservacion

    assert es_ruta_de_conservacion("POST", "/api/v1/preservacion/aip-reempaquetar-internal")
    assert not es_ruta_de_conservacion("POST", "/api/v1/preservacion/aip-reempaquetar-internal\n")
    assert not es_ruta_de_conservacion("GET", "/api/v1/preservacion/aip-reempaquetar-internal")
    assert not es_ruta_de_conservacion("POST", "/api/v1/preservacion/aip-reempaquetar")
