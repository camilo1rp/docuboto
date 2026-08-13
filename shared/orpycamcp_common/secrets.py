# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Verificación de que los secretos del servicio vienen del entorno.

El problema que resuelve no es "falta validación": es que un secreto con
**default funcional** en un repositorio AGPL público es un secreto conocido. Un
despliegue que se olvide de definir ``SIGNATURE_SECRET`` no falla — arranca, y
firma con ``"change-me-in-production"``, que cualquiera puede leer en GitHub.
Ese es el peor modo de fallo posible: el sistema dice que todo está bien
mientras la propiedad criptográfica que sostiene ya no existe.

La corrección tiene dos mitades y **hacen falta las dos**:

1. El default en ``config.py`` pasa a cadena vacía — deja de ser utilizable.
2. El arranque comprueba y **se niega a servir** si falta (esta función).

Solo con (1), el servicio arrancaría con un secreto vacío y el fallo seguiría
siendo silencioso; solo con (2), un despliegue que herede el default publicado
pasaría la comprobación.

El desarrollo no se rompe: ``docker-compose.yml`` define valores explícitos de
desarrollo, y los tests fijan los suyos en ``conftest.py``. Lo que se rompe —a
propósito— es arrancar en producción sin haberlos definido.
"""

from __future__ import annotations

# Marcadores de posición inequívocos: se rechazan aunque vengan del entorno,
# porque un operador que copie el `.env.example` sin tocarlo no debe pasar la
# comprobación.
#
# NO se listan aquí las credenciales de desarrollo del proyecto (`orpycamcp` /
# `orpycamcp_dev`): el `docker-compose.yml` local las define a propósito y
# prohibirlas dejaría el entorno de desarrollo sin arrancar, que es un precio
# que esta comprobación no debe cobrar. Lo que impide heredarlas en producción
# es el manifiesto: `infra/docker-compose.prod.yml` las pasa con el guard
# `${VAR:?}`, así que el despliegue ni siquiera se resuelve sin definirlas.
VALORES_PROHIBIDOS = frozenset(
    {
        "change-me-in-production",
        "change-me",
        "changeme",
        "cambiar",
        "por-definir",
        "todo",
        "xxx",
    }
)


class SecretoNoConfigurado(RuntimeError):
    """El servicio no puede arrancar: falta un secreto o trae un valor de relleno."""


def require_secrets(secretos: dict[str, str], *, servicio: str) -> None:
    """Aborta el arranque si algún secreto está vacío o es un valor conocido.

    ``secretos`` mapea el **nombre de la variable de entorno** (no el del campo
    de ``Settings``) a su valor, para que el mensaje de error diga exactamente
    qué tiene que definir quien despliega.

    El mensaje nunca incluye el valor recibido: acabaría en los registros del
    contenedor, que es justo donde no debe estar un secreto — ni siquiera uno
    incorrecto, porque el operador suele equivocarse de entorno, no de cadena.
    """
    faltantes = [
        nombre
        for nombre, valor in secretos.items()
        if not (valor or "").strip() or (valor or "").strip().lower() in VALORES_PROHIBIDOS
    ]
    if faltantes:
        raise SecretoNoConfigurado(
            f"{servicio}: no arranca sin estas variables de entorno, "
            f"y no pueden traer el valor de ejemplo del repositorio: "
            f"{', '.join(sorted(faltantes))}"
        )
