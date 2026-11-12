# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Guardianes de `.github/workflows/` sobre la imagen, el escáner y la batería WORM (AREA-08-12, AREA-13-01, AREA-13-13).

* **RustFS joven** (AREA-08-12): su compatibilidad on-disk con MinIO está en
  preview. Cambiar el tag/digest de `image: rustfs/rustfs` en cualquier compose sin
  correr la batería WORM contra S3 real es el riesgo. El trabajo `storage-worm-real`
  de `ci.yml` debe correr cuando cambian los compose (lo decide `compose-changed`),
  en cada tag, usar la MISMA imagen que producción y exportar `RUN_S3_WORM=1`.
* **Escaneo de imágenes** (AREA-13-13): `images` de `release.yml` escanea con trivy
  (CRITICAL, con corrección disponible, exit-code 1) ANTES de cada push.
* **Sin `docker scan`** (retirado del CLI) en la documentación de despliegue.
"""

from __future__ import annotations

import re

from tests._compose import (
    CI_WORKFLOW,
    COMPOSE_DEV,
    COMPOSE_PROD,
    RAIZ,
    RELEASE_WORKFLOW,
    guion,
    job,
    servicios,
)


def _job() -> dict:
    return job(CI_WORKFLOW, "storage-worm-real")


def _imagen_rustfs(ruta) -> str:
    imgs = [s["image"] for s in servicios(ruta).values() if "rustfs/rustfs" in str(s.get("image", ""))]
    assert imgs, f"{ruta.name} no declara rustfs/rustfs"
    return imgs[0]


def test_el_job_worm_corre_cuando_cambian_los_compose() -> None:
    script = guion(job(CI_WORKFLOW, "compose-changed"))
    assert "'docker-compose*.yml'" in script, "compose-changed no mira docker-compose*.yml"
    assert "infra/docker-compose.prod.yml" in script, "compose-changed no mira el compose de producción"
    assert _job()["needs"] == "compose-changed"
    assert "needs.compose-changed.outputs.changed == 'true'" in _job()["if"]


def test_el_job_worm_activa_la_bateria_real() -> None:
    v = _job()["env"]
    assert v["RUN_S3_WORM"] == "1" and v["RUN_INTEGRATION"] == "1"
    assert "test_worm_object_lock_real_s3.py" in guion(_job())
    assert "startsWith(github.ref, 'refs/tags/')" in _job()["if"], "en un tag, la batería no puede ser opcional"


def test_el_job_worm_prueba_la_imagen_que_se_despliega() -> None:
    en_ci = _job()["services"]["rustfs"]["image"]
    assert en_ci == _imagen_rustfs(COMPOSE_PROD), (
        "storage-worm-real debe usar la imagen EXACTA (con digest) de producción: "
        f"CI={en_ci!r}, prod={_imagen_rustfs(COMPOSE_PROD)!r}"
    )


def test_el_tag_de_dev_y_prod_coinciden_en_version() -> None:
    dev = _imagen_rustfs(COMPOSE_DEV).split("@")[0]
    prod = _imagen_rustfs(COMPOSE_PROD).split("@")[0]
    assert dev == prod, f"dev prueba {dev} y prod despliega {prod}"


def test_las_imagenes_se_escanean_antes_de_publicarse() -> None:
    script = guion(job(RELEASE_WORKFLOW, "images"))
    assert "trivy image" in script
    assert "--exit-code 1" in script and "--severity CRITICAL" in script and "--ignore-unfixed" in script
    # El escaneo va antes del push de CADA imagen, dentro del bucle.
    pos_escaneo = script.index('escanear "$imagen"')
    pos_push = script.index('docker push "$imagen"')
    assert pos_escaneo < pos_push, "la imagen debe escanearse ANTES de subirse"


def test_la_documentacion_no_recomienda_docker_scan() -> None:
    texto = (RAIZ / "docs" / "es" / "deployment.md").read_text(encoding="utf-8")
    comandos = [l for l in texto.splitlines() if re.match(r"\s*docker scan\b", l)]
    assert not comandos, f"`docker scan` está retirado del CLI de Docker: {comandos}"
