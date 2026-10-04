# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Umbral DETERMINISTA de la auditoría de dependencias (AREA-13-14).

`pip-audit` no da severidad; da vulnerabilidades y, cuando existe, la versión que
las corrige. La decisión de fallar del job `dependency-audit` no puede tomarla un
modelo de lenguaje (antes terminaba en `exit 0` si `claude -p` no escribía nada) ni
depender de que `pip-audit` haya llegado a la red (antes `|| true` convertía un
fallo de resolución en «0 paquetes con vulnerabilidades»). Aquí la regla es:

1. Por cada `requirements*.lock` esperado debe existir `audit-<id>.json` y ser un
   JSON con la forma de `pip-audit`. Falta uno o está corrupto → **fallo**
   (fail-closed): una auditoría que no se pudo hacer no es una auditoría limpia.
2. Cualquier dependencia con una vulnerabilidad que TIENE versión corregida
   (`fix_versions` no vacío) → **fallo**: hay un arreglo disponible y no se aplicó.
3. Vulnerabilidades sin corrección se listan pero no fallan (nadie puede actuar).

Uso: ``python scripts/audit_umbral.py <directorio-con-audit-*.json> <lock>...``
Escribe `audit.json` (agregado) en el directorio y devuelve 0 / 1.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def identificador(lock: Path) -> str:
    """`services/auth-service/requirements.lock` -> `auth-service-requirements`."""
    return f"{lock.parent.name}-{lock.stem}"


def evaluar(directorio: Path, locks: list[Path]) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """(errores de auditoría, hallazgos con corrección, hallazgos sin corrección)."""
    errores: list[str] = []
    con_arreglo: list[dict[str, Any]] = []
    sin_arreglo: list[dict[str, Any]] = []
    if not locks:
        errores.append("no se indicó ningún lockfile: nada que auditar")
    for lock in locks:
        ident = identificador(lock)
        informe = directorio / f"audit-{ident}.json"
        if not informe.is_file():
            errores.append(f"{ident}: falta {informe.name} (pip-audit no llegó a producirlo)")
            continue
        try:
            datos = json.loads(informe.read_text(encoding="utf-8"))
            dependencias = datos["dependencies"]
            if not isinstance(dependencias, list):
                raise TypeError("`dependencies` no es una lista")
        except (ValueError, KeyError, TypeError) as exc:
            errores.append(f"{ident}: {informe.name} no es un informe de pip-audit válido ({exc})")
            continue
        for dep in dependencias:
            for vuln in dep.get("vulns") or []:
                fila = {
                    "lock": ident,
                    "paquete": dep.get("name"),
                    "version": dep.get("version"),
                    "id": vuln.get("id"),
                    "corregida_en": vuln.get("fix_versions") or [],
                }
                (con_arreglo if fila["corregida_en"] else sin_arreglo).append(fila)
    return errores, con_arreglo, sin_arreglo


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("uso: audit_umbral.py <directorio> <lock>...", file=sys.stderr)
        return 2
    directorio = Path(argv[1])
    locks = [Path(a) for a in argv[2:]]
    errores, con_arreglo, sin_arreglo = evaluar(directorio, locks)
    (directorio / "audit.json").write_text(
        json.dumps(
            {"errores": errores, "con_arreglo": con_arreglo, "sin_arreglo": sin_arreglo},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(
        f"{len(locks)} lockfiles; {len(errores)} errores de auditoría; "
        f"{len(con_arreglo)} vulnerabilidades con corrección disponible; "
        f"{len(sin_arreglo)} sin corrección"
    )
    for e in errores:
        print(f"ERROR  {e}")
    for f in con_arreglo:
        print(f"FALLO  {f['lock']}: {f['paquete']} {f['version']} {f['id']} -> {f['corregida_en']}")
    return 1 if errores or con_arreglo else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
