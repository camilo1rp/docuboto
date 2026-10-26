// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/preservacion/api/plan — Proxy SSR del plan de preservación digital
 * (D-05, E10) — `GET`/`PUT /preservacion/plan` (storage-service, RBAC
 * `USUA_PERM_EXPEDIENTE` min_crud 1/3 respectivamente).
 *
 * El plan es VERSIONADO (cada `PUT` crea una fila nueva, nunca actualiza in
 * place — ver `PreservacionRepository.insert_plan`): esta pantalla siempre
 * muestra la versión VIGENTE (`GET`), y "actualizar" es siempre "crear una
 * versión nueva".
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** Tipo MIME `tipo/subtipo` — mismo juego de caracteres que valida el backend
 * (`app/schemas/preservacion.py::_MIME_RE`, RFC 6838 §4.2). */
const MIME_RE = /^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}\/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$/;

export async function GET({ locals }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  try {
    const res = await fetch(`${API_V1}/preservacion/plan`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener el plan de preservación." },
      { status: 503 },
    );
  }
}

export async function PUT({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const {
    formatos_destino,
    formatos_origen,
    num_copias,
    periodicidad_fixity_dias,
    politica_migracion,
    contingencia,
  } = body ?? {};

  /** @type {Record<string, unknown>} */
  const payload = {};

  if (Array.isArray(formatos_destino)) {
    payload.formatos_destino = formatos_destino
      .filter((f) => typeof f === "string" && f.trim())
      .map((f) => f.trim().slice(0, 64));
  }
  // `formatos_origen` (RF-PRE-04): los tipos MIME que la entidad declara en
  // riesgo de obsolescencia. Se valida la FORMA aquí y no solo en el backend
  // porque un valor mal escrito —"msword", "application/ msword"— no casa con
  // ningún archivo: el barrido no convertiría nada y la política aparecería
  // como declarada en la pantalla. Lo peligroso no es convertir de más, es
  // creer que se convirtió.
  if (Array.isArray(formatos_origen)) {
    const limpios = [];
    for (const crudo of formatos_origen) {
      if (typeof crudo !== "string") continue;
      const valor = crudo.trim().toLowerCase();
      if (!valor) continue;
      if (!MIME_RE.test(valor)) {
        return json(
          {
            error: "validation_error",
            detail: `«${crudo}» no es un tipo MIME válido; se espera la forma tipo/subtipo (por ejemplo application/msword).`,
          },
          { status: 422 },
        );
      }
      if (!limpios.includes(valor)) limpios.push(valor);
    }
    payload.formatos_origen = limpios;
  }
  if (num_copias !== undefined && num_copias !== null && num_copias !== "") {
    const n = Number(num_copias);
    if (!Number.isFinite(n) || n < 1) {
      return json(
        { error: "validation_error", detail: "num_copias debe ser un entero >= 1." },
        { status: 422 },
      );
    }
    payload.num_copias = Math.trunc(n);
  }
  if (
    periodicidad_fixity_dias !== undefined &&
    periodicidad_fixity_dias !== null &&
    periodicidad_fixity_dias !== ""
  ) {
    const n = Number(periodicidad_fixity_dias);
    if (!Number.isFinite(n) || n < 1) {
      return json(
        { error: "validation_error", detail: "periodicidad_fixity_dias debe ser un entero >= 1." },
        { status: 422 },
      );
    }
    payload.periodicidad_fixity_dias = Math.trunc(n);
  }
  if (typeof politica_migracion === "string" && politica_migracion.trim()) {
    payload.politica_migracion = politica_migracion.trim().slice(0, 2000);
  }
  if (typeof contingencia === "string" && contingencia.trim()) {
    payload.contingencia = contingencia.trim().slice(0, 2000);
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };

  try {
    const res = await fetch(`${API_V1}/preservacion/plan`, {
      method: "PUT",
      headers,
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo actualizar el plan de preservación." },
      { status: 503 },
    );
  }
}
