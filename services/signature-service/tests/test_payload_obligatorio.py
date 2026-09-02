# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Lo que se firma es el documento, no su nombre (E23 Inc.4, RF-PRO-09).

`payload_sha256` era opcional, y la propia migración lo documentaba: «si no se
provee, la firma se produce sobre `objeto_tipo:objeto_id`». Una firma sobre el
nombre del objeto **sigue siendo válida después de que el texto cambie** — el
fraude exacto que el circuito de aprobación existe para impedir.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.constants import (
    CADENA_OBJETO_TIPOS,
    CADENA_PAYLOAD_AUTORITATIVO,
    CADENA_PAYLOAD_OBLIGATORIO,
)
from app.schemas.cadena import SolicitudCreate

#: Tipos a los que se les exige la huella AL LLAMANTE: los que el servidor no
#: puede resolver por su cuenta. Para los autoritativos la pide al productor.
EXIGIBLE_AL_LLAMANTE = CADENA_PAYLOAD_OBLIGATORIO - CADENA_PAYLOAD_AUTORITATIVO

HUELLA = "a" * 64


def _crear(**kw):
    base = {
        "objeto_tipo": "borrador",
        "objeto_id": str(uuid.uuid4()),
        "firmantes": [uuid.uuid4()],
    }
    base.update(kw)
    return SolicitudCreate(**base)


class TestHuellaObligatoria:
    @pytest.mark.parametrize("tipo", sorted(EXIGIBLE_AL_LLAMANTE))
    def test_sin_huella_no_se_puede_crear_la_cadena(self, tipo: str) -> None:
        with pytest.raises(ValidationError) as exc:
            _crear(objeto_tipo=tipo)
        assert "payload_sha256" in str(exc.value)

    @pytest.mark.parametrize("tipo", sorted(CADENA_PAYLOAD_OBLIGATORIO))
    def test_con_huella_si(self, tipo: str) -> None:
        assert _crear(objeto_tipo=tipo, payload_sha256=HUELLA).payload_sha256 == HUELLA

    @pytest.mark.parametrize("tipo", sorted(CADENA_PAYLOAD_AUTORITATIVO))
    def test_al_tipo_autoritativo_no_se_le_pide_la_huella(self, tipo: str) -> None:
        """La huella la resuelve el servidor, así que exigirla era pedir basura.

        Estos tests fijaban antes lo contrario, y ese era el defecto: el
        validador reclamaba `payload_sha256` para `borrador` mientras el
        servicio lo IGNORA y pide la huella al productor. La única forma de
        satisfacer al validador era inventarse un valor destinado a la papelera
        —justo el gesto que la regla existe para impedir—, y el proxy de la
        pantalla, escrito a propósito sin dónde ponerlo, se llevaba un 422: el
        circuito no se podía abrir desde el navegador.

        La garantía no se afloja, se muda de sitio: sigue siendo imposible
        firmar el nombre del objeto, porque quien resuelve la huella es
        `_resolver_huella` contra document-service, y si no puede, deniega.
        """
        assert _crear(objeto_tipo=tipo).payload_sha256 is None

    def test_una_cadena_vacia_no_cuela_como_huella(self) -> None:
        """Quién rechaza esto, exactamente.

        Lo rechaza el **validador de formato** (64 hex), no la regla de
        obligatoriedad: comprobé por mutación que cambiar `not
        self.payload_sha256` por `is None` deja este test en verde. El `not` se
        queda por defensivo —si algún día el formato admitiera vacío, seguiría
        cubierto— pero la garantía real de este caso es la otra.

        Lo anoto porque escribir aquí «si la comprobación mirara solo `is None`,
        una huella vacía pasaría» habría sido falso, y un comentario falso ya ha
        mantenido cinco defectos vivos en este proyecto.
        """
        with pytest.raises(ValidationError):
            _crear(payload_sha256="")

    @pytest.mark.parametrize("tipo", sorted(CADENA_OBJETO_TIPOS - CADENA_PAYLOAD_OBLIGATORIO))
    def test_los_demas_tipos_siguen_admitiendo_None(self, tipo: str) -> None:
        """Con reserva declarada, no por descuido.

        `radicado` y `expediente` se firman hoy como conjunto y su índice tiene
        su propio camino sellado. Exigirles huella aquí rompería cadenas en
        curso sin cerrar ninguna fuga que no cierre ya su propio circuito.
        """
        assert _crear(objeto_tipo=tipo).payload_sha256 is None

    def test_la_huella_sigue_teniendo_que_ser_sha256(self) -> None:
        with pytest.raises(ValidationError):
            _crear(payload_sha256="A" * 64)  # mayúsculas
        with pytest.raises(ValidationError):
            _crear(payload_sha256="a" * 63)


def test_la_migracion_ya_no_documenta_el_fallback() -> None:
    """Un comentario obsoleto ha mantenido cinco defectos vivos en este proyecto.

    La 003 decía «si no se provee, la firma se produce sobre
    objeto_tipo:objeto_id». Con la huella obligatoria para borrador/documento
    eso describe un comportamiento que ya no existe para el caso que importa, y
    quien lo leyera creería que el fallback sigue siendo la norma.
    """
    sql = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "tenant"
        / "003_firma_cadena.sql"
    ).read_text(encoding="utf-8")
    assert "Si no se provee, la firma se produce" not in sql
    assert re.search(r"documento es OBLIGATORIO", sql), (
        "el comentario tiene que decir la regla NUEVA, no solo callar la vieja"
    )
    assert "resuelve el SERVIDOR contra document-service" in sql, (
        "y tiene que decir que la de `borrador` la resuelve el servidor: si solo "
        "dijera que es obligatoria, describiría el contrato que se acaba de retirar"
    )
