// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * SignAssistant.test.js — Asistente de firma con segundo factor (E06 Inc.2,
 * RF-FIR-13, Fase 3 cierre de desfase API↔UI).
 *
 * Antes de este componente, el reto OTP era INALCANZABLE desde la UI: estos
 * tests son el contrato de que ahora sí lo es, y de que el componente nunca
 * confunde un fallo de red con éxito ni con "sin firmas".
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import SignAssistant from "./SignAssistant.svelte";

const SOLICITUD_ID = "11111111-1111-1111-1111-111111111111";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SignAssistant — camino rápido (sin segundo factor, objeto PUBLICA)", () => {
  it("firma en un solo paso y emite 'signed' con la solicitud actualizada", async () => {
    globalThis.fetch = vi.fn(async (input) => {
      expect(String(input)).toContain(`/firmas/api/${SOLICITUD_ID}/sign`);
      return new Response(JSON.stringify({ solicitud_id: SOLICITUD_ID, estado: "firmado" }), {
        status: 200,
      });
    });

    const { component } = render(SignAssistant, {
      props: { open: true, solicitudId: SOLICITUD_ID, titulo: "Radicado 2026-1" },
    });
    const onSigned = vi.fn();
    component.$on("signed", onSigned);

    await fireEvent.click(screen.getByRole("button", { name: "Firmar" }));

    await waitFor(() => expect(onSigned).toHaveBeenCalledOnce());
    expect(onSigned.mock.calls[0][0].detail).toEqual({
      solicitud_id: SOLICITUD_ID,
      estado: "firmado",
    });
    expect(screen.getByText("Documento firmado correctamente.")).toBeInTheDocument();
  });
});

describe("SignAssistant — segundo factor OTP (E06 Inc.2)", () => {
  it("409 2fa_requerido dispara el reto automáticamente y pasa al paso de verificación", async () => {
    let call = 0;
    globalThis.fetch = vi.fn(async (input) => {
      call += 1;
      if (String(input).endsWith("/sign")) {
        return new Response(JSON.stringify({ error: "2fa_requerido", status: 409 }), {
          status: 409,
        });
      }
      if (String(input).endsWith("/challenge")) {
        return new Response(
          JSON.stringify({
            challenge_id: "chal-1",
            factor_tipo: "otp_email",
            canal: "j***@dominio.gov.co",
            expira_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
          }),
          { status: 201 },
        );
      }
      throw new Error(`llamada inesperada #${call}: ${input}`);
    });

    render(SignAssistant, {
      props: { open: true, solicitudId: SOLICITUD_ID, titulo: "Radicado reservado" },
    });

    await fireEvent.click(screen.getByRole("button", { name: "Firmar" }));

    await waitFor(() => expect(screen.getByText(/Enviamos un código a/)).toBeInTheDocument());
    expect(screen.getByText("j***@dominio.gov.co")).toBeInTheDocument();
  });

  it("otp_incorrecto (401) muestra el mensaje y los intentos restantes, no avanza al comprobante", async () => {
    let signCalls = 0;
    globalThis.fetch = vi.fn(async (input) => {
      const u = String(input);
      if (u.endsWith("/challenge")) {
        return new Response(
          JSON.stringify({
            challenge_id: "chal-1",
            factor_tipo: "otp_email",
            canal: "j***@dominio.gov.co",
            expira_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
          }),
          { status: 201 },
        );
      }
      if (u.endsWith("/sign")) {
        signCalls += 1;
        // Primer intento (sin factor): el backend exige el reto.
        if (signCalls === 1) {
          return new Response(JSON.stringify({ error: "2fa_requerido", status: 409 }), {
            status: 409,
          });
        }
        // Segundo intento (con challenge_id/otp): código incorrecto.
        return new Response(
          JSON.stringify({ error: "otp_incorrecto", intentos_restantes: 2, status: 401 }),
          { status: 401 },
        );
      }
      throw new Error(`llamada inesperada: ${u}`);
    });

    render(SignAssistant, {
      props: { open: true, solicitudId: SOLICITUD_ID, titulo: "Radicado reservado" },
    });

    await fireEvent.click(screen.getByRole("button", { name: "Firmar" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Código de verificación")).toBeInTheDocument(),
    );

    await fireEvent.input(screen.getByLabelText("Código de verificación"), {
      target: { value: "000000" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Confirmar código" }));

    await waitFor(() =>
      expect(screen.getByText(/El código introducido no es correcto/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/2 intentos restantes/)).toBeInTheDocument();
    // Nunca debe aparecer el comprobante de éxito ante un OTP incorrecto.
    expect(screen.queryByText("Documento firmado correctamente.")).not.toBeInTheDocument();
  });

  it("fallo de red al confirmar el código NUNCA se interpreta como éxito", async () => {
    let signAttempts = 0;
    globalThis.fetch = vi.fn(async (input) => {
      const u = String(input);
      if (u.endsWith("/challenge")) {
        return new Response(
          JSON.stringify({
            challenge_id: "chal-1",
            factor_tipo: "otp_email",
            canal: "j***@dominio.gov.co",
            expira_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
          }),
          { status: 201 },
        );
      }
      if (u.endsWith("/sign")) {
        signAttempts += 1;
        // Primer intento (sin factor): el backend exige el reto — pasa al
        // paso de verificación. Solo el SEGUNDO intento (tras introducir el
        // OTP) simula el fallo de red que este test verifica.
        if (signAttempts === 1) {
          return new Response(JSON.stringify({ error: "2fa_requerido", status: 409 }), {
            status: 409,
          });
        }
        throw new Error("network down");
      }
      throw new Error(`llamada inesperada: ${u}`);
    });

    const { component } = render(SignAssistant, {
      props: { open: true, solicitudId: SOLICITUD_ID, titulo: "Radicado reservado" },
    });
    const onSigned = vi.fn();
    component.$on("signed", onSigned);

    await fireEvent.click(screen.getByRole("button", { name: "Firmar" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Código de verificación")).toBeInTheDocument(),
    );

    await fireEvent.input(screen.getByLabelText("Código de verificación"), {
      target: { value: "123456" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Confirmar código" }));

    await waitFor(() =>
      expect(screen.getByText(/No se pudo conectar al servidor de firmas/)).toBeInTheDocument(),
    );
    expect(signAttempts).toBe(2);
    expect(onSigned).not.toHaveBeenCalled();
    expect(screen.queryByText("Documento firmado correctamente.")).not.toBeInTheDocument();
  });
});

describe("SignAssistant — segundo factor TOTP (credencial personal activa)", () => {
  it("409 totp_requerido NO pide ningún código por correo y firma con totp_code", async () => {
    /** @type {string[]} */
    const rutas = [];
    /** @type {any[]} */
    const cuerpos = [];
    globalThis.fetch = vi.fn(async (input, init) => {
      rutas.push(String(input));
      if (String(input).endsWith("/sign")) {
        cuerpos.push(JSON.parse(String(init?.body ?? "{}")));
        if (cuerpos.length === 1) {
          return new Response(JSON.stringify({ error: "totp_requerido", status: 409 }), {
            status: 409,
          });
        }
        return new Response(JSON.stringify({ solicitud_id: SOLICITUD_ID, estado: "firmado" }), {
          status: 200,
        });
      }
      throw new Error(`petición inesperada: ${input}`);
    });

    const { component } = render(SignAssistant, {
      props: { open: true, solicitudId: SOLICITUD_ID, titulo: "Cierre de expediente" },
    });
    const onSigned = vi.fn();
    component.$on("signed", onSigned);

    await fireEvent.click(screen.getByRole("button", { name: "Firmar" }));

    // La aserción que importa: NINGUNA llamada a /challenge. Emitir un OTP por
    // correo aquí es justamente el bucle que dejaba sin firmar a todo firmante
    // con credencial activa — el backend no lo iba a aceptar nunca.
    await waitFor(() =>
      expect(screen.getByLabelText("Código de la app autenticadora")).toBeInTheDocument(),
    );
    expect(rutas.some((r) => r.endsWith("/challenge"))).toBe(false);

    await fireEvent.input(screen.getByLabelText("Código de la app autenticadora"), {
      target: { value: "123456" },
    });
    await fireEvent.click(screen.getByRole("button", { name: /Confirmar código/ }));

    await waitFor(() => expect(onSigned).toHaveBeenCalledOnce());
    expect(cuerpos[1]).toEqual({ totp_code: "123456" });
    expect(rutas.some((r) => r.endsWith("/challenge"))).toBe(false);
  });

  it("401 totp_incorrecto deja al firmante en el mismo paso, sin emitir correo", async () => {
    /** @type {string[]} */
    const rutas = [];
    let intentos = 0;
    globalThis.fetch = vi.fn(async (input) => {
      rutas.push(String(input));
      if (String(input).endsWith("/sign")) {
        intentos += 1;
        if (intentos === 1) {
          return new Response(JSON.stringify({ error: "totp_requerido", status: 409 }), {
            status: 409,
          });
        }
        return new Response(
          JSON.stringify({ error: "totp_incorrecto", intentos_restantes: 2, status: 401 }),
          { status: 401 },
        );
      }
      throw new Error(`petición inesperada: ${input}`);
    });

    render(SignAssistant, {
      props: { open: true, solicitudId: SOLICITUD_ID, titulo: "Cierre de expediente" },
    });

    await fireEvent.click(screen.getByRole("button", { name: "Firmar" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Código de la app autenticadora")).toBeInTheDocument(),
    );
    await fireEvent.input(screen.getByLabelText("Código de la app autenticadora"), {
      target: { value: "000000" },
    });
    await fireEvent.click(screen.getByRole("button", { name: /Confirmar código/ }));

    await waitFor(() => expect(screen.getByText(/no es correcto/)).toBeInTheDocument());
    expect(screen.getByText(/2 intentos restantes/)).toBeInTheDocument();
    expect(rutas.some((r) => r.endsWith("/challenge"))).toBe(false);
  });
});
/**
 * El factor más fuerte estaba construido y sin ofrecer (B6, §7.7.bis del plan
 * de pruebas): `operador` tenía TOTP activo y su firma de cadena salía
 * igualmente por correo, porque la rama TOTP del backend solo se entra si el
 * cliente manda el código — y esta pantalla nunca lo mandaba. El backend SÍ lo
 * admite aquí (`cadena_service._gate_2fa`: `totp_code` presente gana siempre).
 */
describe("SignAssistant — 2fa_requerido con app autenticadora activa", () => {
  /** Mock de fetch con estado TOTP configurable. */
  function conEstadoTotp(estado, { onSign } = {}) {
    const llamadas = [];
    globalThis.fetch = vi.fn(async (input, init) => {
      const url = String(input);
      llamadas.push(url);
      if (url === "/perfil/api/totp") {
        return new Response(JSON.stringify({ estado }), { status: 200 });
      }
      if (url.endsWith("/sign")) {
        const body = init?.body ? JSON.parse(init.body) : {};
        if (onSign) {
          const r = onSign(body);
          if (r) return r;
        }
        if (body.totp_code || body.otp) {
          return new Response(JSON.stringify({ solicitud_id: SOLICITUD_ID, estado: "firmado" }), {
            status: 200,
          });
        }
        return new Response(JSON.stringify({ error: "2fa_requerido", status: 409 }), {
          status: 409,
        });
      }
      if (url.endsWith("/challenge")) {
        return new Response(
          JSON.stringify({
            challenge_id: "chal-9",
            factor_tipo: "otp_email",
            canal: "o***@dominio.gov.co",
            expira_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
          }),
          { status: 200 },
        );
      }
      throw new Error(`URL inesperada: ${url}`);
    });
    return llamadas;
  }

  it("no emite ningún reto por correo y pide el código de la app", async () => {
    const llamadas = conEstadoTotp("activo");

    render(SignAssistant, {
      props: { open: true, solicitudId: SOLICITUD_ID, titulo: "R-1" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Firmar" }));

    await screen.findByLabelText("Código de la app autenticadora");
    // Lo que NO debe haber ocurrido: ni un desafío emitido ni un correo salido.
    expect(llamadas.some((u) => u.endsWith("/challenge"))).toBe(false);
  });

  it("manda el código como totp_code, no como otp", async () => {
    let enviado = null;
    conEstadoTotp("activo", {
      onSign: (body) => {
        if (body.totp_code || body.otp) enviado = body;
        return null;
      },
    });

    render(SignAssistant, {
      props: { open: true, solicitudId: SOLICITUD_ID, titulo: "R-1" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Firmar" }));

    const input = await screen.findByLabelText("Código de la app autenticadora");
    await fireEvent.input(input, { target: { value: "123456" } });
    await fireEvent.click(screen.getByRole("button", { name: "Confirmar código" }));

    await waitFor(() => expect(enviado).not.toBeNull());
    expect(enviado).toEqual({ totp_code: "123456" });
  });

  it("deja salir al correo si el firmante no tiene la app a mano", async () => {
    const llamadas = conEstadoTotp("activo");

    render(SignAssistant, {
      props: { open: true, solicitudId: SOLICITUD_ID, titulo: "R-1" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Firmar" }));
    await screen.findByLabelText("Código de la app autenticadora");

    await fireEvent.click(screen.getByRole("button", { name: /enviarme el código por correo/i }));

    await screen.findByLabelText("Código de verificación");
    expect(llamadas.some((u) => u.endsWith("/challenge"))).toBe(true);
  });

  it("sin app activa se comporta como siempre: reto por correo", async () => {
    const llamadas = conEstadoTotp("no_configurado");

    render(SignAssistant, {
      props: { open: true, solicitudId: SOLICITUD_ID, titulo: "R-1" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Firmar" }));

    await screen.findByLabelText("Código de verificación");
    expect(llamadas.some((u) => u.endsWith("/challenge"))).toBe(true);
  });

  it("si no se puede averiguar el estado, degrada al correo y no deja al firmante sin vía", async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input);
      if (url === "/perfil/api/totp") throw new TypeError("red caída");
      if (url.endsWith("/sign")) {
        return new Response(JSON.stringify({ error: "2fa_requerido", status: 409 }), {
          status: 409,
        });
      }
      return new Response(
        JSON.stringify({
          challenge_id: "chal-9",
          factor_tipo: "otp_email",
          canal: "o***@dominio.gov.co",
          expira_at: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
        }),
        { status: 200 },
      );
    });

    render(SignAssistant, {
      props: { open: true, solicitudId: SOLICITUD_ID, titulo: "R-1" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Firmar" }));

    await screen.findByLabelText("Código de verificación");
  });

  it("si el backend veta el correo (totp_requerido al emitir el reto), pide la app en vez de un error sin salida", async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input);
      if (url === "/perfil/api/totp") throw new TypeError("red caída");
      if (url.endsWith("/sign")) {
        return new Response(JSON.stringify({ error: "2fa_requerido", status: 409 }), {
          status: 409,
        });
      }
      if (url.endsWith("/challenge")) {
        return new Response(JSON.stringify({ error: "totp_requerido", status: 409 }), {
          status: 409,
        });
      }
      throw new Error(`URL inesperada: ${url}`);
    });

    render(SignAssistant, {
      props: { open: true, solicitudId: SOLICITUD_ID, titulo: "R-1" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Firmar" }));

    await screen.findByLabelText("Código de la app autenticadora");
  });

  it("con credencial personal (totp_requerido) NO ofrece el correo: ahí no lo admite el backend", async () => {
    globalThis.fetch = vi.fn(async (input) => {
      const url = String(input);
      if (url === "/perfil/api/totp") {
        return new Response(JSON.stringify({ estado: "activo" }), {
          status: 200,
        });
      }
      if (url.endsWith("/sign")) {
        return new Response(JSON.stringify({ error: "totp_requerido", status: 409 }), {
          status: 409,
        });
      }
      throw new Error(`URL inesperada: ${url}`);
    });

    render(SignAssistant, {
      props: { open: true, solicitudId: SOLICITUD_ID, titulo: "R-1" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "Firmar" }));

    await screen.findByLabelText("Código de la app autenticadora");
    expect(screen.queryByRole("button", { name: /por correo/i })).toBeNull();
  });
});
