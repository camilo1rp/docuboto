// SPDX-License-Identifier: AGPL-3.0-or-later
// OrpycaMCP — https://orpyca.com

/**
 * Nivel de `USUA_PERM_ADMIN` que el backend exige (min_crud=3) en toda escritura de
 * dependencias, catálogos y PINAR. Con un nivel menor esas pantallas quedan de solo lectura.
 */
import { codigoError, mensajeError } from "$lib/utils/apiError.js";

export const NIVEL_ADMIN_ESCRITURA = 3;

/**
 * Espejo del gate de escritura administrativa. Fail-closed: sin nivel conocido, no se ofrece.
 * @param {Record<string, number> | null | undefined} levels
 */
export function puedeEscribirAdmin(levels) {
  return (levels?.["USUA_PERM_ADMIN"] ?? 0) >= NIVEL_ADMIN_ESCRITURA;
}

/** Texto en español para un 403 de escritura administrativa. */
export const MENSAJE_NIVEL_ADMIN =
  "Esta acción exige administración a nivel 3 (crear, editar y borrar). Su nivel actual no alcanza.";

/**
 * Mensaje de un fallo de escritura administrativa: el 403 se explica con el nivel exigido.
 * @param {any} err cuerpo del error (plano o con sobre doble)
 * @param {string} fallback
 */
export function mensajeEscrituraAdmin(err, fallback) {
  const status = err?.status ?? err?.detail?.status;
  if (status === 403 || codigoError(err) === "forbidden") return MENSAJE_NIVEL_ADMIN;
  return mensajeError(err, fallback);
}
