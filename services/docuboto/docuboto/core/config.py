# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""One settings object. Each module still reads its own section.

The process has a single `settings` with a named block per domain package.
A module keeps using `from document.core.config import settings` — that object
IS this block. The facade is what the process lifespan reads.
"""

from __future__ import annotations

from archive.core.config import settings as archive
from auth.core.config import settings as auth
from document.core.config import settings as document
from knowledge.core.config import settings as knowledge
from notification.core.config import settings as notification
from signature.core.config import settings as signature
from storage.core.config import settings as storage
from tenant.core.config import settings as tenant
from workflow.core.config import settings as workflow


class Settings:
    """Sections are the module Settings singletons. One pool size for the process."""

    def __init__(self) -> None:
        self.auth = auth
        self.tenant = tenant
        self.document = document
        self.archive = archive
        self.storage = storage
        self.workflow = workflow
        self.notification = notification
        self.signature = signature
        self.knowledge = knowledge

    @property
    def database_url_asyncpg(self) -> str:
        return self.document.database_url_asyncpg

    @property
    def redis_url(self) -> str:
        return self.document.redis_url

    @property
    def db_pool_min_size(self) -> int:
        return 2

    @property
    def db_pool_max_size(self) -> int:
        return 10

    @property
    def db_command_timeout(self) -> float:
        return self.document.db_command_timeout

    @property
    def log_level(self) -> str:
        return self.document.log_level


settings = Settings()
