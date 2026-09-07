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

    # Token del gate servicio-a-servicio (D-02). Este servicio no lo tenía: era
    # el ÚNICO interno sin ningún gate, y aunque el gateway no lo enruta, dentro
    # de la red de contenedores cualquiera podía hacerle transcribir. Fail-closed
    # como sus cinco gemelos: sin token configurado NO se atiende, en vez de
    # quedar abierto por omisión.
    internal_service_token: str = ""

    # Tope del audio, en bytes. Sin él, `await file.read()` traía a memoria lo
    # que llegara: el servicio carga además un modelo de reconocimiento, así que
    # es el proceso con menos memoria libre del despliegue. Un minuto de dictado
    # ronda el medio mega; 25 MiB deja holgura de sobra para lo que la interfaz
    # envía y acota el resto.
    max_audio_bytes: int = 25 * 1024 * 1024

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
