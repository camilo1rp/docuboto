# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Guardián por CENSO de `FORWARDED_RESPONSE_HEADERS` del api-gateway.

El fallo que este archivo cierra ya ocurrió **dos veces**, con cabeceras
distintas: `X-Total-Count`/`X-Truncated` primero, y
`X-Export-Count`/`X-Export-Excluded`/`X-Export-UUID` después. El patrón es
siempre el mismo, y lo que lo hace caro es que no se nota:

1. Un servicio emite una cabecera propia y documenta su contrato.
2. El gateway la descarta, porque nadie se acordó de la lista.
3. **Nada falla.** El endpoint responde 200 y el cuerpo es correcto; lo único
   que desaparece es el dato que la cabecera llevaba. La pantalla no da error:
   pinta un guion, o un total que colapsa a una página, o calla un aviso.

Un test por cabecera escrito a mano no cierra eso: exige acordarse, que es
justo lo que falló las dos veces. Por eso este guardián **censa los servicios**
y obliga a que cada cabecera propia esté DECIDIDA — o cruza, o se declara por
qué no. La lista de servicios se deriva del árbol: un servicio nuevo entra en
el censo el día que existe.

Vive en `shared/` y no en el gateway porque cruza los once servicios, y el
contenedor del gateway no tiene el código de los demás: `test-unit-shared` es
el único carril de CI que ve el repositorio entero.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[2]
_SERVICIOS = _RAIZ / "services"
_PROXY = _SERVICIOS / "api-gateway" / "app" / "core" / "proxy.py"

# Cabeceras que aparecen en un servicio y que, a propósito, NO deben viajar del
# upstream al cliente. Cada una con su motivo: sin motivo, esta lista degenera
# en el vertedero donde se aparcan los olvidos, que es lo contrario de un
# guardián.
NO_CRUZAN: dict[str, str] = {
    "x-internal-token": "credencial del sello inter-servicio (D-02); jamás hacia el cliente",
    "x-tenant-slug": "cabecera de PETICIÓN que el gateway inyecta hacia dentro",
    "x-user-id": "cabecera de PETICIÓN (identidad resuelta por el gateway)",
    "x-username": "cabecera de PETICIÓN (identidad resuelta por el gateway)",
    "x-user-roles": "cabecera de PETICIÓN (identidad resuelta por el gateway)",
    "x-canal": "cabecera de PETICIÓN (canal de radicación)",
    "x-forwarded-for": "cabecera de PETICIÓN (cadena de proxies)",
    "x-real-ip": "cabecera de PETICIÓN (cadena de proxies)",
    "x-orpycamcp-signature": "firma HMAC de webhook ENTRANTE; la emite el operador postal",
    "x-request-id": "la produce el propio gateway, no viene del upstream",
    "x-gateway-version": "la produce el propio gateway",
}

_NOMBRE_CABECERA = re.compile(r'"(X-[A-Za-z0-9-]+)"')


def _lista_del_gateway() -> set[str]:
    """`FORWARDED_RESPONSE_HEADERS` leída del fuente, no importada.

    Importar `app.core.proxy` arrastraría las dependencias del gateway a un
    carril que no las instala. El literal se lee con `ast`, que es exacto y no
    ejecuta nada.
    """
    arbol = ast.parse(_PROXY.read_text(encoding="utf-8"))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Assign):
            destinos = [t.id for t in nodo.targets if isinstance(t, ast.Name)]
            if "FORWARDED_RESPONSE_HEADERS" in destinos:
                return {
                    e.value
                    for e in nodo.value.elts  # type: ignore[attr-defined]
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                }
    raise AssertionError("FORWARDED_RESPONSE_HEADERS no encontrada en proxy.py")


def _archivos_de_servicios() -> list[Path]:
    """Todo `app/**/*.py` de cada servicio, menos el propio gateway."""
    encontrados: list[Path] = []
    for svc in sorted(_SERVICIOS.iterdir()):
        if not (svc / "app").is_dir() or svc.name == "api-gateway":
            continue
        encontrados.extend(sorted((svc / "app").rglob("*.py")))
    return encontrados


def test_el_censo_no_esta_vacio():
    """Control del propio guardián: un censo vacío aprobaría por vacuidad.

    Sin esto, mover el árbol o equivocar `_RAIZ` convierte este archivo en un
    test que pasa siempre — el modo de fallo más caro que puede tener un
    guardián, porque se lee como cobertura.
    """
    archivos = _archivos_de_servicios()
    servicios = {a.relative_to(_SERVICIOS).parts[0] for a in archivos}
    assert len(servicios) >= 8, f"censo sospechosamente corto: {sorted(servicios)}"
    assert len(archivos) >= 100, f"solo {len(archivos)} archivos censados"
    assert len(_lista_del_gateway()) >= 8, "la lista del gateway se leyó incompleta"


def test_toda_cabecera_propia_de_los_servicios_esta_decidida():
    """Cada `X-…` de un servicio: o cruza el gateway, o se declara aquí.

    No exige que cruce —hay cabeceras que no deben— sino que alguien lo haya
    decidido. Lo que vuelve imposible es el tercer olvido silencioso.
    """
    reenviadas = _lista_del_gateway()
    vistas: dict[str, set[str]] = {}
    for archivo in _archivos_de_servicios():
        texto = archivo.read_text(encoding="utf-8", errors="ignore")
        for bruto in _NOMBRE_CABECERA.findall(texto):
            vistas.setdefault(bruto.lower(), set()).add(str(archivo.relative_to(_RAIZ)))

    assert vistas, "el censo no encontró ninguna cabecera propia: el patrón está roto"

    huerfanas = {
        n: sorted(f)[:3] for n, f in vistas.items() if n not in reenviadas and n not in NO_CRUZAN
    }
    assert not huerfanas, (
        "Cabeceras propias sin decidir. Si viaja en una RESPUESTA, añádela a "
        "FORWARDED_RESPONSE_HEADERS en services/api-gateway/app/core/proxy.py; "
        "si no debe cruzar, decláralo en NO_CRUZAN con su motivo. Sin una de "
        "las dos, el gateway la descarta y nada falla: solo desaparece el dato. "
        f"{huerfanas}"
    )


def test_lo_declarado_como_que_no_cruza_no_cruza_de_verdad():
    """Gemelo: `NO_CRUZAN` y la lista del gateway no pueden solaparse.

    Si una cabecera figura en las dos, una de las dos afirmaciones es mentira y
    la lectura del código deja de valer para saber qué hace el gateway.
    """
    solapadas = sorted(set(NO_CRUZAN) & _lista_del_gateway())
    assert not solapadas, f"declaradas como que no cruzan, pero reenviadas: {solapadas}"


@pytest.mark.parametrize("cabecera", ["x-export-count", "x-export-excluded", "x-export-uuid"])
def test_los_contadores_del_paquete_interoperable_cruzan(cabecera: str):
    """Control positivo del hallazgo que abrió este archivo.

    El ZIP de E11 INT-01 viaja como cuerpo; cuántos radicados lleva y cuántos
    quedaron fuera por no-read-up (Ley 1712/2014 arts. 18-19) solo viajan en
    estas cabeceras. Con la lista sin ellas, `/admin/interoperabilidad` mostraba
    «— radicado(s) exportado(s)» sobre un paquete de seis, y el recorte por
    clearance no se anunciaba en ningún sitio.
    """
    assert cabecera in _lista_del_gateway()
