// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * interoperabilidad-page.test.js — Render de /admin/interoperabilidad
 * (+page.svelte), revisión UX 2026-08-02, lote 1, punto 2.
 *
 * Cubre:
 *  - Sin USUA_PERM_EXPEDIENTE ni PERM_RADI: EmptyState de "sin permiso".
 *  - Con SOLO PERM_RADI (radicador): la pantalla se abre y muestra
 *    ÚNICAMENTE la pestaña "Carga masiva de documentos" — las otras tres
 *    (Exportar/Importar/lote de expedientes) exigen USUA_PERM_EXPEDIENTE y
 *    NO deben ofrecerse (antes llevaban a un 403).
 *  - Con SOLO USUA_PERM_EXPEDIENTE: las tres pestañas de expediente aparecen
 *    y "Carga masiva de documentos" (PERM_RADI) NO aparece.
 *  - Con ambos permisos: las cuatro pestañas aparecen.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";

const { default: InteroperabilidadPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function sessionWith({ permissions = [] } = {}) {
  return {
    sub: "u-1",
    email: "operador@orpyca.com",
    name: "Operadora",
    preferred_username: "operadora",
    roles: [],
    tenant_slug: "icetex",
    permissions,
  };
}

describe("/admin/interoperabilidad — sin USUA_PERM_EXPEDIENTE ni PERM_RADI", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: [] }));
  });

  it("muestra el EmptyState de sin permiso, sin pestañas", () => {
    render(InteroperabilidadPage);
    expect(screen.getByText(/sin permiso/i)).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();
  });
});

describe("/admin/interoperabilidad — con SOLO PERM_RADI (radicador)", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["PERM_RADI"] }));
  });

  it("abre la pantalla y ofrece ÚNICAMENTE la pestaña de carga masiva de documentos", () => {
    render(InteroperabilidadPage);
    expect(screen.getByRole("tab", { name: /Carga masiva de documentos/i })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /^Exportar/i })).toBeNull();
    expect(screen.queryByRole("tab", { name: /^Importar/i })).toBeNull();
    expect(screen.queryByRole("tab", { name: /Carga masiva de expedientes/i })).toBeNull();
  });
});

describe("/admin/interoperabilidad — con SOLO USUA_PERM_EXPEDIENTE", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_EXPEDIENTE"] }));
  });

  it("ofrece Exportar/Importar/lote de expedientes, NO carga masiva de documentos", () => {
    render(InteroperabilidadPage);
    expect(screen.getByRole("tab", { name: /^Exportar/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /^Importar/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Carga masiva de expedientes/i })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /Carga masiva de documentos/i })).toBeNull();
  });
});

describe("/admin/interoperabilidad — con ambos permisos", () => {
  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["USUA_PERM_EXPEDIENTE", "PERM_RADI"] }));
  });

  it("ofrece las cuatro pestañas", () => {
    render(InteroperabilidadPage);
    expect(screen.getByRole("tab", { name: /^Exportar/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /^Importar/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Carga masiva de documentos/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Carga masiva de expedientes/i })).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Revisión UX 2026-08-02, lote 1, punto 1 (C1): regresión de reactividad
// Svelte 4. Antes el estado del sondeo vivía en un objeto plano mutado en
// sitio (`state.status = ...`) sin reasignar — el panel de progreso se
// congelaba y NUNCA mostraba el resultado del job. Este test crea un lote,
// deja que el poller reciba una respuesta y comprueba que el DOM refleja el
// nuevo estado (si el bug reapareciera, este `waitFor` haría timeout).
// ---------------------------------------------------------------------------
describe("/admin/interoperabilidad — carga masiva de documentos: el panel de progreso refleja el sondeo", () => {
  const JOB_ID = "22222222-2222-2222-2222-222222222222";

  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["PERM_RADI"] }));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("tras crear el lote, el panel de progreso muestra el estado devuelto por el sondeo", async () => {
    globalThis.fetch = vi.fn(async (url) => {
      const href = String(url);
      if (href.includes("/lotes/documentos/")) {
        return new Response(
          JSON.stringify({
            status: "processing",
            total_items: 1,
            processed_items: 1,
            failed_items: 0,
            items: [],
          }),
          { status: 200 },
        );
      }
      if (href.endsWith("/lotes/documentos")) {
        return new Response(
          JSON.stringify({ job_id: JOB_ID, job_type: "documents", status: "pending" }),
          {
            status: 202,
          },
        );
      }
      throw new Error(`fetch inesperado en el test: ${href}`);
    });

    render(InteroperabilidadPage);

    await fireEvent.click(screen.getByRole("tab", { name: /Carga masiva de documentos/i }));
    await fireEvent.input(screen.getByLabelText(/^Asunto/i), {
      target: { value: "Asunto de prueba" },
    });
    await fireEvent.click(screen.getByRole("button", { name: /^Crear 1 documento$/i }));
    await fireEvent.click(screen.getByRole("button", { name: /Confirmar y crear/i }));

    await waitFor(() => {
      expect(screen.getByText(/1 \/ 1 procesados · 0 con error/i)).toBeTruthy();
    });
  });
});

// ---------------------------------------------------------------------------
// Reanudación de lotes con errores: se muestra el fallo, se ofrece procesar los
// faltantes solo si el backend dice `reprocesable`, se confirma con la cuenta y
// el aviso de que lo radicado conserva su número, y se vuelve a sondear.
// ---------------------------------------------------------------------------
describe("/admin/interoperabilidad — lote con errores: procesar los faltantes", () => {
  const JOB_ID = "22222222-2222-2222-2222-222222222222";
  const conErrores = (extra = {}) => ({
    status: "completado_con_errores",
    total_items: 3,
    processed_items: 3,
    failed_items: 2,
    pending_items: 2,
    reprocesable: true,
    items: [
      { item_index: 0, status: "success", result_id: "2026-X-E-000001" },
      {
        item_index: 1,
        status: "failed",
        subject: "Oficio uno",
        error_message: "La dependencia destino no existe.",
        error_tipo: "validacion",
        intentos: 1,
      },
      {
        item_index: 2,
        status: "failed",
        subject: "Oficio dos",
        error_message: "asyncpg.exceptions.ConnectionDoesNotExistError: closed",
        intentos: 2,
      },
    ],
    ...extra,
  });

  async function crearLote(estado) {
    const llamadas = [];
    globalThis.fetch = vi.fn(async (url, init) => {
      const href = String(url);
      llamadas.push({ href, method: init?.method ?? "GET" });
      if (href.endsWith("/reprocesar")) {
        return new Response(
          JSON.stringify({ job_id: JOB_ID, status: "pending", items_a_procesar: 2 }),
          { status: 202 },
        );
      }
      if (href.includes("/lotes/documentos/")) {
        return new Response(JSON.stringify(estado), { status: 200 });
      }
      return new Response(JSON.stringify({ job_id: JOB_ID }), { status: 202 });
    });
    render(InteroperabilidadPage);
    await fireEvent.click(screen.getByRole("tab", { name: /Carga masiva de documentos/i }));
    await fireEvent.input(screen.getByLabelText(/^Asunto/i), { target: { value: "x" } });
    await fireEvent.click(screen.getByRole("button", { name: /^Crear 1 documento$/i }));
    await fireEvent.click(screen.getByRole("button", { name: /Confirmar y crear/i }));
    return llamadas;
  }

  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["PERM_RADI"] }));
  });
  afterEach(() => vi.restoreAllMocks());

  it("muestra el aviso, los fallidos con su motivo y el detalle técnico aparte", async () => {
    await crearLote(conErrores());
    await waitFor(() => expect(screen.getByText(/El lote terminó con errores/i)).toBeTruthy());
    expect(screen.getByText("La dependencia destino no existe.")).toBeTruthy();
    expect(screen.getByText("Oficio dos")).toBeTruthy();
    expect(screen.getAllByText(/Detalle técnico/i).length).toBeGreaterThan(0);
  });

  it("ofrece «Procesar los faltantes», confirma con la cuenta y vuelve a sondear", async () => {
    const llamadas = await crearLote(conErrores());
    const boton = await screen.findByRole("button", { name: /Procesar los faltantes/i });
    await fireEvent.click(boton);
    expect(screen.getByText(/Se procesarán 2 documentos/i)).toBeTruthy();
    expect(screen.getByText(/conservan su número/i)).toBeTruthy();
    await fireEvent.click(screen.getByRole("button", { name: /^Procesar 2 faltantes$/i }));
    await waitFor(() =>
      expect(llamadas.some((c) => c.method === "POST" && c.href.endsWith("/reprocesar"))).toBe(
        true,
      ),
    );
    // Tras el 202 se reinicia el sondeo: hay una consulta de estado posterior.
    const iPost = llamadas.findIndex((c) => c.href.endsWith("/reprocesar"));
    await waitFor(() =>
      expect(llamadas.slice(iPost + 1).some((c) => c.href.includes("/lotes/documentos/"))).toBe(
        true,
      ),
    );
  });

  it("un asunto null (radicado reservado) se lee «Asunto reservado», no un hueco", async () => {
    const est = conErrores();
    est.items[1].subject = null;
    est.errores = [est.items[1], est.items[2]];
    await crearLote(est);
    await waitFor(() => expect(screen.getByText("Asunto reservado")).toBeTruthy());
  });

  it("sin `reprocesable` no hay botón aunque haya fallidos", async () => {
    await crearLote(conErrores({ reprocesable: false }));
    await waitFor(() => expect(screen.getByText(/El lote terminó con errores/i)).toBeTruthy());
    expect(screen.queryByRole("button", { name: /Procesar los faltantes/i })).toBeNull();
  });
});
