// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Modal «Cambiar estado» de /envios con el acuse de recibo (triage 24).
 *
 * El proxy ya tenía su test; faltaba el componente: que «entregado» exija el
 * archivo, que lo suba y mande su huella, y que 403 acuse_ajeno / 409
 * acuse_ya_referenciado lleguen a la persona en español (no el código crudo).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { get } from "svelte/store";

const { default: Pagina } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");
const { toasts } = await import("$lib/stores/notifications.js");

const ENVIO = {
  id: "env-1",
  operador: "servientrega",
  guia: "G-123",
  estado: "en_transito",
  destinatario: "Ana",
  created_at: "2026-10-01T10:00:00Z",
  eventos: [],
};

function respuesta(status, cuerpo) {
  return { ok: status < 400, status, json: async () => cuerpo };
}

/** Abre el modal, elige «entregado» y adjunta un PDF. */
async function prepararEntrega({ conArchivo = true } = {}) {
  render(Pagina, {
    props: {
      data: { shipments: [ENVIO], total: 1, causales: [], initialFilters: {}, loadError: null },
    },
  });
  await fireEvent.click(screen.getByRole("button", { name: /Ver detalle del envío G-123/i }));
  await fireEvent.click(await screen.findByRole("button", { name: /Cambiar estado/i }));
  const select = await screen.findByLabelText(/Nuevo estado/i);
  await fireEvent.change(select, { target: { value: "entregado" } });
  if (conArchivo) {
    const input = await screen.findByLabelText(/Acuse de recibo/i);
    const archivo = new File(["%PDF"], "acuse.pdf", { type: "application/pdf" });
    await fireEvent.change(input, { target: { files: [archivo] } });
  }
}

const aplicar = () => fireEvent.click(screen.getByRole("button", { name: /Aplicar/i }));
const mensajes = () => get(toasts).map((t) => t.message);

describe("/envios — modal de acuse de recibo", () => {
  const original = globalThis.fetch;
  beforeEach(() => {
    clearSession();
    initSession({
      sub: "s1",
      user_id: "u1",
      email: "a@b.co",
      name: "A",
      roles: [],
      tenant_slug: "demo",
      permissions: ["PERM_RADI_SALIDA"],
    });
    toasts.set([]);
  });
  afterEach(() => {
    globalThis.fetch = original;
  });

  function simular({ estado }) {
    const f = vi.fn(async (url) => {
      if (String(url).startsWith("/radicar/api/storage")) {
        return respuesta(201, { id: "file-1", sha256: "abc" });
      }
      if (String(url).endsWith("/estado")) return estado;
      return respuesta(200, ENVIO); // detalle del drawer
    });
    globalThis.fetch = f;
    return f;
  }

  it("sin archivo no envía nada y pide el acuse", async () => {
    const f = simular({ estado: respuesta(200, ENVIO) });
    await prepararEntrega({ conArchivo: false });
    await aplicar();
    expect(await screen.findByText(/Adjunte el acuse de recibo/i)).toBeTruthy();
    expect(f.mock.calls.some(([u]) => String(u).endsWith("/estado"))).toBe(false);
  });

  it("sube el archivo y envía su id y huella al cambiar el estado", async () => {
    const f = simular({ estado: respuesta(200, { ...ENVIO, estado: "entregado" }) });
    await prepararEntrega();
    await aplicar();
    await waitFor(() =>
      expect(f.mock.calls.some(([u]) => String(u).endsWith("/estado"))).toBe(true),
    );
    const [, init] = f.mock.calls.find(([u]) => String(u).endsWith("/estado"));
    const cuerpo = JSON.parse(init.body);
    expect(cuerpo).toMatchObject({
      estado: "entregado",
      acuse_file_id: "file-1",
      acuse_sha256: "abc",
    });
  });

  it("403 acuse_ajeno se muestra en español", async () => {
    simular({ estado: respuesta(403, { error: "acuse_ajeno", detail: "x", status: 403 }) });
    await prepararEntrega();
    await aplicar();
    await waitFor(() => expect(mensajes().join(" ")).toMatch(/no sirve como acuse/i));
    expect(mensajes().join(" ")).not.toMatch(/acuse_ajeno/);
  });

  it("409 acuse_ya_referenciado se muestra en español", async () => {
    simular({
      estado: respuesta(409, { error: "acuse_ya_referenciado", detail: "x", status: 409 }),
    });
    await prepararEntrega();
    await aplicar();
    await waitFor(() => expect(mensajes().join(" ")).toMatch(/ya está ligado a otro/i));
  });
});
