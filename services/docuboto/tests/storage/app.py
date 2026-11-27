# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La app de las pruebas de storage: los routers del dominio con el middleware de la app.

`docuboto.main.create_app` es la única definición de middleware y manejadores
(ADR-047); aquí sin lifespan, sin /health ni /metrics y sin cuota por persona.
"""

from docuboto.main import create_app
from storage.main import DOMAIN_ROUTERS

app = create_app(DOMAIN_ROUTERS, with_lifespan=False, with_ops=False, rate_limit=False)
