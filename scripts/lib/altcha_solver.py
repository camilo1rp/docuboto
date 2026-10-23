# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Resuelve un reto ALTCHA (protocolo v1) — para scripts y pruebas de humo (C16, ADR-038).

Solo biblioteca estándar. Hace lo mismo que el widget `altcha@2.3.0` en el
navegador: busca por fuerza bruta el `number` tal que
`sha256(salt + number) == challenge`, hasta `maxnumber`, y arma el campo `altcha`
(`base64(JSON)`) que la ventanilla pública exige al radicar.

Como biblioteca::

    from scripts.lib.altcha_solver import resolver_reto, payload_altcha
    reto = json.load(urlopen(f"{GATEWAY}/api/v1/public/{slug}/ventanilla/reto"))
    campo = payload_altcha(reto)          # -> str, va en datos["altcha"]

Como comando (lee el reto de la entrada estándar, imprime el payload)::

    curl -s $GATEWAY/api/v1/public/demo/ventanilla/reto | python3 scripts/lib/altcha_solver.py

Un reto vale UNA vez y vence a los ~5 minutos: pida uno por radicación.
NINGÚN script del repositorio radica hoy por la puerta pública (`seed_demo.py` y
`seed_corpus.py` usan la API autenticada); este módulo existe para el que lo haga.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import time
from typing import Any


def resolver_reto(reto: dict[str, Any]) -> tuple[int, int]:
    """Devuelve `(number, took_ms)`. `ValueError` si no hay solución dentro de `maxnumber`."""
    if reto.get("algorithm") != "SHA-256":
        raise ValueError(f"algoritmo no soportado: {reto.get('algorithm')!r}")
    salt, challenge = reto["salt"], reto["challenge"]
    inicio = time.monotonic()
    for number in range(int(reto["maxnumber"]) + 1):
        if hashlib.sha256(f"{salt}{number}".encode()).hexdigest() == challenge:
            return number, int((time.monotonic() - inicio) * 1000)
    raise ValueError("el reto no tiene solución dentro de maxnumber")


def payload_altcha(reto: dict[str, Any]) -> str:
    """El campo `altcha` listo para el `datos` del POST: base64 de un JSON, como el widget."""
    number, took = resolver_reto(reto)
    return base64.b64encode(
        json.dumps(
            {
                "algorithm": reto["algorithm"],
                "challenge": reto["challenge"],
                "number": number,
                "salt": reto["salt"],
                "signature": reto["signature"],
                "took": took,
            }
        ).encode()
    ).decode()


if __name__ == "__main__":
    print(payload_altcha(json.load(sys.stdin)))
