#!/usr/bin/env python3
# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
veraPDF CLI FALSO -- ejercita el plumbing REAL de `SubprocessVeraPdfValidator`
(E10 Increment C2b) en CI, sin depender de una JVM/veraPDF real.

Se invoca EXACTAMENTE como espera `subprocess_verapdf.py`::

    fake_verapdf.py --format json --flavour <profile> <input.pdf>

y jamás recibe el entorno del proceso padre (el validador lo scrubbea
deliberadamente a solo PATH+JAVA_TOOL_OPTIONS) -- por eso el "modo" de esta
falsificación NO se controla por variable de entorno, sino por un MARCADOR
dentro del contenido del PDF de entrada. Eso hace que el test controle el
comportamiento simplemente eligiendo los bytes que sube, ejercitando el
mismo camino de datos que un despliegue real recorrería.

Modos soportados (marcador en el contenido del archivo, en cualquier lugar):
  FAKE-VERAPDF-NONCONFORMANT  -> responde `compliant: false`
  FAKE-VERAPDF-HANG           -> nunca termina (prueba timeout + killpg)
  FAKE-VERAPDF-HUGE           -> emite una salida mayor a cualquier límite
                                  razonable (prueba el cap de tamaño de salida)
  FAKE-VERAPDF-BADJSON        -> emite texto que no es JSON válido
  FAKE-VERAPDF-PARSEFAIL      -> simula un PDF corrupto/no parseable
                                  (`taskException` en vez de `validationResult`)
  (sin marcador)               -> responde `compliant: true`

Formato de salida: el reporte JSON REAL de veraPDF (confirmado contra un
binario veraPDF 1.30.2 de verdad, no solo su documentación --
`report.buildInformation.releaseDetails` es una LISTA de componentes y
`report.jobs[N].validationResult` es una LISTA de UN elemento, no un objeto
directo), la misma forma que `subprocess_verapdf._parse_verapdf_json` espera.
"""

from __future__ import annotations

import json
import sys
import time

_HANG_SECONDS = 3600  # muy por encima de cualquier VERAPDF_TIMEOUT_SECONDS de test
_HUGE_OUTPUT_BYTES = 10 * 1024 * 1024  # por encima de _MAX_OUTPUT_BYTES (5 MiB)


def main(argv: list[str]) -> int:
    if "--format" not in argv or "json" not in argv:
        sys.stderr.write("fake-verapdf: solo soporta --format json\n")
        return 2

    input_path = argv[-1]
    try:
        with open(input_path, "rb") as fh:
            content = fh.read()
    except OSError as exc:
        sys.stderr.write(f"fake-verapdf: no se pudo leer {input_path}: {exc}\n")
        return 2

    if b"FAKE-VERAPDF-HANG" in content:
        time.sleep(_HANG_SECONDS)
        return 0

    if b"FAKE-VERAPDF-HUGE" in content:
        sys.stdout.write("x" * _HUGE_OUTPUT_BYTES)
        sys.stdout.write("\n")
        return 0

    if b"FAKE-VERAPDF-BADJSON" in content:
        sys.stdout.write("esto no es un JSON valido\n")
        return 0

    if b"FAKE-VERAPDF-PARSEFAIL" in content:
        report = {
            "report": {
                "buildInformation": {
                    "releaseDetails": [{"id": "core", "version": "fake-verapdf-1.0.0"}],
                },
                "jobs": [
                    {
                        "itemDetails": {"name": input_path, "size": len(content)},
                        "taskException": {
                            "exception": "Couldn't parse stream",
                            "type": "PARSE",
                            "exceptionMessage": "simulado por fake-verapdf: PDF no parseable",
                        },
                    }
                ],
            }
        }
        sys.stdout.write(json.dumps(report))
        sys.stdout.write("\n")
        return 7

    compliant = b"FAKE-VERAPDF-NONCONFORMANT" not in content

    profile = "2b"
    if "--flavour" in argv:
        profile = argv[argv.index("--flavour") + 1]

    statement = (
        "PDF file is compliant with Validation Profile requirements."
        if compliant
        else "PDF file is not compliant with Validation Profile requirements."
    )
    # Forma REAL confirmada contra veraPDF 1.30.2 (no solo su documentación,
    # que la simplifica): `buildInformation.releaseDetails` es una LISTA de
    # componentes, y `jobs[N].validationResult` es una LISTA de UN elemento
    # -- ambos detalles que `_parse_verapdf_json` maneja explícitamente.
    report = {
        "report": {
            "buildInformation": {
                "releaseDetails": [{"id": "core", "version": "fake-verapdf-1.0.0"}],
            },
            "jobs": [
                {
                    "itemDetails": {"name": input_path},
                    "validationResult": [
                        {
                            "profileName": f"PDF/A-{profile} validation profile",
                            "compliant": compliant,
                            "statement": statement,
                        }
                    ],
                }
            ],
        }
    }
    sys.stdout.write(json.dumps(report))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
