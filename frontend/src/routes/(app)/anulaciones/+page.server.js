// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * anulaciones/+page.server.js — Carga SSR de la cola de anulaciones (Fase 0).
 *
 * HUECO DE BACKEND DECLARADO: document-service expone el estado de anulación
 * POR RADICADO (`GET /api/v1/documents/{id}/anulacion`) pero NO existe ningún
 * endpoint que LISTE las anulaciones pendientes del tenant — ni un filtro en
 * `GET /api/v1/documents` (su `status` es el ciclo de vida del radicado:
 * registered/distributed/in_progress/closed, no el estado de anulación), ni
 * un campo en `DocumentResponse` que delate una anulación en trámite. Se
 * investigaron `app/routers/documents.py` y `app/schemas/document.py` en
 * document-service antes de escribir esta pantalla — confirmado que no hay
 * forma de construir la cola server-side sin tocar el backend (fuera de
 * alcance de esta tarea, ver `frontend/CLAUDE.md`).
 *
 * Degradación honesta (documentada, no disimulada): esta pantalla NO carga
 * una lista al entrar. En su lugar:
 *   1. Reutiliza `/busqueda/api` (GET /search, ya existente) como localizador
 *      de radicados por texto/número de tracking — el aprobador escribe y
 *      ve candidatos con id + tracking_number.
 *   2. Para cada candidato consulta el estado real vía
 *      `/anulaciones/api/[id]` (GET /documents/{id}/anulacion).
 *   3. Decide (aprobar/rechazar, motivo obligatorio) vía
 *      `/anulaciones/api/[id]/aprobar` | `/rechazar`.
 *
 * Sin listado servidor, no hay datos que precargar aquí — la búsqueda es
 * 100% client-driven (fetch a los proxies anteriores, que sí llevan el
 * access_token de `locals` en el servidor, D-05). Este archivo existe para
 * mantener la simetría SSR del resto de `(app)/**` y como punto de futura
 * carga si el backend llega a exponer un listado real.
 */

export async function load() {
  return {};
}
