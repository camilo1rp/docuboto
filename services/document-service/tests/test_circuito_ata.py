# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El circuito de aprobación tiene que atar (E23, RF-PRO-09).

Estos tests existen por un defecto que ninguna suite veía porque no estaba en
ninguna pieza: `aprobar` no consultaba nada. Se abría un circuito de tres
turnos sobre un borrador, nadie resolvía ninguno, y quien tuviera `PERM_RADI`
pulsaba «Aprobar» y radicaba. El visto bueno de los funcionarios era
decorativo, y el gate `documento_modificado` que protege cada turno no protegía
nada, porque nadie estaba obligado a pasar por él.

Los tres casos son las tres respuestas posibles a «¿hay circuito?», y el
tercero —no se sabe— es el que suele quedarse sin escribir: es justo donde un
sistema degradado tiende a fallar hacia el lado permisivo.
"""

from __future__ import annotations

import uuid

import pytest

from app.clients import signature_client as signature_client_mod
from tests.conftest import TENANT_HEADERS
from tests.test_borradores import _CLEARANCE_ROW, _USER, _borrador_row


def _fija_circuito(monkeypatch, **estado):
    """Hace que la consulta a signature-service devuelva `estado`."""

    async def _fake(borrador_id: str, tenant_slug: str) -> dict:
        return {"pendiente": False, "completo": False, "solicitud_id": None, **estado}

    monkeypatch.setattr(signature_client_mod, "get_estado_circuito", _fake)


@pytest.mark.asyncio
async def test_con_circuito_abierto_no_se_puede_aprobar(client_with_conn, monkeypatch):
    """Éste es el defecto: antes devolvía 200 y el circuito se quedaba colgando."""
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    sol = str(uuid.uuid4())
    _fija_circuito(monkeypatch, pendiente=True, solicitud_id=sol)
    mock_conn.fetchrow.side_effect = [_CLEARANCE_ROW, _borrador_row(id=bid, estado="borrador")]

    resp = await client.post(f"/api/v1/borradores/{bid}/aprobar", headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "circuito_pendiente"
    # Y no se tocó el estado: `set_estado` habría sido un segundo fetchrow.
    assert mock_conn.fetchrow.call_count == 2


@pytest.mark.asyncio
async def test_sin_circuito_se_aprueba_como_siempre(client_with_conn, monkeypatch):
    """El gate es vinculante SI EXISTE, no obligatorio.

    Hay entidades donde una salida no pasa por visto bueno; convertir el
    circuito en requisito de todas habría sido un cambio que nadie pidió.
    """
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    _fija_circuito(monkeypatch)
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _borrador_row(id=bid, estado="borrador"),
        _borrador_row(id=bid, estado="aprobado"),
    ]

    resp = await client.post(f"/api/v1/borradores/{bid}/aprobar", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert resp.json()["estado"] == "aprobado"


@pytest.mark.asyncio
async def test_circuito_ya_completo_deja_aprobar(client_with_conn, monkeypatch):
    """Resuelto el último turno, el botón vuelve a estar disponible.

    Es lo que permite que el aviso automático de signature-service sea
    best-effort sin abrir ninguna ventana: si el aviso se pierde, se aprueba
    con un clic; lo que nunca ocurre es aprobar con turnos sin resolver.
    """
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    _fija_circuito(monkeypatch, pendiente=False, completo=True)
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _borrador_row(id=bid, estado="borrador"),
        _borrador_row(id=bid, estado="aprobado"),
    ]

    resp = await client.post(f"/api/v1/borradores/{bid}/aprobar", headers=TENANT_HEADERS)

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_si_no_se_puede_preguntar_no_se_aprueba(client_with_conn, monkeypatch):
    """503, no «supongo que no hay circuito».

    Suponer reabriría el agujero por la puerta de atrás, y justo cuando el
    sistema está degradado. La única consecuencia de no saber es no poder
    aprobar; nunca aprobar de más.
    """
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()

    async def _cae(borrador_id: str, tenant_slug: str) -> dict:
        raise signature_client_mod.CircuitoNoVerificableError("signature caído")

    monkeypatch.setattr(signature_client_mod, "get_estado_circuito", _cae)
    mock_conn.fetchrow.side_effect = [_CLEARANCE_ROW, _borrador_row(id=bid, estado="borrador")]

    resp = await client.post(f"/api/v1/borradores/{bid}/aprobar", headers=TENANT_HEADERS)

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "circuito_no_verificable"
    assert mock_conn.fetchrow.call_count == 2


@pytest.mark.asyncio
async def test_la_consulta_viaja_con_el_slug_del_tenant(client_with_conn, monkeypatch):
    """Sin slug la consulta va contra el schema equivocado y el gate no ve nada.

    Es la forma silenciosa de que este gate desaparezca: seguiría respondiendo
    200 en todos los casos, que es exactamente lo que hacía antes.
    """
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    visto: dict = {}

    async def _fake(borrador_id: str, tenant_slug: str) -> dict:
        visto["borrador_id"] = borrador_id
        visto["tenant_slug"] = tenant_slug
        return {"pendiente": False, "completo": False, "solicitud_id": None}

    monkeypatch.setattr(signature_client_mod, "get_estado_circuito", _fake)
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _borrador_row(id=bid, estado="borrador"),
        _borrador_row(id=bid, estado="aprobado"),
    ]

    await client.post(f"/api/v1/borradores/{bid}/aprobar", headers=TENANT_HEADERS)

    assert visto["borrador_id"] == str(bid)
    assert visto["tenant_slug"] == TENANT_HEADERS["X-Tenant-Slug"]


# ---------------------------------------------------------------------------
# Radicar es el acto que de verdad había que atar
# ---------------------------------------------------------------------------
#
# El gate en `aprobar` no bastaba, y creerlo suficiente fue un error de
# análisis: en la pantalla el circuito se abre sobre un borrador **ya
# aprobado** —ese es el orden del trámite: se aprueba el texto, se manda a
# visto bueno, firma quien responde, y entonces sale—. Así que la vía que de
# verdad se usaba para saltárselo no era aprobar dos veces: era radicar con los
# turnos sin resolver.
#
# Y radicar es peor. Aprobar de más se deshace devolviendo a edición; un
# consecutivo gastado no se devuelve nunca, porque la ley lo hace inmutable y
# un hueco en la secuencia hay que explicárselo a un auditor.
#
# Se comprobó en `tenant_demo`: dos borradores aprobados con su circuito
# abierto, a un clic de radicarse.


@pytest.mark.asyncio
async def test_con_circuito_abierto_no_se_puede_radicar(client_with_conn, monkeypatch):
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    _fija_circuito(monkeypatch, pendiente=True, solicitud_id=str(uuid.uuid4()))
    mock_conn.fetchval.return_value = True  # permiso concedido: no es lo que se prueba
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _borrador_row(id=bid, doc_type="S", estado="aprobado"),
    ]

    resp = await client.post(
        f"/api/v1/borradores/{bid}/radicar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "circuito_pendiente"
    # El mensaje nombra el acto: «no se aprueba desde aquí» delante de un botón
    # de radicar manda a buscar un problema que no existe.
    assert "radica" in resp.json()["detail"]["detail"]


@pytest.mark.asyncio
async def test_el_numero_no_se_consume_si_el_circuito_esta_abierto(
    client_with_conn, monkeypatch
):
    """Lo que importa no es el código de estado: es que no se gastó nada.

    El gate va ANTES de combinar, renderizar y pedir el consecutivo — el mismo
    orden por el que un fallo de render tampoco gasta un número.
    """
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    _fija_circuito(monkeypatch, pendiente=True, solicitud_id=str(uuid.uuid4()))
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _borrador_row(id=bid, doc_type="S", estado="aprobado"),
    ]

    await client.post(
        f"/api/v1/borradores/{bid}/radicar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    # Dos lecturas: el clearance del llamante y el propio borrador. Ni el
    # codigo del tenant, ni la secuencia, ni el INSERT del radicado.
    assert mock_conn.fetchrow.call_count == 2
    mock_conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_editar_tras_firmar_no_bloquea_pero_radicar_exige_firmar_de_nuevo(
    client_with_conn, monkeypatch
):
    """El texto cambió después de firmarse: se puede, pero no se radica así.

    Cambiar un documento ya firmado es parte del trámite —se recogen los vistos
    buenos y alguien recuerda un anexo o corrige una cifra a última hora—, así
    que el sistema NO lo impide: para eso existe `devolver_a_borrador`
    (RF-PRO-10). Lo que no puede es dar por buenas las firmas anteriores.

    Antes salía un documento oficial, con número inmutable, cuyo texto nadie
    aprobó, mientras los asientos seguían atestiguando que sí. Como en el test
    gemelo del circuito abierto, lo que importa no es el código de estado: es
    que no se gastó el consecutivo.
    """
    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    _fija_circuito(
        monkeypatch, pendiente=False, completo=True, payload_sha256="huella-firmada"
    )
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _borrador_row(
            id=bid, doc_type="S", estado="aprobado", cuerpo_sha256="huella-DEL-TEXTO-NUEVO"
        ),
    ]

    resp = await client.post(
        f"/api/v1/borradores/{bid}/radicar",
        headers={**TENANT_HEADERS, "X-User-Id": _USER},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "texto_modificado_tras_firmar"
    # El mensaje tiene que decir cómo seguir, no solo que no.
    assert "circuito nuevo" in resp.json()["detail"]["detail"]
    # Y el número no se gastó: ni la secuencia ni el INSERT del radicado.
    assert mock_conn.fetchrow.call_count == 2
    mock_conn.execute.assert_not_awaited()


def _gate(payload_sha256, cuerpo_sha256, completo=True):
    """Ejerce la DECISIÓN, sin el andamiaje de una radicación completa."""
    from app.services.borrador_service import BorradorService

    estado = {"pendiente": False, "completo": completo, "payload_sha256": payload_sha256}
    BorradorService._gate_texto_firmado(
        None, {"cuerpo_sha256": cuerpo_sha256}, estado
    )


def test_el_texto_que_se_firmo_si_se_radica():
    """Si nadie tocó el texto, el gate calla. Un gate que nadie puede satisfacer suprime."""
    _gate("la-misma-huella", "la-misma-huella")


def test_sin_circuito_el_camino_directo_sigue_abierto():
    """Sin circuito no hay nada que comparar: no se exige una firma que nadie pidió."""
    _gate(None, "cualquiera", completo=False)


def test_con_el_texto_cambiado_el_gate_se_dispara():
    """El control positivo del par de arriba: si no distinguiera, no probaría nada."""
    import pytest as _pytest
    from fastapi import HTTPException

    with _pytest.raises(HTTPException) as exc:
        _gate("huella-vieja", "huella-nueva")
    assert exc.value.detail["error"] == "texto_modificado_tras_firmar"


@pytest.mark.asyncio
async def test_editar_el_texto_invalida_los_circuitos_anteriores(client_with_conn, monkeypatch):
    """Al cambiar el texto, los turnos del circuito superado dejan de pedirse.

    Es la mitad humana de la corrección. La garantía dura es la comparación de
    huellas al radicar; esto evita que la bandeja siga pidiéndole a alguien
    firmar un documento que ya no existe, para después esfumarse sin motivo.
    """
    from app.clients import signature_client as sc

    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    llamadas = []

    async def _invalidar(borrador_id, tenant_slug):
        llamadas.append((borrador_id, tenant_slug))
        return 1

    monkeypatch.setattr(sc, "invalidar_circuitos", _invalidar)
    # Antes y después de editar: la huella CAMBIA.
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _borrador_row(id=bid, estado="aprobado", cuerpo_sha256="huella-vieja"),
        _borrador_row(id=bid, estado="aprobado", cuerpo_sha256="huella-nueva"),
    ]

    resp = await client.patch(
        f"/api/v1/borradores/{bid}",
        json={"cuerpo": "texto corregido a última hora"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, "editar un borrador firmado NO se bloquea: es el trámite"
    assert [c[0] for c in llamadas] == [str(bid)], "no se invalidó el circuito anterior"


@pytest.mark.asyncio
async def test_guardar_sin_cambiar_el_texto_no_tira_un_circuito_vivo(
    client_with_conn, monkeypatch
):
    """La condición mira la HUELLA, no el campo enviado.

    `cuerpo` puede venir en el payload con el mismo texto de siempre —o cambiar
    solo el asunto—, y tirar el circuito ahí obligaría a recoger las firmas otra
    vez sin que nadie haya tocado el documento.
    """
    from app.clients import signature_client as sc

    client, mock_conn = client_with_conn
    bid = uuid.uuid4()
    llamadas = []

    async def _invalidar(borrador_id, tenant_slug):
        llamadas.append(borrador_id)
        return 1

    monkeypatch.setattr(sc, "invalidar_circuitos", _invalidar)
    mock_conn.fetchrow.side_effect = [
        _CLEARANCE_ROW,
        _borrador_row(id=bid, estado="aprobado", cuerpo_sha256="la-misma"),
        _borrador_row(id=bid, estado="aprobado", cuerpo_sha256="la-misma"),
    ]

    resp = await client.patch(
        f"/api/v1/borradores/{bid}", json={"subject": "Otro asunto"}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 200
    assert llamadas == [], "se tiró un circuito vivo sin que el texto cambiara"
