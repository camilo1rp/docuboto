#!/usr/bin/env python3
# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Carga de la búsqueda contra el SLA de RF-BUS-09 (AREA-09-17).

RF-BUS-09 (R.13.31) exige que la búsqueda simple responda en **menos de 3 s** y la
compleja (cuatro términos) en **menos de 10 s**, y una *suite de pruebas de
rendimiento que lo verifique*. Hasta ahora no había ninguna: el SLA era una cifra
en un documento.

Este guion **no es parte del gate unitario**: corre contra la pila VIVA (sembrada
con `scripts/seed_corpus.py` para que haya volumen), pide un token por usuario con
`POST /api/v1/auth/token` y mide el **percentil 95** de `GET /api/v1/search` por
escenario y por persona. Se mide con más de una persona a propósito: el recorte por
clearance (RF-SEG-08) va en el `WHERE`, así que quien alcanza el nivel 3 y quien
alcanza el 1 ejecutan consultas distintas.

Solo usa la biblioteca estándar (no instala nada en la máquina de quien lo corra).

Uso:
    python3 scripts/carga/busqueda_sla.py --gateway http://localhost:19080 \\
        --tenant demo --users operador,jefe,archivista --requests 40 --concurrency 4

Sale con 1 si algún p95 supera su umbral, con 2 si no pudo medir (sin token o sin
respuestas): un SLA que no se pudo medir **no** es un SLA cumplido.

Límite honesto: contra un corpus de cientos de filas el índice FTS (ADR-014) no se
distingue de un barrido secuencial; el guion prueba el contrato, y solo con un
corpus de decenas de miles de radicados prueba que el índice trabaja.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial

#: Umbrales de RF-BUS-09 (R.13.31), en segundos.
UMBRAL_SIMPLE_S = 3.0
UMBRAL_COMPLEJA_S = 10.0

#: Escenarios: nombre → (umbral, parámetros de la consulta).
ESCENARIOS: dict[str, tuple[float, dict[str, str]]] = {
    "simple": (UMBRAL_SIMPLE_S, {"q": "solicitud"}),
    "compleja": (
        UMBRAL_COMPLEJA_S,
        {
            "q": "solicitud información certificación contrato",
            "doc_type": "E",
            "date_from": "2020-01-01",
            "date_to": "2030-12-31",
        },
    ),
}


def percentil(muestras: list[float], p: float) -> float:
    """Percentil `p` (0-100) por el método del rango más cercano.

    Con rango más cercano el p95 de 20 muestras es la 19.ª, no una interpolación que
    pueda quedar por DEBAJO de un valor realmente observado: para un SLA se prefiere
    el estimador que no mejora la cifra.
    """
    if not muestras:
        raise ValueError("sin muestras no hay percentil")
    ordenadas = sorted(muestras)
    rango = max(1, math.ceil(p / 100 * len(ordenadas)))
    return ordenadas[rango - 1]


@dataclass(frozen=True)
class Resultado:
    escenario: str
    usuario: str
    muestras: int
    errores: int
    p50: float
    p95: float
    umbral: float

    @property
    def cumple(self) -> bool:
        # Un escenario sin una sola respuesta correcta no cumple nada.
        return self.muestras > 0 and self.errores == 0 and self.p95 < self.umbral


def veredicto(resultados: list[Resultado]) -> int:
    """0 si todo cumple, 1 si algún p95 supera el umbral, 2 si no se pudo medir."""
    if not resultados or any(r.muestras == 0 for r in resultados):
        return 2
    return 0 if all(r.cumple for r in resultados) else 1


def _token(gateway: str, tenant: str, usuario: str, password: str) -> str:
    cuerpo = json.dumps({"username": usuario, "password": password, "tenant_slug": tenant}).encode()
    req = urllib.request.Request(
        f"{gateway}/api/v1/auth/token", data=cuerpo, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 - URL del operador
        return str(json.load(r)["access_token"])


def _una_peticion(gateway: str, tenant: str, token: str, params: dict[str, str]) -> tuple[bool, float]:
    url = f"{gateway}/api/v1/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "X-Tenant-Slug": tenant})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            r.read()
            ok = r.status == 200
    except (urllib.error.URLError, TimeoutError):
        ok = False
    return ok, time.perf_counter() - t0


def medir(
    gateway: str, tenant: str, usuarios: list[str], password: str, peticiones: int, concurrencia: int
) -> list[Resultado]:
    out: list[Resultado] = []
    for usuario in usuarios:
        try:
            token = _token(gateway, tenant, usuario, password)
        except (urllib.error.URLError, KeyError, ValueError) as exc:
            print(f"  [{usuario}] sin token: {exc}", file=sys.stderr)
            continue
        for nombre, (umbral, params) in ESCENARIOS.items():
            una = partial(_una_peticion, gateway, tenant, token, params)
            with ThreadPoolExecutor(max_workers=concurrencia) as pool:
                medidas = list(pool.map(lambda _, f=una: f(), range(peticiones)))
            buenas = [t for ok, t in medidas if ok]
            errores = len(medidas) - len(buenas)
            out.append(
                Resultado(
                    escenario=nombre,
                    usuario=usuario,
                    muestras=len(buenas),
                    errores=errores,
                    p50=percentil(buenas, 50) if buenas else float("nan"),
                    p95=percentil(buenas, 95) if buenas else float("nan"),
                    umbral=umbral,
                )
            )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gateway", default="http://localhost:19080")
    ap.add_argument("--tenant", default="demo")
    ap.add_argument("--users", default="operador,jefe,archivista")
    ap.add_argument("--password", default="orpycamcp_dev", help="solo la de desarrollo")
    ap.add_argument("--requests", type=int, default=40, help="peticiones por escenario y usuario")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    resultados = medir(
        args.gateway.rstrip("/"),
        args.tenant,
        [u for u in args.users.split(",") if u],
        args.password,
        args.requests,
        args.concurrency,
    )
    print(f"{'escenario':<10} {'usuario':<12} {'n':>4} {'err':>4} {'p50 s':>8} {'p95 s':>8} {'umbral':>7}  veredicto")
    for r in resultados:
        print(
            f"{r.escenario:<10} {r.usuario:<12} {r.muestras:>4} {r.errores:>4} "
            f"{r.p50:>8.3f} {r.p95:>8.3f} {r.umbral:>7.1f}  {'CUMPLE' if r.cumple else 'NO CUMPLE'}"
        )
    return veredicto(resultados)


if __name__ == "__main__":
    sys.exit(main())
