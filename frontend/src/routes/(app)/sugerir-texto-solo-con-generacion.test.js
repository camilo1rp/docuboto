// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * sugerir-texto-solo-con-generacion.test.js — «Sugerir con antecedentes» en
 * /radicar y /borradores aparece solo si la pasarela de conocimiento tiene
 * generación para la entidad (ADR-046 §3, fase D0).
 *
 * En modo `local` `/rag` responde 503 `rag_unavailable`: mostrar el botón sería
 * ofrecer algo que siempre falla. Las cargas SSR preguntan a
 * `GET /knowledge/availability` y la vista obedece a `data.generationAvailable`.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));

const { load: loadRadicar } = await import("./radicar/+page.server.js");
const { load: loadBorradores } = await import("./borradores/+page.server.js");
const { default: RadicarPage } = await import("./radicar/+page.svelte");
const { default: BorradoresPage } = await import("./borradores/+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

const BOTON = { name: /sugerir con antecedentes/i };

const USUARIO = {
  sub: "u-1",
  email: "op@orpyca.com",
  name: "Operador",
  preferred_username: "operador",
  roles: [],
  tenant_slug: "demo",
  permissions: ["PERM_RADI", "PERM_RADI_SALIDA"],
  asistente_habilitado: true,
  permission_levels: { PERM_CONOCIMIENTO: 1 },
};

/** fetch de carga: catálogos vacíos y `availability` según el caso. */
function fetchDeCarga(availability) {
  return vi.fn(async (url) => {
    if (String(url).endsWith("/knowledge/availability")) return availability();
    return { ok: true, status: 200, json: async () => [] };
  });
}
const ok = (cuerpo) => () => ({ ok: true, status: 200, json: async () => cuerpo });

function evento(fetch, user = USUARIO) {
  return {
    cookies: { get: () => "tok" },
    parent: async () => ({ user }),
    url: new URL("http://localhost/borradores"),
    fetch,
  };
}

describe("cargas SSR — generationAvailable", () => {
  for (const [nombre, load] of [
    ["radicar", loadRadicar],
    ["borradores", loadBorradores],
  ]) {
    it(`${nombre}: generation:true llega a la vista, con el token del usuario (control positivo)`, async () => {
      const f = fetchDeCarga(ok({ generation: true, mode: "ppi" }));
      const data = await load(evento(f));
      expect(data.generationAvailable).toBe(true);
      const [, init] = f.mock.calls.find(([u]) => String(u).endsWith("/knowledge/availability"));
      expect(init.headers).toMatchObject({ Authorization: "Bearer tok", "X-Tenant-Slug": "demo" });
    });

    it(`${nombre}: modo local, 403 o red caída dejan generationAvailable en false`, async () => {
      for (const respuesta of [
        ok({ generation: false, mode: "local" }),
        () => ({ ok: false, status: 403, json: async () => ({}) }),
        () => Promise.reject(new TypeError("fetch failed")),
      ]) {
        const data = await load(evento(fetchDeCarga(respuesta)));
        expect(data.generationAvailable).toBe(false);
      }
    });
  }
});

describe("vistas — el botón obedece a generationAvailable", () => {
  const original = globalThis.fetch;
  beforeEach(() => {
    clearSession();
    initSession(USUARIO);
    globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => [] }));
  });
  afterEach(() => {
    clearSession();
    globalThis.fetch = original;
  });

  async function abrirCuerpoRadicar(generationAvailable) {
    render(RadicarPage, {
      props: {
        data: {
          user: USUARIO,
          dependencias: [],
          trdSeries: [],
          tiposDocumentales: [],
          generationAvailable,
        },
      },
    });
    await fireEvent.click(screen.getByRole("radio", { name: /Salida/i }));
    await fireEvent.click(screen.getByRole("button", { name: "Siguiente" }));
    // El editor del cuerpo marca que estamos en el paso donde vive el botón.
    await screen.findByText(/Contenido \/ cuerpo del documento/i);
  }

  async function abrirBorrador(generationAvailable) {
    render(BorradoresPage, {
      props: {
        data: {
          borradores: [],
          plantillas: [],
          dependencias: [],
          dependenciasError: null,
          tiposDocumentales: [],
          tiposError: null,
          loadError: null,
          initialEstado: "",
          generationAvailable,
        },
      },
    });
    await fireEvent.click(screen.getByRole("button", { name: /Nuevo borrador/i }));
    await screen.findByRole("dialog", { name: /Nuevo borrador/i });
  }

  it("radicar con generación: se ofrece sugerir (control positivo)", async () => {
    await abrirCuerpoRadicar(true);
    expect(screen.getByRole("button", BOTON)).toBeInTheDocument();
  });

  it("radicar sin generación: no hay botón", async () => {
    await abrirCuerpoRadicar(false);
    expect(screen.queryByRole("button", BOTON)).toBeNull();
  });

  it("borradores con generación: se ofrece sugerir (control positivo)", async () => {
    await abrirBorrador(true);
    expect(screen.getByRole("button", BOTON)).toBeInTheDocument();
  });

  it("borradores sin generación: no hay botón", async () => {
    await abrirBorrador(false);
    expect(screen.queryByRole("button", BOTON)).toBeNull();
  });
});
