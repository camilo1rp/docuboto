// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * VoiceInput — dictado del asistente (E18/ADR-020, bloque 11).
 *
 * Lo que estos tests blindan no es que el micrófono funcione: es que **el botón
 * no aparezca cuando dictar significaría mandar la voz de un funcionario a un
 * tercero**. El plan justificaba el reconocimiento del navegador con que «el
 * audio no sale del equipo», y eso es falso en Chrome y Edge, cuya Web Speech
 * API clásica envía el audio a un servicio de Google. ADR-020 decidió lo
 * contrario por escrito («STT en servidor con Whisper; el audio no sale a
 * terceros»).
 *
 * El caso peligroso no es el fallo ruidoso: es el que funciona perfectamente y
 * nadie mira. Por eso el control importante aquí es el NEGATIVO.
 *
 * OJO al leer los tests de este primer bloque: «sin `availableOnDevice` no hay
 * botón» vale porque **tampoco hay transcripción en el servidor** — estos tests
 * no simulan `fetch`, así que la consulta de capacidad falla y devuelve «no
 * disponible». El segundo camino (Whisper en la propia entidad, ADR-020) tiene
 * su propio bloque más abajo. Lo que NUNCA existe es un tercero que mande la voz
 * fuera.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/svelte";
import { tick } from "svelte";
import VoiceInput from "./VoiceInput.svelte";

/** Doble del reconocedor. `instancias` deja ver qué se le pidió. */
function fabricarSR({ availableOnDevice } = {}) {
  const instancias = [];
  class SR {
    constructor() {
      this.lang = "";
      this.continuous = false;
      this.interimResults = false;
      this.processLocally = false;
      this.iniciado = false;
      this.parado = false;
      instancias.push(this);
    }
    start() {
      this.iniciado = true;
    }
    stop() {
      this.parado = true;
    }
  }
  if (availableOnDevice !== undefined) {
    SR.availableOnDevice = availableOnDevice;
  }
  return { SR, instancias };
}

function instalar(SR) {
  vi.stubGlobal("SpeechRecognition", SR);
  if (SR === undefined) {
    // `stubGlobal(undefined)` deja la propiedad definida a undefined, que es
    // exactamente lo que queremos comprobar: la API ausente.
    vi.stubGlobal("webkitSpeechRecognition", undefined);
  }
}

/** Deja pasar el `onMount` asíncrono que resuelve la disponibilidad.
 *
 * Varias vueltas a propósito: el camino del servidor encadena DOS promesas
 * (comprobar el dispositivo y luego preguntar la capacidad al backend), y con
 * un solo `tick` el componente aún no ha decidido si hay botón — el test daría
 * «no hay» por una carrera, no por la regla que quiere comprobar.
 */
async function asentar() {
  for (let i = 0; i < 6; i += 1) {
    await tick();
    await Promise.resolve();
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("VoiceInput — el botón solo existe si el reconocimiento es LOCAL", () => {
  it("sin Web Speech API no hay botón (degradación honesta)", async () => {
    instalar(undefined);
    render(VoiceInput);
    await asentar();

    expect(screen.queryByTestId("voice-toggle")).not.toBeInTheDocument();
  });

  it("con la API pero SIN `availableOnDevice` NO hay botón", async () => {
    // Este es el caso de Chrome/Edge clásicos: `SpeechRecognition` existe y
    // funciona — mandando el audio a un servidor de Google. Ofrecer el botón
    // aquí sería la fuga silenciosa que este componente existe para impedir.
    const { SR } = fabricarSR(); // sin availableOnDevice
    instalar(SR);
    render(VoiceInput);
    await asentar();

    expect(screen.queryByTestId("voice-toggle")).not.toBeInTheDocument();
  });

  it("si el modelo está «downloadable» todavía NO hay botón", async () => {
    // Hasta que el modelo esté en el dispositivo, el navegador puede recurrir
    // al servicio remoto sin decir nada. "Casi local" no es local.
    const { SR } = fabricarSR({ availableOnDevice: async () => "downloadable" });
    instalar(SR);
    render(VoiceInput);
    await asentar();

    expect(screen.queryByTestId("voice-toggle")).not.toBeInTheDocument();
  });

  it("si `availableOnDevice` revienta, NO hay botón (fail-closed)", async () => {
    const { SR } = fabricarSR({
      availableOnDevice: async () => {
        throw new Error("boom");
      },
    });
    instalar(SR);
    render(VoiceInput);
    await asentar();

    expect(screen.queryByTestId("voice-toggle")).not.toBeInTheDocument();
  });

  it("control positivo: con el modelo en el dispositivo SÍ aparece", async () => {
    // Sin este control, una comprobación que dijera «no» a todo dejaría los
    // cuatro tests de arriba verdes y el dictado muerto para siempre.
    const { SR } = fabricarSR({ availableOnDevice: async () => "available" });
    instalar(SR);
    render(VoiceInput);
    await asentar();

    expect(screen.getByTestId("voice-toggle")).toBeInTheDocument();
  });

  it("pregunta la disponibilidad PARA EL IDIOMA que va a dictar", async () => {
    const espia = vi.fn(async () => "available");
    const { SR } = fabricarSR({ availableOnDevice: espia });
    instalar(SR);
    render(VoiceInput, { props: { lang: "es-CO" } });
    await asentar();

    expect(espia).toHaveBeenCalledWith("es-CO");
  });
});

describe("VoiceInput — al dictar se exige proceso local otra vez", () => {
  async function montarConDictado() {
    const { SR, instancias } = fabricarSR({ availableOnDevice: async () => "available" });
    instalar(SR);
    const util = render(VoiceInput, { props: { lang: "es-CO" } });
    await asentar();
    return { ...util, instancias };
  }

  it("la instancia se configura en es-CO y con `processLocally`", async () => {
    const { instancias } = await montarConDictado();

    await fireEvent.click(screen.getByTestId("voice-toggle"));
    await tick();

    expect(instancias).toHaveLength(1);
    expect(instancias[0].lang).toBe("es-CO");
    expect(instancias[0].processLocally).toBe(true);
    expect(instancias[0].iniciado).toBe(true);
  });

  it("un resultado definitivo se emite hacia el compositor, marcado como tal", async () => {
    const { component, instancias } = await montarConDictado();
    const oido = vi.fn();
    component.$on("texto", (e) => oido(e.detail));

    await fireEvent.click(screen.getByTestId("voice-toggle"));
    await tick();

    instancias[0].onresult({
      resultIndex: 0,
      results: [{ isFinal: true, 0: { transcript: "radica esta tutela" } }],
    });

    expect(oido).toHaveBeenCalledWith({ texto: "radica esta tutela", definitivo: true });
  });

  it("lo parcial viaja marcado como NO definitivo", async () => {
    const { component, instancias } = await montarConDictado();
    const oido = vi.fn();
    component.$on("texto", (e) => oido(e.detail));

    await fireEvent.click(screen.getByTestId("voice-toggle"));
    await tick();

    instancias[0].onresult({
      resultIndex: 0,
      results: [{ isFinal: false, 0: { transcript: "radica esta" } }],
    });

    expect(oido).toHaveBeenCalledWith({ texto: "radica esta", definitivo: false });
  });

  it("el dictado NUNCA envía ni confirma: solo emite texto", async () => {
    // La garantía del punto 11.3. Una acción con consecuencias sobre un
    // registro oficial no puede dispararse por lo que el reconocedor creyó oír.
    const { component, instancias } = await montarConDictado();
    const eventos = [];
    for (const nombre of ["texto", "submit", "confirmar", "enviar"]) {
      component.$on(nombre, () => eventos.push(nombre));
    }

    await fireEvent.click(screen.getByTestId("voice-toggle"));
    await tick();
    instancias[0].onresult({
      resultIndex: 0,
      results: [{ isFinal: true, 0: { transcript: "confirma la radicación" } }],
    });

    expect(eventos).toEqual(["texto"]);
  });

  it("volver a pulsar detiene el reconocedor", async () => {
    const { instancias } = await montarConDictado();

    const btn = screen.getByTestId("voice-toggle");
    await fireEvent.click(btn);
    await tick();
    await fireEvent.click(btn);
    await tick();

    expect(instancias[0].parado).toBe(true);
  });
});

describe("VoiceInput — los fallos dicen lo que son", () => {
  async function montar() {
    const { SR, instancias } = fabricarSR({ availableOnDevice: async () => "available" });
    instalar(SR);
    render(VoiceInput);
    await asentar();
    await fireEvent.click(screen.getByTestId("voice-toggle"));
    await tick();
    return instancias;
  }

  it("permiso denegado se distingue de un micrófono ausente", async () => {
    const instancias = await montar();

    instancias[0].onerror({ error: "not-allowed" });
    await tick();

    // "No se pudo dictar" dejaría a la persona sin saber si el problema es el
    // permiso, el micrófono o el idioma — y por tanto sin saber qué hacer.
    expect(screen.getByTestId("voice-aviso")).toHaveTextContent(/permiso/i);
  });

  it("un idioma no soportado en el dispositivo RETIRA el botón", async () => {
    const instancias = await montar();

    instancias[0].onerror({ error: "language-not-supported" });
    await tick();

    expect(screen.queryByTestId("voice-toggle")).not.toBeInTheDocument();
    expect(screen.getByTestId("voice-aviso")).toHaveTextContent(/dispositivo/i);
  });
});

describe("VoiceInput — segundo camino: transcripción en el servidor de la entidad", () => {
  /** Sin reconocimiento local, pero con Whisper activado en la instalación. */
  function instalarSinReconocedorLocal() {
    class SR {
      start() {}
      stop() {}
    }
    vi.stubGlobal("SpeechRecognition", SR); // sin availableOnDevice
  }

  function stubFetch(respuestaGet, respuestaPost) {
    const fn = vi.fn(async (url, opciones) => {
      if (!opciones || opciones.method !== "POST") {
        return { ok: true, json: async () => respuestaGet };
      }
      return respuestaPost;
    });
    vi.stubGlobal("fetch", fn);
    return fn;
  }

  it("con Whisper activado SÍ hay botón, aunque el navegador no reconozca en local", async () => {
    // Es el caso de Chrome y Edge: su API existe pero manda el audio a Google,
    // así que el camino local no se ofrece — y sin embargo la persona puede
    // dictar, porque la entidad transcribe en su propio servidor.
    instalarSinReconocedorLocal();
    stubFetch({ disponible: true, proveedor: "whisper", procesa_en: "la propia entidad" });
    render(VoiceInput);
    await asentar();

    expect(screen.getByTestId("voice-toggle")).toBeInTheDocument();
  });

  it("SIN Web Speech API en el navegador, Whisper de la entidad SÍ da botón", async () => {
    // Firefox no trae `SpeechRecognition`, y Chrome puede retirarla. Ese
    // navegador es justamente el que NO tiene forma de mandar la voz a un
    // tercero, así que negarle el dictado deja la única vía soberana —el
    // servidor de la propia entidad— reservada a quien ya tenía la que sí
    // manda el audio fuera. La versión anterior salía en cuanto faltaba la API
    // del navegador y no llegaba a preguntar por el servidor.
    vi.stubGlobal("SpeechRecognition", undefined);
    vi.stubGlobal("webkitSpeechRecognition", undefined);
    stubFetch({ disponible: true, proveedor: "whisper", procesa_en: "la propia entidad" });
    render(VoiceInput);
    await asentar();

    expect(screen.getByTestId("voice-toggle")).toBeInTheDocument();
  });

  it("SIN Web Speech API y SIN Whisper sigue sin haber botón (fail-closed)", async () => {
    // Gemelo del anterior: lo que abre la puerta es que la instalación tenga
    // transcripción propia, no el hecho de que el navegador carezca de API.
    vi.stubGlobal("SpeechRecognition", undefined);
    vi.stubGlobal("webkitSpeechRecognition", undefined);
    stubFetch({ disponible: false, proveedor: "disabled", procesa_en: "ningún sitio" });
    render(VoiceInput);
    await asentar();

    expect(screen.queryByTestId("voice-toggle")).not.toBeInTheDocument();
  });

  it("la etiqueta DICE que se transcribe en el servidor de la entidad", async () => {
    // Quien dicta tiene derecho a saber por dónde pasa su voz, y los dos
    // caminos no son equivalentes en eso.
    instalarSinReconocedorLocal();
    stubFetch({ disponible: true, proveedor: "whisper" });
    render(VoiceInput);
    await asentar();

    expect(screen.getByTestId("voice-toggle")).toHaveAttribute(
      "aria-label",
      expect.stringContaining("servidor de la entidad"),
    );
  });

  it("si la instalación NO tiene Whisper, no hay botón", async () => {
    // Control negativo del bloque: se pregunta ANTES de ofrecer, para que nadie
    // grabe, espere y reciba un 503.
    instalarSinReconocedorLocal();
    stubFetch({ disponible: false, proveedor: "disabled" });
    render(VoiceInput);
    await asentar();

    expect(screen.queryByTestId("voice-toggle")).not.toBeInTheDocument();
  });

  it("si la consulta de capacidad falla, no hay botón (fail-closed)", async () => {
    instalarSinReconocedorLocal();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("red caída");
      }),
    );
    render(VoiceInput);
    await asentar();

    expect(screen.queryByTestId("voice-toggle")).not.toBeInTheDocument();
  });
});

describe("VoiceInput — lo que el servidor NO entendió no se inventa", () => {
  /** Doble de MediaRecorder: deja disparar `onstop` desde el test. */
  function instalarGrabadora() {
    const instancias = [];
    class MR {
      constructor(stream, opciones) {
        this.stream = stream;
        this.mimeType = opciones?.mimeType || "audio/webm";
        instancias.push(this);
      }
      start() {
        this.ondataavailable?.({ data: new Blob(["audio"], { type: "audio/webm" }) });
      }
      stop() {
        this.onstop?.();
      }
    }
    vi.stubGlobal("MediaRecorder", MR);
    vi.stubGlobal("navigator", {
      ...globalThis.navigator,
      mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) },
    });
    return instancias;
  }

  async function dictarConRespuesta(respuestaPost) {
    class SR {
      start() {}
      stop() {}
    }
    vi.stubGlobal("SpeechRecognition", SR); // sin reconocimiento local
    const peticiones = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url, opciones) => {
        peticiones.push(opciones?.method ?? "GET");
        if (opciones?.method !== "POST") {
          return { ok: true, json: async () => ({ disponible: true, proveedor: "whisper" }) };
        }
        return respuestaPost;
      }),
    );
    const grabadoras = instalarGrabadora();
    const util = render(VoiceInput);
    await asentar();

    const oido = vi.fn();
    util.component.$on("texto", (e) => oido(e.detail));

    await fireEvent.click(screen.getByTestId("voice-toggle"));
    await asentar();
    grabadoras[0].stop();
    await asentar();
    return { oido, peticiones };
  }

  it("un audio que no se entendió NO rellena el compositor", async () => {
    // El caso donde la tentación de «devolver algo» es mayor: un compositor
    // vacío parece un fallo del sistema. No lo es — es lo que se oyó. Inventar
    // una frase plausible es el único fallo grave posible en todo el dictado,
    // porque ese texto acaba en el cuerpo de un documento oficial.
    const { oido } = await dictarConRespuesta({
      ok: true,
      json: async () => ({ texto: "   ", proveedor: "whisper" }),
    });

    expect(oido).not.toHaveBeenCalled();
    expect(screen.getByTestId("voice-aviso")).toHaveTextContent(/no se entendió/i);
  });

  it("control positivo: lo que SÍ se transcribió llega al compositor", async () => {
    // Sin esto, un componente que nunca emitiera dejaría el test de arriba
    // verde y el dictado por servidor inservible.
    const { oido } = await dictarConRespuesta({
      ok: true,
      json: async () => ({ texto: "radica esta tutela", proveedor: "whisper" }),
    });

    expect(oido).toHaveBeenCalledWith({ texto: "radica esta tutela", definitivo: true });
  });

  it("un error del backend muestra SU motivo, no un genérico", async () => {
    // El detalle dice qué falta configurar; sustituirlo por «no se pudo
    // transcribir» deja a la persona sin saber qué hacer.
    await dictarConRespuesta({
      ok: false,
      json: async () => ({ detail: "Falta STT_PROVIDER en esta instalación." }),
    });

    expect(screen.getByTestId("voice-aviso")).toHaveTextContent(/STT_PROVIDER/);
  });
});
