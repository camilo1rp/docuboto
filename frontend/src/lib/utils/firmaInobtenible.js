// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * firmaInobtenible.js — Reglas puras de la pantalla de «firma inobtenible» del
 * acta de transferencia (AREA-07-10, ADR-030) y de las advertencias de
 * verificación (ADR-016, addendum 2026-10-04).
 *
 * Todo lo que aquí se decide ESPEJA al backend y nunca lo sustituye: el
 * servidor re-valida cada acto. La regla de oro es que la interfaz no ofrezca
 * lo que la API rechazaría, y que ante un dato que no conoce falle CERRADA
 * (sin identidad no se ofrece declarar; sin verificación tampoco).
 */

/** Roles del FUID, en el orden en que se muestran. */
export const ROLES_ACTA = ["elabora", "entrega", "recibe"];

export const ROL_LABELS = {
  elabora: "Elaborador(a)",
  entrega: "Remitente (entrega)",
  recibe: "Receptor(a) (recibe)",
};

export const MOTIVOS = [
  { value: "fallecimiento", label: "Fallecimiento" },
  { value: "retiro", label: "Retiro de la entidad" },
  { value: "incapacidad", label: "Incapacidad" },
  { value: "otro", label: "Otro" },
];

export const FUNDAMENTO_MIN = 20;
export const FUNDAMENTO_MAX = 2000;
/** Motivo mínimo para relajar la política XAdES (`PUT /config/params`). */
export const MOTIVO_PARAMETRO_MIN = 10;

/**
 * Niveles que exige el backend para declarar o revocar: `USUA_PERM_ADMIN` ≥ 3
 * y `PERM_FIRMA` ≥ 2 (el sello se estampa a nombre del declarante).
 */
export const NIVEL_ADMIN_FIRMA_INOBTENIBLE = 3;
export const NIVEL_PERM_FIRMA_INOBTENIBLE = 2;

/**
 * ¿La persona es una de las tres partes de la transferencia (`creada_por`,
 * `enviada_por`, `decidida_por`)? Sin identidad conocida se supone que SÍ:
 * ofrecer el acto a quien podría ser parte es el error caro.
 *
 * @param {{creada_por?: string|null, enviada_por?: string|null, decidida_por?: string|null}|null|undefined} transferencia
 * @param {string|null|undefined} userId — identidad INTERNA (`auth_users.id`)
 * @returns {boolean}
 */
export function esParteDeLaTransferencia(transferencia, userId) {
  if (!transferencia || !userId) return true;
  return (
    userId === transferencia.creada_por ||
    userId === transferencia.enviada_por ||
    userId === transferencia.decidida_por
  );
}

/**
 * Puerta de declarar y de revocar: misma para ambos actos.
 *
 * @param {{levels: Record<string, number>, userId: string|null|undefined, transferencia: any}} p
 * @returns {boolean}
 */
export function puedeGestionarExcepciones({ levels, userId, transferencia }) {
  const l = levels ?? {};
  if ((l["USUA_PERM_ADMIN"] ?? 0) < NIVEL_ADMIN_FIRMA_INOBTENIBLE) return false;
  if ((l["PERM_FIRMA"] ?? 0) < NIVEL_PERM_FIRMA_INOBTENIBLE) return false;
  return !esParteDeLaTransferencia(transferencia, userId);
}

/**
 * Constancias del acta. La verificación trae `excepciones` con la validez ya
 * comprobada; el acta trae `excepciones_firma` (sin ella). Se prefiere la primera.
 *
 * @param {any} verificacion
 * @param {any} acta
 * @returns {any[]}
 */
export function excepcionesDe(verificacion, acta) {
  if (Array.isArray(verificacion?.excepciones)) return verificacion.excepciones;
  if (Array.isArray(acta?.excepciones_firma)) return acta.excepciones_firma;
  return [];
}

/**
 * Constancia MÁS RECIENTE del rol (mayor `ordinal`): desde FIRMA-2 un rol tiene una
 * cadena lineal (declarada, revocada, vuelta a declarar…) y manda el último eslabón.
 *
 * @param {any[]} excepciones @param {string} rol
 */
export function excepcionDelRol(excepciones, rol) {
  let ultima = null;
  for (const e of excepciones) {
    if (e.rol === rol && (!ultima || (e.ordinal ?? 1) >= (ultima.ordinal ?? 1))) ultima = e;
  }
  return ultima;
}

/** Excepción vigente (no revocada) de un rol, o null. */
export function excepcionVigenteDelRol(excepciones, rol) {
  const e = excepcionDelRol(excepciones, rol);
  return e && !e.revocada ? e : null;
}

/**
 * Roles para los que se puede DECLARAR: sin firma personal y sin excepción
 * VIGENTE —una revocada admite declarar de nuevo (ordinal n+1, FIRMA-2); si el
 * servidor la rechaza por WORM pendiente o firma previa lo dice— y solo con el
 * acta ya recibida. Sin verificación cargada no se ofrece nada.
 *
 * @param {{transferencia: any, verificacion: any, acta: any}} p
 * @returns {string[]}
 */
export function rolesDeclarables({ transferencia, verificacion, acta }) {
  if (!transferencia || transferencia.estado !== "recibida" || !verificacion) return [];
  const excepciones = excepcionesDe(verificacion, acta);
  const firmados = new Set((verificacion.firmas_personales ?? []).map((f) => f.rol));
  return ROLES_ACTA.filter(
    (rol) => !firmados.has(rol) && !excepcionVigenteDelRol(excepciones, rol),
  );
}

/** Roles con excepción vigente: los únicos que admiten revocación. */
export function rolesRevocables(excepciones) {
  return ROLES_ACTA.filter((rol) => excepcionVigenteDelRol(excepciones, rol));
}

/**
 * ¿Hay que pedir el código TOTP al declarar?
 *  - `si`: la política de la entidad es `todos`, o `clasificados` con expediente de nivel ≥ 2.
 *  - `posible`: `clasificados` y no se conoce el nivel (el detalle del expediente no lo
 *    trae): el campo se ofrece y, si el servidor lo exige, lo dice con 422.
 *  - `no`: `off` (o política desconocida distinta de las anteriores).
 *
 * @param {{politica: string|null|undefined, nivelExpediente?: number|null}} p
 * @returns {"si"|"posible"|"no"}
 */
export function exigeTotp({ politica, nivelExpediente = null }) {
  if (politica === "todos") return "si";
  if (politica === "clasificados") {
    if (typeof nivelExpediente === "number") return nivelExpediente >= 2 ? "si" : "no";
    return "posible";
  }
  return "no";
}

/**
 * Compromiso del fundamento (SHA-256 con sal por registro). ÚNICO sitio que nombra los
 * campos de la huella: si el backend los vuelve a cambiar, se cambia aquí.
 *
 * @param {any} e constancia @returns {string|null}
 */
export const huellaFundamento = (e) => e?.fundamento_compromiso ?? null;
/** @param {any} e constancia @returns {string|null} */
export const huellaRevocacion = (e) => e?.revocacion_fundamento_compromiso ?? null;
/** @param {any} e constancia @returns {string|null} */
export const algoritmoHuella = (e) =>
  ETIQUETA_ALGORITMO[e?.fundamento_compromiso_algoritmo] ??
  e?.fundamento_compromiso_algoritmo ??
  null;

/** Algoritmos de huella en lenguaje llano. */
export const ETIQUETA_ALGORITMO = {
  "SHA-256-salted": "huella SHA-256 con sal propia de este registro",
};

/** Ayuda breve de la huella del fundamento. */
export const AYUDA_HUELLA =
  "Permite comprobar después que el texto no cambió, sin exponerlo; solo quien tenga el texto original puede reproducirla.";

/** Causas de revocación (vocabulario cerrado del backend). */
export const CAUSAS_REVOCACION = [
  { value: "error_de_declaracion", label: "Error al declarar" },
  { value: "parte_puede_firmar", label: "La parte sí puede firmar" },
  { value: "otro", label: "Otra causa" },
];
export const ETIQUETA_CAUSA = Object.fromEntries(CAUSAS_REVOCACION.map((c) => [c.value, c.label]));

/**
 * ¿Ofrecer «Reintentar protección»? Solo a administración 3 y cuando el Object-Lock del
 * eslabón NO está confirmado (`false`; `null` = no hay artefacto o no se sabe). El backend
 * repone solo los artefactos `agotado` y dice el estado real al terminar.
 *
 * @param {Record<string, number>|undefined} levels
 * @param {any} e constancia
 * @param {string} acto
 */
export function puedeReintentarWorm(levels, e, acto) {
  if (!puedeVerificarFundamento(levels) || !e?.id) return false;
  if (acto === "revocacion") {
    return (
      !!e.revocada &&
      (e.revocacion_worm_protegido === false || e.revocacion_worm_sobre_protegido === false)
    );
  }
  return e.worm_protegido === false || e.worm_sobre_protegido === false;
}

/** @param {any} r respuesta de `WormReintentoResponse` */
export function resumenReintento(r) {
  const art = Object.entries(r?.artefactos ?? {});
  const pendientes = art.filter(([, v]) => v === "pendiente" || v === "agotado");
  return {
    ok: pendientes.length === 0,
    texto:
      pendientes.length === 0
        ? `Protección confirmada.`
        : `Quedan sin proteger: ${pendientes.map(([k, v]) => etiquetaArtefacto(k, v)).join("; ")}.`,
  };
}

/** Texto para una referencia documental recortada por el nivel del lector (`null`). */
export const SOPORTE_NO_VISIBLE = "Soporte no visible con su nivel de acceso";

/** Nivel de `USUA_PERM_ADMIN` para verificar el fundamento de una constancia. */
export const NIVEL_ADMIN_VERIFICAR_FUNDAMENTO = 3;

/** @param {Record<string, number>|undefined} levels */
export function puedeVerificarFundamento(levels) {
  return ((levels ?? {})["USUA_PERM_ADMIN"] ?? 0) >= NIVEL_ADMIN_VERIFICAR_FUNDAMENTO;
}

/**
 * Persona de una constancia (declarante, parte o revocante): nombre y cargo con la
 * fuente de la instantánea. `no_resuelta` se declara, no se esconde.
 *
 * @param {string|null|undefined} nombre
 * @param {string|null|undefined} cargo
 * @param {string|null|undefined} fuente
 * @returns {string}
 */
export function describirPersona(nombre, cargo, fuente) {
  if (fuente === "no_resuelta" || (!nombre && !cargo)) return "No resuelta al declarar";
  const base = [nombre, cargo].filter(Boolean).join(", ");
  return fuente ? `${base} (${fuente})` : base;
}

/**
 * Lectura de `FundamentoVerificacion`: una copia `null` no comprobada NO cuenta como
 * coincidencia. Devuelve el veredicto y la lista de copias con su estado.
 *
 * @param {any} v
 */
export function resumenVerificacionFundamento(v) {
  if (!v || v.verificable === false) {
    return {
      status: "warning",
      texto:
        v?.motivo === "constancia_sin_compromiso_salado"
          ? "Constancia sin compromiso con sal (anterior a la versión 3): no se puede verificar."
          : "El fundamento no es verificable.",
      copias: [],
    };
  }
  const copias = [
    ["Registro guardado", v.columna],
    ["Documento firmado", v.xml],
    ["Asiento de auditoría", v.asiento],
    ["Revocación: registro guardado", v.revocacion_columna],
    ["Revocación: documento firmado", v.revocacion_xml],
    ["Revocación: asiento", v.revocacion_asiento],
    ["Sobre firmado", v.sobre],
    ["Revocación: sobre firmado", v.revocacion_sobre],
  ].filter(([, ok]) => ok !== undefined);
  return {
    status: v.coincide ? "success" : "error",
    texto: v.coincide
      ? "El fundamento coincide en todas las copias comprobables."
      : "El fundamento NO coincide en alguna copia o no se pudo comprobar.",
    copias: copias.map(([nombre, ok]) => ({
      nombre,
      estado: ok === true ? "coincide" : ok === false ? "no coincide" : "no comprobada",
    })),
  };
}

/**
 * Etiquetas de `completitud` del acta. `sellada_con_excepcion` NO es «firmada»:
 * ninguna parte firmó, solo está el sello de la entidad.
 */
export const COMPLETITUD_MAP = {
  sellada: { label: "Solo sello institucional", status: "info" },
  firma_personal_pendiente: { label: "Sin firmas personales", status: "warning" },
  firmada_receptor: { label: "Firmada por el receptor", status: "info" },
  firmada_remitente: { label: "Firmada por el remitente", status: "info" },
  firmada_completa: { label: "Firma personal completa", status: "success" },
  firmada_con_excepcion: {
    label: "Firmada con excepción: una parte firmó y la otra no pudo",
    status: "warning",
  },
  sellada_con_excepcion: {
    label: "Sellada con excepción: ninguna parte firmó; solo el sello de la entidad",
    status: "warning",
  },
};

export const POLITICA_XADES_LABELS = {
  off: "Firma personal opcional",
  clasificados: "Firma personal obligatoria en expedientes clasificados (nivel 2 o más)",
  todos: "Firma personal obligatoria en todos los casos",
};

/** Avisos de una firma que sigue siendo válida (vocabulario cerrado de signature-service). */
export const ADVERTENCIAS_LABELS = {
  tsa_sin_esscertid_anterior_al_corte:
    "El sello de tiempo no identifica el certificado de la autoridad de sellado (ESSCertID). Es anterior a la fecha de corte, por eso se acepta, pero con reservas.",
};

/** @param {string} codigo */
export function etiquetaAdvertencia(codigo) {
  return ADVERTENCIAS_LABELS[codigo] ?? `Aviso de la verificación: ${codigo}`;
}

export const ETIQUETA_MOTIVO = Object.fromEntries(MOTIVOS.map((m) => [m.value, m.label]));

/** @param {string|null|undefined} fundamento */
export function longitudFundamentoValida(fundamento) {
  const n = (fundamento ?? "").trim().length;
  return n >= FUNDAMENTO_MIN && n <= FUNDAMENTO_MAX;
}

// ---------------------------------------------------------------------------
// Política XAdES de la entidad (`firma_personal_require_xades`)
// ---------------------------------------------------------------------------

export const CLAVE_POLITICA_XADES = "firma_personal_require_xades";

export const OPCIONES_POLITICA_XADES = [
  { value: "off", label: "off — la firma personal es opcional" },
  { value: "clasificados", label: "clasificados — obligatoria en expedientes de nivel 2 o más" },
  { value: "todos", label: "todos — obligatoria siempre" },
];

const RANGO_POLITICA = { off: 0, clasificados: 1, todos: 2 };

/**
 * ¿Pasar de `vigente` a `nuevo` baja la exigencia? Un vigente `null` (heredado del
 * entorno) o desconocido se supone el más estricto —como lo aplica el backend—, así
 * que fijar desde ahí cualquier cosa distinta de `todos` RELAJA y pide motivo.
 *
 * @param {unknown} vigente
 * @param {unknown} nuevo
 * @returns {boolean}
 */
export function relajaPolitica(vigente, nuevo) {
  if (typeof nuevo !== "string" || !(nuevo in RANGO_POLITICA)) return false;
  const rangoVigente =
    typeof vigente === "string" && vigente in RANGO_POLITICA ? RANGO_POLITICA[vigente] : 2;
  return RANGO_POLITICA[nuevo] < rangoVigente;
}

/** @param {unknown} valor */
export function etiquetaPolitica(valor) {
  if (valor === null || valor === undefined)
    return "Heredado del entorno (la entidad no lo define)";
  return typeof valor === "string"
    ? (POLITICA_XADES_LABELS[valor] ?? String(valor))
    : String(valor);
}

/** Nivel de `USUA_PERM_ADMIN` que exige `PUT /config/params/{key}`. */
export const NIVEL_ADMIN_PARAMETROS = 3;

/**
 * Descarga un XML en el navegador sin pintarlo. Devuelve false si no hay navegador.
 *
 * @param {string} nombre @param {string} xml
 */
export function descargarXml(nombre, xml) {
  if (typeof document === "undefined" || typeof URL.createObjectURL !== "function") return false;
  const url = URL.createObjectURL(new Blob([xml], { type: "application/xml" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = nombre;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return true;
}

/** Aviso visible bajo el campo de fundamento (declarar y revocar): el texto es permanente. */
export const AVISO_FUNDAMENTO =
  "Indique la causal y el soporte, no el diagnóstico: este texto se conserva sellado y no se puede borrar.";

// ---------------------------------------------------------------------------
// Etiquetas en español de los códigos de la verificación de la cadena
// ---------------------------------------------------------------------------

/** Motivos de `enlace_motivo` / `cadena_motivos` (vocabulario del backend). */
export const ETIQUETA_MOTIVO_CADENA = {
  constancia_xml_ilegible: "el documento firmado de la constancia no se puede leer",
  constancia_huella_distinta: "la huella de la constancia no coincide con la guardada",
  constancia_id_distinto: "el identificador dentro del documento firmado no coincide",
  ordinal_distinto: "el número de declaración del documento firmado no coincide",
  ordinal_no_consecutivo: "la numeración no es consecutiva",
  compromiso_xml_distinto: "la huella del fundamento del documento firmado no coincide",
  sustituyeA_no_coincide: "la declaración que sustituye no coincide con la registrada",
  sustituida_inexistente: "la declaración que sustituye no existe",
  sustituida_no_revocada: "la declaración que sustituye no está revocada",
  revocacionSha256_no_coincide: "la huella de la revocación anterior no coincide",
  revocacion_sustituida_sin_worm: "la revocación anterior no está protegida en el almacén",
  revocacion_xml_ausente: "falta el documento firmado de la revocación",
  revocacion_xml_ilegible: "el documento firmado de la revocación no se puede leer",
  revocacion_huella_distinta: "la huella de la revocación no coincide con la guardada",
  revocacion_no_nombra_la_constancia: "la revocación no identifica la constancia que revoca",
  constanciaSha256_no_coincide: "la revocación apunta a una constancia con otra huella",
  causa_xml_distinta: "la causa de la revocación no coincide con el documento firmado",
  compromiso_revocacion_xml_distinto:
    "la huella del fundamento de la revocación no coincide con el documento firmado",
  artefacto_sello_no_firma_el_xml_guardado:
    "el sello institucional no corresponde al documento guardado",
  artefacto_personal_no_firma_el_xml_guardado:
    "la firma personal no corresponde al documento guardado",
};

/** @param {string} codigo */
export function etiquetaMotivoEnlace(codigo) {
  const c = String(codigo ?? "").trim();
  return ETIQUETA_MOTIVO_CADENA[c] ?? `Motivo sin descripción: ${c}`;
}

/** `enlace_motivo` llega como códigos separados por coma. @param {string|null|undefined} motivo */
export function etiquetaMotivosEnlace(motivo) {
  return (motivo ?? "")
    .split(",")
    .map((m) => m.trim())
    .filter(Boolean)
    .map(etiquetaMotivoEnlace)
    .join("; ");
}

/**
 * Elemento de `cadena_motivos` (`recibe#2: ordinal_no_consecutivo`) en español:
 * «Receptor(a) (recibe), declaración 2: la numeración no es consecutiva».
 *
 * @param {string} m
 */
export function etiquetaCadenaMotivo(m) {
  const mt = /^(\w+)#(\d+):\s*(.+)$/.exec(String(m ?? ""));
  if (!mt) return etiquetaMotivoEnlace(m);
  const rol = ROL_LABELS[mt[1]] ?? mt[1];
  const razon = etiquetaMotivoEnlace(mt[3]);
  return `${rol}, declaración ${mt[2]}: ${razon}`;
}

export const ETIQUETA_ARTEFACTO = {
  sello: "sello institucional",
  personal: "firma personal",
  sobre: "sobre firmado",
};
const ETIQUETA_ESTADO_ARTEFACTO = {
  protegido: "protegido",
  pendiente: "en espera de protección",
  agotado: "se agotaron los reintentos automáticos",
  omitido: "no aplica",
};
/** @param {string} nombre @param {string} estado */
export function etiquetaArtefacto(nombre, estado) {
  return `${ETIQUETA_ARTEFACTO[nombre] ?? nombre}: ${ETIQUETA_ESTADO_ARTEFACTO[estado] ?? estado}`;
}
