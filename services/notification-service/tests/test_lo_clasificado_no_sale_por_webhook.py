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


def test_sin_nivel_declarado_no_se_recorta():
    """El nivel es dato del payload; recortar lo que no lo declara dejaría sin
    integración a los eventos que no son documentos."""
    sobre = {"event_type": "workflow.step.created", "payload": {"paso": 3}}
    assert recortar_si_clasificado(sobre) == sobre


@pytest.mark.asyncio
async def test_el_recorte_ESTA_CABLEADO_en_el_envio():
    """Probar la función aislada no prueba que se invoque.

    Se mira lo que de verdad viaja por el cable, y con un control positivo
    delante: una aserción de ausencia es cierta también si no se envió nada.
    """
    enviados = []

    async def _entregar(client, sub, body):
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
