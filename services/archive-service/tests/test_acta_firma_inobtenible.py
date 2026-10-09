# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""AREA-07-10: acto «firma inobtenible» del acta de entrega.

Una parte del acta (elabora/entrega/recibe) que no puede firmar (fallecimiento, retiro,
incapacidad) se declara con un acto administrativo explícito: constancia inmutable, FIRMADA
(sello institucional XAdES-T + firma personal del declarante según la política de la entidad) y
protegida WORM, con asiento atómico; el acta queda `firmada_con_excepcion` (o
`sellada_con_excepcion` si ninguna parte firmó), NUNCA `firmada_completa`. Pure-unit; la forma
real del SQL (CHECK, triggers, atomicidad) está en `tests/integration/test_firma_inobtenible_sql.py`.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.clients.document_client import DocumentClientError
from app.clients.signature_client import (
    SignedIndice,
    SignOutcome,
    SignPersonalOutcome,
    VerifyResult,
)
from app.core.actor import Actor
from app.schemas.transferencia import FirmaInobtenibleRequest, RevocarFirmaInobtenibleRequest
from app.services.constancia_firma_excepcion import (
    canonicalizar,
    compromiso_fundamento,
    sha256_c14n,
    sha256_hex,
)
from app.services.transferencia_service import TransferenciaService
from tests.conftest import TENANT_HEADERS, USER_ID, make_expediente_record
from tests.test_transferencia_acta_firma_personal import _personal_row, _signed_personal
from tests.test_transferencias import _acta_firma_row, _acta_row, _transfer_row

TENANT = "demo"
ADMIN = uuid.uuid4()
PARTE = uuid.uuid4()
RAD = uuid.uuid4()
ANEXO = uuid.uuid4()
FUNDAMENTO = "Certificado de defuncion del receptor, radicado en la entidad el 2026-09-30."
FUNDAMENTO_REVOCACION = "Se declaro por error: el receptor esta vivo y firmara el acta."
NUMERO_RAD = "2026-DEMO-E-000123"
SHA_ANEXO = "e" * 64
SAL = bytes(range(32))  # sal de la fila de prueba (la real es aleatoria por registro)
SAL_REV = bytes(range(32, 64))
COMPROMISO = compromiso_fundamento(SAL, FUNDAMENTO)
COMPROMISO_REV = compromiso_fundamento(SAL_REV, FUNDAMENTO_REVOCACION)


def _usuario(uid: uuid.UUID, username: str, *, cn: str | None = None, cargo: str | None = None,
             cert_activo: bool = False) -> dict:
    return {
        "id": uid,
        "username": username,
        "subject_dn": f"CN={cn},O=Entidad de prueba,C=CO" if cn else None,
        "cargo": cargo,
        "certificado_activo": cert_activo,
    }


def _conn(*, politica: str | None = None, usuarios: list[dict] | None = None) -> AsyncMock:
    """`politica`: valor JSON de `sgd_param_admin.firma_personal_require_xades` (None = sin fila).
    `usuarios`: filas de `auth_users` (+ clave de firma) que resuelve la instantánea de identidad."""
    conn = AsyncMock()
    conn.execute.return_value = None
    catalogo = {
        u["id"]: u
        for u in (
            usuarios
            if usuarios is not None
            else [_usuario(ADMIN, "admin.user"), _usuario(PARTE, "parte.user")]
        )
    }

    async def _fetch(query, *args):
        if "auth_users" in query:
            return [catalogo[i] for i in args[0] if i in catalogo]
        return []

    conn.fetch.side_effect = _fetch
    conn.fetchrow.return_value = None

    async def _fetchval(query, *args):
        if "sgd_param_admin" in query:
            return json.dumps(politica) if politica is not None else None
        if "information_schema.columns" in query:
            return 7  # el catálogo de usuarios y la clave de firma tienen la forma esperada
        return None

    conn.fetchval.side_effect = _fetchval
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _payload(**kw) -> FirmaInobtenibleRequest:
    base = dict(
        rol="recibe",
        motivo="fallecimiento",
        fundamento=FUNDAMENTO,
        referencia_radicado_id=RAD,
        referencia_anexo_id=ANEXO,
    )
    base.update(kw)
    return FirmaInobtenibleRequest(**base)


def _excepcion_row(tid: uuid.UUID, **kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "transferencia_id": tid,
        "rol": "recibe",
        "parte_id": PARTE,
        "motivo": "fallecimiento",
        "fundamento": FUNDAMENTO,
        "referencia_documental": f"radicado:{RAD}",
        "referencia_radicado_id": RAD,
        "referencia_anexo_id": ANEXO,
        "referencia_radicado_numero": NUMERO_RAD,
        "referencia_anexo_sha256": SHA_ANEXO,
        "ordinal": 1,
        "sustituye_a": None,
        "constancia_version": 3,
        "fundamento_sal": SAL,
        "fundamento_compromiso": COMPROMISO,
        "fundamento_compromiso_algoritmo": "SHA-256-salted",
        "declarante_nombre": "admin.user",
        "declarante_cargo": None,
        "declarante_fuente": "usuario",
        "parte_nombre": "parte.user",
        "parte_cargo": None,
        "parte_fuente": "usuario",
        "declarada_por": ADMIN,
        "declarada_en": datetime.now(tz=UTC),
        "constancia_sha256": "c" * 64,
        "firma_id": uuid.uuid4(),
        "minio_key_firmado": "file-id/constancia-firmada.xml",
        "xades_level": "XAdES-T",
        "firma_personal_id": None,
        "minio_key_firmado_personal": None,
        "xades_level_personal": None,
        "firma_personal_requerida": False,
        "revocacion_id": None,
        "revocada_por": None,
        "revocada_en": None,
        "revocacion_fundamento": None,
        "revocacion_version": None,
        "revocacion_sha256": None,
        "revocacion_firma_id": None,
        "revocacion_minio_key_firmado": None,
        "revocacion_xades_level": None,
        "revocacion_firma_personal_id": None,
        "revocacion_minio_key_firmado_personal": None,
        "revocacion_xades_level_personal": None,
        "revocante_nombre": None,
        "revocante_cargo": None,
        "revocante_fuente": None,
        "revocacion_causa": None,
        "revocacion_fundamento_sal": None,
        "revocacion_fundamento_compromiso": None,
        "revocacion_fundamento_compromiso_algoritmo": None,
        "worm_retain_until": None,
        "revocacion_worm_retain_until": None,
        "worm_protegido": None,
        "worm_personal_protegido": None,
        "revocacion_worm_protegido": None,
        "revocacion_worm_personal_protegido": None,
    }
    base.update(kw)
    return base


def _revocada(tid: uuid.UUID, *, protegida: bool = True, personal: bool = False, **kw) -> dict:
    """Eslabón YA revocado: con la revocación sellada (y protegida WORM, salvo `protegida=False`)."""
    base = _excepcion_row(
        tid,
        revocacion_id=uuid.uuid4(),
        revocada_por=ADMIN,
        revocada_en=datetime.now(tz=UTC),
        revocacion_fundamento=FUNDAMENTO_REVOCACION,
        revocacion_version=3,
        revocacion_sha256="d" * 64,
        revocacion_firma_id=uuid.uuid4(),
        revocacion_minio_key_firmado="file-id/revocacion-firmada.xml",
        revocacion_xades_level="XAdES-T",
        revocante_nombre="admin.user",
        revocante_fuente="usuario",
        revocacion_causa="error_de_declaracion",
        revocacion_fundamento_sal=SAL_REV,
        revocacion_fundamento_compromiso=COMPROMISO_REV,
        revocacion_fundamento_compromiso_algoritmo="SHA-256-salted",
        revocacion_worm_protegido=protegida,
    )
    if personal:
        base.update(
            revocacion_firma_personal_id=uuid.uuid4(),
            revocacion_minio_key_firmado_personal="file-id/revocacion-personal.xml",
            revocacion_xades_level_personal="XAdES-T",
            revocacion_worm_personal_protegido=protegida,
        )
    base.update(kw)
    return base


def _sello_ok() -> SignOutcome:
    return SignOutcome(
        signed=SignedIndice(
            firma_id=uuid.uuid4(),
            minio_key_firmado="file-id/constancia-firmada.xml",
            nivel_conformidad="xades_t_no_acreditado",
            xades_level="XAdES-T",
            tipo_firma="xades_local",
            acreditado=False,
            certificado_fingerprint="ab:cd",
        )
    )


@contextmanager
def _externos(
    *,
    sello: SignOutcome | None = None,
    personal: SignPersonalOutcome | None = None,
    radicado_visible: bool = True,
    anexos: dict | None = None,
    doc_caido: bool = False,
    permiso_firma: bool = True,
    huella_anexo: str | None = SHA_ANEXO,
):
    """Los colaboradores HTTP de `declarar_firma_inobtenible`: document-service (soporte),
    signature-service (sello y firma personal) y el Object-Lock post-commit."""
    sello = sello or _sello_ok()
    firma_ok = permiso_firma
    visibles = (
        {str(RAD): {"subject": "x", "tracking_number": NUMERO_RAD}} if radicado_visible else {}
    )
    meta = (
        AsyncMock(side_effect=DocumentClientError("caído"))
        if doc_caido
        else AsyncMock(return_value=visibles)
    )
    with (
        patch(
            "app.services.transferencia_service.check_permission",
            new=AsyncMock(return_value=firma_ok),
        ),
        patch("app.clients.document_client.fetch_radicado_metadata_internal", new=meta),
        patch(
            "app.clients.document_client.fetch_anexos_internal",
            new=AsyncMock(
                return_value=anexos
                if anexos is not None
                else {str(RAD): [{"file_id": str(ANEXO), "filename": "soporte.pdf"}]}
            ),
        ),
        patch(
            "app.clients.storage_client.fetch_file_hashes_internal",
            new=AsyncMock(return_value={str(ANEXO): huella_anexo} if huella_anexo else {}),
        ),
        patch(
            "app.clients.signature_client.sign_xml_outcome", new=AsyncMock(return_value=sello)
        ) as sign_xml,
        patch(
            "app.clients.signature_client.sign_personal",
            new=AsyncMock(return_value=personal or SignPersonalOutcome(signed=_signed_personal())),
        ) as sign_pers,
        patch(
            "app.services.transferencia_service.TransferenciaService.proteger_worm_firma_excepcion",
            new=AsyncMock(return_value="protegido"),
        ) as worm,
        patch(
            "app.services.transferencia_service.TransferenciaService.proteger_worm_revocacion",
            new=AsyncMock(return_value="protegido"),
        ),
    ):
        yield sign_xml, sign_pers, worm


def _secuencia(conn: AsyncMock, tid, exp_id, *, final: list, nivel: int = 1, **transfer_kw) -> None:
    """get -> caller_clearance -> expediente -> acta -> [final...]"""
    transfer_kw.setdefault("decidida_por", str(PARTE))
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", **transfer_kw),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=nivel),
        _acta_row(transferencia_id=tid),
        *final,
    ]


def _asientos(conn: AsyncMock) -> list:
    return [
        c
        for c in conn.execute.call_args_list
        if c.args and isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]


def _texto_asientos(conn: AsyncMock) -> str:
    return json.dumps([str(a) for c in _asientos(conn) for a in c.args], default=str)


def _insert_excepcion(conn: AsyncMock):
    return next(
        c
        for c in conn.fetchrow.call_args_list
        if "INSERT INTO transferencia_acta_firma_excepcion " in c.args[0]
    )


# --- el acto ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_declarar_deja_constancia_firmada_y_asiento_en_la_misma_tx() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    fila = _excepcion_row(tid)
    _secuencia(conn, tid, exp_id, final=[None, None, fila])

    with _externos() as (sign_xml, sign_pers, worm):
        resp = await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
            tid, Actor(id=ADMIN), _payload()
        )

    assert resp.rol == "recibe" and resp.parte_id == PARTE and resp.declarada_por == ADMIN
    assert resp.firmada is True and resp.fundamento == FUNDAMENTO  # el declarante ve su texto
    assert resp.worm_protegido is True and resp.worm_personal_protegido is None  # estado REAL
    insert = _insert_excepcion(conn)
    # la parte sale del acta congelada (decidida_por), nunca del cliente
    assert PARTE in insert.args and ADMIN in insert.args
    assert "ON CONFLICT DO NOTHING" in insert.args[0]
    # la referencia es la forma canónica resuelta por el servidor, no un texto del cliente
    assert f"radicado:{RAD}#anexo:{ANEXO}" in insert.args
    # constancia v3: ordinal 1 sin sustituida, soporte autosuficiente e instantánea de identidad
    assert 3 in insert.args and 1 in insert.args  # constancia_version, ordinal
    assert NUMERO_RAD in insert.args and SHA_ANEXO in insert.args
    assert "admin.user" in insert.args and "parte.user" in insert.args and "usuario" in insert.args
    # A1: compromiso con sal por registro = SHA-256(sal || texto); la sal queda en la base
    sal = next(a for a in insert.args if isinstance(a, bytes))
    assert len(sal) == 32
    assert compromiso_fundamento(sal, FUNDAMENTO) in insert.args
    conn.transaction.assert_called()
    asientos = _asientos(conn)
    assert len(asientos) == 1
    texto = _texto_asientos(conn)
    assert "archive.acta_firma_inobtenible_declarada" in texto
    assert f"radicado:{RAD}" in texto
    # el texto del fundamento NO entra al registro permanente: solo su compromiso salado (nunca
    # un SHA-256 plano, que se ataca por diccionario) y JAMÁS la sal (A1)
    assert "defuncion" not in texto and sha256_hex(FUNDAMENTO) not in texto
    assert compromiso_fundamento(sal, FUNDAMENTO) in texto
    assert sal.hex() not in texto and "fundamento_sal" not in texto
    # S4: el asiento deja el id y la huella del soporte, no el número del radicado
    assert NUMERO_RAD not in texto and SHA_ANEXO in texto
    # bookkeeping WORM del sello en la MISMA tx (la fila existe aunque muera el post-commit)
    worm_inserts = [
        c for c in conn.execute.call_args_list
        if c.args and "INSERT INTO transferencia_acta_firma_excepcion_worm" in str(c.args[0])
    ]
    assert [c.args[2] for c in worm_inserts] == ["sello", "sobre"]  # 064: el sobre también
    assert [c.args[1] for c in worm.await_args_list] == ["sello", "sobre"]
    sign_pers.assert_not_awaited()  # política 'off' y sin TOTP: solo el sello


@pytest.mark.asyncio
async def test_sellar_a_nombre_del_declarante_exige_perm_firma_y_se_dice_antes_de_llamar() -> None:
    """`POST /sign-xml` exige PERM_FIRMA >= 2 y el cliente de sello clasifica su 403 como
    «transitorio»: sin esta comprobación previa el declarante recibiría un 503 sin remedio."""
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None])
    with _externos(permiso_firma=False) as (sign_xml, sign_pers, worm):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload(totp_code="123456")
            )
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "declarante_sin_permiso_de_firma"
    sign_xml.assert_not_awaited()
    sign_pers.assert_not_awaited()
    assert _asientos(conn) == []


@pytest.mark.asyncio
async def test_el_xml_firmado_ata_el_acta_la_parte_y_la_huella_pero_no_lleva_el_texto() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None, _excepcion_row(tid)])

    with _externos() as (sign_xml, _p, _w):
        await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
            tid, Actor(id=ADMIN), _payload()
        )

    kw = sign_xml.await_args_list[0].kwargs
    xml = kw["payload_xml"]
    assert kw["objeto_tipo"] == "acta_transferencia"  # mismo camino institucional que el acta
    assert "a" * 64 in xml  # huella del acta congelada
    assert "urn:orpycamcp:acta-firma-excepcion:v3" in xml
    assert str(PARTE) in xml and str(ADMIN) in xml
    # el fundamento viaja como compromiso salado, jamás como SHA-256 plano ni como texto, y la
    # sal no viaja nunca
    insert_sal = next(a for a in _insert_excepcion(conn).args if isinstance(a, bytes))
    assert compromiso_fundamento(insert_sal, FUNDAMENTO) in xml and sha256_hex(FUNDAMENTO) not in xml
    assert 'algoritmo="SHA-256-salted"' in xml and insert_sal.hex() not in xml
    assert str(RAD) in xml and "recibe" in xml and "fallecimiento" in xml
    # constancia autosuficiente: número del radicado, SHA-256 del anexo, nombres resueltos
    assert NUMERO_RAD in xml and SHA_ANEXO in xml and str(ANEXO) in xml
    assert "admin.user" in xml and "parte.user" in xml
    assert "defuncion" not in xml  # el texto libre no viaja dentro del artefacto replicado
    insert = _insert_excepcion(conn)
    assert xml in insert.args  # lo persistido es exactamente lo firmado
    # A2: lo firmado ES la C14N exclusiva y la huella se calcula sobre esos bytes
    assert xml == canonicalizar(xml)
    assert sha256_c14n(xml) in insert.args


@pytest.mark.asyncio
async def test_con_totp_y_credencial_el_declarante_tambien_firma_y_se_protegen_los_dos_xml() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    pers = _signed_personal(minio_key_firmado="file-id/constancia-personal.xml")
    fila = _excepcion_row(
        tid, firma_personal_id=pers.firma_id,
        minio_key_firmado_personal=pers.minio_key_firmado, xades_level_personal="XAdES-T",
    )
    _secuencia(conn, tid, exp_id, final=[None, None, fila])

    with _externos(personal=SignPersonalOutcome(signed=pers)) as (sign_xml, sign_pers, worm):
        resp = await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
            tid, Actor(id=ADMIN), _payload(totp_code="123456"), "Bearer tk"
        )

    assert resp.firma_personal is True
    kw = sign_pers.await_args.kwargs
    assert kw["payload_xml"] == sign_xml.await_args_list[0].kwargs["payload_xml"]  # los mismos bytes
    assert kw["totp_code"] == "123456" and kw["authorization"] == "Bearer tk"
    assert kw["firmante_id"] == str(ADMIN)
    artefactos = [c.args[1] for c in worm.await_args_list]
    assert artefactos == ["sello", "personal", "sobre"]
    worm_inserts = [
        c.args[2] for c in conn.execute.call_args_list
        if c.args and "INSERT INTO transferencia_acta_firma_excepcion_worm" in str(c.args[0])
    ]
    assert worm_inserts == ["sello", "sobre", "personal"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("politica", "nivel", "requerida"),
    [
        ("todos", 1, True),
        ("clasificados", 2, True),
        ("clasificados", 1, False),
        ("off", 3, False),
        (None, 3, False),  # sin parámetro de la entidad: respalda el entorno ('off')
    ],
)
async def test_la_firma_personal_es_obligatoria_segun_la_politica_de_la_entidad(
    politica, nivel, requerida
) -> None:
    conn = _conn(politica=politica)
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, nivel=nivel, final=[None, None, _excepcion_row(tid)])

    with _externos() as (sign_xml, _p, _w):
        if requerida:
            with pytest.raises(HTTPException) as exc:
                await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                    tid, Actor(id=ADMIN), _payload()
                )
            assert exc.value.status_code == 422
            assert exc.value.detail["error"] == "firma_personal_declarante_requerida"
            sign_xml.assert_not_awaited()  # antes de firmar nada
            assert _asientos(conn) == []
        else:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload()
            )
            assert sign_xml.await_count == 2  # la constancia y su sobre de revelación


@pytest.mark.asyncio
async def test_sin_credencial_la_firma_personal_opcional_se_omite_pero_la_obligatoria_se_rechaza() -> None:
    sin_cert = SignPersonalOutcome(
        signed=None, rejected=(422, {"error": "sin_certificado_activo", "status": 422})
    )
    # opcional (política 'off'): se sigue con el solo sello
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None, _excepcion_row(tid)])
    with _externos(personal=sin_cert):
        resp = await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
            tid, Actor(id=ADMIN), _payload(totp_code="123456")
        )
    assert resp.firma_personal is False
    assert _insert_excepcion(conn).args.count(None) >= 3  # columnas de la firma personal vacías

    # obligatoria (política 'todos'): sin credencial NO hay constancia
    conn = _conn(politica="todos")
    _secuencia(conn, tid, exp_id, final=[None, None, _excepcion_row(tid)])
    with _externos(personal=sin_cert):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload(totp_code="123456")
            )
    assert exc.value.status_code == 422 and exc.value.detail["error"] == "sin_certificado_activo"
    assert _asientos(conn) == []
    assert not any("INSERT INTO transferencia_acta_firma_excepcion " in c.args[0]
                   for c in conn.fetchrow.call_args_list)


@pytest.mark.asyncio
async def test_un_totp_incorrecto_no_se_traga_aunque_la_firma_personal_sea_opcional() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None])
    malo = SignPersonalOutcome(signed=None, rejected=(401, {"error": "totp_incorrecto"}))
    with _externos(personal=malo):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload(totp_code="000000")
            )
    assert exc.value.status_code == 401 and _asientos(conn) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("seal_absent", [True, False])
async def test_sin_sello_no_hay_constancia(seal_absent: bool) -> None:
    """Una constancia sin firma es justo lo que no sirve ante el AGN: si no se puede sellar,
    no se declara nada (503), no se deja una fila 'pendiente'."""
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None])
    caido = SignOutcome(signed=None, seal_absent=seal_absent, transient=not seal_absent)
    with _externos(sello=caido) as (_s, sign_pers, worm):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload(totp_code="123456")
            )
    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == (
        "sello_institucional_no_disponible" if seal_absent else "sellado_constancia_no_obtenido"
    )
    sign_pers.assert_not_awaited()  # no se quema el TOTP en un acto que no va a existir
    worm.assert_not_awaited()
    assert _asientos(conn) == []
    assert not any("INSERT INTO transferencia_acta_firma_excepcion " in c.args[0]
                   for c in conn.fetchrow.call_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rol", "columna"),
    [("entrega", "enviada_por"), ("recibe", "decidida_por"), ("elabora", "creada_por")],
)
async def test_la_parte_se_deriva_de_la_columna_de_identidad_de_su_rol(rol: str, columna: str) -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    otra = uuid.uuid4()
    kw = {"enviada_por": str(uuid.uuid4()), "creada_por": str(uuid.uuid4())}
    kw[columna] = str(otra)
    _secuencia(conn, tid, exp_id, final=[None, None, _excepcion_row(tid, rol=rol, parte_id=otra)], **kw)
    with _externos():
        await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
            tid, Actor(id=ADMIN), _payload(rol=rol)
        )
    assert otra in _insert_excepcion(conn).args


@pytest.mark.asyncio
async def test_con_certificado_activo_el_declarante_siempre_firma_personalmente() -> None:
    """FIRMA-2: quien PUEDE firmar, firma — aunque la política de la entidad sea 'off'. Sin
    `totp_code` no hay acto (422) y no se sella nada; con él se firma y el nombre sale del CN
    del certificado, no del username."""
    usuarios = [
        _usuario(ADMIN, "admin.user", cn="Ana Admin Pérez", cargo="Jefe de archivo", cert_activo=True),
        _usuario(PARTE, "parte.user"),
    ]
    conn = _conn(usuarios=usuarios)
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None])
    with _externos() as (sign_xml, sign_pers, _w):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload()
            )
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "firma_personal_declarante_requerida"
    sign_xml.assert_not_awaited()
    sign_pers.assert_not_awaited()
    assert _asientos(conn) == []

    conn = _conn(usuarios=usuarios)
    pers = _signed_personal(minio_key_firmado="file-id/constancia-personal.xml")
    fila = _excepcion_row(tid, firma_personal_id=pers.firma_id,
                          minio_key_firmado_personal=pers.minio_key_firmado,
                          xades_level_personal="XAdES-T", firma_personal_requerida=True)
    _secuencia(conn, tid, exp_id, final=[None, None, fila])
    with _externos(personal=SignPersonalOutcome(signed=pers)) as (sign_xml, sign_pers, _w):
        await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
            tid, Actor(id=ADMIN), _payload(totp_code="123456"), "Bearer tk"
        )
    sign_pers.assert_awaited_once()
    xml = sign_xml.await_args_list[0].kwargs["payload_xml"]
    assert "Ana Admin Pérez" in xml and "Jefe de archivo" in xml and "certificado" in xml
    insert = _insert_excepcion(conn)
    assert "Ana Admin Pérez" in insert.args and "certificado" in insert.args and True in insert.args


@pytest.mark.asyncio
async def test_la_parte_fuera_del_catalogo_se_declara_no_resuelta_sin_inventar_un_nombre() -> None:
    conn = _conn(usuarios=[_usuario(ADMIN, "admin.user")])
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None, _excepcion_row(tid)])
    with _externos() as (sign_xml, _p, _w):
        await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
            tid, Actor(id=ADMIN), _payload()
        )
    assert "no_resuelta" in sign_xml.await_args_list[0].kwargs["payload_xml"]
    assert "no_resuelta" in _insert_excepcion(conn).args


@pytest.mark.asyncio
async def test_cada_registro_lleva_su_propia_sal_y_el_compromiso_la_incluye() -> None:
    """A1 (mutación: sal fija/omitida o compromiso sin sal): dos declaraciones del MISMO texto
    producen sal y compromiso distintos; y el compromiso es SHA-256(sal || texto), no SHA-256(texto)."""
    sales = []
    for _ in range(2):
        conn = _conn()
        tid, exp_id = uuid.uuid4(), uuid.uuid4()
        _secuencia(conn, tid, exp_id, final=[None, None, _excepcion_row(tid)])
        with _externos():
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload()
            )
        insert = _insert_excepcion(conn)
        sal = next(a for a in insert.args if isinstance(a, bytes))
        compromiso = next(
            a for a in insert.args if isinstance(a, str) and a == compromiso_fundamento(sal, FUNDAMENTO)
        )
        assert compromiso != sha256_hex(FUNDAMENTO)  # no es un hash plano del texto
        sales.append((sal, compromiso))
    assert sales[0][0] != sales[1][0] and sales[0][1] != sales[1][1]
    assert all(len(sal) == 32 for sal, _ in sales)


@pytest.mark.asyncio
async def test_sin_la_huella_del_anexo_la_constancia_no_se_declara_a_medias() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None])
    with _externos(huella_anexo=None) as (sign_xml, _p, _w):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload()
            )
    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "soporte_documental_sin_huella"
    sign_xml.assert_not_awaited()
    assert _asientos(conn) == []


# --- conflicto de interés -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("columna", ["enviada_por", "decidida_por", "creada_por"])
@pytest.mark.parametrize("rol", ["recibe", "entrega", "elabora"])
async def test_quien_declara_no_puede_ser_ninguna_de_las_partes_de_la_transferencia(
    columna: str, rol: str
) -> None:
    """No solo la parte afectada: quien ENVIÓ, RECIBIÓ o ELABORÓ la transferencia tampoco
    puede declarar la excepción de otra parte (los dos auditores)."""
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    declarante = uuid.uuid4()
    kw = {"enviada_por": str(uuid.uuid4()), "decidida_por": str(uuid.uuid4()),
          "creada_por": str(uuid.uuid4())}
    kw[columna] = str(declarante)
    _secuencia(conn, tid, exp_id, final=[], **kw)

    with _externos() as (sign_xml, _p, _w):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=declarante), _payload(rol=rol)
            )

    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "declarante_es_parte_de_la_transferencia"
    sign_xml.assert_not_awaited()
    assert not any("INSERT INTO transferencia_acta_firma_excepcion " in c.args[0]
                   for c in conn.fetchrow.call_args_list)
    # el rechazo deja su propio asiento (denegación), sin constancia
    texto = _texto_asientos(conn)
    assert "acta_firma_inobtenible_denegada" in texto
    assert "declarante_es_parte_de_la_transferencia" in texto


@pytest.mark.asyncio
async def test_el_motor_ataja_la_carrera_del_conflicto_de_interes_y_se_dice_403() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    motor = asyncpg.CheckViolationError.new(
        {"M": "quien declara...", "C": "23514", "n": "ck_firma_excepcion_declarante_es_parte"}
    )
    _secuencia(conn, tid, exp_id, final=[None, None, motor])
    with _externos():
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload()
            )
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "declarante_es_parte_de_la_transferencia"


# --- soporte documental ----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "caso",
    ["radicado_inexistente_o_invisible", "anexo_ajeno"],
)
async def test_la_referencia_debe_existir_y_ser_visible_para_el_declarante(caso: str) -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None])
    if caso == "radicado_inexistente_o_invisible":
        ctx = _externos(radicado_visible=False)
        payload = _payload()
    else:
        ctx = _externos(anexos={str(RAD): [{"file_id": str(uuid.uuid4()), "filename": "x"}]})
        payload = _payload(referencia_anexo_id=ANEXO)
    with ctx as (sign_xml, _p, _w):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), payload
            )
    # un solo cuerpo para «no existe» y «no lo puede ver»: sin oráculo
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "referencia_documental_invalida"
    sign_xml.assert_not_awaited()
    assert _asientos(conn) == []


@pytest.mark.asyncio
async def test_el_soporte_se_resuelve_con_el_clearance_del_declarante() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", decidida_por=str(PARTE)),
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),  # `caller_clearance` está parchado
        _acta_row(transferencia_id=tid),
        None,
        None,
        _excepcion_row(tid),
    ]
    with _externos(anexos={str(RAD): [{"file_id": str(ANEXO), "filename": "x"}]}):
        with patch(
            "app.repositories.archive_repository.ArchiveRepository.caller_clearance",
            new=AsyncMock(return_value=2),
        ):
            from app.clients import document_client

            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload(referencia_anexo_id=ANEXO)
            )
            meta = document_client.fetch_radicado_metadata_internal
            assert meta.await_args.kwargs["max_clearance"] == 2
            assert document_client.fetch_anexos_internal.await_args.kwargs["max_clearance"] == 2
    assert f"radicado:{RAD}#anexo:{ANEXO}" in _insert_excepcion(conn).args


@pytest.mark.asyncio
async def test_si_document_service_no_responde_no_se_asienta_una_referencia_sin_comprobar() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None])
    with _externos(doc_caido=True) as (sign_xml, _p, _w):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload()
            )
    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "document_service_no_disponible"
    sign_xml.assert_not_awaited()


# --- exclusiones y carreras ------------------------------------------------------------


@pytest.mark.asyncio
async def test_una_parte_que_ya_firmo_no_admite_excepcion() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, _personal_row(transferencia_id=tid)])

    with _externos() as (sign_xml, _p, _w):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload()
            )

    assert exc.value.status_code == 409 and exc.value.detail["error"] == "parte_ya_firmo"
    sign_xml.assert_not_awaited()
    assert _asientos(conn) == []


@pytest.mark.asyncio
async def test_sobre_una_excepcion_vigente_no_se_declara_otra() -> None:
    """La cadena es lineal: solo se re-declara tras REVOCAR. Una vigente es 409 y no se firma
    nada (ni se quema un TOTP)."""
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[_excepcion_row(tid)])

    with _externos() as (sign_xml, _p, _w):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload()
            )

    assert exc.value.status_code == 409 and exc.value.detail["error"] == "excepcion_vigente"
    sign_xml.assert_not_awaited()
    assert _asientos(conn) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "previa",
    [
        # revocación sin sello (legada) / con sello pero sin Object-Lock / personal sin proteger
        pytest.param(lambda tid: _revocada(tid, protegida=False), id="sello_sin_proteger"),
        pytest.param(
            lambda tid: _revocada(tid, revocacion_firma_id=None, revocacion_worm_protegido=None),
            id="revocacion_legada_sin_sello",
        ),
        pytest.param(
            lambda tid: _revocada(tid, personal=True, revocacion_worm_personal_protegido=False),
            id="personal_sin_proteger",
        ),
    ],
)
async def test_no_se_redeclara_si_la_revocacion_no_esta_sellada_y_protegida(previa) -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[previa(tid), None])

    with _externos() as (sign_xml, _p, _w):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload()
            )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "revocacion_sin_proteger"
    sign_xml.assert_not_awaited()
    assert _asientos(conn) == []


@pytest.mark.asyncio
async def test_tras_revocar_y_proteger_se_redeclara_con_ordinal_siguiente_y_ligada_a_la_revocada() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    previa = _revocada(tid, ordinal=2, personal=True)
    nueva = _excepcion_row(tid, ordinal=3, sustituye_a=previa["id"])
    _secuencia(conn, tid, exp_id, final=[previa, None, nueva])

    with _externos() as (sign_xml, _p, _w):
        await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
            tid, Actor(id=ADMIN), _payload()
        )

    insert = _insert_excepcion(conn)
    assert previa["id"] in insert.args and 3 in insert.args  # sustituye_a y ordinal n+1
    xml = sign_xml.await_args_list[0].kwargs["payload_xml"]
    assert str(previa["id"]) in xml and previa["revocacion_sha256"] in xml  # ligada a la baja
    payload_asiento = next(
        json.loads(a) for a in _asientos(conn)[0].args if isinstance(a, str) and a.startswith("{")
    )
    assert payload_asiento["ordinal"] == 3 and payload_asiento["sustituye_a"] == str(previa["id"])


@pytest.mark.asyncio
async def test_si_la_parte_firmo_en_la_ventana_revocada_la_cadena_esta_cerrada() -> None:
    """Revocada y protegida, pero la parte ya firmó durante la ventana: la firma prevalece y no
    hay nueva excepción (ni siquiera se mira si la revocación está protegida)."""
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[_revocada(tid), _personal_row(transferencia_id=tid)])

    with _externos() as (sign_xml, _p, _w):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload()
            )

    assert exc.value.status_code == 409 and exc.value.detail["error"] == "parte_ya_firmo"
    sign_xml.assert_not_awaited()
    assert _asientos(conn) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("constraint", "error"),
    [
        ("ck_firma_excepcion_revocacion_sin_proteger", "revocacion_sin_proteger"),
        ("ck_firma_excepcion_parte_ya_firmo", "parte_ya_firmo"),
        ("ck_firma_excepcion_cadena_invalida", "cadena_de_constancias_invalida"),
    ],
)
async def test_el_motor_ataja_la_cadena_y_el_servicio_lo_traduce_a_409(constraint, error) -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    motor = asyncpg.CheckViolationError.new({"M": "x", "C": "23514", "n": constraint})
    _secuencia(conn, tid, exp_id, final=[None, None, motor])
    with _externos():
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload()
            )
    assert exc.value.status_code == 409 and exc.value.detail["error"] == error


@pytest.mark.asyncio
async def test_la_perdedora_de_la_carrera_no_asienta_un_acto_que_no_produjo() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None, None, None])  # ON CONFLICT DO NOTHING -> None

    with _externos() as (_s, _p, worm):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
                tid, Actor(id=ADMIN), _payload()
            )

    assert exc.value.status_code == 409 and exc.value.detail["error"] == "excepcion_vigente"
    assert _asientos(conn) == []
    worm.assert_not_awaited()


@pytest.mark.asyncio
async def test_solo_sobre_un_acta_ya_congelada() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada"),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
    ]
    with pytest.raises(HTTPException) as exc:
        await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
            tid, Actor(id=ADMIN), _payload()
        )
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "transferencia_no_recibida"


@pytest.mark.asyncio
async def test_no_read_up_404_neutro_si_el_declarante_no_puede_leer_el_expediente() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,
        None,  # el expediente no es legible con ese clearance
    ]
    with pytest.raises(HTTPException) as exc:
        await TransferenciaService(conn, TENANT).declarar_firma_inobtenible(
            tid, Actor(id=ADMIN), _payload()
        )
    assert exc.value.status_code == 404 and exc.value.detail["error"] == "expediente_not_found"


@pytest.mark.parametrize(
    "malo",
    [
        {"fundamento": "corto"},
        {"fundamento": " " * 30},
        {"referencia_radicado_id": "texto libre"},
        {"referencia_anexo_id": "no-es-uuid"},
        {"referencia_anexo_id": None},  # el anexo es obligatorio (la constancia lleva su SHA-256)
        {"motivo": "porque si"},
        {"rol": "terceros"},
    ],
)
def test_fundamento_y_referencia_son_obligatorios_y_el_catalogo_es_cerrado(malo: dict) -> None:
    with pytest.raises(ValueError):
        _payload(**malo)


def test_la_referencia_ya_no_es_texto_libre() -> None:
    with pytest.raises(ValueError):
        FirmaInobtenibleRequest(
            rol="recibe", motivo="otro", fundamento=FUNDAMENTO,
            referencia_documental="Resolucion 123 de 2026",  # el campo viejo no basta
        )


# --- revocar --------------------------------------------------------------------------


def _revocar_payload(**kw) -> RevocarFirmaInobtenibleRequest:
    base = {"rol": "recibe", "causa": "error_de_declaracion", "fundamento": FUNDAMENTO_REVOCACION}
    base.update(kw)
    return RevocarFirmaInobtenibleRequest(**base)


def _fila_revocacion(previa: dict, **kw) -> dict:
    base = {
        "id": uuid.uuid4(), "excepcion_id": previa["id"], "revocada_por": ADMIN,
        "fundamento": FUNDAMENTO_REVOCACION, "revocada_en": datetime.now(tz=UTC),
        "revocacion_version": 3, "revocacion_sha256": "d" * 64, "firma_id": uuid.uuid4(),
        "minio_key_firmado": "file-id/revocacion-firmada.xml", "xades_level": "XAdES-T",
        "firma_personal_id": None, "minio_key_firmado_personal": None,
        "xades_level_personal": None, "revocante_nombre": "admin.user", "revocante_cargo": None,
        "revocante_fuente": "usuario", "causa": "error_de_declaracion",
        "fundamento_sal": SAL_REV, "fundamento_compromiso": COMPROMISO_REV,
        "fundamento_compromiso_algoritmo": "SHA-256-salted",
        "sobre_firma_id": uuid.uuid4(), "sobre_minio_key": "file-id/sobre-revocacion.xml",
        "sobre_sha256": "f" * 64, "sobre_xades_level": "XAdES-T",
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_revocar_es_una_fila_firmada_con_asiento_atomico_y_nunca_un_update() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    previa = _excepcion_row(tid)
    revocacion = _fila_revocacion(previa)
    _secuencia(conn, tid, exp_id, final=[previa, revocacion])

    with _externos() as (sign_xml, _p, _w):
        resp = await TransferenciaService(conn, TENANT).revocar_firma_inobtenible(
            tid, Actor(id=ADMIN), _revocar_payload()
        )

    assert resp.revocada is True and resp.revocada_por == ADMIN
    assert resp.revocada_en == revocacion["revocada_en"] and resp.revocacion_firmada is True
    assert resp.revocacion_worm_protegido is True  # estado REAL tras el Object-Lock post-commit
    sqls = [c.args[0] for c in conn.fetchrow.call_args_list] + [
        c.args[0] for c in conn.execute.call_args_list if c.args and isinstance(c.args[0], str)
    ]
    assert any("INSERT INTO transferencia_acta_firma_excepcion_revocacion" in q for q in sqls)
    assert any("INSERT INTO transferencia_acta_firma_excepcion_revocacion_worm" in q for q in sqls)
    assert not any(q.lstrip().upper().startswith("UPDATE TRANSFERENCIA_ACTA_FIRMA_EXCEPCION") for q in sqls)
    conn.transaction.assert_called()
    # el XML ata la baja a la huella de la constancia que revoca y lleva la causa y el
    # compromiso salado, no el texto ni la sal
    kw = sign_xml.await_args_list[0].kwargs
    xml = kw["payload_xml"]
    assert kw["objeto_tipo"] == "acta_transferencia"
    assert "urn:orpycamcp:acta-firma-excepcion-revocacion:v3" in xml
    assert str(previa["id"]) in xml and previa["constancia_sha256"] in xml
    assert "<causa>error_de_declaracion</causa>" in xml.replace(' xmlns="urn:orpycamcp:acta-firma-excepcion-revocacion:v3"', "")
    rev_insert = next(
        c for c in conn.fetchrow.call_args_list
        if "INSERT INTO transferencia_acta_firma_excepcion_revocacion " in c.args[0]
    )
    rsal = next(a for a in rev_insert.args if isinstance(a, bytes))
    rcomp = compromiso_fundamento(rsal, FUNDAMENTO_REVOCACION)
    assert rcomp in xml and rcomp in rev_insert.args and "error_de_declaracion" in rev_insert.args
    assert "por error" not in xml and sha256_hex(FUNDAMENTO_REVOCACION) not in xml
    assert rsal.hex() not in xml
    assert sha256_c14n(xml) in rev_insert.args
    texto = _texto_asientos(conn)
    assert "archive.acta_firma_inobtenible_revocada" in texto
    assert rcomp in texto and "error_de_declaracion" in texto
    assert rsal.hex() not in texto
    assert sha256_hex(FUNDAMENTO_REVOCACION) not in texto and "por error" not in texto


@pytest.mark.asyncio
async def test_sin_sello_no_hay_revocacion_ni_fila_ni_asiento() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[_excepcion_row(tid)])
    caido = SignOutcome(signed=None, transient=True)
    with _externos(sello=caido) as (_s, sign_pers, _w):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).revocar_firma_inobtenible(
                tid, Actor(id=ADMIN), _revocar_payload(totp_code="123456")
            )
    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "sellado_revocacion_no_obtenido"
    sign_pers.assert_not_awaited()
    assert _asientos(conn) == []
    assert not any("INSERT INTO transferencia_acta_firma_excepcion_revocacion" in str(c.args[0])
                   for c in conn.fetchrow.call_args_list + conn.execute.call_args_list if c.args)


@pytest.mark.asyncio
async def test_revocar_exige_perm_firma_porque_el_sello_se_estampa_a_su_nombre() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[_excepcion_row(tid)])
    with _externos(permiso_firma=False) as (sign_xml, _p, _w):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).revocar_firma_inobtenible(
                tid, Actor(id=ADMIN), _revocar_payload()
            )
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "revocante_sin_permiso_de_firma"
    sign_xml.assert_not_awaited()
    assert _asientos(conn) == []


@pytest.mark.asyncio
async def test_quien_revoca_con_certificado_activo_firma_personalmente() -> None:
    usuarios = [_usuario(ADMIN, "admin.user", cn="Ana Admin", cert_activo=True)]
    conn = _conn(usuarios=usuarios)
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[_excepcion_row(tid)])
    with _externos() as (sign_xml, sign_pers, _w):
        with pytest.raises(HTTPException) as exc:
            await TransferenciaService(conn, TENANT).revocar_firma_inobtenible(
                tid, Actor(id=ADMIN), _revocar_payload()
            )
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "firma_personal_revocante_requerida"
    sign_xml.assert_not_awaited()

    conn = _conn(usuarios=usuarios)
    previa = _excepcion_row(tid)
    pers = _signed_personal(minio_key_firmado="file-id/revocacion-personal.xml")
    rev = _fila_revocacion(previa, firma_personal_id=pers.firma_id,
                           minio_key_firmado_personal=pers.minio_key_firmado,
                           xades_level_personal="XAdES-T", revocante_nombre="Ana Admin",
                           revocante_fuente="certificado")
    _secuencia(conn, tid, exp_id, final=[previa, rev])
    with _externos(personal=SignPersonalOutcome(signed=pers)) as (sign_xml, sign_pers, _w):
        resp = await TransferenciaService(conn, TENANT).revocar_firma_inobtenible(
            tid, Actor(id=ADMIN), _revocar_payload(totp_code="123456"), "Bearer tk"
        )
    assert sign_pers.await_args.kwargs["payload_xml"] == sign_xml.await_args_list[0].kwargs["payload_xml"]
    assert sign_pers.await_args.kwargs["authorization"] == "Bearer tk"
    assert resp.revocacion_firma_personal is True and resp.revocante_nombre == "Ana Admin"
    worm_inserts = [
        c.args[2] for c in conn.execute.call_args_list
        if c.args and "INSERT INTO transferencia_acta_firma_excepcion_revocacion_worm" in str(c.args[0])
    ]
    assert worm_inserts == ["sello", "sobre", "personal"]


@pytest.mark.asyncio
async def test_revocar_lo_que_no_existe_o_ya_se_revoco() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _secuencia(conn, tid, exp_id, final=[None])
    with pytest.raises(HTTPException) as exc:
        await TransferenciaService(conn, TENANT).revocar_firma_inobtenible(
            tid, Actor(id=ADMIN), _revocar_payload()
        )
    assert exc.value.status_code == 404 and exc.value.detail["error"] == "excepcion_not_found"

    conn = _conn()
    _secuencia(conn, tid, exp_id, final=[_revocada(tid)])
    with pytest.raises(HTTPException) as exc:
        await TransferenciaService(conn, TENANT).revocar_firma_inobtenible(
            tid, Actor(id=ADMIN), _revocar_payload()
        )
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "excepcion_ya_revocada"
    assert _asientos(conn) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("columna", ["enviada_por", "decidida_por", "creada_por"])
async def test_quien_revoca_tampoco_puede_ser_parte_de_la_transferencia(columna: str) -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    revocante = uuid.uuid4()
    kw = {"enviada_por": str(uuid.uuid4()), "decidida_por": str(uuid.uuid4()),
          "creada_por": str(uuid.uuid4())}
    kw[columna] = str(revocante)
    _secuencia(conn, tid, exp_id, final=[], **kw)
    with pytest.raises(HTTPException) as exc:
        await TransferenciaService(conn, TENANT).revocar_firma_inobtenible(
            tid, Actor(id=revocante), _revocar_payload()
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "declarante_es_parte_de_la_transferencia"
    assert "acta_firma_inobtenible_revocacion_denegada" in _texto_asientos(conn)


# --- el rol con excepción ya no firma ------------------------------------------------


@pytest.mark.asyncio
async def test_una_parte_con_excepcion_no_firma_y_no_quema_el_totp() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", decidida_por=str(USER_ID)),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,  # sin firma previa
        _excepcion_row(tid, parte_id=USER_ID),  # pero con excepción VIGENTE declarada
    ]
    with patch("app.clients.signature_client.sign_personal", new=AsyncMock()) as sign:
        with pytest.raises(Exception) as exc:
            await TransferenciaService(conn, TENANT).firmar_personal(
                tid, Actor(id=USER_ID), "123456", "Bearer x"
            )
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "firma_excepcion_declarada"
    sign.assert_not_awaited()


@pytest.mark.asyncio
async def test_la_consulta_previa_de_la_firma_solo_cuenta_excepciones_no_revocadas() -> None:
    """Con la excepción revocada la parte vuelve a poder firmar: la lectura que usa
    `firmar_personal` (`get_excepcion_vigente`) excluye las revocadas en el `WHERE` (no en
    Python); la de la cadena (`get_ultima_excepcion`) las incluye y toma el último eslabón."""
    conn = _conn()
    from app.repositories.transferencia_repository import TransferenciaRepository

    await TransferenciaRepository(conn, TENANT).get_excepcion_vigente(uuid.uuid4(), rol="recibe")
    consulta = conn.fetchrow.call_args.args[0]
    assert "r.id IS NULL" in consulta
    await TransferenciaRepository(conn, TENANT).get_ultima_excepcion(uuid.uuid4(), rol="recibe")
    ultima = conn.fetchrow.call_args.args[0]
    assert "r.id IS NULL" not in ultima and "ORDER BY e.ordinal DESC" in ultima


@pytest.mark.asyncio
async def test_una_excepcion_revocada_en_la_historia_no_impide_firmar() -> None:
    """`firmar_personal` mira solo la VIGENTE: con el único eslabón revocado, la lectura ya no
    devuelve nada y la firma sigue su curso (sign_personal se llama)."""
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", decidida_por=str(USER_ID)),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,  # sin firma previa
        None,  # get_excepcion_vigente: el único eslabón está revocado -> no hay vigente
        _personal_row(transferencia_id=tid, firmante_id=USER_ID),
    ]
    with patch(
        "app.clients.signature_client.sign_personal",
        new=AsyncMock(return_value=SignPersonalOutcome(signed=_signed_personal())),
    ) as sign:
        await TransferenciaService(conn, TENANT).firmar_personal(
            tid, Actor(id=USER_ID), "123456", "Bearer x"
        )
    sign.assert_awaited_once()


@pytest.mark.asyncio
async def test_carrera_la_firma_pierde_ante_la_excepcion_y_se_dice_409() -> None:
    """Entre el chequeo previo y el INSERT se declaró la excepción: el trigger de la 059/061
    levanta un CHECK y se traduce a 409, no a 500."""
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", decidida_por=str(USER_ID)),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,
        None,
        asyncpg.CheckViolationError("excepción declarada"),
    ]
    with patch(
        "app.clients.signature_client.sign_personal",
        new=AsyncMock(return_value=SignPersonalOutcome(signed=_signed_personal())),
    ):
        with pytest.raises(Exception) as exc:
            await TransferenciaService(conn, TENANT).firmar_personal(
                tid, Actor(id=USER_ID), "123456", "Bearer x"
            )
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "firma_excepcion_declarada"


# --- la verificación -----------------------------------------------------------------


async def _verificar(
    personales: list[str],
    excepciones: list[str],
    *,
    gate: str = "off",
    politica: str | None = None,
    revocadas: tuple[str, ...] = (),
    nivel: int = 1,
    advertencias: list[str] | None = None,
    puede_ver: bool = False,
    verify_ok: bool = True,
    visibles: dict | None = None,
    filas: list[dict] | None = None,
    verify_por_firma: dict | None = None,
    meta_out: list | None = None,
    clearance: int | None = None,
    xml_cadena: list | None = None,
    descarga: AsyncMock | None = None,
):
    conn = _conn(politica=politica)
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        *([] if clearance is not None else [None]),  # `caller_clearance` parchado: no lee la fila
        make_expediente_record(exp_id=exp_id, nivel_seguridad=nivel),
        _acta_row(transferencia_id=tid),
        _acta_firma_row(
            transferencia_id=tid, estado="firmado", xades_level="XAdES-T",
            minio_key_firmado="file-id/acta-firmado.xml",
        ),
    ]
    conn.fetch.side_effect = [
        [],
        [_personal_row(transferencia_id=tid, rol=r) for r in personales],
        filas
        if filas is not None
        else [
            _excepcion_row(
                tid, rol=r, revocada_en=datetime.now(tz=UTC) if r in revocadas else None
            )
            for r in excepciones
        ],
        xml_cadena or [],  # XML guardados de la cadena (A2); vacío = enlaces no comprobables
    ]
    ok = VerifyResult(
        valida=verify_ok, nivel_conformidad="xades_personal_no_acreditado", acreditado=False,
        motivo=None, revocacion_status="vigente", advertencias=advertencias or [],
    )

    async def _verify(*, tenant_slug, firma_id, payload_xml):
        if verify_por_firma and str(firma_id) in verify_por_firma:
            return VerifyResult(
                valida=verify_por_firma[str(firma_id)], nivel_conformidad="x",
                acreditado=False, motivo=None,
            )
        return ok

    with (
        patch(
            "app.clients.storage_client.download_signed_xml",
            new=descarga or AsyncMock(return_value="<xml/>"),
        ),
        patch("app.clients.signature_client.verify", new=_verify),
        patch(
            "app.clients.document_client.fetch_radicado_metadata_internal",
            new=AsyncMock(
                return_value=visibles
                if visibles is not None
                else {str(RAD): {"tracking_number": NUMERO_RAD}}
            ),
        ) as meta,
        patch("app.services.transferencia_service.settings.acta_firma_personal_require", gate),
        patch(
            "app.services.transferencia_service.check_permission",
            new=AsyncMock(return_value=puede_ver),
        ),
    ):
        if clearance is None:
            res = await TransferenciaService(conn, TENANT).acta_verificacion(tid, USER_ID)
        else:
            with patch(
                "app.repositories.archive_repository.ArchiveRepository.caller_clearance",
                new=AsyncMock(return_value=clearance),
            ):
                res = await TransferenciaService(conn, TENANT).acta_verificacion(tid, USER_ID)
    if meta_out is not None:
        meta_out.append(meta)
    return res


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("personales", "excepciones", "revocadas", "esperado"),
    [
        (["entrega", "recibe"], [], (), "firmada_completa"),
        (["entrega"], ["recibe"], (), "firmada_con_excepcion"),
        (["recibe"], ["entrega"], (), "firmada_con_excepcion"),
        # ninguna parte firmó: no se miente con «firmada_*»; solo conserva el sello
        ([], ["entrega", "recibe"], (), "sellada_con_excepcion"),
        # con una excepción de CUALQUIER rol nunca es firmada_completa
        (["entrega", "recibe"], ["elabora"], (), "firmada_con_excepcion"),
        # una parte sin cubrir: se queda el valor factual de lo firmado
        (["entrega"], ["elabora"], (), "firmada_remitente"),
        ([], ["recibe"], (), "sellada"),
        # una excepción REVOCADA ya no cubre a la parte ni impide la completitud
        (["entrega", "recibe"], ["recibe"], ("recibe",), "firmada_completa"),
        (["entrega"], ["recibe"], ("recibe",), "firmada_remitente"),
        ([], ["entrega", "recibe"], ("entrega", "recibe"), "sellada"),
    ],
)
async def test_completitud_con_excepcion(personales, excepciones, revocadas, esperado) -> None:
    res = await _verificar(personales, excepciones, revocadas=revocadas)
    assert res.completitud == esperado
    # las revocadas se MUESTRAN (un hecho no se esconde), marcadas como tales
    assert [e.rol for e in res.excepciones] == excepciones
    assert [e.rol for e in res.excepciones if e.revocada] == list(revocadas)
    assert res.acreditado is False


@pytest.mark.asyncio
async def test_la_excepcion_cubre_la_parte_pero_no_apaga_el_gate_por_si_sola() -> None:
    """Con el gate activo y solo una excepción parcial, el acta sigue 'pendiente'; con ambas
    partes cubiertas, el gate se da por atendido y el estado dice la verdad (con excepción)."""
    parcial = await _verificar([], ["recibe"], gate="todos")
    assert parcial.completitud == "firma_personal_pendiente"
    cubierta = await _verificar(["entrega"], ["recibe"], gate="todos")
    assert cubierta.completitud == "firmada_con_excepcion"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("politica_entidad", "gate_entorno", "nivel", "esperado"),
    [
        # la ENTIDAD manda aunque el entorno diga 'off'...
        ("todos", "off", 1, "firma_personal_pendiente"),
        ("clasificados", "off", 2, "firma_personal_pendiente"),
        ("clasificados", "off", 1, "sellada"),
        # ...y puede relajar lo que el entorno exigiría
        ("off", "todos", 3, "sellada"),
        # sin parámetro, respalda el entorno
        (None, "todos", 1, "firma_personal_pendiente"),
        (None, "off", 3, "sellada"),
        # valor corrupto: el más estricto (fail-closed), como signature-service
        ("quizas", "off", 1, "firma_personal_pendiente"),
    ],
)
async def test_firma_personal_pendiente_lee_la_politica_de_la_entidad_no_solo_el_entorno(
    politica_entidad, gate_entorno, nivel, esperado
) -> None:
    res = await _verificar([], [], gate=gate_entorno, politica=politica_entidad, nivel=nivel)
    assert res.completitud == esperado
    assert res.politica_xades == (
        politica_entidad if politica_entidad in ("off", "clasificados", "todos")
        else ("todos" if politica_entidad else gate_entorno)
    )


@pytest.mark.asyncio
async def test_la_verificacion_comprueba_el_sello_de_la_constancia_y_su_veredicto_cuenta() -> None:
    bien = await _verificar([], ["recibe"], verify_ok=True)
    assert bien.excepciones[0].sello_valida is True and bien.valida is True
    mal = await _verificar([], ["recibe"], verify_ok=False)
    assert mal.excepciones[0].sello_valida is False and mal.valida is False


@pytest.mark.asyncio
async def test_una_constancia_legada_sin_firma_se_declara_y_no_se_da_por_valida() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        _acta_firma_row(transferencia_id=tid, estado="firmado", xades_level="XAdES-T",
                        minio_key_firmado="file-id/acta-firmado.xml"),
    ]
    conn.fetch.side_effect = [
        [], [],
        [_excepcion_row(tid, firma_id=None, minio_key_firmado=None, constancia_sha256=None)],
        [],  # XML de la cadena
    ]
    ok = VerifyResult(valida=True, nivel_conformidad="x", acreditado=False, motivo=None)
    with (
        patch("app.clients.storage_client.download_signed_xml", new=AsyncMock(return_value="<xml/>")),
        patch("app.clients.signature_client.verify", new=AsyncMock(return_value=ok)),
        patch("app.services.transferencia_service.check_permission", new=AsyncMock(return_value=False)),
    ):
        res = await TransferenciaService(conn, TENANT).acta_verificacion(tid, USER_ID)
    assert res.constancias_sin_firma == 1 and res.valida is False
    assert res.excepciones[0].firmada is False


@pytest.mark.asyncio
async def test_las_advertencias_de_signature_llegan_al_acta_verificacion() -> None:
    res = await _verificar(
        ["entrega"], ["recibe"], advertencias=["tsa_sin_esscertid_anterior_al_corte"]
    )
    assert res.sello.advertencias == ["tsa_sin_esscertid_anterior_al_corte"]
    assert res.firmas_personales[0].advertencias == ["tsa_sin_esscertid_anterior_al_corte"]
    assert res.excepciones[0].advertencias == ["tsa_sin_esscertid_anterior_al_corte"]
    assert res.advertencias == ["tsa_sin_esscertid_anterior_al_corte"]  # agregado, sin repetir


# --- el fundamento es dato sensible ------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("puede_ver", [True, False])
async def test_la_verificacion_del_acta_nunca_devuelve_el_fundamento_ni_la_sal(puede_ver: bool) -> None:
    """Contrato: ni a administración nivel 3. El texto sale solo por `GET .../fundamento`."""
    res = await _verificar([], ["recibe"], puede_ver=puede_ver)
    e = res.excepciones[0]
    assert e.fundamento_compromiso == COMPROMISO  # el compromiso lo ve cualquiera
    assert e.motivo == "fallecimiento" and e.referencia_documental == f"radicado:{RAD}"
    assert e.fundamento is None and e.fundamento_sal is None
    assert e.revocacion_fundamento is None and e.revocacion_fundamento_sal is None
    assert FUNDAMENTO not in res.model_dump_json() and SAL.hex() not in res.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("puede_ver", [True, False])
async def test_get_acta_nunca_devuelve_el_fundamento_ni_la_sal_ni_asienta_lectura(puede_ver: bool) -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,  # sin sello
    ]
    conn.fetch.side_effect = [[], [_excepcion_row(tid)]]  # radicados del expediente, constancias
    with (
        patch(
            "app.services.transferencia_service.check_permission",
            new=AsyncMock(return_value=puede_ver),
        ) as cp,
        patch(
            "app.clients.document_client.fetch_radicado_metadata_internal",
            new=AsyncMock(return_value={str(RAD): {"tracking_number": NUMERO_RAD}}),
        ),
    ):
        res = await TransferenciaService(conn, TENANT).acta(tid, USER_ID)
    del cp
    assert res.excepciones_firma[0].fundamento is None  # mutación: devolverlo -> rojo
    assert res.excepciones_firma[0].fundamento_compromiso == COMPROMISO
    assert res.excepciones_firma[0].fundamento_sal is None
    assert FUNDAMENTO not in res.model_dump_json() and SAL.hex() not in res.model_dump_json()
    assert sha256_hex(FUNDAMENTO) not in res.model_dump_json()  # ningún SHA-256 plano en la respuesta
    assert _asientos(conn) == []  # visitar la pantalla no deja asiento de lectura


@pytest.mark.asyncio
async def test_un_lector_anonimo_nunca_ve_el_fundamento() -> None:
    conn = _conn()
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),  # anónimo: sin lectura de clearance
        _acta_row(transferencia_id=tid),
        None,
    ]
    conn.fetch.side_effect = [[], [_excepcion_row(tid)]]
    with patch(
        "app.clients.document_client.fetch_radicado_metadata_internal",
        new=AsyncMock(return_value={}),  # el anónimo no ve el radicado de soporte
    ):
        res = await TransferenciaService(conn, TENANT).acta(tid, None)
    assert res.excepciones_firma[0].fundamento is None


# --- la referencia de soporte se recorta por el clearance del LECTOR ------------------------

_CAMPOS_REFERENCIA = (
    "referencia_documental", "referencia_radicado_id", "referencia_radicado_numero",
    "referencia_anexo_id", "referencia_anexo_sha256",
)


@pytest.mark.asyncio
async def test_dos_lectores_con_distinto_clearance_ven_referencias_distintas() -> None:
    """Un lote por lectura, con el clearance de ESE lector. El que puede ver el radicado recibe
    la referencia; el que no, `None` en todos los campos `referencia_*`."""
    meta: list = []
    alto = await _verificar([], ["recibe"], clearance=3, meta_out=meta)
    bajo = await _verificar([], ["recibe"], clearance=1, visibles={}, meta_out=meta)
    assert meta[0].await_args.kwargs["max_clearance"] == 3
    assert meta[1].await_args.kwargs["max_clearance"] == 1
    assert meta[0].await_args.kwargs["radicado_ids"] == [RAD]  # un lote, no una llamada por fila
    e_alto, e_bajo = alto.excepciones[0], bajo.excepciones[0]
    assert e_alto.referencia_documental == f"radicado:{RAD}" and e_alto.referencia_radicado_numero == NUMERO_RAD
    assert e_alto.referencia_anexo_sha256 == SHA_ANEXO
    assert all(getattr(e_bajo, c) is None for c in _CAMPOS_REFERENCIA)
    # el resto de la constancia (huella, motivo, identidades) sigue visible para ambos
    assert e_bajo.fundamento_compromiso == COMPROMISO and e_bajo.motivo == "fallecimiento"
    assert str(RAD) not in bajo.model_dump_json()


@pytest.mark.asyncio
async def test_un_error_de_document_service_es_indistinguible_de_no_poder_verlo() -> None:
    """Si el lote falla, la referencia se oculta igual que si el lector no pudiera verla: un
    503 distinto sería un oráculo sobre la existencia del radicado."""
    fallo = _verificar_con_meta_caido()
    res = await fallo
    assert all(getattr(res.excepciones_firma[0], c) is None for c in _CAMPOS_REFERENCIA)


async def _verificar_con_meta_caido():
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
            "app.clients.document_client.fetch_radicado_metadata_internal",
            new=AsyncMock(side_effect=DocumentClientError("caído")),
        ),
        patch("app.services.transferencia_service.check_permission", new=AsyncMock(return_value=False)),
    ):
        return await TransferenciaService(conn, TENANT).acta(tid, USER_ID)


# --- la cadena completa se verifica ---------------------------------------------------------


@pytest.mark.asyncio
async def test_la_verificacion_recorre_toda_la_cadena_constancias_y_revocaciones() -> None:
    """Ordinal 1 revocada, ordinal 2 revocada, ordinal 3 vigente: se verifican los sellos de las
    TRES constancias y de las DOS revocaciones; la vigente es la única que cubre a la parte."""
    tid = uuid.uuid4()
    c1 = _revocada(tid, ordinal=1)
    c2 = _revocada(tid, ordinal=2, sustituye_a=c1["id"], personal=True)
    c3 = _excepcion_row(tid, ordinal=3, sustituye_a=c2["id"])
    res = await _verificar([], [], filas=[c1, c2, c3])
    assert [e.ordinal for e in res.excepciones] == [1, 2, 3]
    assert [e.revocada for e in res.excepciones] == [True, True, False]
    assert all(e.sello_valida is True for e in res.excepciones)
    assert [e.revocacion_sello_valida for e in res.excepciones] == [True, True, None]
    assert [e.revocacion_firma_personal_valida for e in res.excepciones] == [None, True, None]
    assert res.valida is True
    assert res.completitud == "sellada"  # entrega sin cubrir; solo `recibe` tiene vigente


@pytest.mark.asyncio
@pytest.mark.parametrize("eslabon", ["constancia_revocada", "revocacion"])
async def test_un_eslabon_historico_alterado_hace_invalida_el_acta(eslabon: str) -> None:
    tid = uuid.uuid4()
    c1 = _revocada(tid, ordinal=1)
    c2 = _excepcion_row(tid, ordinal=2, sustituye_a=c1["id"])
    malo = str(c1["firma_id"] if eslabon == "constancia_revocada" else c1["revocacion_firma_id"])
    res = await _verificar([], [], filas=[c1, c2], verify_por_firma={malo: False})
    assert res.valida is False


# --- el router -----------------------------------------------------------------------


_URL = "/api/v1/transferencias/{}/acta/firma-inobtenible"
_BODY = {
    "rol": "recibe",
    "motivo": "fallecimiento",
    "fundamento": FUNDAMENTO,
    "referencia_radicado_id": str(RAD),
    "referencia_anexo_id": str(ANEXO),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("sufijo", ["", "/revocar"])
async def test_el_gate_es_administracion_nivel_3_y_no_el_permiso_de_transferencias(
    client_with_conn: tuple[AsyncClient, AsyncMock], sufijo: str
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    body = _BODY if not sufijo else {
        "rol": "recibe", "causa": "error_de_declaracion", "fundamento": FUNDAMENTO_REVOCACION
    }
    resp = await client.post(_URL.format(uuid.uuid4()) + sufijo, json=body, headers=TENANT_HEADERS)
    assert resp.status_code == 403
    consulta = next(c for c in mock_conn.fetchval.call_args_list if "auth_permissions" in c.args[0])
    assert consulta.args[2:] == ("USUA_PERM_ADMIN", 3)


@pytest.mark.asyncio
async def test_el_router_devuelve_201_con_la_constancia_y_reenvia_el_bearer(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida", decidida_por=str(PARTE)),
        None,
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid),
        None,
        None,
        _excepcion_row(tid),
    ]
    mock_conn.fetchval.side_effect = None
    mock_conn.fetchval.return_value = None  # política: sin parámetro -> entorno 'off'
    with (
        patch("app.core.clearance.check_permission", new=AsyncMock(return_value=True)),
        _externos(personal=SignPersonalOutcome(signed=_signed_personal())) as (_s, sign_pers, _w),
    ):
        resp = await client.post(
            _URL.format(tid),
            json={**_BODY, "totp_code": "123456"},
            headers={**TENANT_HEADERS, "Authorization": "Bearer abc"},
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["rol"] == "recibe" and resp.json()["motivo"] == "fallecimiento"
    assert resp.json()["fundamento_compromiso"] == COMPROMISO and "fundamento_sha256" not in resp.json()
    assert "fundamento_hmac" not in resp.json()
    assert sign_pers.await_args.kwargs["authorization"] == "Bearer abc"
