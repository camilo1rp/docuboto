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
 * @property {number|null}          [nivel_seguridad]   nivel de seguridad: 1=Publica 2=Reservada 3=Clasificada (Ley 1712)
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
 * @property {ActorVincular[]} [actores]
 */

/**
 * Un actor del radicado POR REFERENCIA al catálogo de terceros (RF-RAD-05).
 *
 * Los campos de contacto son opcionales y **corrigen la ficha solo para ESTE
 * radicado**: se congelan en `radicado_actor` y no tocan el catálogo, porque
 * valen para este envío y no dicen nada sobre dónde vive esa persona.
 *
 * @typedef {object} ActorVincular
 * @property {string}                     tercero_id
 * @property {'remitente'|'destinatario'} rol
 * @property {boolean}                    [principal]
 * @property {'email'|'fisico'|'ambos'|null} [canal]
 * @property {string|null}                [nombre]
 * @property {string|null}                [entidad]
 * @property {string|null}                [email]
 * @property {string|null}                [telefono]
 * @property {string|null}                [direccion]
 * @property {string|null}                [municipio]
 * @property {string|null}                [departamento]
 * @property {string|null}                [municipio_codigo]
 * @property {string|null}                [departamento_codigo]
 * @property {string|null}                [pais]
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
 * @property {string|null}                                    [tracking_number]
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

/**
 * Respuesta de la consulta PULL de rastreo contra el operador postal
 * (GET /api/v1/envios/{id}/tracking, F5, Fase 8).
 * `operador_conectado=false` = no hay integración en vivo (stub del conector
 * postal, E11 roadmap): el estado mostrado es el último conocido
 * internamente, no una consulta real al operador.
 *
 * @typedef {object} TrackingResponse
 * @property {string}      shipment_id
 * @property {string}      operador
 * @property {string}      guia
 * @property {string}      estado_interno_actual
 * @property {string|null} estado_operador
 * @property {string|null} estado_interno_mapeado
 * @property {boolean}     operador_conectado
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
 * @property {'visto_bueno'|'firma'} rol — qué acto pide este turno (E23, RF-PRO-10).
 *   El backend lo manda desde que `PendienteItem` lo declara; el typedef se
 *   quedó atrás, así que la pantalla no podía distinguir «revise esto» de
 *   «comprométase con esto» sin que `svelte-check` protestara. Con él, la
 *   bandeja limita la firma en lote a los turnos de firma y nombra el acto
 *   real en cada fila.
 * @property {string} created_at      — ISO 8601 UTC
 */

/**
 * Un turno individual dentro de la cadena de firmas.
 *
 * @typedef {object} SignatureTurn
 * @property {number}      orden         — posición en la cadena (1-based)
 * @property {string}      firmante_id   — UUID del firmante asignado
 * @property {'visto_bueno'|'firma'} rol — qué acto es este turno (E23, RF-PRO-10).
 *   Un visto bueno APRUEBA; solo el turno de firma compromete a la entidad y
 *   produce el documento definitivo. Presentarlos igual atribuiría a un revisor
 *   un compromiso que no adquirió.
 * @property {'pendiente'|'firmado'|'rechazado'|'invalidada'} estado
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
 * Estado de la anulación de un radicado (paso 1 solicitar, paso 2 decidir).
 * GET/POST /api/v1/documents/{id}/anulacion[/aprobar|/rechazar].
 *
 * @typedef {object} AnulacionResponse
 * @property {string}                                id
 * @property {string}                                radicado_id
 * @property {'solicitada'|'aprobada'|'rechazada'}    estado
 * @property {string}                                causal
 * @property {string|null}                           motivo         — de la solicitud
 * @property {string|null}                           solicitado_por — UUID
 * @property {string}                                solicitado_at  — ISO 8601 UTC
 * @property {string|null}                           decidido_por   — UUID
 * @property {string|null}                           decidido_at    — ISO 8601 UTC
 * @property {string|null}                           observacion    — de la decisión
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

/**
 * Reto de segundo factor OTP-por-correo emitido para el turno activo del
 * actor (E06 Inc.2, RF-FIR-13). POST /api/v1/signature/requests/{id}/challenge.
 * `canal` viene YA enmascarado por el backend (ej. "j***@dominio.gov.co") —
 * la UI nunca debe intentar desenmascararlo ni mostrar el correo completo.
 *
 * @typedef {object} SignatureChallengeResponse
 * @property {string} challenge_id
 * @property {'otp_email'} factor_tipo
 * @property {string} canal        — correo enmascarado
 * @property {string} expira_at    — ISO 8601 UTC
 */

/**
 * Cuerpo opcional para firmar el turno activo, con el segundo factor
 * (E06 Inc.2/RF-FIR-13). Un objeto PUBLICA sin `firma_2fa_required` puede
 * firmar con `{}`; RESERVADA/CLASIFICADA lo exige SIEMPRE.
 *
 * @typedef {object} SignatureSignBody
 * @property {string} [challenge_id]  - del ChallengeResponse (factor OTP-correo)
 * @property {string} [otp]           - código recibido por correo
 * @property {string} [totp_code]     - factor TOTP alternativo (6 dígitos)
 */

/**
 * Cuerpo para crear una nueva cadena de firma sobre un objeto.
 * POST /api/v1/signature/requests.
 *
 * @typedef {object} SignatureRequestCreateBody
 * @property {string}   objeto_tipo
 * @property {string}   objeto_id
 * @property {string[]} firmantes         — UUIDs, al menos uno
 * @property {string}   [titulo]
 * @property {string}   [payload_sha256]  - SHA-256 hex (64 caracteres)
 * @property {1|2|3}    [nivel_seguridad]
 */

/**
 * Metadatos completos de una firma individual (certificado técnico de
 * firma). GET /api/v1/signature/firmas/{firma_id}.
 *
 * IMPORTANTE (RF-FIR-15): `acreditado` es SIEMPRE false en este incremento
 * — la firma es válida bajo Ley 527/1999 art. 7 (reconocimiento general de
 * firma electrónica), NO art. 28 (firma digital certificada/acreditada
 * ONAC). La UI que consuma este tipo NUNCA debe presentar la firma como
 * "acreditada" ni insinuar control exclusivo del firmante: la clave privada
 * se custodia en servidor (sin sole-control en sentido legal estricto).
 *
 * @typedef {object} SignatureCertificado
 * @property {string}      firma_id
 * @property {string}      objeto_tipo
 * @property {string|null} objeto_id
 * @property {string|null} formato_solicitado
 * @property {string|null} firmante_id
 * @property {string|null} [firmante_nombre] - resuelto al leer por el servicio
 *   de firma; `null` cuando el firmante no es un usuario del catálogo (sello
 *   del sistema). El identificador nunca se sustituye: sigue siendo la clave
 *   con la que se cruza el comprobante con el registro de firmas.
 * @property {string}      payload_sha256
 * @property {'nativa'|'xades_local'|'xades_personal'} provider
 * @property {string}      sello_tiempo    — ISO 8601 UTC (fecha/hora del acto)
 * @property {string}      created_at      — ISO 8601 UTC
 * @property {string|null} [xades_level]        - 'XAdES-B'|'XAdES-T'|'XAdES-LT'|'XAdES-LTA'
 * @property {string|null} [nivel_conformidad]
 * @property {'institucional'|'personal'|null} [tipo_firma]
 * @property {string|null} [certificado_fingerprint]
 * @property {boolean}     acreditado      — SIEMPRE false (ver docstring arriba)
 * @property {string|null} [tsa_timestamp]
 * @property {boolean}     [tsa_acreditado]
 * @property {boolean}     [lt_material_present]
 * @property {boolean}     [archive_timestamp_present]
 * @property {string|null} [revocation_provenance]
 * @property {string|null} [crl_next_update]
 * @property {boolean}     [ocsp_present]
 */

/**
 * Resultado de verificar una firma existente contra el documento firmado.
 * POST /api/v1/signature/verify.
 *
 * `motivo` explica un `valida: false` sin ambigüedad — la UI SIEMPRE debe
 * mostrarlo cuando esté presente, nunca reducir la respuesta a un simple
 * "sí/no".
 *
 * @typedef {object} SignatureVerifyResult
 * @property {string}      firma_id
 * @property {boolean}     valida
 * @property {string}      provider
 * @property {string|null} [xades_level]
 * @property {string|null} [nivel_conformidad]
 * @property {boolean}     acreditado
 * @property {string|null} [motivo]
 * @property {boolean|null} [sello_tiempo_valido]
 * @property {string|null}  [revocacion_status]  - 'vigente'|'revocado'|'no_verificable'|'desconocido'
 * @property {boolean}      [firma_criptograficamente_valida]
 */

/**
 * Firma a nivel documento (radicado/anexo) — distinta de la cadena de
 * firma-service: aquí se firma el hash SHA-256 del contenido directamente.
 * POST/GET /api/v1/documents/{document_id}/signatures.
 *
 * @typedef {object} DocumentSignature
 * @property {string}      id
 * @property {string}      radicado_id
 * @property {string|null} anexo_id
 * @property {string|null} signer_id
 * @property {string|null} signer_name
 * @property {string}      content_hash
 * @property {string|null} reason
 * @property {string}      signed_at   — ISO 8601 UTC
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

// ---------------------------------------------------------------------------
// Flujos de distribución (workflow-service, E05, T-Fase1)
// ---------------------------------------------------------------------------

/**
 * @typedef {object} FlowStepResponse
 * @property {string}      id            — id del PASO (flow_steps.id) — NUNCA confundir con radicado_id.
 * @property {string}      radicado_id
 * @property {string}      tracking_number
 * @property {number}      step_number
 * @property {string|null} from_dept
 * @property {string}      to_dept
 * @property {string|null} assigned_to   — uuid
 * @property {string}      assigned_by   — uuid
 * @property {string}      action        — 'assign'|'transfer'|'return'|'close'
 * @property {string|null} notes
 * @property {string}      status        — 'pending'|'in_progress'|'completed'|'cancelled'
 * @property {string}      created_at
 * @property {string|null} completed_at
 * @property {string|null} doc_type      — 'E'|'S'|'I'
 * @property {string|null} due_at
 * @property {string|null} semaforo      — 'verde'|'amarillo'|'rojo'|'vencido'
 */

// ---------------------------------------------------------------------------
// Respuesta rápida (document-service, respuesta.py) y RAG/antecedentes
// (knowledge-service, E21 Inc.2/3, F6 frontend)
// ---------------------------------------------------------------------------

/**
 * GET /api/v1/documents/{document_id}/respuestas — Salidas que responden a un radicado.
 *
 * @typedef {object} RespuestaItem
 * @property {string} id
 * @property {string} tracking_number
 * @property {string} doc_type
 * @property {string} subject
 * @property {string} status
 * @property {string} registered_at — ISO 8601 UTC
 */

/**
 * GET /api/v1/terceros/radicado/{id}/actores — un actor tal y como quedó
 * CONGELADO en el radicado (E01, RF-RAD-05).
 *
 * No es la ficha vigente del catálogo: dice a quién iba dirigido ese radicado,
 * no dónde vive hoy esa persona. `tercero_id` viaja para poder ir a la ficha
 * actual si alguien la pide expresamente.
 *
 * @typedef {object} ActorRadicado
 * @property {string} id
 * @property {string} tercero_id
 * @property {string} rol — `remitente` | `destinatario`
 * @property {number} orden
 * @property {boolean} principal
 * @property {string|null} canal — `email` | `fisico` | `ambos`
 * @property {string} nombre
 * @property {string|null} entidad
 * @property {string|null} email
 * @property {string|null} telefono
 * @property {string|null} direccion
 * @property {string|null} municipio
 * @property {string|null} departamento
 * @property {string|null} municipio_codigo
 * @property {string|null} departamento_codigo
 * @property {string|null} pais
 * @property {string} creado_at — ISO 8601 UTC
 */

/**
 * Cita de un precedente dentro de una respuesta del RAG (knowledge-service).
 *
 * @typedef {object} RagCitation
 * @property {string} cited_text
 * @property {string} source_type
 * @property {string} source_ref  — tracking_number del radicado fuente
 * @property {number} score
 */

/**
 * POST /api/v1/knowledge/rag — respuesta del RAG generativo con citas (E21 Inc.3).
 * `answer`/`modelo`/`suggestion_id` son `null` cuando no hubo generación (Patrón A,
 * fallback honesto): el llamante NUNCA debe ofrecer insertar en ese caso.
 *
 * @typedef {object} RagResponse
 * @property {string|null}     answer
 * @property {RagCitation[]}   citations
 * @property {string}          proveedor
 * @property {string|null}     modelo
 * @property {number}          confianza
 * @property {string|null}     suggestion_id
 * @property {string}          advisory
 */

/**
 * Precedente citado, sin generación (knowledge-service POST /antecedentes, Patrón A).
 *
 * @typedef {object} Antecedente
 * @property {string} source_type
 * @property {string} source_ref
 * @property {string} snippet
 * @property {number} score
 */

// ---------------------------------------------------------------------------
// Interoperabilidad: export/import ZIP + carga masiva (E11, Fase 8)
// ---------------------------------------------------------------------------

/**
 * Resumen de una importación de paquete interoperable (POST /api/v1/import,
 * RF-INT-05). Éxito 200 = todo-o-nada: el paquete completo pasó fixity +
 * esquema y se ingirió. `antecedentes_no_resueltos` lista los NÚMEROS de
 * radicado (no un conteo) cuyo antecedente declarado no se encontró — se
 * ingirieron igual, sin esa relación.
 *
 * @typedef {object} ImportResult
 * @property {string}   import_uuid
 * @property {number}   importados
 * @property {number}   omitidos_existentes       — colisión de tracking (no se sobreescribe)
 * @property {number}   omitidos_clearance        — no-write-up (RF-SEG-08)
 * @property {number}   relaciones_reconstruidas
 * @property {string[]} antecedentes_no_resueltos
 */

/**
 * Un ítem dentro de un job de carga masiva (documentos o expedientes).
 *
 * @typedef {object} BatchItemResult
 * @property {number}      item_index
 * @property {'success'|'failed'} status
 * @property {string|null} result_id       — UUID del radicado/expediente creado
 * @property {string|null} expediente_code — solo presente en jobs de expedientes (undefined en jobs de documentos)
 * @property {string|null} error_message
 */

/**
 * Estado de un job de carga masiva (GET /api/v1/batch/{job_id}/status),
 * compartido por document-service (documentos) y archive-service (expedientes).
 *
 * @typedef {object} BatchJobStatusResponse
 * @property {string}            job_id
 * @property {'documents'|'expedientes'} job_type
 * @property {'pending'|'processing'|'completed'|'failed'} status
 * @property {number}            total_items
 * @property {number}            processed_items
 * @property {number}            failed_items
 * @property {string}            created_at
 * @property {string|null}       started_at
 * @property {string|null}       completed_at
 * @property {BatchItemResult[]} items
 * @property {string|null}       error_message
 */

export {};

/**
 * Un documento dentro del índice electrónico (E15, `IndiceItemResponse` de
 * archive-service).
 *
 * Declarado aquí porque su ausencia era el hueco por el que se colaron dos
 * defectos del mismo tipo: sin `@typedef`, leer `item.fecha_archivado` —un
 * campo que la respuesta no tiene— no produce ningún error, ni en
 * `svelte-check` ni en tiempo de ejecución. La columna simplemente sale vacía,
 * y el «—» de cortesía la hace pasar por un dato no declarado. Ya ocurrió con
 * la foliación (que leía campos de este mismo esquema sobre otra respuesta) y
 * volvió a ocurrir en la tabla del índice.
 *
 * Los nombres siguen al XML probatorio: `fecha_incorporacion` es
 * `<fechaIncorporacion>` de RT-15 art. 4.3.2.3.
 *
 * @typedef {object} IndiceItem
 * @property {string}      document_id
 * @property {number}      orden
 * @property {string|null} titulo
 * @property {string|null} tipologia
 * @property {string|null} fecha_incorporacion — NO `fecha_archivado`
 * @property {string|null} valor_huella
 * @property {string}      funcion_resumen
 * @property {string|null} formato
 * @property {number|null} tamano_bytes
 * @property {number|null} folios
 * @property {'vinculado'|'excluido'|null} estado — `excluido` es el hueco trazable
 * @property {number|null} nivel_seguridad
 * @property {number|null} dependencia_productora_codigo
 * @property {string|null} dependencia_productora_nombre
 */

/**
 * Índice electrónico de un expediente (GET /api/v1/expedientes/{id}/indice).
 *
 * `num_documentos` cuenta **solo los vigentes**; `items` incluye además los
 * excluidos. Los dos números difieren a propósito y la pantalla tiene que
 * explicarlo: enseñar solo el primero junto a una tabla más larga se lee como
 * una contradicción.
 *
 * @typedef {object} IndiceElectronico
 * @property {string}      id
 * @property {string}      expediente_id
 * @property {number}      version
 * @property {string}      estado
 * @property {number}      num_documentos — vigentes; `items.length` incluye excluidos
 * @property {string}      xml_sha256
 * @property {string}      algoritmo
 * @property {string|null} firma_id
 * @property {string}      generado_ts
 * @property {IndiceItem[]} items
 */
