// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * apiError.js — Leer el error de una respuesta del gateway, venga como venga.
 *
 * ## El sobre doble
 *
 * El formato de error del proyecto es `{error, detail, status}`. Pero los
 * routers lo levantan con `HTTPException(detail={...})`, y FastAPI serializa
 * **eso** dentro de su propio `detail`:
 *
 *     {"detail": {"error": "metadata_validation_failed", "detail": "…", "status": 422}}
 *
 * Los proxies BFF reenvían el cuerpo tal cual (`json(data, {status})`), así que
 * el sobre doble llega intacto al navegador. El frontend, en cambio, comparaba
 * `err.error` — el campo de PRIMER nivel, que no existe.
 *
 * Consecuencias medidas, no supuestas:
 *  - `HojaRuta` no reconocía `no_active_step` y un radicado recién radicado
 *    —sin flujo todavía, algo normal— decía «No se pudo cargar el paso actual»
 *    en vez de «Sin paso activo». El estado vacío correcto estaba escrito y no
 *    llegaba a pintarse nunca.
 *  - `/radicar` hacía `err?.detail ?? "Error al crear el radicado"` para el
 *    aviso: como `detail` es un OBJETO, el mensaje salía como
 *    «[object Object]», y el mapeo del error al campo concreto no se activaba.
 *
 * Estas funciones aceptan las dos formas a propósito: la plana existe de verdad
 * (errores que fabrica el propio proxy BFF, p. ej. `connection_error`) y la
 * anidada es la que produce el backend. Elegir una sola volvería a partir el
 * manejo de errores por la mitad.
 *
 * ## Y una tercera: la validación de Pydantic
 *
 * Cuando el que rechaza es un validador del esquema, FastAPI no usa el sobre
 * del proyecto: responde 422 con una LISTA.
 *
 *     {"detail": [{"type": "value_error", "loc": ["body"],
 *                  "msg": "Value error, fundamento_juridico es obligatorio…"}]}
 *
 * Ninguna de las dos ramas anteriores la reconocía, así que el aviso caía al
 * texto por defecto. Medido en la reclasificación de un radicado: el backend
 * decía exactamente qué faltaba y por qué —«fundamento_juridico es obligatorio
 * al reclasificar a nivel_seguridad >= 2 (Ley 1712/2014 art. 19/28)»— y la
 * pantalla mostraba «Error 422 al reclasificar el nivel de seguridad». La
 * persona veía que no se podía, sin ninguna vía para averiguar qué le faltaba.
 * Afecta a TODA validación de esquema del sistema, no a esa pantalla.
 */

/**
 * Prefijo que Pydantic antepone a los `ValueError` que lanza un validador
 * ESCRITO POR NOSOTROS. Es lo que separa las dos clases de error de validación,
 * y la distinción importa:
 *
 *  - Con prefijo: el mensaje lo redactó este proyecto, en español y pensado
 *    para quien está delante («fundamento_juridico es obligatorio al
 *    reclasificar a nivel_seguridad >= 2, Ley 1712/2014 art. 19/28»). Mostrarlo
 *    es justo lo que falta cuando el aviso genérico deja a la persona sin saber
 *    qué corregir.
 *  - Sin prefijo: lo generó Pydantic solo, por tipo o formato («invalid uuid»,
 *    «field required»). Está en inglés, nombra el campo interno y no dice nada
 *    accionable: para eso es mejor el texto por defecto de la pantalla, que al
 *    menos explica qué operación falló.
 */
const PREFIJO_VALIDADOR_PROPIO = /^Value error,\s*/;

/**
 * Mensajes de una lista de errores de validación de FastAPI/Pydantic,
 * quedándose SOLO con los que escribió este proyecto.
 * @param {any} detail
 * @returns {string|null}
 */
function mensajeDeValidacion(detail) {
  if (!Array.isArray(detail) || detail.length === 0) return null;
  const mensajes = detail
    .map((e) => (typeof e?.msg === "string" ? e.msg : ""))
    .filter((m) => PREFIJO_VALIDADOR_PROPIO.test(m))
    .map((m) => m.replace(PREFIJO_VALIDADOR_PROPIO, "").trim())
    .filter(Boolean);
  if (mensajes.length === 0) return null;
  // Varios campos inválidos a la vez se enumeran: quedarse con el primero
  // dejaría a la persona corrigiendo de uno en uno sin saber cuántos faltan.
  return mensajes.join(" · ");
}

/**
 * Código de error (`error`) de un cuerpo de respuesta, plano o anidado.
 * @param {any} data
 * @returns {string|null}
 */
export function codigoError(data) {
  if (!data || typeof data !== "object") return null;
  if (typeof data.error === "string") return data.error;
  if (data.detail && typeof data.detail === "object" && typeof data.detail.error === "string") {
    return data.detail.error;
  }
  return null;
}

/**
 * Propiedad a la que el backend atribuye un 422 de metadatos (`field`, ruta con
 * puntos: `a.b`, `lista.1`), en el sobre plano o en el anidado. `null` si el
 * fallo no es atribuible a una propiedad concreta.
 * @param {any} data
 * @returns {string|null}
 */
export function campoError(data) {
  if (!data || typeof data !== "object") return null;
  const campo =
    typeof data.field === "string"
      ? data.field
      : data.detail && typeof data.detail === "object" && typeof data.detail.field === "string"
        ? data.detail.field
        : null;
  return campo || null;
}

/**
 * Códigos de los actos de trámite (ADR-028) con su mensaje para la persona.
 * Se consultan ANTES que el `detail` del backend: el texto ya viene en español
 * pero habla del sistema, y aquí hay que decir qué hacer. `no_active_step` es a
 * propósito ambiguo —«no hay paso» y «no es suyo» son el mismo 404 sin oráculo—.
 * @type {Record<string,string>}
 */
export const MENSAJES_ACTO_TRAMITE = {
  radicado_terminal: "El radicado está cerrado o anulado y ya no admite actos de trámite.",
  no_active_step: "Este trámite no está a su cargo: no tiene paso activo o lo posee otra persona.",
  no_flow:
    "No se encontró el flujo de este radicado: no existe, no tiene pasos o está por encima de su nivel de acceso.",
  step_not_found:
    "Ese paso ya no está abierto o no está a su cargo. Actualice la bandeja para ver el estado actual.",
  radicado_not_found: "No se encontró el radicado, o está por encima de su nivel de acceso.",
  status_managed_by_workflow:
    "El estado del radicado lo mueve el flujo de trámite y no se edita a mano. Use «Marcar en trámite» o «Finalizar trámite».",
};

/**
 * Códigos de la firma (cadenas sobre radicado/expediente, ADR-016 addendum
 * 2026-10-04) y de la constancia de firma inobtenible (ADR-030), con su mensaje
 * en español para quien está delante. Como el de los actos de trámite, se
 * consulta ANTES del `detail` del backend: aquel habla del sistema, este dice
 * qué hacer.
 * @type {Record<string,string>}
 */
export const MENSAJES_FIRMA = {
  // --- cadenas de firma: el contenido firmado es el del documento, no su id ---
  radicado_sin_documento:
    "El radicado no tiene un documento principal que firmar. Marque un anexo como principal y vuelva a intentarlo.",
  indice_no_disponible:
    "El expediente no tiene un índice electrónico vigente que firmar. Genere o cierre el índice primero.",
  anexo_sin_huella:
    "El documento principal no tiene huella de integridad registrada, así que no se puede firmar. Avise al administrador.",
  documento_modificado:
    "El documento cambió después de abrir la cadena de firmas. Se debe invalidar esta cadena y abrir una nueva sobre la versión vigente.",
  cadena_sin_huella:
    "Esta cadena se abrió antes de que se fijara la huella del documento y ya no se puede firmar. Pida que se invalide y abra una nueva.",
  verificacion_documento_no_disponible:
    "No se pudo comprobar que el documento siga siendo el mismo. No se firmó nada; intente de nuevo en unos minutos.",
  // --- firma inobtenible ---
  declarante_es_parte_de_la_transferencia:
    "Quien elaboró, envió o recibió esta transferencia no puede declarar ni revocar una firma inobtenible. Debe hacerlo otra persona autorizada.",
  declarante_sin_permiso_de_firma:
    "Necesita el permiso de firma (nivel 2 o superior): el sello de la constancia se estampa a su nombre.",
  excepcion_ya_declarada: "Ese rol ya tiene una excepción declarada.",
  excepcion_vigente:
    "Ese rol ya tiene una excepción vigente. Para declarar de nuevo, primero hay que revocarla.",
  revocacion_sin_proteger:
    "La revocación anterior aún no está sellada ni protegida en el almacén. Espere a que termine la protección e intente de nuevo.",
  declarante_sin_certificado:
    "No tiene un certificado de firma activo, y la constancia exige su firma personal. Solicite su certificado antes de declarar.",
  soporte_sin_huella:
    "El anexo de soporte no tiene huella de integridad registrada. Elija otro anexo.",
  soporte_documental_sin_huella:
    "No se pudo obtener la huella del anexo de soporte. No se declaró nada; intente de nuevo.",
  storage_no_disponible:
    "El almacén de documentos no responde. No se declaró nada; intente de nuevo más tarde.",
  revocante_sin_permiso_de_firma:
    "Necesita el permiso de firma (nivel 2 o superior) para revocar: el sello se estampa a su nombre.",
  firma_personal_revocante_requerida:
    "Tiene certificado de firma activo, así que debe firmar también la revocación: escriba su código de verificación (TOTP).",
  valor_nulo:
    "El valor no puede quedar vacío: un parámetro no se puede dejar sin valor para que se herede. Escriba un valor concreto.",
  parte_ya_firmo: "Esa parte ya firmó el acta personalmente, así que no admite una excepción.",
  parte_sin_identidad:
    "La transferencia no tiene registrada la persona de ese rol, así que no se puede declarar su excepción.",
  transferencia_no_recibida:
    "La transferencia todavía no se ha recibido: el acta aún no está congelada y no admite este acto.",
  excepcion_ya_revocada: "Esa excepción ya fue revocada.",
  excepcion_not_found: "Ese rol no tiene una excepción de firma declarada.",
  referencia_documental_invalida:
    "El radicado o el anexo de soporte no existe o usted no puede verlo. Elija otro.",
  firma_personal_declarante_requerida:
    "La política de la entidad exige que usted firme además la constancia: escriba su código de verificación (TOTP).",
  sin_certificado_activo:
    "No tiene un certificado de firma activo y la política de la entidad exige firmar la constancia con él.",
  totp_incorrecto: "El código de verificación no es correcto.",
  sello_institucional_no_disponible:
    "El sello institucional no está disponible. No se declaró nada; intente de nuevo más tarde.",
  sellado_constancia_no_obtenido:
    "No se pudo sellar la constancia. No se declaró nada; intente de nuevo más tarde.",
  document_service_no_disponible:
    "No se pudo comprobar el radicado de soporte porque el servicio de documentos no responde. No se declaró nada.",
  canal_firma_personal_no_disponible:
    "El canal de firma personal no está disponible en este momento. No se firmó nada; intente de nuevo.",
  firma_excepcion_declarada:
    "Esa parte tiene una excepción de firma vigente y no puede firmar. Si sí puede, pida que se revoque la excepción.",
  sellado_sobre_no_obtenido:
    "No se pudo sellar el sobre de la constancia. No se declaró nada; intente de nuevo más tarde.",
  sobre_not_found: "Ese eslabón no tiene sobre firmado.",
  sobre_no_disponible:
    "El sobre firmado no está disponible en este momento. Intente de nuevo más tarde.",
  fundamento_caracteres_invalidos:
    "El fundamento contiene caracteres no permitidos (de control o invisibles). Quítelos y vuelva a intentar.",
  // --- parámetros ---
  motivo_requerido:
    "Relajar la política de firma exige un motivo de al menos 10 caracteres. Escríbalo y vuelva a guardar.",
};

/**
 * Códigos de la ventanilla en línea (política de tratamiento por entidad, ADR-031)
 * y del acuse de envío postal, con su mensaje en español.
 * @type {Record<string,string>}
 */
export const MENSAJES_VENTANILLA_ENVIO = {
  politica_desactualizada:
    "La política de tratamiento de datos cambió mientras llenaba el formulario. Revísela y vuelva a autorizar.",
  politica_no_publicada:
    "Esta entidad aún no ha publicado su política de tratamiento de datos, por lo que no puede recibir radicaciones en línea. Intente por otro canal.",
  politica_no_verificable:
    "No se pudo verificar la política de tratamiento de datos de la entidad. Vuelva a intentarlo en unos minutos.",
  acuse_ajeno: "El archivo no sirve como acuse: suba la constancia de entrega usted mismo.",
  acuse_invalido: "El archivo no sirve como acuse: suba la constancia de entrega usted mismo.",
  acuse_ya_referenciado:
    "Ese archivo ya está ligado a otro documento o envío y no puede servir como acuse de este.",
};

/**
 * Códigos del antirrobots ALTCHA de la ventanilla pública (C-3 / C16), en
 * español. Los 422/409 piden un reto nuevo (el cliente reinicia el widget); el
 * 503 nombra los canales alternos, porque sin reto vigente no se radica.
 * @type {Record<string,string>}
 */
export const MENSAJES_ANTIRROBOTS = {
  antirrobots_invalido:
    "No se pudo completar la verificación automática. Espere unos segundos a que el sistema la repita e intente de nuevo.",
  antirrobots_vencido:
    "La verificación automática venció mientras llenaba el formulario. Se renovó sola: vuelva a enviar.",
  antirrobots_usado: "Esa verificación ya se utilizó. Se renovó una nueva: vuelva a enviar.",
  antirrobots_no_disponible:
    "La verificación automática no está disponible en este momento, así que no se puede radicar en línea. Presente su documento de forma presencial en la ventanilla de la entidad o por el correo de atención de la entidad.",
};

/**
 * Códigos de la conciliación de fichas provisionales de terceros (C17), con su
 * mensaje en español para quien concilia. `motivo_requerido` NO está aquí a
 * propósito: ya existe en `MENSAJES_FIRMA` con otro sentido y, al consultarse
 * antes, taparía este; la pantalla de conciliación lo resuelve por su cuenta.
 * @type {Record<string,string>}
 */
export const MENSAJES_CONCILIACION = {
  tercero_no_encontrado:
    "La ficha no existe, dejó de ser visible o está por encima de su nivel de acceso. Actualice la lista.",
  medio_verificacion_requerido:
    "Indique cómo se comprobó que es la misma persona: este acto exige un medio de verificación.",
  medio_verificacion_no_aplica: "Descartar no lleva medio de verificación.",
  tercero_ya_conciliado: "Esa ficha ya fue conciliada por otra persona. Actualice la lista.",
  destino_invalido:
    "La ficha de destino no sirve: debe existir, estar confirmada, vigente y ser distinta de la provisional. Pudo haberse fusionado con otra mientras trabajaba: busque de nuevo.",
  documento_ya_registrado:
    "Ya existe una ficha confirmada con ese documento. Vincule la ficha provisional a ella en lugar de confirmarla como nueva.",
  destino_requerido: "Elija la ficha existente a la que se vincula.",
  destino_no_aplica: "Esta acción no lleva ficha de destino.",
  motivo_insuficiente: "El motivo es muy breve: escriba al menos 10 caracteres.",
  documento_sin_tipo:
    "La ficha declaró un número de documento sin tipo, así que no se puede confirmar como identidad nueva. Vincúlela o descártela.",
  tercero_provisional:
    "Esa ficha es provisional: primero debe conciliarse en «Fichas por conciliar».",
};

/**
 * Códigos de la dependencia identificada por CÓDIGO (ADR-032), con su mensaje
 * en español. Como los demás diccionarios, se consulta antes del `detail`.
 * @type {Record<string,string>}
 */
export const MENSAJES_DEPENDENCIA = {
  dependencia_codigo_inmutable:
    "El código de una dependencia no se puede cambiar: es lo que une la dependencia con sus radicados, reglas y bandejas. Si necesita otro código, cree una dependencia nueva y desactive esta.",
  dependencia_ambigua:
    "El nombre de la dependencia coincide con más de una. Elíjala del catálogo para enviar su código.",
  dependencia_desconocida:
    "La dependencia no existe o está inactiva. Elíjala de nuevo del catálogo.",
  dependencia_con_tramite_en_curso:
    "La dependencia tiene trámites en curso (pasos de flujo vivos o reglas que la usan). Reasígnelos antes de desactivarla.",
  dependencia_con_historia:
    "La dependencia tiene historia (radicados, flujos, reglas o usuarios) y no se puede eliminar por trazabilidad. Desactívela en su lugar.",
  origen_desactualizado:
    "El radicado ya cambió de dependencia mientras usted trabajaba. Actualice la vista e intente de nuevo.",
  condicion_por_nombre_obsoleta:
    "Las condiciones «es igual a», «es distinto de» y «está entre» sobre una dependencia deben usar el campo de código, no el nombre.",
};

/**
 * Códigos de la desclasificación con segunda persona (C-2, ADR-039) y del índice
 * público de información clasificada. `indice_no_disponible` YA existe en
 * `MENSAJES_FIRMA` con otro sentido (índice electrónico del expediente) y se
 * consulta antes: la página pública del índice lo resuelve por su cuenta.
 * @type {Record<string,string>}
 */
export const MENSAJES_DESCLASIFICACION = {
  desclasificacion_requiere_aprobacion:
    "Bajar el nivel de un radicado clasificado exige la aprobación de otra persona. Use «Solicitar desclasificación».",
  no_requiere_aprobacion:
    "Este radicado no está en el nivel que exige segunda persona: reclasifíquelo directamente.",
  desclasificacion_pendiente:
    "Este radicado ya tiene una solicitud de desclasificación pendiente. Retírela o espere la decisión.",
  separacion_de_funciones:
    "No puede aprobar ni rechazar una solicitud que usted mismo hizo: debe decidirla otra persona.",
  solicitud_caducada:
    "La solicitud caducó sin que nadie la decidiera. Quien la hizo debe presentarla de nuevo.",
  solicitud_ya_resuelta:
    "Otra persona ya resolvió esta solicitud. Actualice la lista para ver el estado actual.",
  nivel_cambio:
    "El nivel del radicado cambió mientras se tramitaba. No se hizo ningún cambio; revise el radicado y vuelva a intentarlo.",
  solo_el_solicitante: "Solo quien hizo la solicitud puede retirarla.",
  solicitud_not_found:
    "No se encontró la solicitud, o el radicado está por encima de su nivel de acceso.",
};

/**
 * Códigos de la custodia física (préstamo de unidades, C-5/C11), de la
 * retención legal por motivo y la valoración de la TVD (C7) y del anexo citado
 * por una constancia de firma inobtenible (C14).
 *
 * Los `unidad_*` se resuelven con el `detail` del backend cuando existe (nombra
 * la unidad por su código: «la caja CJ-014 está prestada»); este texto es solo
 * el respaldo cuando el detalle no llega. Ver `MENSAJES_CUSTODIA_CON_DETALLE`.
 * @type {Record<string,string>}
 */
export const MENSAJES_CUSTODIA = {
  unidad_ya_prestada: "La unidad (o una que la contiene o está dentro de ella) ya está prestada.",
  unidad_prestada:
    "La unidad está prestada: mientras esté fuera no se puede transferir, eliminar ni vincularle expedientes.",
  unidad_en_transferencia:
    "La unidad contiene expedientes de una transferencia en curso y no se puede prestar.",
  unidad_en_lote_eliminacion:
    "La unidad contiene expedientes de un lote de eliminación vivo y no se puede prestar.",
  prestatario_sin_acceso:
    "La persona que recibe no tiene el nivel de acceso para el contenido de la unidad. Elija otra o ajuste el acceso.",
  prestatario_inexistente:
    "El prestatario interno no existe o está inactivo. Elíjalo de la lista de personas elegibles.",
  clearance_insuficiente: "El contenido de la unidad está por encima de su nivel de acceso.",
  hold_de_sistema:
    "Esta retención la fija y la levanta el sistema con la valoración del Comité; no se opera a mano.",
  motivo_no_coincide:
    "El motivo escrito no coincide con el de la retención. Para levantarla, repita su motivo exacto.",
  hold_ya_levantado: "Esa retención legal ya fue levantada. Actualice la lista.",
  tvd_no_aprobada:
    "La TVD debe estar aprobada o convalidada para registrar la valoración del Comité.",
  valoracion_ya_registrada: "Esta serie ya tiene una valoración del Comité registrada.",
  legal_hold_activo: "El expediente tiene una retención legal activa y no puede eliminarse.",
  soporte_citado_no_avalado:
    "Una constancia de firma inobtenible cita el soporte de este expediente y su baja no fue avalada en el lote. Anule el lote y proponga de nuevo.",
  radicado_en_lote_de_eliminacion:
    "El radicado de soporte está en un lote de eliminación vivo: no se puede citar en una constancia.",
  anexo_citado_por_constancia:
    "El anexo es el soporte de una constancia de firma inobtenible vigente y no se puede borrar. Revoque la constancia o incluya su baja en un lote de eliminación avalado.",
  retencion_diferida_sin_hold: "No hay retención legal de valoración que respalde esta protección.",
};

/** Códigos de custodia cuyo `detail` del backend (nombra la unidad) tiene prioridad. */
const MENSAJES_CUSTODIA_CON_DETALLE = new Set([
  "unidad_ya_prestada",
  "unidad_prestada",
  "unidad_en_transferencia",
  "unidad_en_lote_eliminacion",
  "prestatario_sin_acceso",
  "anexo_citado_por_constancia",
  "soporte_citado_no_avalado",
]);

/**
 * Códigos de la TRD como fuente única de fases, dependencias productoras y
 * `publicable` del tipo documental (C-4, ADR-040) y del organigrama sin ciclos.
 * Los que nombran dependencias (`codigos`) prefieren el `detail` del backend.
 * @type {Record<string,string>}
 */
export const MENSAJES_TRD = {
  dependencia_inactiva:
    "Una de las dependencias productoras está inactiva. Elija solo dependencias activas.",
  productora_solo_en_serie:
    "La dependencia productora solo se declara en series, no en subseries ni en otros niveles.",
  productora_ya_declarada:
    "Esta versión ya tiene dependencia productora declarada: no se puede declarar de nuevo al ratificar.",
  productora_solo_en_ratificacion:
    "La dependencia productora solo se declara al ratificar una versión migrada; en las demás se declara en el borrador.",
  trd_serie_sin_productora:
    "La serie no tiene dependencia productora. Declare al menos una en el borrador (o al ratificar, si es migrada) antes de aprobar o convalidar.",
  trd_jerarquia_dos_niveles:
    "La jerarquía de la TRD admite solo dos niveles: serie y subserie. La subserie no puede tener subseries propias.",
  trd_padre_no_existe: "La serie padre no existe. Actualice la lista y elíjala de nuevo.",
  trd_version_inmutable:
    "Esta versión ya no es un borrador y no se puede modificar. Abra una versión nueva.",
  trd_retencion_minima:
    "La retención debe sumar al menos 1 año entre archivo de gestión y archivo central.",
  catalogo_dependencias_no_disponible:
    "El catálogo de dependencias no está disponible en este momento. Intente de nuevo en unos minutos.",
  organigrama_con_ciclo:
    "El organigrama tiene un ciclo (una dependencia aparece como su propia ascendiente), así que el cuadro no se puede derivar. Corrija el padre de las dependencias señaladas.",
  dependencia_ciclo:
    "Esa dependencia padre crearía un ciclo en el organigrama: una dependencia no puede depender de sí misma ni de una de sus descendientes.",
  tipo_documental_no_publicable:
    "El tipo documental del radicado no está marcado como publicable (Ley 1712) o está inactivo. Un administrador con nivel 3 debe habilitarlo antes de publicar.",
  extra_forbidden:
    "La petición incluye un campo que ya no existe. Recargue la página e intente de nuevo.",
};

/** Códigos de TRD cuyo `detail` del backend (nombra las dependencias por código) tiene prioridad. */
const MENSAJES_TRD_CON_DETALLE = new Set([
  "dependencia_inactiva",
  "dependencia_desconocida",
  "organigrama_con_ciclo",
  "dependencia_ciclo",
]);

/**
 * Mensaje legible de un cuerpo de respuesta. **Nunca devuelve un objeto**: ese
 * es justo el fallo que motivó este módulo — un `[object Object]` en un aviso
 * de error deja a la persona sin saber qué pasó ni cómo arreglarlo.
 * @param {any} data
 * @param {string} porDefecto
 * @returns {string}
 */
export function mensajeError(data, porDefecto) {
  if (typeof data === "string" && data.trim()) return data;
  if (data && typeof data === "object") {
    const cod = codigoError(data) ?? "";
    if (MENSAJES_CUSTODIA_CON_DETALLE.has(cod) || MENSAJES_TRD_CON_DETALLE.has(cod)) {
      const d = typeof data.detail === "string" ? data.detail : data.detail?.detail;
      if (typeof d === "string" && d.trim()) return d;
    }
    const delActo =
      MENSAJES_ACTO_TRAMITE[codigoError(data) ?? ""] ??
      MENSAJES_FIRMA[codigoError(data) ?? ""] ??
      MENSAJES_VENTANILLA_ENVIO[codigoError(data) ?? ""] ??
      MENSAJES_DEPENDENCIA[codigoError(data) ?? ""] ??
      MENSAJES_ANTIRROBOTS[codigoError(data) ?? ""] ??
      MENSAJES_CONCILIACION[codigoError(data) ?? ""] ??
      MENSAJES_DESCLASIFICACION[codigoError(data) ?? ""] ??
      MENSAJES_CUSTODIA[codigoError(data) ?? ""] ??
      MENSAJES_TRD[codigoError(data) ?? ""];
    if (delActo) return delActo;
    if (typeof data.detail === "string" && data.detail.trim()) return data.detail;
    const validacion = mensajeDeValidacion(data.detail);
    if (validacion) return validacion;
    if (data.detail && typeof data.detail === "object" && typeof data.detail.detail === "string") {
      return data.detail.detail;
    }
    const codigo = codigoError(data);
    if (codigo) return codigo;
  }
  return porDefecto;
}
