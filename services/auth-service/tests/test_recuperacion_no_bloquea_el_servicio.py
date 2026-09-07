# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Comprobar un código de recuperación no puede detener el servicio entero.

El derivado de claves gasta 16 MiB y se repite sobre los ~10 códigos vivos para
no cortar en el primer acierto. Hasta ahora eso corría en el hilo del bucle de
eventos: mientras duraba, el servicio de autenticación no atendía ninguna otra
petición, de ninguna entidad. Unos pocos intentos con un código inventado
bastaban para dejarlo sin responder, sin acertar nada.
"""

import asyncio
import time
import uuid

import pytest

from app.services.totp_service import _casar_codigo


def _candidato(codigo_real: str):
    import os

    from app.core.recovery_codes import hash_recovery_code

    sal = os.urandom(16)
    return {"id": uuid.uuid4(), "code_salt": sal, "code_hash": hash_recovery_code(codigo_real, salt=sal)}


@pytest.mark.asyncio
async def test_el_bucle_de_eventos_sigue_atendiendo_durante_el_barrido():
    """Es la aserción que importa: no que el barrido sea rápido, sino que el
    resto del servicio siga vivo mientras dura."""
    candidatos = [_candidato(f"CODIGO-{i}") for i in range(10)]

    latidos = 0

    async def _latir():
        nonlocal latidos
        while True:
            await asyncio.sleep(0.01)
            latidos += 1

    tarea = asyncio.create_task(_latir())
    inicio = time.monotonic()
    resultado = await asyncio.to_thread(_casar_codigo, "NO-ES-NINGUNO", candidatos)
    duracion = time.monotonic() - inicio
    tarea.cancel()

    assert resultado is None
    assert duracion > 0.02, "el barrido fue demasiado rápido: no se está midiendo nada"
    assert latidos > 0, (
        "el bucle de eventos estuvo parado todo el barrido: el servicio no "
        "atendía a nadie mientras se comprobaba un código inventado"
    )


def test_no_corta_en_el_primer_acierto():
    """Cortar filtraría por tiempo cuántos códigos quedan vivos y si alguno casó."""
    candidatos = [_candidato("EL-BUENO")] + [_candidato(f"OTRO-{i}") for i in range(3)]

    assert _casar_codigo("EL-BUENO", candidatos) == candidatos[0]["id"]
    assert _casar_codigo("NINGUNO", candidatos) is None
