// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxy SSR del catálogo de terceros (E01, RF-RAD-05).
 *
 * `GET` busca (sin tildes ni mayúsculas, en el backend) y `POST` da de alta.
 *
 * **El `creado: false` del alta se reenvía tal cual**, y es la parte que más
 * importa: dice que ya existía una ficha con ese documento y que se está
 * reutilizando en vez de crear otra. Colapsarlo a «creado» dejaría a quien
 * registra creyendo que hizo una ficha nueva — que es exactamente la confusión
 * de la que salieron los treinta «José Pérez» del Orfeo original.
 */
import { json } from "@sveltejs/kit";

import { API_V1 } from "$lib/utils/config.server.js";

const TIPOS = new Set([
  "ciudadano",
  "empresa",
  "direccion_libre",
  "usuario_interno",
  "entidad_publica",
]);

/** @param {any} locals */
function cabeceras(locals, conCuerpo = false) {
  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${locals.accessToken}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
  };
  if (locals.user?.sub) headers["X-User-Id"] = locals.user.sub;
  if (conCuerpo) headers["Content-Type"] = "application/json";
  return headers;
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url, fetch }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const params = new URLSearchParams();
  const q = url.searchParams.get("q");
  if (q) params.set("q", q.slice(0, 120));
  const tipo = url.searchParams.get("tipo");
  if (tipo && TIPOS.has(tipo)) params.set("tipo", tipo);
  params.set("limit", "20");

  try {
    const res = await fetch(`${API_V1}/terceros?${params}`, { headers: cabeceras(locals) });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al catálogo de terceros." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request, fetch }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const body = await request.json().catch(() => ({}));

  if (!TIPOS.has(body?.tipo)) {
    return json(
      { error: "validation_error", detail: "Tipo de tercero inválido." },
      { status: 400 },
    );
  }
  const nombre = typeof body?.nombre === "string" ? body.nombre.trim() : "";
  if (nombre.length < 2) {
    return json({ error: "validation_error", detail: "Indica el nombre." }, { status: 400 });
  }
  // El tipo y el número de documento van juntos o ninguno: uno sin el otro no
  // identifica a nadie, y dejarlo pasar crearía una ficha que PARECE
  // identificada y no participa de la deduplicación.
  const tipoDoc = body?.tipo_documento || null;
  const numDoc = body?.numero_documento || null;
  if (Boolean(tipoDoc) !== Boolean(numDoc)) {
    return json(
      {
        error: "validation_error",
        detail: "El tipo y el número de documento van juntos o ninguno.",
      },
      { status: 400 },
    );
  }

  const payload = {
    tipo: body.tipo,
    nombre,
    entidad: body?.entidad || null,
    tipo_documento: tipoDoc,
    numero_documento: numDoc,
    email: body?.email || null,
    telefono: body?.telefono || null,
    direccion: body?.direccion || null,
    municipio: body?.municipio || null,
    departamento: body?.departamento || null,
    // Los códigos DIVIPOLA se validan por FORMA aquí y por CATÁLOGO en el
    // backend. La forma se comprueba antes para no gastar un viaje en «11001x»;
    // que el código EXISTA solo lo sabe el catálogo, y ahí es donde se decide.
    municipio_codigo: /^[0-9]{5}$/.test(body?.municipio_codigo ?? "")
      ? body.municipio_codigo
      : null,
    departamento_codigo: /^[0-9]{2}$/.test(body?.departamento_codigo ?? "")
      ? body.departamento_codigo
      : null,
  };

  try {
    const res = await fetch(`${API_V1}/terceros`, {
      method: "POST",
      headers: cabeceras(locals, true),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al catálogo de terceros." },
      { status: 503 },
    );
  }
}
