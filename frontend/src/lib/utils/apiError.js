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
    const delActo =
      MENSAJES_ACTO_TRAMITE[codigoError(data) ?? ""] ??
      MENSAJES_FIRMA[codigoError(data) ?? ""] ??
      MENSAJES_VENTANILLA_ENVIO[codigoError(data) ?? ""] ??
      MENSAJES_DEPENDENCIA[codigoError(data) ?? ""];
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
