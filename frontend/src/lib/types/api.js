// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * api.js — Tipos JSDoc compartidos para las respuestas del api-gateway.
 *
 * Este módulo es SOLO tipos: no importa nada de runtime ni toca el gateway.
 * Los proxies server-side (+page.server.js, api/+server.js) consumen el
 * gateway directamente a través de config.server.js; este archivo solo
 * describe la forma de los datos que devuelven.
 *
 * Fuente de verdad: esquemas Pydantic del backend (document-service,
 * archive-service, report endpoints — todos vía api-gateway, ADR-011).
 */

// ---------------------------------------------------------------------------
// Documentos / Radicados (document-service, E01)
// ---------------------------------------------------------------------------

/**
 * @typedef {object} BandejaParams
 * @property {string}   [token]
 * @property {string}   [tenant]
 * @property {string}   [docType]   - 'E'|'S'|'I'
 * @property {string}   [status]    - 'registered'|'distributed'|'in_progress'|'closed'
 * @property {number}   [page]
 * @property {number}   [size]
 * @property {Function} [fetchFn]   - fetch de SvelteKit (SSR) o undefined (cliente)
 */

/**
 * @typedef {object} AnexoResponse
 * @property {string}      id
 * @property {string}      file_id
 * @property {string}      filename
 * @property {number|null} file_size
 * @property {string|null} mime_type
 * @property {string}      created_at
 * @property {string|null} tipo_anexo
 * @property {boolean}     es_principal
 * @property {number|null} folios
 */

/**
 * @typedef {object} DocumentResponse
 * @property {string}              id
 * @property {string}              tracking_number     — ej. '2024-ICETEX-E-001234'
 * @property {string}              doc_type            — 'E'|'S'|'I'
 * @property {number}              year
 * @property {number}              sequence
 * @property {string}              subject
 * @property {string|null}         doc_class
 * @property {string|null}         origin_dept
 * @property {string|null}         dest_dept
 * @property {number|null}         origin_dept_code
 * @property {number|null}         dest_dept_code
 * @property {string|null}         sender_name
 * @property {string|null}         sender_entity
 * @property {string}              status              — 'registered'|'distributed'|'in_progress'|'closed'
 * @property {number}              pages
 * @property {string|null}         observations
 * @property {string} [observations_html] cuerpo S/I saneado server-side (T-12b)
 * @property {string|null}         registered_by       — UUID
 * @property {string}              registered_at       — ISO 8601
 * @property {string}              updated_at          — ISO 8601
 * @property {string|null}         due_date            — ISO date (RF-RAD-04)
 * @property {Record<string,any>}  metadata
 * @property {AnexoResponse[]}     anexos
 */

/**
 * @typedef {object} DocumentUpdate
 * @property {string|null} [subject]
 * @property {string|null} [dest_dept]
 * @property {string|null} [observations]
 * @property {'registered'|'distributed'|'in_progress'|'closed'|null} [status]
 * @property {number|null} [pages]
 */

/**
 * @typedef {object} AnexoInput
 * @property {string}      file_id         — UUID del archivo subido al storage
 * @property {string}      filename
 * @property {number|null} [file_size]
 * @property {string|null} [mime_type]
 * @property {string|null} [tipo_anexo]
 * @property {boolean}     [es_principal]
 * @property {number|null} [folios]
 */

/**
 * @typedef {object} DocumentCreate
 * @property {'E'|'S'|'I'}  doc_type
 * @property {string}        subject
 * @property {string|null}   [doc_class]
 * @property {string|null}   [origin_dept]
 * @property {string|null}   [dest_dept]
 * @property {number|null}   [origin_dept_code]
 * @property {number|null}   [dest_dept_code]
 * @property {string|null}   [sender_name]
 * @property {string|null}   [sender_entity]
 * @property {number}        [pages]
 * @property {string|null}   [observations]
 * @property {1|2|3}         [nivel_seguridad]
 * @property {number|null}   [response_days]
 * @property {object}        [metadata]
 * @property {AnexoInput[]}  [anexos]
 */

// ---------------------------------------------------------------------------
// Reportes / estadísticas (reports endpoint, E09)
// ---------------------------------------------------------------------------

/**
 * @typedef {object} MonthCount
 * @property {string} month  — 'YYYY-MM'
 * @property {number} count
 */

/**
 * @typedef {object} ReportSummary
 * @property {number}                total
 * @property {Record<string,number>} by_doc_type   — { E: n, S: n, I: n }
 * @property {Record<string,number>} by_status      — { registered: n, ... }
 * @property {MonthCount[]}          by_month
 * @property {Record<string,number>} by_dependencia — { nombre_dep: n, ... }
 */

export {};
