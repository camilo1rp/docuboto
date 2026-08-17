<!--
  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
  Contacto: aurigadl@gmail.com

  Software libre: este archivo se distribuye bajo la Licencia Pública General
  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
-->
<!--
  VoiceInput — dictado para el compositor del asistente (E18/ADR-020, bloque 11).

  ## Por qué esto exige reconocimiento EN EL DISPOSITIVO, y no simplemente
  ## "SpeechRecognition si existe"

  El plan justificaba usar el reconocimiento del navegador con que «el audio no
  sale del equipo». **Eso es falso en Chrome y Edge**, que son los navegadores
  con los que se trabaja: su implementación clásica de la Web Speech API envía
  el audio a un servicio de reconocimiento de Google. ADR-020 decidió lo
  contrario y lo decidió por escrito — «STT en servidor con Whisper; el audio no
  sale a terceros», «soberanía del dato: LLM y STT locales» — y este sistema
  gestiona radicados reservados de entidades públicas.

  Así que el botón NO se ofrece por el hecho de que la API exista. Se ofrece solo
  cuando el navegador puede reconocer **en el dispositivo** (`availableOnDevice`
  + `processLocally`) y lo confirma él mismo. Si no puede, no aparece — la misma
  degradación honesta del punto 11.2, aplicada a la soberanía y no solo al
  soporte: un botón que funciona mandando la voz de un funcionario a un tercero
  no es "una función disponible con un matiz", es otra cosa distinta de la que
  se aprobó.

  La comprobación es **defensiva por construcción**: se exige que la API de
  disponibilidad exista Y responda que sí. Si el navegador cambia esa superficie
  o no la trae, el resultado es que no hay botón. El peor caso posible es
  quedarse sin dictado; nunca dictar hacia fuera sin que nadie lo sepa.

  ## El dictado NUNCA confirma

  Rellena el compositor y ahí se acaba: la persona lee, corrige y envía. Y una
  propuesta de escritura se aprueba con la mano, en su tarjeta. Una acción con
  consecuencias sobre un registro oficial no puede dispararse por lo que el
  reconocedor creyó oír.

  ## SSR

  Todo el acceso a `window` vive dentro de `onMount`, y la limpieza va en su
  RETORNO y no en `onDestroy` — que también corre en SSR, donde no hay nada que
  parar (bug D-16 del proyecto).
-->
<script>
  import { onMount, createEventDispatcher } from "svelte";

  /** Idioma del reconocimiento. Colombia por defecto. */
  export let lang = "es-CO";
  /** Deshabilita el botón mientras el compositor está bloqueado. */
  export let disabled = false;

  const dispatch = createEventDispatcher();

  /** `null` mientras se comprueba; `false` fija que no se ofrece. */
  let soportado = null;
  let escuchando = false;
  let aviso = "";

  /** @type {any} */
  let rec = null;

  function Reconocedor() {
    if (typeof window === "undefined") return null;
    // La Web Speech API no está en los tipos del DOM del proyecto (es una
    // extensión vendor en varios navegadores): se accede por índice en vez de
    // declarar un `.d.ts` global para dos nombres.
    const w = /** @type {any} */ (window);
    return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
  }

  /**
   * ¿Puede este navegador reconocer SIN mandar el audio fuera?
   *
   * Se exige que la propia API lo AFIRME. No basta con que `SpeechRecognition`
   * exista —en Chrome existe y manda el audio a Google— ni con poner
   * `processLocally = true` y confiar: hay que preguntarle al navegador si tiene
   * el modelo en el dispositivo para este idioma.
   */
  async function reconoceEnElDispositivo(SR) {
    if (typeof SR?.availableOnDevice !== "function") return false;
    try {
      const estado = await SR.availableOnDevice(lang);
      // "downloadable"/"downloading" NO valen: hasta que el modelo esté, el
      // navegador puede recurrir al servicio remoto sin decir nada.
      return estado === "available" || estado === true;
    } catch {
      return false;
    }
  }

  onMount(() => {
    let vivo = true;

    (async () => {
      const SR = Reconocedor();
      if (!SR) {
        if (vivo) soportado = false;
        return;
      }
      const local = await reconoceEnElDispositivo(SR);
      if (!vivo) return;
      soportado = local;
    })();

    return () => {
      vivo = false;
      detener();
    };
  });

  function detener() {
    escuchando = false;
    if (!rec) return;
    try {
      rec.stop();
    } catch {
      // Ya estaba parado o el navegador lo cerró: no hay nada que hacer.
    }
    rec = null;
  }

  async function alternar() {
    if (escuchando) {
      detener();
      return;
    }

    const SR = Reconocedor();
    if (!SR) {
      soportado = false;
      return;
    }

    aviso = "";
    rec = new SR();
    rec.lang = lang;
    rec.continuous = true;
    rec.interimResults = true;
    // Segunda vuelta de la misma exigencia: aunque `availableOnDevice` haya
    // dicho que sí, se pide explícitamente proceso local. Si el navegador no
    // honra la propiedad, `onerror` lo corta más abajo.
    try {
      rec.processLocally = true;
    } catch {
      detener();
      soportado = false;
      return;
    }

    rec.onresult = (evento) => {
      let finales = "";
      let parcial = "";
      for (let i = evento.resultIndex; i < evento.results.length; i += 1) {
        const r = evento.results[i];
        if (r.isFinal) finales += r[0].transcript;
        else parcial += r[0].transcript;
      }
      // Se emite SIEMPRE hacia el compositor, nunca hacia un envío. Lo parcial
      // va marcado como tal para que la vista pueda distinguir lo que el
      // reconocedor aún está decidiendo de lo que ya dio por bueno.
      if (finales) dispatch("texto", { texto: finales, definitivo: true });
      else if (parcial) dispatch("texto", { texto: parcial, definitivo: false });
    };

    rec.onerror = (evento) => {
      // Cada fallo dice lo que es. "No se pudo dictar" deja a la persona sin
      // saber si el problema es el micrófono, el permiso o el idioma.
      const codigo = evento?.error ?? "";
      if (codigo === "not-allowed" || codigo === "service-not-allowed") {
        aviso = "El navegador no dio permiso para usar el micrófono.";
      } else if (codigo === "no-speech") {
        aviso = "No se escuchó nada.";
      } else if (codigo === "audio-capture") {
        aviso = "No se encontró un micrófono.";
      } else if (codigo === "language-not-supported") {
        aviso = "El reconocimiento en el dispositivo no admite este idioma.";
        soportado = false;
      } else {
        aviso = "El dictado se interrumpió.";
      }
      detener();
    };

    rec.onend = () => {
      escuchando = false;
    };

    try {
      rec.start();
      escuchando = true;
    } catch {
      aviso = "No se pudo iniciar el dictado.";
      detener();
    }
  }
</script>

{#if soportado}
  <button
    type="button"
    class="voice-input"
    class:voice-input--on={escuchando}
    {disabled}
    aria-pressed={escuchando}
    aria-label={escuchando ? "Detener el dictado" : "Dictar el mensaje"}
    title={escuchando ? "Detener el dictado" : "Dictar el mensaje"}
    data-testid="voice-toggle"
    on:click={alternar}
  >
    <i class="fas {escuchando ? 'fa-stop' : 'fa-microphone'}" aria-hidden="true"></i>
  </button>
{/if}

{#if aviso}
  <p class="voice-input__aviso" role="status" data-testid="voice-aviso">{aviso}</p>
{/if}

<style lang="scss">
  .voice-input {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 2.75rem;
    min-height: 2.75rem;
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-sm);
    background: var(--op-surface-alt);
    color: var(--op-text-muted);
    cursor: pointer;

    &:hover:not(:disabled) {
      color: var(--op-text);
    }

    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }

    &:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
  }

  .voice-input--on {
    border-color: var(--op-danger);
    color: var(--op-danger);
  }

  .voice-input__aviso {
    flex-basis: 100%;
    color: var(--op-text-muted);
    font-size: var(--op-font-size-xs);
  }
</style>
