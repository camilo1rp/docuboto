# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Producción arranca de imágenes inmutables por SHA, o no hay vuelta atrás (AREA-13-09).

`infra/docker-compose.prod.yml` tenía `build:` y ningún `image:`. Así, `docker
compose pull` ignoraba los doce servicios propios y `ORPYCA_TAG=<sha-anterior>
up -d` no volvía a ninguna versión: el procedimiento de vuelta atrás de
`docs/es/deployment.md` no hacía nada, aunque `build-images` ya publicaba
`$CI_REGISTRY_IMAGE/<svc>:$CI_COMMIT_SHA`.

Vigila: todo servicio propio del manifiesto de producción (los de `services/*/`
con Dockerfile, más `frontend`) lleva `image:` con `${ORPYCA_TAG:?...}`
obligatorio, sin `latest` ni tag fijo escrito a mano, y NO lleva `build:` (la
construcción local vive en `infra/docker-compose.build.yml`, que no se usa por
defecto).
"""

from __future__ import annotations

from tests._compose import COMPOSE_PROD, RAIZ, cargar, servicios, servicios_propios

_PROPIOS = [*servicios_propios(), "frontend"]


def _propios_del_manifiesto() -> dict[str, dict]:
    todos = servicios(COMPOSE_PROD)
    return {n: s for n, s in todos.items() if n in _PROPIOS}


def test_se_encontraron_los_servicios_propios() -> None:
    propios = _propios_del_manifiesto()
    assert len(propios) >= 11, f"solo {len(propios)} servicios propios en el manifiesto: guardián ciego"


def test_todo_servicio_propio_lleva_imagen_con_tag_obligatorio() -> None:
    mal: list[str] = []
    for nombre, svc in _propios_del_manifiesto().items():
        imagen = svc.get("image")
        if not isinstance(imagen, str):
            mal.append(f"{nombre}: sin `image:`")
        elif "${ORPYCA_TAG:?" not in imagen:
            mal.append(f"{nombre}: la imagen no exige `${{ORPYCA_TAG:?...}}`: {imagen}")
        elif ":latest" in imagen:
            mal.append(f"{nombre}: usa latest: {imagen}")
        elif not imagen.rstrip().endswith("}") or "/" + nombre + ":" not in imagen:
            mal.append(f"{nombre}: la imagen no es <registro>/{nombre}:<tag>: {imagen}")
    assert not mal, "Sin imagen inmutable no hay rollback:\n  " + "\n  ".join(mal)


def test_el_manifiesto_de_produccion_no_construye() -> None:
    con_build = [n for n, s in servicios(COMPOSE_PROD).items() if "build" in s]
    assert not con_build, (
        "Producción no construye en el servidor (`pull` ignoraría estos servicios y "
        "no habría vuelta atrás); la construcción local vive en "
        "infra/docker-compose.build.yml: " + ", ".join(con_build)
    )


def test_el_override_de_construccion_cubre_los_servicios_propios() -> None:
    """El override no puede quedarse atrás: un servicio nuevo sin bloque de
    construcción no se podría construir en un servidor sin registro."""
    build = cargar(RAIZ / "infra" / "docker-compose.build.yml")["services"]
    faltan = sorted(set(_propios_del_manifiesto()) - set(build))
    assert not faltan, f"sin bloque de build en infra/docker-compose.build.yml: {faltan}"
