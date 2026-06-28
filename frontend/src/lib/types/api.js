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
// Borradores / Plantillas (document-service, T-12c)
// ---------------------------------------------------------------------------

/**
 * @typedef {object} BorradorResponse
 * @property {string}                           id
 * @property {'S'|'I'}                          doc_type
 * @property {string}                           subject
 * @property {string|null}                      dest_dept
 * @property {string|null}                      cuerpo
 * @property {string} [cuerpo_html]             cuerpo saneado server-side (T-12c)
 * @property {string|null}                      plantilla_id
 * @property {'borrador'|'aprobado'|'radicado'} estado
 * @property {string|null}                      radicado_id
 * @property {string|null} [_tracking_number]   N.° radicado (frontend-only; set after radicar)
 * @property {string|null}                      creado_por
 * @property {string}                           created_at
 * @property {string}                           updated_at
 */

/**
 * @typedef {object} PlantillaResponse
 * @property {string}      id
 * @property {string}      nombre
 * @property {string|null} tipo_documental
 * @property {string}      cuerpo
 * @property {string} [cuerpo_html]       cuerpo saneado server-side (T-12c)
 * @property {boolean}     activo
 * @property {string}      created_at
 */

// ---------------------------------------------------------------------------
// Envíos postales (document-service, T-12e)
// ---------------------------------------------------------------------------

/**
 * Un evento de trazabilidad del envío (elemento del historial de estados).
 *
 * @typedef {object} ShipmentEvent
 * @property {'registrado'|'en_transito'|'entregado'|'devuelto'|'fallido'} estado
 * @property {string|null} descripcion  — nota libre del operador
 * @property {string}      ts           — ISO 8601 UTC
 */

/**
 * Respuesta del gateway para un envío postal.
 * Los envíos se crean por radicado (POST /documents/{id}/envios) y se consultan
 * tanto por radicado (GET /documents/{id}/envios) como de forma transversal
 * (GET /envios?estado=&page=&size=) con paginación X-Total-Count.
 *
 * @typedef {object} ShipmentResponse
 * @property {string}                                         id
 * @property {string}                                         radicado_id
 * @property {'4-72'|'servientrega'|'otro'}                   operador
 * @property {string|null}                                    guia
 * @property {'registrado'|'en_transito'|'entregado'|'devuelto'|'fallido'} estado
 * @property {string|null}                                    destinatario
 * @property {string|null}                                    direccion
 * @property {string}                                         created_at
 * @property {string}                                         updated_at
 * @property {ShipmentEvent[]}                                eventos
 */

/**
 * Cuerpo para crear un nuevo envío.
 * `radicado_id` (= document_id) va en la ruta del endpoint, no en el body del gateway.
 * Se usa en el proxy frontend para saber a qué documento asociar el envío.
 *
 * @typedef {object} ShipmentCreate
 * @property {string}                          radicado_id    — UUID del documento de salida
 * @property {'4-72'|'servientrega'|'otro'}    operador
 * @property {string|null} [destinatario]
 * @property {string|null} [direccion]
 */

/**
 * Cuerpo para cambiar el estado de un envío.
 * Para registrar una devolución con causal, el campo `descripcion` lleva el prefijo
 * "Causal: <causal> — <nota>" (T-12e). La causal estructurada en catálogo es roadmap backend.
 *
 * @typedef {object} ShipmentStatusUpdate
 * @property {'registrado'|'en_transito'|'entregado'|'devuelto'|'fallido'} estado
 * @property {string|null} [descripcion]
 */

// ---------------------------------------------------------------------------
// Firmas electrónicas (signature-service, E06, T-12d, RF-UI-17)
// ---------------------------------------------------------------------------

/**
 * Ítem de la bandeja del firmante: una solicitud cuyo turno activo es del usuario.
 * Devuelto por GET /api/v1/signature/pending.
 *
 * @typedef {object} SignaturePendingItem
 * @property {string} solicitud_id    — UUID de la solicitud de firma
 * @property {string} objeto_tipo     — tipo del documento (ej. 'radicado'|'borrador'|'documento')
 * @property {string} objeto_id       — UUID del objeto a firmar
 * @property {string} titulo          — título descriptivo del documento
 * @property {number} orden           — posición del turno activo en la cadena
 * @property {string} created_at      — ISO 8601 UTC
 */

/**
 * Un turno individual dentro de la cadena de firmas.
 *
 * @typedef {object} SignatureTurn
 * @property {number}      orden         — posición en la cadena (1-based)
 * @property {string}      firmante_id   — UUID del firmante asignado
 * @property {'pendiente'|'firmado'|'rechazado'} estado
 * @property {string|null} firma_id      — UUID del registro de firma (si firmado)
 * @property {string|null} comentario    — nota o motivo de rechazo
 * @property {string|null} decidido_at   — ISO 8601 UTC (timestamp de firma/rechazo)
 */

/**
 * Respuesta completa del estado de una solicitud de firma (cadena).
 * Devuelto por GET /api/v1/signature/requests/{id}.
 *
 * @typedef {object} SignatureRequestResponse
 * @property {string}            solicitud_id  — UUID de la solicitud
 * @property {string}            objeto_tipo
 * @property {string}            objeto_id
 * @property {string}            titulo
 * @property {'pendiente'|'firmado'|'rechazado'} estado
 * @property {SignatureTurn[]}   items         — turnos en orden
 * @property {string}            created_at    — ISO 8601 UTC
 */

/**
 * Cuerpo para rechazar una solicitud de firma.
 *
 * @typedef {object} SignatureRejectBody
 * @property {string} motivo   — obligatorio, min_length 1
 */

/**
 * Cuerpo para firma en lote.
 *
 * @typedef {object} SignatureBatchBody
 * @property {string[]} solicitud_ids  — UUIDs de las solicitudes a firmar
 */

/**
 * Resultado individual de una operación de firma en lote.
 *
 * @typedef {object} SignatureBatchResult
 * @property {string}             solicitud_id
 * @property {'firmado'|'omitido'} resultado
 * @property {string|null}        [detalle]    razon si fue omitido
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
