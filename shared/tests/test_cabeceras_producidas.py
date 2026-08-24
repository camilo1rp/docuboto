# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Ninguna decisión de autorización depende de una cabecera que nadie emite.

**Este defecto ha aparecido dos veces en el repositorio.** La primera en
`mcp-server`, que resolvía permisos leyendo `X-User-Permissions` — sus propios
tests lo dejan escrito: «cabecera que nadie producía». La segunda al construir
las cuentas de correo de E19, donde `_es_admin()` volvió a leerla.

**Por qué se cuela.** El nombre es perfectamente plausible, el código parece
correcto y no falla: `request.headers.get("X-User-Permissions", "")` devuelve la
cadena vacía y la comprobación resuelve `False`. Fail-closed, que es la
polaridad segura — pero el efecto es que **el control nunca autoriza a nadie**.
No hay excepción, no hay registro, no hay 500. Solo una pantalla que sale vacía,
indistinguible de «no hay nada configurado». Un mecanismo que reporta
funcionamiento sin comprobar nada.

**Cómo se decide qué cabeceras son legítimas.** No hay lista escrita a mano: se
comprueba que alguien las PRODUZCA. Una cabecera vale si el gateway la fija
(`headers["X-…"] = …`) o si está en su lista de reenvío. Si nadie la escribe en
ningún sitio, leerla es leer el vacío. Escrito por FORMA, no por catálogo de
nombres prohibidos: un catálogo solo recuerda los casos que alguien ya sufrió.
"""

from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"

# Toda cabecera `X-*` que el código LEE de una petición.
_LEIDA = re.compile(r"""headers\s*\.\s*get\(\s*["'](X-[A-Za-z0-9-]+)["']""")

# Toda cabecera `X-*` que aparece como destino de una asignación o dentro de un
# literal de diccionario/lista — es decir, que alguien la produce o la declara
# reenviable.
_PRODUCIDA = re.compile(r"""["'](X-[A-Za-z0-9-]+)["']\s*(?::|\]\s*=|,|\))""")


def _ficheros() -> list[Path]:
    return sorted(
        p
        for p in SERVICIOS.glob("*/app/**/*.py")
        if "__pycache__" not in p.parts
    )


def _cabeceras_leidas() -> dict[str, list[str]]:
    """{cabecera: [sitios]} — solo las que se leen de una petición entrante."""
    encontradas: dict[str, list[str]] = {}
    for f in _ficheros():
        texto = f.read_text(encoding="utf-8")
        for n, linea in enumerate(texto.splitlines(), 1):
            for m in _LEIDA.finditer(linea):
                encontradas.setdefault(m.group(1), []).append(
                    f"{f.relative_to(RAIZ)}:{n}"
                )
    return encontradas


def _cabeceras_producidas() -> set[str]:
    """Cabeceras que alguien fija, reenvía o declara en cualquier servicio.

    **Las LECTURAS se borran del texto antes de buscar.** Sin eso el censo se
    encuentra a sí mismo: `headers.get("X-User-Permissions", "")` lleva una coma
    detrás del nombre, así que el patrón de «producida» casaba con la propia
    línea que se quería denunciar, y el guardián daba verde sobre el defecto
    exacto que existe para cazar. Lo destapó la verificación por mutación —y es
    la segunda vez que un censo de este repositorio se cuenta a sí mismo—.
    """
    producidas: set[str] = set()
    for f in _ficheros():
        texto = _LEIDA.sub("", f.read_text(encoding="utf-8"))
        for m in _PRODUCIDA.finditer(texto):
            producidas.add(m.group(1))
    return producidas


def test_el_censo_ve_el_codigo(): 
    """Anti-vacuidad: un glob roto daría verde sin haber mirado nada."""
    assert len(_ficheros()) > 200
    leidas = _cabeceras_leidas()
    # Las que sabemos que existen y se usan de verdad.
    assert "X-Tenant-Slug" in leidas
    assert "X-User-Id" in leidas


def test_ninguna_cabecera_leida_carece_de_productor() -> None:
    leidas = _cabeceras_leidas()
    producidas = _cabeceras_producidas()

    huerfanas = {
        cabecera: sitios
        for cabecera, sitios in leidas.items()
        if cabecera not in producidas
    }

    assert not huerfanas, (
        "Estas cabeceras se leen y nadie las emite, así que siempre valen la "
        "cadena vacía. Si gobiernan una decisión de autorización, el control "
        "nunca autoriza y la pantalla sale vacía sin ningún error: "
        f"{ {k: v[:2] for k, v in huerfanas.items()} }"
    )
