// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * radicar-page.test.js — Render de /radicar (+page.svelte, T-09).
 *
 * Cubre la migración del campo dinámico de metadatos del tipo documental
 * (E03) al componente compartido SchemaField (fuente única con el Form
 * Builder de expediente, `$lib/utils/schemaField.js`): antes del incremento
 * un `enum`/`boolean` de la plantilla degradaba a texto libre (→ 422 del
 * backend); ahora renderiza select y el `metadata` del submit va tipado.
 *
 * `$app/navigation` se mockea porque el componente se monta aislado (sin el
 * router real de SvelteKit) — mismo patrón que expedientes-page.test.js. El
 * flujo se conduce con docType="E" para no montar RichEditor (TipTap,
 * dependencia opcional solo usada en Salida/Interno).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/svelte";

const gotoMock = vi.fn();
vi.mock("$app/navigation", () => ({ goto: gotoMock }));

const { default: RadicarPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function sessionWith({ permissions = [] } = {}) {
  return {
    sub: "u-1",
    email: "op@orpyca.com",
    name: "Operador",
    preferred_username: "operador",
    roles: [],
    tenant_slug: "icetex",
    permissions,
  };
}

/** @param {Partial<{dependencias: any[], trdSeries: any[], tiposDocumentales: any[]}>} overrides */
function baseData(overrides = {}) {
  return {
    user: sessionWith({ permissions: ["PERM_RADI_ENTRADA"] }),
    dependencias: [],
    trdSeries: [],
    tiposDocumentales: [{ code: "RES", name: "Resolución" }],
    ...overrides,
  };
}

/** Instala un fetch mock que responde /radicar/api/templates con `templates`. */
function mockTemplatesFetch(templates) {
  return vi.fn(async (url) => {
    if (String(url).startsWith("/radicar/api/templates")) {
      return { ok: true, json: async () => templates };
    }
    return { ok: true, json: async () => [] };
  });
}

/** Selecciona docType="Entrada", llena Asunto/Remitente y avanza al paso 1. */
async function goToMetadataStep() {
  await fireEvent.click(screen.getByRole("radio", { name: /Entrada/i }));
  await fireEvent.click(screen.getByRole("button", { name: "Siguiente" }));
  await fireEvent.input(screen.getByLabelText("Asunto", { exact: false }), {
    target: { value: "Radicación de prueba" },
  });
  await fireEvent.input(screen.getByLabelText("Nombre del remitente", { exact: false }), {
    target: { value: "Juan Pérez" },
  });
}

/** Elige el tipo documental "Resolución" y espera a que cargue la plantilla. */
async function selectDocClass() {
  await fireEvent.change(screen.getByLabelText("Tipo documental", { exact: false }), {
    target: { value: "RES" },
  });
}

describe("/radicar — campo dinámico de metadatos vía SchemaField (E03/T-12)", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    clearSession();
    initSession(sessionWith({ permissions: ["PERM_RADI_ENTRADA"] }));
    gotoMock.mockClear();
  });
  afterEach(() => {
    clearSession();
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("un campo `enum` de la plantilla se renderiza como <select> (antes degradaba a texto libre)", async () => {
    globalThis.fetch = mockTemplatesFetch([
      {
        id: "tpl-1",
        activo: true,
        schema: {
          properties: { prioridad: { enum: ["alta", "media", "baja"], title: "Prioridad" } },
          required: [],
        },
      },
    ]);

    render(RadicarPage, { props: { data: baseData() } });
    await goToMetadataStep();
    await selectDocClass();

    const prioridadSelect = await screen.findByLabelText("Prioridad", { exact: false });
    expect(prioridadSelect.tagName).toBe("SELECT");
    expect(prioridadSelect).toHaveAttribute("name", "meta_prioridad");
    expect(within(prioridadSelect).getByRole("option", { name: "alta" })).toBeInTheDocument();
  });

  it("un campo `boolean` de la plantilla se renderiza como <select> Sí/No (antes degradaba a texto libre)", async () => {
    globalThis.fetch = mockTemplatesFetch([
      {
        id: "tpl-2",
        activo: true,
        schema: { properties: { urgente: { type: "boolean", title: "Urgente" } }, required: [] },
      },
    ]);

    render(RadicarPage, { props: { data: baseData() } });
    await goToMetadataStep();
    await selectDocClass();

    const urgenteSelect = await screen.findByLabelText("Urgente", { exact: false });
    expect(urgenteSelect.tagName).toBe("SELECT");
    expect(urgenteSelect).toHaveAttribute("name", "meta_urgente");
    expect(within(urgenteSelect).getByRole("option", { name: "Sí" })).toBeInTheDocument();
    expect(within(urgenteSelect).getByRole("option", { name: "No" })).toBeInTheDocument();
  });

  it("regresión: string/number/format:date siguen renderizando el MISMO control que antes de la migración", async () => {
    globalThis.fetch = mockTemplatesFetch([
      {
        id: "tpl-3",
        activo: true,
        schema: {
          properties: {
            motivo: { type: "string", title: "Motivo" },
            cantidad: { type: "integer", title: "Cantidad" },
            fecha_limite: { type: "string", format: "date", title: "Fecha límite" },
          },
          required: ["motivo"],
        },
      },
    ]);

    render(RadicarPage, { props: { data: baseData() } });
    await goToMetadataStep();
    await selectDocClass();

    const motivoInput = await screen.findByLabelText("Motivo", { exact: false });
    expect(motivoInput.tagName).toBe("INPUT");
    expect(motivoInput).toHaveAttribute("type", "text");

    const cantidadInput = screen.getByLabelText("Cantidad", { exact: false });
    expect(cantidadInput).toHaveAttribute("type", "number");

    const fechaInput = screen.getByLabelText("Fecha límite", { exact: false });
    expect(fechaInput).toHaveAttribute("type", "date");
  });

  it("una propiedad no soportada (object) NO se renderiza y muestra el aviso de campos no editables", async () => {
    globalThis.fetch = mockTemplatesFetch([
      {
        id: "tpl-4",
        activo: true,
        schema: {
          properties: {
            motivo: { type: "string", title: "Motivo" },
            anexos_extra: { type: "object", title: "Anexos extra", properties: {} },
          },
          required: [],
        },
      },
    ]);

    render(RadicarPage, { props: { data: baseData() } });
    await goToMetadataStep();
    await selectDocClass();

    await screen.findByLabelText("Motivo", { exact: false });
    expect(screen.queryByLabelText("Anexos extra", { exact: false })).not.toBeInTheDocument();
    expect(
      screen.getByText("1 campo de la plantilla no se puede editar en este formulario."),
    ).toBeInTheDocument();
  });

  it("el submit envía el metadata TIPADO: boolean como bool, number como number, string tal cual, y omite lo vacío/no soportado", async () => {
    const TEMPLATE = {
      id: "tpl-5",
      activo: true,
      schema: {
        properties: {
          motivo: { type: "string", title: "Motivo" },
          cantidad: { type: "integer", title: "Cantidad" },
          urgente: { type: "boolean", title: "Urgente" },
          prioridad: { enum: ["alta", "media", "baja"], title: "Prioridad" },
          fecha_limite: { type: "string", format: "date", title: "Fecha límite" },
          detalle: { type: "object", title: "Detalle", properties: {} },
        },
        required: ["motivo"],
      },
    };

    /** @type {any} */
    let capturedBody = null;
    globalThis.fetch = vi.fn(async (url, opts) => {
      const href = String(url);
      if (href.startsWith("/radicar/api/templates")) {
        return { ok: true, json: async () => [TEMPLATE] };
      }
      if (href === "/radicar/api/documents") {
        capturedBody = JSON.parse(opts.body);
        return { ok: true, json: async () => ({ tracking_number: "2026-ICETEX-E-000001" }) };
      }
      return { ok: true, json: async () => [] };
    });

    render(RadicarPage, { props: { data: baseData() } });
    await goToMetadataStep();
    await selectDocClass();

    await fireEvent.input(await screen.findByLabelText("Motivo", { exact: false }), {
      target: { value: "Solicitud de prueba" },
    });
    await fireEvent.input(screen.getByLabelText("Cantidad", { exact: false }), {
      target: { value: "5" },
    });
    await fireEvent.change(screen.getByLabelText("Urgente", { exact: false }), {
      target: { value: "true" },
    });
    await fireEvent.change(screen.getByLabelText("Prioridad", { exact: false }), {
      target: { value: "alta" },
    });
    // fecha_limite se deja vacío deliberadamente (debe omitirse del metadata).

    // Pasos 2 (Anexos), 3 (TRD) y 4 (Flujo/Destino): sin campos obligatorios.
    await fireEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    await fireEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    await fireEvent.click(screen.getByRole("button", { name: "Siguiente" }));

    await fireEvent.click(screen.getByRole("button", { name: "Radicar" }));
    await fireEvent.click(screen.getByRole("button", { name: "Confirmar radicación" }));

    expect(capturedBody).not.toBeNull();
    expect(capturedBody.metadata).toEqual({
      motivo: "Solicitud de prueba",
      cantidad: 5,
      urgente: true,
      prioridad: "alta",
    });
    expect(typeof capturedBody.metadata.cantidad).toBe("number");
    expect(typeof capturedBody.metadata.urgente).toBe("boolean");
    expect(capturedBody.metadata.fecha_limite).toBeUndefined();
    expect(capturedBody.metadata.detalle).toBeUndefined();
  });
});
