# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Configuración del whisper-service."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_port: int = 8012
    log_level: str = "info"

    # `small` es el equilibrio para castellano en CPU: `tiny`/`base` confunden
    # nombres propios y números de radicado —justo lo que se dicta aquí— y
    # `medium`/`large` multiplican el tiempo sin GPU. Se puede cambiar por
    # entorno sin tocar código.
    whisper_model: str = "small"
    # `int8` en CPU: la cuantización recorta memoria y tiempo con una pérdida
    # que no se nota en dictado corto.
    whisper_compute_type: str = "int8"
    whisper_device: str = "cpu"

    # Dónde vive el modelo descargado. Montado como volumen en Compose para que
    # no se baje de nuevo en cada arranque del contenedor.
    whisper_cache_dir: str = "/models"

    # El idioma por defecto de la instalación. Se puede pasar por petición.
    whisper_language: str = "es"


settings = Settings()
