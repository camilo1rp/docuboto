// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/interoperabilidad/api/lotes/expedientes — Proxy SSR de carga masiva
 * de expedientes (D-05, Fase 8).
 *
 * POST /api/v1/batch/expedientes — cuerpo {expedientes: BatchExpedienteCreate[]}
 * (1-100). 202 con job_id; progreso vía GET .../[jobId]/status.
 *
 * **Los radicados se vinculan por `radicado_ids`, y solo por ahí.** Este
 * docstring decía antes que «archive-service resuelve ambos», y era falso:
 * `tracking_numbers` es una lista de etiquetas PARALELA por índice a
 * `radicado_ids` (E15 perfil v4, R1), no una forma alternativa de referirse a
 * un radicado. La pantalla pedía «números de radicado a vincular», los mandaba
 * en `tracking_numbers` con `radicado_ids` vacío, y el lote terminaba
 * «Completado · 0 con error» habiendo creado un expediente sin un solo
 * radicado dentro. La frase se borra además de arreglar el código: mientras
 * siguiera escrita, el siguiente en leerla repetiría el fallo.
 *
 * Por eso este proxy RESUELVE aquí los números a UUID (`radicadoRef.js`) y
 * manda las dos listas alineadas. Es el mismo criterio de `expedienteRef.js`:
 * las pantallas hablan en números de radicado —la identidad oficial, Ac. AGN
 * 060/2001— porque el UUID no se muestra en ninguna parte del sistema.
 *
 * Gate real: `USUA_PERM_EXPEDIENTE`
 * (`services/archive-service/app/routers/batch.py`). El gateway
 * (`services/api-gateway/app/routers/proxy.py`) tiene registrado
 * `/api/v1/batch/expedientes` hacia archive-service.
 */

import { API_V1 } from "$lib/utils/config.server.js";
import { resolverRadicados } from "$lib/server/radicadoRef.js";
import { json } from "@sveltejs/kit";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  if (!userId) {
    return json(
      { error: "unauthenticated", detail: "Sesión sin identidad de usuario." },
      { status: 401 },
    );
  }

  let body = {};
  try {
    body = await request.json();
  } catch {
    return json({ error: "validation_error", detail: "Cuerpo JSON inválido." }, { status: 400 });
  }

  const rawExpedientes = Array.isArray(body.expedientes) ? body.expedientes : [];
  if (rawExpedientes.length < 1 || rawExpedientes.length > 100) {
    return json(
      { error: "validation_error", detail: "Debe incluir entre 1 y 100 expedientes." },
      { status: 400 },
    );
  }

  // Allowlist campo a campo — espejo de BatchExpedienteCreate (archive-service).
  const sinResolver = [];
  const expedientes = [];
  for (const e of rawExpedientes) {
    /** @type {Record<string, unknown>} */
    const item = { name: typeof e?.name === "string" ? e.name.trim() : "" };
    if (typeof e?.description === "string" && e.description.trim())
      item.description = e.description.trim();
    if (typeof e?.trd_serie_id === "string" && UUID_RE.test(e.trd_serie_id))
      item.trd_serie_id = e.trd_serie_id;

    // Referencias a radicados: se aceptan UUID y número, y salen SIEMPRE como
    // las dos listas paralelas que el backend espera. Un número que no
    // resuelve —no existe, o está por encima del clearance de quien opera— se
    // devuelve como error en vez de crear el expediente sin él: un expediente
    // al que le falta un documento no es un expediente incompleto, es uno
    // equivocado, y nadie lo iba a notar mirando «0 con error».
    const referencias = [
      ...(Array.isArray(e?.radicado_ids) ? e.radicado_ids : []),
      ...(Array.isArray(e?.tracking_numbers) ? e.tracking_numbers : []),
    ].filter((t) => typeof t === "string" && t.trim());

    if (referencias.length > 0) {
      const { ids, numeros, noResueltos } = await resolverRadicados(referencias, token, tenant);
      sinResolver.push(...noResueltos);
      if (ids.length > 0) {
        item.radicado_ids = ids;
        item.tracking_numbers = numeros;
      }
    }
    expedientes.push(item);
  }

  if (sinResolver.length > 0) {
    return json(
      {
        error: "radicado_no_resuelto",
        detail:
          `No se pudo identificar ${sinResolver.length === 1 ? "el radicado" : "los radicados"}: ` +
          `${sinResolver.join(", ")}. Compruebe el número, o si tiene acceso a ese documento.`,
      },
      { status: 400 },
    );
  }

  const invalid = expedientes.find(
    (e) => typeof e.name !== "string" || !e.name || e.name.length > 500,
  );
  if (invalid) {
    return json(
      {
        error: "validation_error",
        detail: "Cada expediente requiere un nombre (1-500 caracteres).",
      },
      { status: 400 },
    );
  }

  let res;
  try {
    res = await fetch(`${API_V1}/batch/expedientes`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId,
      },
      body: JSON.stringify({ expedientes }),
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de carga masiva." },
      { status: 503 },
    );
  }

  const data = await res.json().catch(() => ({
    error: "upstream_error",
    detail: `HTTP ${res.status}`,
    status: res.status,
  }));
  return json(data, { status: res.status });
}
