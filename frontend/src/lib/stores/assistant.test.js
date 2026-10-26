// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * assistant.test.js — Store de la conversación con el asistente (E22/E18).
 *
 * Cubre las transiciones de estado descritas en el contrato:
 *  - send(): turno de usuario optimista (texto plano) → "sending" → éxito
 *    ("idle" + turno del asistente + conversationId) | unavailable | error.
 *  - El turno de usuario se conserva SIEMPRE, incluso si el backend falla
 *    (nunca se pierde lo escrito).
 *  - reset() limpia la conversación (nuevo hilo, solo en memoria).
 *  - Mensajes en blanco no disparan send() (sin turno vacío, sin llamada al servicio).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { get } from "svelte/store";

const sendMessageMock = vi.fn();

vi.mock("$lib/services/assistant.service.js", async () => {
  const actual = await vi.importActual("$lib/services/assistant.service.js");
  return { ...actual, sendMessage: (...args) => sendMessageMock(...args) };
});

const { assistantState, send, reset } = await import("./assistant.js");
const { AssistantError } = await import("$lib/services/assistant.service.js");

beforeEach(() => {
  sendMessageMock.mockReset();
  reset();
});

describe("assistant store — estado inicial", () => {
  it("arranca vacío, idle, sin conversación", () => {
    const s = get(assistantState);
    expect(s.messages).toEqual([]);
    expect(s.estado).toBe("idle");
    expect(s.conversationId).toBeNull();
    expect(s.errorDetail).toBeNull();
  });
});

describe("assistant store — send() éxito", () => {
  it("empuja el turno de usuario de inmediato (texto plano, html:null) y pasa a sending", async () => {
    let sawSendingWithUserTurn = false;
    sendMessageMock.mockImplementation(async () => {
      const s = get(assistantState);
      if (s.estado === "sending" && s.messages.length === 1 && s.messages[0].role === "user") {
        sawSendingWithUserTurn = true;
      }
      return { conversation_id: "c-1", message: { id: "a-1", texto: "hola", html: null, ts: "t" } };
    });

    await send("Hola asistente");

    expect(sawSendingWithUserTurn).toBe(true);
    const s = get(assistantState);
    expect(s.messages[0]).toMatchObject({ role: "user", texto: "Hola asistente", html: null });
  });

  it("en éxito: añade el turno del asistente, guarda conversationId, vuelve a idle", async () => {
    sendMessageMock.mockResolvedValue({
      conversation_id: "conv-42",
      message: {
        id: "a-1",
        texto: "Respuesta",
        html: "<p>Respuesta</p>",
        ts: "2026-07-15T10:00:00Z",
      },
    });

    await send("¿Cómo radico una entrada?");

    const s = get(assistantState);
    expect(s.estado).toBe("idle");
    expect(s.conversationId).toBe("conv-42");
    expect(s.errorDetail).toBeNull();
    expect(s.messages).toHaveLength(2);
    expect(s.messages[1]).toMatchObject({
      role: "assistant",
      texto: "Respuesta",
      html: "<p>Respuesta</p>",
    });
  });

  it("reenvía el conversationId activo en la siguiente llamada al servicio", async () => {
    sendMessageMock.mockResolvedValue({
      conversation_id: "conv-1",
      message: { id: "a-1", texto: "ok", html: null, ts: "t" },
    });
    await send("primero");

    sendMessageMock.mockResolvedValue({
      conversation_id: "conv-1",
      message: { id: "a-2", texto: "ok2", html: null, ts: "t2" },
    });
    await send("segundo");

    expect(sendMessageMock).toHaveBeenLastCalledWith({
      mensaje: "segundo",
      conversationId: "conv-1",
    });
  });
});

describe("assistant store — send() fallos", () => {
  it('kind:"unavailable" → estado="unavailable", conserva el turno de usuario', async () => {
    sendMessageMock.mockRejectedValue(
      new AssistantError("unavailable", "El asistente aún no está disponible.", null),
    );

    await send("Radica esto");

    const s = get(assistantState);
    expect(s.estado).toBe("unavailable");
    expect(s.messages).toHaveLength(1);
    expect(s.messages[0]).toMatchObject({ role: "user", texto: "Radica esto" });
  });

  it('kind:"auth"/"invalid"/"network" → estado="error" con errorDetail, conserva el turno de usuario', async () => {
    sendMessageMock.mockRejectedValue(
      new AssistantError("network", "fallo de red", "detalle upstream"),
    );

    await send("hola");

    const s = get(assistantState);
    expect(s.estado).toBe("error");
    expect(s.errorDetail).toBe("detalle upstream");
    expect(s.messages).toHaveLength(1);
  });

  it("un error no-AssistantError también cae en estado=error sin perder el turno", async () => {
    sendMessageMock.mockRejectedValue(new Error("boom"));

    await send("hola");

    const s = get(assistantState);
    expect(s.estado).toBe("error");
    expect(s.errorDetail).toBe("boom");
    expect(s.messages).toHaveLength(1);
  });

  it("no fabrica un turno de asistente cuando el envío falla", async () => {
    sendMessageMock.mockRejectedValue(new AssistantError("unavailable", "no disponible"));
    await send("hola");
    const s = get(assistantState);
    expect(s.messages.every((m) => m.role !== "assistant")).toBe(true);
  });
});

describe("assistant store — send() con mensaje en blanco", () => {
  it("mensaje vacío/solo espacios no dispara el servicio ni añade turnos", async () => {
    await send("   ");
    expect(sendMessageMock).not.toHaveBeenCalled();
    expect(get(assistantState).messages).toEqual([]);
  });
});

describe("assistant store — reset()", () => {
  it("limpia mensajes, estado, conversationId y errorDetail", async () => {
    sendMessageMock.mockRejectedValue(new AssistantError("network", "fallo"));
    await send("hola");
    expect(get(assistantState).messages).toHaveLength(1);

    reset();

    const s = get(assistantState);
    expect(s.messages).toEqual([]);
    expect(s.estado).toBe("idle");
    expect(s.conversationId).toBeNull();
    expect(s.errorDetail).toBeNull();
  });
});
