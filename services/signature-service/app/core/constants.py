# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Service-identifying constants shared across modules that must NOT import
each other directly (avoids a circular import between the worker and the job).

``DLQ_GROUP`` is the single source of truth for this service's Redis consumer
group name, which doubles as the ``origin_group`` value written to (and
filtered on) the shared ``evento_dead_letter`` table — see the module-level
note in ``app/repositories/dead_letter_repository.py``. Every reader of
``evento_dead_letter`` (the admin router, the retention job, and the worker's
``_persist_dead_letter``) MUST source the value from here rather than
hardcoding the literal, so the three call sites can never drift apart.

This lives in its own module (not in ``app/workers/document_consumer.py``,
where it used to be defined) because ``document_consumer`` imports
``app.jobs.deadletter_retention`` at module scope, and the retention job needs
this same constant: importing it back from ``document_consumer`` would create
an import cycle (and ``GROUP`` would not exist yet at that point in
``document_consumer``'s own module initialisation).
"""

from __future__ import annotations

import uuid

DLQ_GROUP = "signature-service"

# Actor atribuido a las firmas del índice producidas por el job de
# reconciliación de archive-service (`app/jobs/indice_reconciliation.py`) vía
# la ruta interna `POST /internal/sign-indice` (residual Ac. AGN 001/2024 art.
# 4.3.2.4) — NUNCA un usuario humano; distingue en `audit_log`/`firma.firmante_id`
# un sellado diferido-por-el-sistema de uno manual (`/sign-xml`, X-User-Id real).
#
# MISMO valor literal que `archive-service/app/core/constants.py::SYSTEM_RECONCILER_ID`
# — ambos servicios deben coincidir byte a byte; no hay una fuente única
# compartida entre microservicios independientes (ADR-003, sin librería común
# de constantes de dominio). Cambiar este valor en un solo lado rompe la
# atribución sin que ningún tipo lo detecte — verificar ambos si se rota.
SYSTEM_RECONCILER_ID = uuid.UUID("00000000-0000-0000-0000-000000005ec1")

# Allow-set de `objeto_tipo` elegibles para el proveedor XAdES institucional
# (H-H, E06/F4 — firma del acta de transferencia): `signing_service.
# _select_provider_name` solo produce XAdES-B/T/LT/LTA real para los tipos de
# este conjunto (con `settings.signer_provider == "xades_local"`); cualquier otro
# `objeto_tipo` cae SIEMPRE al HMAC 'nativa', igual que antes de este
# incremento. También es el allow-set que valida `InternalSignIndiceRequest.
# objeto_tipo` (`app/schemas/firma.py`) — la ruta interna de reconciliación
# (`POST /internal/sign-indice`, gate `require_internal_token`) NUNCA puede
# sellar un tipo de objeto fuera de este conjunto, aunque el body JSON lo
# declare.
# `acta_eliminacion` (E04/RF-TRD-07 fase 4, 2026-08-29) entra aquí y no en
# otro sitio porque, sin estar en este conjunto, el acta caería al HMAC
# 'nativa' SIN ERROR y sin diferencia visible: quedaría marcada «firmada»
# siendo estructuralmente más débil que el acta de transferencia. Y es el
# documento que sobrevive a lo eliminado, cuando ya no queda nada con qué
# contrastarlo.
XADES_ELIGIBLE_OBJETO_TIPOS = frozenset(
    {"indice", "acta_transferencia", "acta_eliminacion"}
)

# Allow-set de `objeto_tipo` de una CADENA de firma personal (`POST
# /api/v1/signature/requests`, `SolicitudCreate`). Cerrarlo no es cosmética de
# validación: el nivel de seguridad de la cadena se resolvía comparando este
# campo —texto libre del body— contra el literal `"radicado"`, de modo que
# `"Radicado"` bastaba para saltarse la fuente autoritativa y DECLARAR el nivel
# desde el cliente (RF-SEG-08). Una llave de autorización que el llamante puede
# elegir no es un control; con el conjunto cerrado, la variante de mayúsculas ni
# siquiera se puede crear (422).
#
# El único cliente es la UI de firmas (`frontend/.../firmas`): ningún servicio
# interno crea cadenas — el sello institucional del índice y del acta va por
# `/sign-xml` e `/internal/sign-indice`, con su propio allow-set arriba.
CADENA_OBJETO_TIPOS = frozenset({"radicado", "expediente", "borrador", "documento"})

# Tipos para los que `payload_sha256` es OBLIGATORIO (E23, RF-PRO-09).
#
# Sin él, la firma se producía sobre la cadena `"{objeto_tipo}:{objeto_id}"` —el
# NOMBRE del objeto, no su contenido—, y una firma sobre el nombre **sigue
# siendo válida después de que el texto cambie**. Es justo el fraude que el
# circuito de aprobación existe para impedir: varios dan visto bueno, alguien
# edita, y la firma anterior sigue en pie.
#
# `borrador` y `documento` son los dos que producen un fichero con huella
# propia, así que para ellos no hay excusa. `radicado` y `expediente` se quedan
# fuera **a propósito y con reserva**: hoy se firman como conjunto (el índice
# electrónico del expediente tiene su propio camino sellado, `/sign-xml`), y
# exigirles una huella aquí rompería cadenas en curso sin cerrar ninguna fuga
# que no cierre ya su propio circuito. Cuando cada uno tenga un artefacto
# canónico que firmar, entra aquí.
CADENA_PAYLOAD_OBLIGATORIO = frozenset({"borrador", "documento"})

# Tipos de cadena cuyo nivel de seguridad tiene FUENTE AUTORITATIVA en el schema
# del tenant: `objeto_tipo -> (tabla, columnas de identidad)`. Para estos, el
# `nivel_seguridad` del body se IGNORA y, si el objeto no resuelve, la creación
# se rechaza (fail-closed) en vez de caer al valor declarado.
#
# `borrador`/`documento` no están aquí a propósito: no son documentos oficiales
# registrados, no hay tabla que consultar, y el nivel que declara su autor ES la
# única verdad disponible.
#
# Las columnas de identidad son dobles porque el llamante puede pasar el número
# de radicado / el código del expediente o el UUID (mismo motivo que en la
# consulta de auditoría de auth-service).
CADENA_NIVEL_AUTORITATIVO: dict[str, tuple[str, tuple[str, ...]]] = {
    "radicado": ("radicados", ("tracking_number", "id::text")),
    "expediente": ("expedientes", ("code", "id::text")),
}
