// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/auditoria/+page.server.js — Carga SSR de la primera página del
 * registro de auditoría inmutable y la verificación inicial de la cadena de
 * hash (E08, ADR-008, `services/auth-service/app/routers/audit.py`).
 *
 * `GET /audit` (USUA_PERM_ADMIN a nivel de router) SÍ pagina con
 * `X-Total-Count`; los filtros/página posteriores del cliente van por
 * `admin/auditoria/api/*` (D-05). `GET /audit/verify` recalcula la cadena de
 * hash del tenant contra Postgres — es una operación real (no un booleano
 * cacheado), así que también se pide en el `load` inicial para que la
 * pantalla nazca con un veredicto verdadero, no un placeholder optimista.
 *
 * HALLAZGO DE DOMINIO **CERRADO** (2026-08-29, al revisar la traza de
 * lecturas). Este comentario decía que `AuditQueryRepository` (auth-service)
 * filtraba el listado SOLO por `tenant_slug` + `_FILTERABLE`, sin acotar por
 * clearance — de modo que un admin con clearance baja vería en el propio
 * `audit_log` la existencia de acciones sobre documentos clasificados. **Ya no
 * es cierto**: el repositorio aplica `_clearance_clause` (no-read-up,
 * RF-SEG-08) resolviendo el nivel EN VIVO contra las tablas dueñas, con una
 * lista de admisión para los tipos que no pueden referirse a un documento.
 *
 * Se deja escrito el cierre en vez de borrar el párrafo, porque el comentario
 * obsoleto **ya había sido vector aquí antes**: un aviso de agujero abierto que
 * en realidad está tapado manda al siguiente lector a auditar lo que ya está
 * bien —o, peor, a "arreglarlo" otra vez—. Lo que esta pantalla sigue sin hacer
 * es inventar un filtro de clearance en el cliente, y eso sí es a propósito: el
 * dato ya viajó al navegador antes de cualquier filtro JS, así que sería una
 * falsa sensación de seguridad. El recorte vive donde debe, en la fuente.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

const PAGE_SIZE = 20;

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  let entries = [];
  let totalCount = 0;
  let loadError = null;

  try {
    const res = await fetch(`${API_V1}/audit?page=1&size=${PAGE_SIZE}`, { headers });
    if (res.ok) {
      const raw = await res.json().catch(() => []);
      entries = Array.isArray(raw) ? raw : [];
      totalCount = Number(res.headers.get("X-Total-Count") ?? entries.length);
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    loadError = "No se pudo conectar al servidor de auditoría.";
  }

  // Verificación de la cadena de hash. `verifyResult = null` es un estado
  // DISTINTO de "íntegra" o "comprometida": significa que la verificación no
  // pudo ejecutarse (red caída, 5xx, etc.) y así debe comunicarlo la vista —
  // nunca como "todo correcto" por omisión (ver +page.svelte).
  let verifyResult = null;
  let verifyError = null;
  try {
    const verifyRes = await fetch(`${API_V1}/audit/verify`, { headers });
    if (verifyRes.ok) {
      // Un 200 con cuerpo ilegible NO es una respuesta: no dice ni que la
      // cadena esté íntegra ni que esté rota. Sin este guard, `verifyResult`
      // quedaría `null` con `verifyError` también `null`, y la vista lo
      // resolvería como "comprometida" — afirmar una ruptura que nadie
      // comprobó es tan falso como afirmar la integridad, y además dispara
      // una alarma de incidente sin incidente.
      verifyResult = await verifyRes.json().catch(() => null);
      if (verifyResult === null) {
        verifyError = "El servidor respondió con un resultado de verificación ilegible.";
      }
    } else {
      const body = await verifyRes.json().catch(() => ({}));
      verifyError = mensajeError(body, `Error ${verifyRes.status}`);
    }
  } catch {
    verifyError = "No se pudo conectar al servidor para verificar la cadena.";
  }

  return {
    entries,
    totalCount,
    pageSize: PAGE_SIZE,
    loadError,
    verifyResult,
    verifyError,
  };
}
