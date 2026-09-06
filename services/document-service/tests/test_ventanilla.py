# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Ventanilla única de radicación (superficie PÚBLICA, sin autenticar).

La versión anterior asumía que lo único que entra sin autenticar es una PQRS y
fijaba en un `Literal` la taxonomía de la Ley 1755. Una ventanilla única recibe
TODA la correspondencia externa: tutelas, oficios de otras entidades, facturas.

Lo que estos tests fijan, en orden de importancia:

1. **Nada entra sin dependencia destino.** Antes se radicaba con `dest_dept`
   nulo: el documento no estaba en la bandeja de nadie, y un derecho de petición
   con quince días hábiles de plazo que nadie ve es cómo se incumple un término.
2. **El tipo tiene que estar declarado admisible**, y un tipo no admitido se
   rechaza — nunca se acepta reclasificándolo en silencio, porque el tipo
   determina la serie TRD y con ella la retención.
3. **Sin ventanilla habilitada, la radicación se niega** en vez de dejar el
   documento en el limbo con un número que hace creer que el trámite empezó.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from starlette.datastructures import UploadFile

from app.clients.storage_client import ArchivoSubido

BASE = "/api/v1/public/demo/ventanilla"


def _multipart(cuerpo: dict, ficheros: list | None = None) -> dict:
    """El endpoint es multipart: los datos van en una parte JSON y los anexos en
    otras, para que la radicación y sus soportes sean UNA sola petición."""
    kwargs: dict = {"data": {"datos": json.dumps(cuerpo)}}
    if ficheros:
        kwargs["files"] = [("anexos", f) for f in ficheros]
    return kwargs


#: PDF mínimo: la comprobación es por CONTENIDO, no por extensión ni por el
#: `Content-Type` que declare el cliente.
PDF = b"%PDF-1.4\n%mini\n"


def _cuerpo(**kw) -> dict:
    base = {
        "tipo_documental": "DP-GEN",
        "nombre": "Ana Ruiz",
        "identificacion": "1020304050",
        "email": "ana@example.org",
        "asunto": "Solicitud de certificación",
        "descripcion": "Requiero certificación laboral de los últimos dos años.",
    }
    base.update(kw)
    return base


def _resultado_radicado():
    r = AsyncMock()
    r.id = uuid.uuid4()
    r.tracking_number = "2026-DEMO-E-000042"
    return r


@pytest.mark.asyncio
async def test_los_tipos_ofrecidos_son_SOLO_los_declarados(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetch.return_value = [{"code": "DP-GEN", "nombre": "Derecho de petición"}]

    resp = await client.get("/api/v1/public/demo/ventanilla/tipos")

    assert resp.status_code == 200
    assert resp.json() == [{"code": "DP-GEN", "nombre": "Derecho de petición"}]
    sql = conn.fetch.await_args.args[0]
    # Opt-in: el catálogo interno de la entidad no se expone por existir.
    assert "admite_ventanilla_publica" in sql
    assert "activo" in sql


@pytest.mark.asyncio
async def test_un_tipo_no_admitido_se_RECHAZA_y_no_se_reclasifica(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchval.return_value = None  # el tipo no está declarado admisible

    resp = await client.post(BASE, **_multipart(_cuerpo(tipo_documental="ACTA-COMITE")))

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "tipo_no_admitido_en_ventanilla"


@pytest.mark.asyncio
async def test_sin_ventanilla_habilitada_la_radicacion_se_NIEGA(client_with_conn) -> None:
    """503 explícito: entrar sin destino sería peor que no entrar.

    Quien radica se iría con un número creyendo que su trámite empezó, y el
    documento no estaría en la bandeja de nadie.
    """
    client, conn = client_with_conn
    conn.fetchval.return_value = "Derecho de petición"  # el tipo sí es admisible
    conn.fetchrow.return_value = None  # ninguna dependencia con `depe_rad_tp2`

    resp = await client.post(BASE, **_multipart(_cuerpo()))

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "ventanilla_no_habilitada"


@pytest.mark.asyncio
async def test_lo_radicado_LLEGA_a_la_dependencia_de_ventanilla(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchval.side_effect = ["Derecho de petición", "cod-verificacion"]
    conn.fetchrow.return_value = {"codigo": 901, "nombre": "Ventanilla Única de Radicación"}

    creado = _resultado_radicado()
    with patch(
        "app.services.document_service.DocumentService.create",
        AsyncMock(return_value=creado),
    ) as mock_create:
        resp = await client.post(BASE, **_multipart(_cuerpo()))

    assert resp.status_code == 201, resp.text
    doc = mock_create.await_args.args[0]
    # LO QUE IMPORTA: no entra sin destino.
    assert doc.dest_dept_code == 901
    # …y el tipo viaja como clasificación, así que hereda la serie TRD.
    assert doc.doc_class == "DP-GEN"
    assert doc.doc_type == "E"
    assert resp.json()["dependencia_destino"] == "Ventanilla Única de Radicación"

    sql_dep = conn.fetchrow.await_args.args[0]
    assert "depe_rad_tp2" in sql_dep, "la ventanilla se resuelve por la bandera del organigrama"


@pytest.mark.asyncio
async def test_una_ENTIDAD_remitente_no_se_escribe_en_el_campo_de_persona(
    client_with_conn,
) -> None:
    """Un oficio lo manda una entidad con su NIT, no un ciudadano.

    `sender_entity` existe en el modelo desde siempre; la ventanilla pública era
    la única puerta que lo ignoraba y obligaba a meter el nombre de la empresa
    en el campo de la persona natural.
    """
    client, conn = client_with_conn
    conn.fetchval.side_effect = ["Oficio", "cod"]
    conn.fetchrow.return_value = {"codigo": 901, "nombre": "VUR"}

    with patch(
        "app.services.document_service.DocumentService.create",
        AsyncMock(return_value=_resultado_radicado()),
    ) as mock_create:
        resp = await client.post(BASE, **_multipart(_cuerpo(
                tipo_documental="OFICIO",
                nombre="María Gómez",
                entidad="Contraloría General de la República",
                identificacion="800123456",
                tipo_identificacion="NIT",
            )))

    assert resp.status_code == 201, resp.text
    doc = mock_create.await_args.args[0]
    assert doc.sender_entity == "Contraloría General de la República"
    assert doc.sender_name == "María Gómez"


@pytest.mark.asyncio
async def test_quien_se_identifica_debe_dejar_un_medio_de_respuesta(client_with_conn) -> None:
    """Aceptar a alguien identificado sin forma de contestarle garantiza el
    incumplimiento del término: la entidad tendría la obligación y ningún medio."""
    client, _ = client_with_conn

    resp = await client.post(BASE, **_multipart(_cuerpo(email=None, telefono=None, direccion=None)))

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_una_radicacion_ANONIMA_es_legitima(client_with_conn) -> None:
    """Una denuncia anónima no espera contestación personal: exigir un canal la
    impediría. El código de verificación le sirve de seguimiento."""
    client, conn = client_with_conn
    conn.fetchval.side_effect = ["Denuncia", "cod"]
    conn.fetchrow.return_value = {"codigo": 901, "nombre": "VUR"}

    with patch(
        "app.services.document_service.DocumentService.create",
        AsyncMock(return_value=_resultado_radicado()),
    ):
        resp = await client.post(BASE, **_multipart(_cuerpo(
                tipo_documental="DENUNCIA",
                identificacion=None,
                email=None,
                telefono=None,
                direccion=None,
            )))

    assert resp.status_code == 201, resp.text
    assert resp.json()["verification_code"] == "cod"


# ---------------------------------------------------------------------------
# Anexos — la parte con riesgo: subida SIN autenticar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_el_tipo_del_anexo_sale_del_CONTENIDO_no_de_lo_que_declare_el_cliente(
    client_with_conn,
) -> None:
    """El `Content-Type` de una subida lo escribe el cliente.

    Un desconocido puede declarar `application/pdf` y enviar cualquier cosa. Si
    no se comprueban los bytes, la lista de formatos es una cortesía que se
    esquiva cambiando una cabecera. Además, el MIME que se GUARDA es el
    deducido: así un fichero no queda registrado con un tipo que no es el suyo.
    """
    client, conn = client_with_conn
    conn.fetchval.side_effect = ["Petición", "cod"]
    conn.fetchrow.return_value = {"codigo": 901, "nombre": "VUR"}

    with (
        patch(
            "app.services.ventanilla_service.upload_anexo_externo",
            # ArchivoSubido, NO un dict con una clave inventada: un mock que
            # devuelve la forma equivocada comprueba que el código coincide con
            # la creencia de quien lo escribió, no con el servicio real.
            AsyncMock(return_value=ArchivoSubido(file_id=uuid.uuid4())),
        ) as mock_up,
        patch(
            "app.services.document_service.DocumentService.create",
            AsyncMock(return_value=_resultado_radicado()),
        ) as mock_create,
    ):
        resp = await client.post(
            BASE,
            **_multipart(
                _cuerpo(),
                # Se declara `image/png` y el contenido es un PDF: manda el contenido.
                [("soporte.png", PDF, "image/png")],
            ),
        )

    assert resp.status_code == 201, resp.text
    assert mock_up.await_args.kwargs["mime_type"] == "application/pdf"
    anexo = mock_create.await_args.args[0].anexos[0]
    assert anexo.mime_type == "application/pdf"


@pytest.mark.asyncio
async def test_un_formato_no_admitido_se_rechaza_y_NO_se_radica(client_with_conn) -> None:
    """Sin radicar nada: un número de radicado es inmutable (Ley 594/2000), así
    que no puede quedar uno emitido al que le falten sus soportes."""
    client, conn = client_with_conn
    conn.fetchval.side_effect = ["Petición", "cod"]
    conn.fetchrow.return_value = {"codigo": 901, "nombre": "VUR"}

    with (
        patch("app.services.ventanilla_service.upload_anexo_externo", AsyncMock()) as mock_up,
        patch(
            "app.services.document_service.DocumentService.create", AsyncMock()
        ) as mock_create,
    ):
        resp = await client.post(
            BASE,
            **_multipart(_cuerpo(), [("virus.exe", b"MZ\x90\x00ejecutable", "application/pdf")]),
        )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "anexo_rechazado"
    mock_up.assert_not_awaited()
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_mas_anexos_de_la_cuenta_se_rechazan_ANTES_de_leerlos(client_with_conn) -> None:
    from app.core.anexos_publicos import MAX_ANEXOS

    client, _ = client_with_conn
    ficheros = [(f"s{i}.pdf", PDF, "application/pdf") for i in range(MAX_ANEXOS + 1)]

    with patch("app.services.ventanilla_service.upload_anexo_externo", AsyncMock()) as mock_up:
        resp = await client.post(BASE, **_multipart(_cuerpo(), ficheros))

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "anexo_rechazado"
    mock_up.assert_not_awaited()


@pytest.mark.asyncio
async def test_si_falla_el_almacen_NO_queda_un_radicado_sin_soportes(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchval.side_effect = ["Petición", "cod"]
    conn.fetchrow.return_value = {"codigo": 901, "nombre": "VUR"}

    from app.clients.storage_client import StorageContentError

    with (
        patch(
            "app.services.ventanilla_service.upload_anexo_externo",
            AsyncMock(side_effect=StorageContentError("caído")),
        ),
        patch(
            "app.services.document_service.DocumentService.create", AsyncMock()
        ) as mock_create,
    ):
        resp = await client.post(
            BASE, **_multipart(_cuerpo(), [("soporte.pdf", PDF, "application/pdf")])
        )

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "anexo_no_almacenado"
    # LO QUE IMPORTA: no se asignó número.
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_radicar_SIN_anexos_sigue_siendo_valido(client_with_conn) -> None:
    """El caso simple no necesita otra puerta: el multipart lleva una sola parte."""
    client, conn = client_with_conn
    conn.fetchval.side_effect = ["Petición", "cod"]
    conn.fetchrow.return_value = {"codigo": 901, "nombre": "VUR"}

    with patch(
        "app.services.document_service.DocumentService.create",
        AsyncMock(return_value=_resultado_radicado()),
    ) as mock_create:
        resp = await client.post(BASE, **_multipart(_cuerpo()))

    assert resp.status_code == 201, resp.text
    assert mock_create.await_args.args[0].anexos == []


@pytest.mark.asyncio
async def test_un_anexo_gigante_no_llega_entero_a_memoria(client_with_conn) -> None:
    """El tope de tamaño se aplica ANTES de traer el fichero a memoria.

    Es el gemelo del test de número, que ya existía. La puerta es anónima: si el
    rechazo llega después de leer, un solo multipart grande basta para tumbar la
    radicación de la entidad, y el 422 no evita nada porque el coste ya se pagó.

    La aserción NO puede ser solo el 422 —el código defectuoso también respondía
    422— así que se mide lo que de verdad distingue: cuántos bytes se leyeron.
    """
    from app.core.anexos_publicos import MAX_BYTES_POR_ANEXO

    client, _ = client_with_conn
    gigante = PDF + b"\x00" * (MAX_BYTES_POR_ANEXO + 4096)

    leidos: list[int] = []
    original = UploadFile.read

    async def read_contando(self, size: int = -1):  # type: ignore[no-untyped-def]
        datos = await original(self, size)
        leidos.append(len(datos))
        return datos

    with patch.object(UploadFile, "read", read_contando):
        with patch("app.services.ventanilla_service.upload_anexo_externo", AsyncMock()) as mock_up:
            resp = await client.post(
                BASE, **_multipart(_cuerpo(), [("enorme.pdf", gigante, "application/pdf")])
            )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "anexo_rechazado"
    mock_up.assert_not_awaited()
    assert all(n <= MAX_BYTES_POR_ANEXO + 1 for n in leidos), (
        f"se trajo a memoria el anexo entero antes de rechazarlo: {leidos} bytes; "
        "el tope llegó tarde y la puerta anónima sigue siendo un vector de caída"
    )
