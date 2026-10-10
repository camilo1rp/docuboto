# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Un webhook no puede llevarse el texto de un documento reservado.

Dar de alta una suscripción exige el permiso de administración, que no es lo
mismo que tener nivel de acceso sobre lo clasificado. Y con la lista de tipos
vacía —que significa «todos»— una sola suscripción se llevaba cada radicación
del sistema a una dirección externa, con asunto, nombre del remitente y, en las
reclasificaciones, la causal de reserva y su fundamento jurídico.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.webhook_service import dispatch, recortar_si_clasificado


def _sobre(nivel: int, **extra):
    payload = {
        "id": "r-1",
        "tracking_number": "2026-DEMO-E-000123",
        "subject": "Tutela de Juan Pérez sobre certificado",
        "sender_name": "Juan Pérez",
        "nivel_seguridad": nivel,
    }
    payload.update(extra)
    return {
        "event_id": "evt-1",
        "event_type": "document.radicado.created",
        "tenant_slug": "demo",
        "payload": payload,
    }


def test_un_radicado_reservado_sale_sin_su_contenido():
    salida = recortar_si_clasificado(_sobre(2))
    texto = json.dumps(salida, ensure_ascii=False)

    assert "Juan Pérez" not in texto
    assert "Tutela" not in texto
    # El sobre sobrevive: quien se integra sigue sabiendo QUÉ pasó y CUÁNDO.
    assert salida["event_type"] == "document.radicado.created"
    assert salida["payload"]["tracking_number"] == "2026-DEMO-E-000123"
    assert "_recorte" in salida["payload"]


def test_la_ruta_de_dependencias_del_radicado_reservado_no_sale():
    salida = recortar_si_clasificado(
        _sobre(3, dest_dept="Despacho Secreto", dest_dept_code="DS01", origin_dept="Inteligencia")
    )
    texto = json.dumps(salida, ensure_ascii=False)
    assert "Despacho Secreto" not in texto
    assert "DS01" not in texto
    assert "Inteligencia" not in texto


def test_la_causal_de_reserva_tampoco_sale():
    """Es el peor caso: el evento que dice POR QUÉ algo es reservado."""
    salida = recortar_si_clasificado(
        _sobre(3, causal_reserva="seguridad nacional", fundamento_juridico="Ley 1712 art. 19")
    )
    texto = json.dumps(salida, ensure_ascii=False)

    assert "seguridad nacional" not in texto
    assert "1712" not in texto


def test_lo_publico_sale_entero():
    """El recorte no puede dejar inservible la integración que protege."""
    sobre = _sobre(1)
    assert recortar_si_clasificado(sobre) == sobre


def test_un_evento_ajeno_a_radicados_sin_nivel_sale_entero():
    """Lo que no es de `document.*`/`workflow.*` no se refiere a un radicado."""
    sobre = {"event_type": "notification.sent", "payload": {"canal": "email"}}
    assert recortar_si_clasificado(sobre) == sobre


def test_un_evento_de_workflow_sin_nivel_sale_minimo():
    """Antes se afirmaba lo contrario («no se recorta»): era el supuesto falso.
    Un evento de radicado que no declara nivel se trata como clasificado."""
    sobre = {
        "event_id": "e-9",
        "event_type": "workflow.step.created",
        "tenant_slug": "demo",
        "occurred_at": "2026-10-02T10:00:00Z",
        "actor": "u-1",
        "dedup_key": "2026-DEMO-E-000123:x",
        "payload": {
            "radicado_id": "r-9",
            "tracking_number": "2026-DEMO-E-000123",
            "from_dept": "Jurídica",
            "to_dept": "Dirección",
        },
    }
    salida = recortar_si_clasificado(sobre)
    texto = json.dumps(salida, ensure_ascii=False)
    assert "000123" not in texto and "Jurídica" not in texto and "Dirección" not in texto
    assert salida["event_type"] == "workflow.step.created"
    assert salida["event_id"] == "e-9" and salida["payload"]["id"] == "r-9"


def test_la_ruta_de_un_workflow_reservado_declarado_tampoco_sale():
    sobre = {
        "event_type": "workflow.step.created",
        "payload": {"nivel_seguridad": 3, "to_dept": "Dirección", "step_number": 2},
    }
    texto = json.dumps(recortar_si_clasificado(sobre), ensure_ascii=False)
    assert "Dirección" not in texto
    assert '"step_number": 2' in texto


def test_la_reclasificacion_no_saca_la_causal_ni_el_fundamento():
    """El productor envía `nivel_nuevo`/`nivel_anterior`, no `nivel_seguridad`."""
    sobre = {
        "event_type": "document.radicado.reclassified",
        "payload": {
            "id": "r-1",
            "tracking_number": "2026-DEMO-E-000123",
            "nivel_anterior": 1,
            "nivel_nuevo": 3,
            "causal_reserva": "seguridad nacional",
            "fundamento_juridico": "Ley 1712 art. 19 literal a",
            "plazo_reserva_meses": 60,
        },
    }
    salida = recortar_si_clasificado(sobre)
    texto = json.dumps(salida, ensure_ascii=False)
    assert "seguridad nacional" not in texto and "1712" not in texto
    assert "plazo_reserva" not in texto
    assert salida["payload"]["nivel_nuevo"] == 3


def test_desclasificar_no_filtra_lo_que_fue_reservado():
    """3 -> 1: el nivel efectivo es el MÁXIMO, no el nuevo."""
    sobre = {
        "event_type": "document.radicado.reclassified",
        "payload": {"nivel_anterior": 3, "nivel_nuevo": 1, "subject": "Tutela de Juan Pérez"},
    }
    assert "Tutela" not in json.dumps(recortar_si_clasificado(sobre))


def test_la_causal_se_quita_aun_de_un_publico():
    sobre = {
        "event_type": "document.radicado.reclassified",
        "payload": {"nivel_anterior": 1, "nivel_nuevo": 1, "causal_reserva": "x-causal"},
    }
    assert "x-causal" not in json.dumps(recortar_si_clasificado(sobre))


@pytest.mark.asyncio
async def test_el_recorte_ESTA_CABLEADO_en_el_envio():
    """Probar la función aislada no prueba que se invoque.

    Se mira lo que de verdad viaja por el cable, y con un control positivo
    delante: una aserción de ausencia es cierta también si no se envió nada.
    """
    enviados = []

    async def _entregar(client, sub, body, tenant_slug="", **_kw):
        enviados.append(body)

    with patch("app.services.webhook_service.WebhookRepository") as repo, patch(
        "app.services.webhook_service.acquire_tenant"
    ) as tenant, patch("app.services.webhook_service._deliver_one", _entregar):
        tenant.return_value.__aenter__ = AsyncMock(return_value=object())
        tenant.return_value.__aexit__ = AsyncMock(return_value=False)
        repo.return_value.active_for_event = AsyncMock(
            return_value=[{"id": "s1", "url": "https://x.example/e", "secret": "s"}]
        )
        await dispatch(_sobre(2))

    assert len(enviados) == 1, "no se envió nada: la prueba no mide el recorte"
    cuerpo = enviados[0].decode()
    assert "2026-DEMO-E-000123" in cuerpo, "el evento no llegó: no se está midiendo"
    assert "Juan Pérez" not in cuerpo, "el nombre del ciudadano salió a la URL externa"
    assert "Tutela" not in cuerpo


@pytest.mark.asyncio
async def test_la_reclasificacion_con_la_forma_del_productor_sale_sin_causal_por_el_cable():
    """Forma EXACTA de `DocumentService.reclassify` (document_service.py): sin
    `nivel_seguridad`, con `nivel_nuevo`/`nivel_anterior`. Por el cable, con
    control positivo."""
    enviados = []

    async def _entregar(client, sub, body, tenant_slug="", **_kw):
        enviados.append(body)

    sobre = {
        "event_id": "evt-2",
        "event_type": "document.radicado.reclassified",
        "tenant_slug": "demo",
        "dedup_key": "2026-DEMO-E-000123:3",
        "payload": {
            "id": "r-1",
            "tracking_number": "2026-DEMO-E-000123",
            "nivel_anterior": 1,
            "nivel_nuevo": 3,
            "causal_reserva": "seguridad nacional",
            "fundamento_juridico": "Ley 1712 art. 19 literal a",
            "plazo_reserva_meses": 60,
        },
    }
    with patch("app.services.webhook_service.WebhookRepository") as repo, patch(
        "app.services.webhook_service.acquire_tenant"
    ) as tenant, patch("app.services.webhook_service._deliver_one", _entregar):
        tenant.return_value.__aenter__ = AsyncMock(return_value=object())
        tenant.return_value.__aexit__ = AsyncMock(return_value=False)
        repo.return_value.active_for_event = AsyncMock(
            return_value=[{"id": "s1", "url": "https://x.example/e", "secret": "s"}]
        )
        await dispatch(sobre)

    assert len(enviados) == 1
    cuerpo = enviados[0].decode()
    assert "document.radicado.reclassified" in cuerpo and '"nivel_nuevo": 3' in cuerpo
    assert "seguridad nacional" not in cuerpo
    assert "1712" not in cuerpo
