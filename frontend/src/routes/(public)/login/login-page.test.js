// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * login-page.test.js — /login: el marcado del formulario y su acción (ADR-048).
 *
 * El formulario envía usuario y contraseña a la acción del servidor, que llama
 * a `POST /api/v1/auth/login` y guarda la sesión en cookies httpOnly.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/svelte";
import LoginPage from "./+page.svelte";

vi.mock("$app/environment", () => ({ dev: true }));
vi.mock("$env/dynamic/private", () => ({ env: { INTERNAL_API_URL: "http://app:8000" } }));

describe("/login — formulario", () => {
  it("pide usuario y contraseña y envía por POST", () => {
    const { container } = render(LoginPage, { data: {}, form: null });
    expect(screen.getByLabelText("Usuario")).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText("Contraseña")).toHaveAttribute("type", "password");
    expect(container.querySelector("form")).toHaveAttribute("method", "POST");
    expect(screen.queryByLabelText("Entidad")).not.toBeInTheDocument();
  });

  it("pide la entidad solo si el despliegue no la fija", () => {
    render(LoginPage, { data: { pedirEntidad: true }, form: null });
    expect(screen.getByLabelText("Entidad")).toBeInTheDocument();
  });

  it("muestra el error con role=alert y conserva el usuario", () => {
    render(LoginPage, {
      data: {},
      form: { error: "Usuario o contraseña incorrectos.", username: "operador" },
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Usuario o contraseña incorrectos.");
    expect(screen.getByLabelText("Usuario")).toHaveValue("operador");
  });
});

describe("/login — acción del servidor", () => {
  /** @type {any} */
  let mod;
  /** @type {Map<string, string>} */
  let tarro;
  const cookies = {
    get: (/** @type {string} */ k) => tarro.get(k),
    set: (/** @type {string} */ k, /** @type {string} */ v) => tarro.set(k, v),
    delete: (/** @type {string} */ k) => tarro.delete(k),
  };

  beforeEach(async () => {
    vi.resetModules();
    tarro = new Map();
    mod = await import("./+page.server.js");
  });

  /** @param {Record<string, string>} campos */
  function evento(campos, next = "") {
    const fd = new FormData();
    for (const [k, v] of Object.entries(campos)) fd.set(k, v);
    return {
      request: { formData: async () => fd },
      cookies,
      url: new URL(`http://localhost/login${next ? `?next=${encodeURIComponent(next)}` : ""}`),
    };
  }

  it("con credenciales buenas guarda las cookies y redirige a next interno", async () => {
    const access = `x.${btoa(JSON.stringify({ exp: Date.now() / 1000 + 900 }))}.y`;
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ access_token: access, refresh_token: "demo.r", expires_in: 900 }),
        {
          status: 200,
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(
      mod.actions.default(
        evento({ username: "operador", password: "p", tenant: "demo" }, "/bandeja"),
      ),
    ).rejects.toMatchObject({ status: 303, location: "/bandeja" });
    expect(fetchMock.mock.calls[0][0]).toBe("http://app:8000/api/v1/auth/login");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      username: "operador",
      password: "p",
      tenant_slug: "demo",
    });
    expect(tarro.get("op_access_token")).toBe(access);
    expect(tarro.get("op_refresh_token")).toBe("demo.r");
    vi.unstubAllGlobals();
  });

  it("un next externo no convierte el login en redirector abierto", async () => {
    const access = `x.${btoa(JSON.stringify({ exp: Date.now() / 1000 + 900 }))}.y`;
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ access_token: access, refresh_token: "r", expires_in: 900 }),
          ),
        ),
    );
    await expect(
      mod.actions.default(
        evento({ username: "u", password: "p", tenant: "demo" }, "//evil.example"),
      ),
    ).rejects.toMatchObject({ location: "/dashboard" });
    vi.unstubAllGlobals();
  });

  it("credenciales malas: 401 con mensaje en español y sin cookies", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: { error: "invalid_credentials" } }), {
          status: 401,
        }),
      ),
    );
    const r = await mod.actions.default(evento({ username: "u", password: "mal", tenant: "demo" }));
    expect(r.status).toBe(401);
    expect(r.data.error).toBe("Usuario o contraseña incorrectos.");
    expect(tarro.size).toBe(0);
    vi.unstubAllGlobals();
  });

  it("cuenta bloqueada: lo dice", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ detail: { error: "account_locked" } }), { status: 423 }),
        ),
    );
    const r = await mod.actions.default(evento({ username: "u", password: "p", tenant: "demo" }));
    expect(r.data.error).toMatch(/bloqueada/);
    vi.unstubAllGlobals();
  });
});
