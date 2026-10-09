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
#
# `documento` SALIÓ de este conjunto (2026-09-12). Estaba aquí sin productor:
# ningún servicio crea, guarda ni resuelve un objeto de ese tipo, así que una
# cadena sobre él se podía CREAR y no se podía firmar NUNCA — el gate de
# vigencia lo buscaba como borrador, no lo encontraba, y devolvía
# `409 documento_desaparecido` diciendo que era «un borrador que ya no existe»
# sobre algo que jamás lo fue. Comprobado contra el sistema levantado: 201 al
# crear, 409 al firmar, sin salida. Además su `payload_sha256` lo declaraba el
# llamante y no había contra qué contrastarlo, que es exactamente lo que este
# subsistema existe para impedir. El criterio de admisión ya estaba escrito
# abajo para `radicado` y `expediente` —«cuando cada uno tenga un artefacto
# canónico que firmar, entra aquí»— y `documento` no lo cumplía. Vuelve el día
# que exista, con su productor y su fuente de huella, y el guardián
# `test_todo_tipo_de_cadena_se_puede_firmar` lo exigirá.
CADENA_OBJETO_TIPOS = frozenset({"radicado", "expediente", "borrador"})

# Tipos para los que `payload_sha256` es OBLIGATORIO (E23, RF-PRO-09).
#
# Sin él, la firma se producía sobre la cadena `"{objeto_tipo}:{objeto_id}"` —el
# NOMBRE del objeto, no su contenido—, y una firma sobre el nombre **sigue
# siendo válida después de que el texto cambie**. Es justo el fraude que el
# circuito de aprobación existe para impedir: varios dan visto bueno, alguien
# edita, y la firma anterior sigue en pie.
#
# `borrador` es el que produce un fichero con huella propia, así que para él no
# hay excusa. `radicado` y `expediente` estuvieron fuera «a propósito y con
# reserva» —se firmaba el identificador— hasta AREA-04-09 Media-2 (2026-10-04):
# ya tienen un artefacto canónico que firmar y quién lo resuelva en el servidor
# (PDF vigente del radicado en document-service; índice electrónico vigente del
# expediente en archive-service), así que entran aquí. Las cadenas abiertas ANTES
# de ese cambio (huella NULL) no se firman a ciegas: `409 cadena_sin_huella`.
#
# Exigir huella y no tener quién la resuelva es la contradicción que dejó a
# `documento` sin poder firmarse: las dos mitades del trato viven ahora atadas
# —ver `RESOLUTOR_DE_HUELLA` en `cadena_service`— y un guardián las cruza.
CADENA_PAYLOAD_OBLIGATORIO = frozenset({"borrador", "radicado", "expediente"})

# Tipos de cadena que se firman COMO CONJUNTO, sin huella de contenido, y que
# por tanto no necesitan quién se la resuelva. Es la otra mitad de la partición:
# todo tipo admitido en `CADENA_OBJETO_TIPOS` está o aquí o en
# `CADENA_PAYLOAD_OBLIGATORIO`, nunca en ninguno y nunca en los dos.
#
# Se escribe por ADMISIÓN y no por omisión a propósito: `documento` vivió en el
# allow-set exigiendo huella y sin nadie que pudiera resolverla, y nada en el
# código ataba las dos cosas, así que la contradicción no la delataba ningún
# test. Añadir un tipo nuevo obliga ahora a decir a cuál de los dos lados
# pertenece, o el guardián se cae.
CADENA_SIN_HUELLA_DECLARADA: frozenset[str] = frozenset()

# Tipos cuya huella tiene FUENTE AUTORITATIVA fuera de este servicio: el
# `payload_sha256` del body se IGNORA y se resuelve contra el productor.
#
# Es el gemelo exacto de `CADENA_NIVEL_AUTORITATIVO` de más abajo, y por el
# mismo motivo: **una huella que el llamante elige no vincula a nadie**. Si el
# cliente pudiera declararla, bastaría con enviar la del documento anterior para
# que la cadena siguiera pareciendo válida después de editar el texto — el
# fraude que todo esto existe para impedir, entrando por la puerta principal.
#
# `documento` NO está aquí: es un tipo genérico sin productor conocido, así que
# la huella que declara su autor es la única verdad disponible. Por eso el
# schema la sigue exigiendo — para ese caso es la única defensa.
CADENA_PAYLOAD_AUTORITATIVO = frozenset({"borrador", "radicado", "expediente"})

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
# Estado de un radicado ANULADO, tal como lo escribe document-service
# (`UPDATE radicados SET status='anulado'`). Un documento sin efecto no recoge
# firmas: ni se le abre una cadena ni se resuelve un turno sobre él.
#
# El valor se escribe aquí, una vez y con su procedencia anotada, porque el
# mismo control en el frontend se escribió contra `"cancelled"` —que es
# vocabulario de PASOS DE FLUJO, no de radicados— y quedó inerte sin que nada
# lo dijera: la guarda no excluía nada y su test pasaba en verde usando el mismo
# valor equivocado.
RADICADO_ESTADO_ANULADO = "anulado"

CADENA_NIVEL_AUTORITATIVO: dict[str, tuple[str, tuple[str, ...]]] = {
    "radicado": ("radicados", ("tracking_number", "id::text")),
    "expediente": ("expedientes", ("code", "id::text")),
}

# `firma.nivel_conformidad` que declaran una firma ACREDITADA (ONAC). Lista
# cerrada por admisión: `/verify` y `GET /firmas/{id}` reportan `acreditado=True`
# solo para estos valores (y, en `/verify`, solo si la criptografía valida). Un
# nivel nuevo no hereda la acreditación por parecerse a uno que la tiene.
NIVELES_CONFORMIDAD_ACREDITADOS = frozenset(
    {"xades_b_acreditado", "xades_lt_acreditado", "xades_lta_acreditado"}
)
