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
  /radicacion — VENTANILLA ÚNICA EN LÍNEA, sin cuenta.

  Exigir registrarse para presentar un derecho de petición sería poner una
  barrera donde la ley no la pone (Ley 1755/2015). La ventanilla de mostrador
  sigue existiendo en `/radicar`, que sí es de usuario del sistema: son dos
  puertas al mismo trámite, no alternativas.

  Dos cosas que la pantalla NO hace y son deliberadas:
  - No repite los límites de anexos como validación propia. Los anuncia (para
    que nadie descubra el límite fallando) pero **quien decide es el backend**:
    dos listas que pueden divergir dejarían al ciudadano viendo la que se puede
    saltar.
  - No promete plazos. El plazo lo fija la ley según el trámite y lo calcula la
    entidad; escribirlo aquí sería una promesa que esta pantalla no puede
    cumplir.
-->
<script>
  import { onDestroy, onMount } from "svelte";
  import "altcha/altcha.css";
  import CanalesAlternos from "$lib/components/features/ventanilla/CanalesAlternos.svelte";
  import { normalizarContacto } from "$lib/utils/contactoEntidad.js";
  import { urlHttpSegura } from "$lib/utils/urlSegura.js";
  import { codigoError, mensajeError } from "$lib/utils/apiError.js";

  /** @type {import('./$types').PageData} */
  export let data;

  const MAX_ARCHIVOS = 5;

  let tipoDocumental = data.tipos?.[0]?.code ?? "";
  let nombre = "";
  let entidad = "";
  let tipoIdentificacion = "";
  let identificacion = "";
  let email = "";
  let telefono = "";
  let direccion = "";
  let asunto = "";
  let descripcion = "";
  /** @type {FileList|null} */
  let archivos = null;
  // Ley 1581/2012: sin autorización expresa no se radica por esta puerta.
  let aceptaTratamiento = false;
  // Honeypot (AREA-02-07): un campo que ninguna persona ve ni rellena. Un script que
  // rellena todo lo que encuentra en el HTML lo delata y el backend lo rechaza antes
  // de consumir un número.
  let sitioWeb = "";
  // Clave de idempotencia: UNA por contenido distinto. Un reintento idéntico reutiliza
  // la misma (el backend devuelve el radicado ya creado); si el usuario edita el
  // formulario tras un fallo, el contenido cambia y se genera otra, porque la misma
  // clave con otro contenido sería un 409.
  /** Política vigente de la entidad; se recarga si el servidor dice que cambió. */
  let politica = data.politica ?? { publicada: false, version: "", url: "", verificable: true };
  $: sinPolitica = !politica.publicada;
  $: urlPolitica = urlHttpSegura(politica.url);
  $: urlSupresion = urlPolitica ? `${urlPolitica.split("#")[0]}#supresion` : "";
  let ultimoEnvio = { firma: "", clave: "" };

  function nuevaClave() {
    const c = globalThis.crypto;
    return c?.randomUUID ? c.randomUUID() : `k${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  /** @param {string} firma */
  function claveParaEnvio(firma) {
    if (ultimoEnvio.firma !== firma) ultimoEnvio = { firma, clave: nuevaClave() };
    return ultimoEnvio.clave;
  }

  // --- Antirrobots ALTCHA (C16) -------------------------------------------------
  // El widget se carga SOLO en el navegador (`onMount`): `altcha/external` toca
  // `customElements` al importarse y reventaría en SSR. El worker sale del propio
  // origen (`?worker` lo empaqueta bajo /_app/): el bundle por defecto de altcha
  // lo crearía desde un `blob:` y la CSP (sin `worker-src blob:`) lo bloquearía.
  /** @type {any} */
  let widget = null;
  /** @type {HTMLFieldSetElement|null} */
  let grupoAltcha = null;
  let altchaListo = false;
  let altchaFallo = false;
  let altchaEstado = "unverified";
  let altchaPayload = "";
  /** @type {((ok: boolean) => void)|null} */
  let esperaAltcha = null;

  const CANALES_ALTERNOS =
    "Puede presentar su documento de forma presencial en la ventanilla de la entidad o por " +
    "el correo de atención de la entidad.";

  onMount(async () => {
    // `addEventListener` y no `on:statechange`: el tipo del elemento personalizado de altcha
    // no declara eventos y svelte-check rechazaría la directiva.
    widget?.addEventListener("statechange", alCambiarAltcha);
    try {
      // El orden importa: `altcha/external` fija su propio `altchaCreateWorker`
      // (basado en `new URL(…, import.meta.url)`) al importarse; el nuestro va DESPUÉS.
      await import("altcha/external");
      await import("altcha/i18n/es-419");
      const { default: AltchaWorker } = await import("altcha/worker?worker");
      /** @type {any} */ (globalThis).altchaCreateWorker = () => new AltchaWorker();
      altchaListo = true;
    } catch {
      altchaFallo = true;
    }
  });

  onDestroy(() => widget?.removeEventListener?.("statechange", alCambiarAltcha));

  $: textoAltcha = altchaFallo
    ? "No se pudo cargar la verificación automática."
    : !altchaListo
      ? "Preparando la verificación automática…"
      : altchaEstado === "verified"
        ? "Verificación automática completada."
        : altchaEstado === "verifying"
          ? "Verificando… unos segundos."
          : altchaEstado === "expired"
            ? "La verificación venció; se repetirá al enviar."
            : "Se hará al enviar el formulario.";

  /** @param {CustomEvent} e */
  function alCambiarAltcha(e) {
    const estado = e.detail?.state ?? "";
    altchaEstado = estado;
    altchaPayload = estado === "verified" ? String(e.detail?.payload ?? "") : "";
    if (estado === "error") altchaFallo = true;
    else if (estado) altchaFallo = false;
    if (esperaAltcha && (estado === "verified" || estado === "error" || estado === "expired")) {
      const resolver = esperaAltcha;
      esperaAltcha = null;
      resolver(estado === "verified");
    }
  }

  /** Lanza la prueba de trabajo y espera su resultado (máx. 90 s). */
  function esperarAltcha() {
    return new Promise((resolver) => {
      esperaAltcha = resolver;
      setTimeout(() => {
        if (esperaAltcha === resolver) {
          esperaAltcha = null;
          resolver(false);
        }
      }, 90000);
      try {
        widget?.verify?.();
      } catch {
        esperaAltcha = null;
        resolver(false);
      }
    });
  }

  /** Reto quemado o inservible: pedir otro y llevar la mirada al control. */
  function renovarAltcha() {
    altchaPayload = "";
    try {
      widget?.reset?.();
    } catch {
      /* el widget se reiniciará solo en el próximo envío */
    }
  }

  /** Códigos que dejan inservible el reto actual (el 409/422 lo QUEMAN en el servidor). */
  const RETO_QUEMADO = new Set([
    "antirrobots_invalido",
    "antirrobots_vencido",
    "antirrobots_usado",
  ]);

  // Aviso ANTES de escribir: el servidor dice si la verificación está operativa. Si no,
  // el formulario no se puede enviar y se muestran los canales alternos con el contacto
  // que la entidad declaró (sin contacto, el texto genérico).
  $: sinAntirrobots = data.estado?.antirrobotsDisponible === false;
  /** Contacto que trae un 503 `antirrobots_no_disponible` (si no, el del estado). */
  /** @type {any} */
  let contactoVivo = null;
  let antirrobotsCaido = false;
  $: contactoActual = contactoVivo ?? data.estado?.contacto ?? null;
  $: mostrarAlternos = altchaFallo || antirrobotsCaido;

  let enviando = false;
  let error = "";
  /** @type {any|null} */
  let acuse = null;

  // Ley 1755/2015 art. 16: quien se identifica espera respuesta. Se avisa aquí
  // ANTES de enviar, además de que el backend lo exija — descubrir un requisito
  // fallando es la peor forma de conocerlo.
  $: faltaCanal =
    Boolean(identificacion.trim()) && !(email.trim() || telefono.trim() || direccion.trim());

  const MENSAJE_NO_PUBLICADA = mensajeError({ error: "politica_no_publicada" }, "");
  const MENSAJE_NO_VERIFICABLE = mensajeError({ error: "politica_no_verificable" }, "");

  /** 409 politica_desactualizada: traer la versión vigente y pedir aceptar de nuevo. */
  async function recargarPolitica() {
    aceptaTratamiento = false;
    try {
      const res = await fetch("/radicacion/api/politica");
      if (res.ok) politica = await res.json();
    } catch {
      /* se conserva la versión mostrada; el servidor volverá a verificarla */
    }
  }

  async function radicar() {
    error = "";
    if (!tipoDocumental) {
      error = "Seleccione el tipo de trámite.";
      return;
    }
    if (!nombre.trim() || !asunto.trim() || !descripcion.trim()) {
      error = "Nombre, asunto y descripción son obligatorios.";
      return;
    }
    if (faltaCanal) {
      error =
        "Indique al menos un medio para recibir la respuesta (correo, teléfono o dirección), " +
        "o deje el documento de identidad en blanco si prefiere no identificarse.";
      return;
    }

    if (sinPolitica) {
      error = politica.verificable === false ? MENSAJE_NO_VERIFICABLE : MENSAJE_NO_PUBLICADA;
      return;
    }
    if (!aceptaTratamiento) {
      error =
        "Debe autorizar el tratamiento de sus datos personales para poder radicar " +
        "(Ley 1581 de 2012).";
      return;
    }

    if (!altchaListo) {
      error =
        "No se pudo cargar la verificación automática, así que no se puede radicar en línea. " +
        CANALES_ALTERNOS;
      return;
    }

    enviando = true;
    if (!altchaPayload && !(await esperarAltcha())) {
      enviando = false;
      error =
        "No se pudo completar la verificación automática. Intente de nuevo; si persiste, " +
        CANALES_ALTERNOS.charAt(0).toLowerCase() +
        CANALES_ALTERNOS.slice(1);
      antirrobotsCaido = true;
      renovarAltcha();
      grupoAltcha?.focus();
      return;
    }

    const datos = {
      acepta_tratamiento: true,
      politica_version: politica.version,
      sitio_web: sitioWeb,
      altcha: altchaPayload,
      tipo_documental: tipoDocumental,
      nombre: nombre.trim(),
      entidad: entidad.trim() || null,
      tipo_identificacion: tipoIdentificacion.trim() || null,
      identificacion: identificacion.trim() || null,
      email: email.trim() || null,
      telefono: telefono.trim() || null,
      direccion: direccion.trim() || null,
      asunto: asunto.trim(),
      descripcion: descripcion.trim(),
    };

    const cuerpo = new FormData();
    cuerpo.append("datos", JSON.stringify(datos));
    for (const archivo of archivos ?? []) cuerpo.append("anexos", archivo);

    try {
      // La huella de idempotencia EXCLUYE el reto, como el backend: un reintento de
      // red reenvía el mismo reto ya consumido y recibe el radicado ya hecho.
      const { altcha: _reto, ...sinReto } = datos;
      const firma =
        JSON.stringify(sinReto) +
        Array.from(archivos ?? [], (a) => `${a.name}:${a.size}:${a.lastModified}`).join("|");
      const res = await fetch("/radicacion/api", {
        method: "POST",
        body: cuerpo,
        headers: { "Idempotency-Key": claveParaEnvio(firma) },
      });
      const resultado = await res.json().catch(() => null);
      if (!res.ok) {
        const codigo = codigoError(resultado);
        if (codigo === "antirrobots_no_disponible") {
          antirrobotsCaido = true;
          contactoVivo = normalizarContacto(resultado?.contacto ?? resultado?.detail?.contacto);
        }
        if (codigo === "politica_desactualizada") await recargarPolitica();
        error = mensajeError(resultado, "No se pudo radicar en este momento.");
        // Un 409 de política o un 422 de anexo QUEMAN el reto en el servidor; nunca se
        // reenvía el mismo. Lo escrito por la persona no se toca.
        if (res.status === 409 || res.status === 422 || RETO_QUEMADO.has(codigo ?? "")) {
          renovarAltcha();
          if (codigo?.startsWith("antirrobots_")) grupoAltcha?.focus();
        }
        return;
      }
      acuse = resultado;
    } catch {
      error = "No se pudo radicar en este momento. Revise su conexión.";
    } finally {
      enviando = false;
    }
  }
</script>

<svelte:head>
  <title>Radicar un documento — OrpycaMCP</title>
  <meta
    name="description"
    content="Presente peticiones, quejas, reclamos y demás documentos ante la entidad, sin necesidad de crear una cuenta."
  />
</svelte:head>

<div class="vent">
  <div class="vent__card">
    <a class="vent__volver" href="/">← Inicio</a>

    {#if acuse}
      <!-- Acuse: lo único que se lleva quien radica. El código va destacado
           porque es la ÚNICA forma de consultar después sin identificarse, y
           perderlo no tiene arreglo. -->
      <h1 class="vent__title">Su documento quedó radicado</h1>
      <p class="vent__lead">Guarde estos datos. Los necesitará para hacer seguimiento.</p>

      <dl class="vent__acuse">
        <div>
          <dt>Número de radicado</dt>
          <dd><code class="op-dato">{acuse.tracking_number}</code></dd>
        </div>
        <div>
          <dt>Código de verificación</dt>
          <dd><code class="op-dato vent__dato--clave">{acuse.verification_code}</code></dd>
        </div>
        {#if acuse.dependencia_destino}
          <div>
            <dt>Recibido por</dt>
            <dd>{acuse.dependencia_destino}</dd>
          </div>
        {/if}
      </dl>

      <p class="vent__aviso">
        <strong>Conserve el código de verificación.</strong> Es lo único que permite consultar el estado
        de su trámite sin identificarse, y no podemos volver a mostrárselo.
      </p>

      <a class="vent__btn" href="/consulta">Consultar mi trámite</a>
    {:else if !data.habilitada}
      <h1 class="vent__title">Radicación en línea no disponible</h1>
      {#if data.canalSuspendido}
        <p class="vent__lead" role="status">
          Esta entidad tiene suspendida temporalmente la recepción de comunicaciones. Las consultas
          y verificaciones siguen disponibles.
        </p>
        <a class="vent__btn" href="/consulta">Consultar mi trámite</a>
      {:else if data.sinVentanilla}
        <p class="vent__lead" role="status">
          Esta entidad no tiene ventanilla disponible. Preséntese en la ventanilla de radicación de
          la entidad.
        </p>
      {:else}
        <p class="vent__lead">
          Esta entidad todavía no ha habilitado la radicación en línea. Preséntese en la ventanilla
          de radicación de la entidad.
        </p>
      {/if}
    {:else}
      <h1 class="vent__title">Radicar un documento</h1>
      <p class="vent__lead">
        Presente su documento ante la entidad. <strong>No necesita crear una cuenta.</strong> Al terminar
        recibirá un número de radicado y un código para consultar su estado.
      </p>

      <form class="vent__form" on:submit|preventDefault={radicar}>
        {#if sinAntirrobots}
          <p class="vent__aviso vent__aviso--atencion" role="status" id="antirrobots-no-disponible">
            <strong>La radicación en línea no está disponible en este momento</strong>, porque la
            verificación automática de la entidad no está operativa. No necesita llenar el
            formulario. <CanalesAlternos contacto={contactoActual} />
          </p>
        {/if}
        <fieldset class="vent__grupo">
          <legend>Trámite</legend>

          <label class="vent__label" for="tipo">Tipo de trámite</label>
          <select id="tipo" class="vent__input" bind:value={tipoDocumental}>
            {#each data.tipos as t (t.code)}
              <option value={t.code}>{t.nombre}</option>
            {/each}
          </select>

          <label class="vent__label" for="asunto">Asunto</label>
          <input
            id="asunto"
            class="vent__input"
            type="text"
            bind:value={asunto}
            maxlength="2000"
            placeholder="Resuma en una línea de qué se trata"
          />

          <label class="vent__label" for="descripcion">Descripción</label>
          <textarea
            id="descripcion"
            class="vent__input vent__input--area"
            bind:value={descripcion}
            rows="6"
            placeholder="Explique su solicitud con el detalle que considere necesario"
          ></textarea>
        </fieldset>

        <fieldset class="vent__grupo">
          <legend>Quién radica</legend>

          <label class="vent__label" for="nombre">Nombre completo</label>
          <input id="nombre" class="vent__input" type="text" bind:value={nombre} maxlength="255" />

          <label class="vent__label" for="entidad">
            Entidad o empresa <span class="vent__opt">(si escribe en nombre de una)</span>
          </label>
          <input
            id="entidad"
            class="vent__input"
            type="text"
            bind:value={entidad}
            maxlength="255"
          />

          <div class="vent__fila">
            <div>
              <label class="vent__label" for="tipoid">Tipo de documento</label>
              <input
                id="tipoid"
                class="vent__input"
                type="text"
                bind:value={tipoIdentificacion}
                placeholder="CC, NIT, CE…"
                maxlength="20"
              />
            </div>
            <div>
              <label class="vent__label" for="identificacion">
                Número <span class="vent__opt">(opcional)</span>
              </label>
              <input
                id="identificacion"
                class="vent__input"
                type="text"
                bind:value={identificacion}
                maxlength="40"
              />
            </div>
          </div>
          <p class="vent__hint">
            Puede radicar sin identificarse. Si lo hace, indique abajo dónde recibir la respuesta.
          </p>
        </fieldset>

        <fieldset class="vent__grupo">
          <legend>Dónde recibir la respuesta</legend>

          <label class="vent__label" for="email">Correo electrónico</label>
          <input id="email" class="vent__input" type="email" bind:value={email} maxlength="255" />

          <label class="vent__label" for="telefono">Teléfono</label>
          <input
            id="telefono"
            class="vent__input"
            type="tel"
            bind:value={telefono}
            maxlength="40"
          />

          <label class="vent__label" for="direccion">Dirección</label>
          <input
            id="direccion"
            class="vent__input"
            type="text"
            bind:value={direccion}
            maxlength="500"
          />

          {#if faltaCanal}
            <p class="vent__aviso vent__aviso--atencion" role="status">
              Escribió un documento de identidad: indique al menos un medio para recibir la
              respuesta, o deje el documento en blanco si prefiere no identificarse.
            </p>
          {/if}
        </fieldset>

        <fieldset class="vent__grupo">
          <legend>Soportes <span class="vent__opt">(opcional)</span></legend>
          <input
            id="anexos"
            class="vent__input"
            type="file"
            multiple
            accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff"
            bind:files={archivos}
            aria-describedby="anexos-ayuda"
          />
          <p class="vent__hint" id="anexos-ayuda">
            Hasta {MAX_ARCHIVOS} archivos PDF, JPG, PNG o TIFF, de 10 MB cada uno y 25 MB en total. Se
            comprueba el contenido del archivo, no su extensión.
          </p>
        </fieldset>

        <!-- Honeypot: fuera de pantalla y del orden de tabulación; no es un campo para personas. -->
        <div class="vent__trampa" aria-hidden="true">
          <label for="sitio_web">No rellenar</label>
          <input
            id="sitio_web"
            name="sitio_web"
            type="text"
            tabindex="-1"
            autocomplete="off"
            bind:value={sitioWeb}
          />
        </div>

        <fieldset
          class="vent__grupo"
          bind:this={grupoAltcha}
          tabindex="-1"
          id="grupo-altcha"
          aria-describedby="altcha-ayuda"
        >
          <legend>Verificación automática</legend>
          <p class="vent__hint" id="altcha-ayuda">
            No tiene que resolver nada: su navegador hace una comprobación de unos segundos al
            enviar el formulario.
          </p>
          <altcha-widget
            bind:this={widget}
            challengeurl="/radicacion/api/reto"
            auto="onfocus"
            name="altcha"
            hidelogo
            hidefooter
            language="es-419"
          ></altcha-widget>
          <p class="vent__hint" role="status" id="altcha-estado">{textoAltcha}</p>
          {#if mostrarAlternos}
            <p class="vent__aviso vent__aviso--atencion" id="altcha-alternos">
              {#if altchaFallo}Si la verificación no funciona en su navegador:{:else}La verificación
                automática no está disponible:{/if}
              <CanalesAlternos contacto={contactoActual} />
            </p>
          {/if}
          <noscript>
            <p class="vent__aviso vent__aviso--atencion">
              Esta verificación necesita JavaScript. {CANALES_ALTERNOS}
            </p>
          </noscript>
        </fieldset>

        <fieldset class="vent__grupo">
          <legend>Tratamiento de datos personales</legend>
          <label class="vent__check" for="acepta-tratamiento">
            <input
              id="acepta-tratamiento"
              type="checkbox"
              bind:checked={aceptaTratamiento}
              aria-describedby="politica-ayuda"
            />
            <span>
              Autorizo a la entidad el tratamiento de mis datos personales para gestionar esta
              solicitud, conforme a la Ley 1581 de 2012{#if urlPolitica}
                y a su
                <a href={urlPolitica} target="_blank" rel="noopener noreferrer"
                  >política de tratamiento de datos</a
                >{/if}{#if politica.version}
                (versión {politica.version}){/if}.
            </span>
          </label>
          <p class="vent__hint" id="politica-ayuda">
            Se conserva constancia de su autorización (versión de la política, fecha y hora).
          </p>
          {#if sinPolitica}
            <p class="vent__aviso vent__aviso--error" role="alert" id="politica-no-publicada">
              {politica.verificable === false ? MENSAJE_NO_VERIFICABLE : MENSAJE_NO_PUBLICADA}
            </p>
          {/if}
          <p class="vent__hint" id="politica-supresion">
            Mientras corra el plazo legal de conservación no procede la supresión; vea la
            {#if urlSupresion}
              <a href={urlSupresion} target="_blank" rel="noopener noreferrer">política</a
              >{:else}política{/if}.
          </p>
        </fieldset>

        {#if error}
          <p class="vent__aviso vent__aviso--error" role="alert">{error}</p>
        {/if}

        <button
          class="vent__btn"
          type="submit"
          disabled={enviando || sinPolitica || sinAntirrobots}
        >
          {enviando ? "Radicando…" : "Radicar documento"}
        </button>
      </form>
    {/if}
  </div>
</div>

<style lang="scss">
  // Los `--altcha-*` del widget se resuelven a los tokens Orpyca (cero hex sueltos);
  // `:global` porque el elemento personalizado vive fuera del ámbito de este componente.
  :global(altcha-widget) {
    --altcha-color-base: var(--op-surface);
    --altcha-color-text: var(--op-text);
    --altcha-color-border: var(--op-border);
    --altcha-color-border-focus: var(--op-primary);
    --altcha-color-active: var(--op-primary);
    --altcha-color-error-text: var(--op-error-fg);
    --altcha-color-footer-bg: var(--op-bg);
    --altcha-max-width: 100%;
    --altcha-border-radius: var(--op-radius);
    display: block;
  }

  @media (prefers-reduced-motion: reduce) {
    :global(altcha-widget *) {
      animation: none !important;
      transition: none !important;
    }
  }

  #grupo-altcha:focus-visible {
    outline: 2px solid var(--op-primary);
    outline-offset: 2px;
  }

  // `position: absolute` solo se recorta con un bloque contenedor posicionado en la
  // cadena: el formulario lo es (ver `.vent__form`), así que la trampa NO puede
  // desplazar el documento a lo ancho (el defecto de los `sr-only` de 1 px).
  .vent__trampa {
    position: absolute;
    top: 0;
    left: 0;
    width: 1px;
    height: 1px;
    overflow: hidden;
    opacity: 0;
    pointer-events: none;
  }

  .vent__check {
    display: flex;
    gap: var(--op-space-2);
    align-items: flex-start;
    cursor: pointer;

    input {
      margin-top: 0.25rem;
    }
  }

  .vent {
    min-height: 100vh;
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding: var(--op-space-5) var(--op-space-4);
    background: linear-gradient(160deg, var(--op-brand-900), var(--op-brand-700));

    &__card {
      width: 100%;
      max-width: 760px;
      background: #fff;
      border-radius: var(--op-radius);
      border-top: 4px solid var(--op-primary);
      box-shadow: var(--op-shadow-sobre-oscuro);
      padding: var(--op-space-5);
    }

    &__volver {
      display: inline-block;
      margin-bottom: var(--op-space-3);
      color: var(--op-primary-dark);
      font-size: var(--op-font-sm);
    }

    &__title {
      margin: 0 0 var(--op-space-2);
      color: var(--op-text);
    }

    &__lead {
      margin: 0 0 var(--op-space-4);
      color: var(--op-text-secondary);
    }

    &__form {
      position: relative; // bloque contenedor de la trampa (ver `.vent__trampa`)
      display: grid;
      gap: var(--op-space-5);
    }

    &__grupo {
      display: grid;
      gap: var(--op-space-2);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      padding: var(--op-space-4);
      margin: 0;

      legend {
        padding: 0 var(--op-space-2);
        font-weight: 600;
        color: var(--op-text);
      }
    }

    &__fila {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: var(--op-space-3);

      @media (max-width: 640px) {
        grid-template-columns: 1fr;
      }
    }

    &__label {
      font-size: var(--op-font-sm);
      font-weight: 600;
      color: var(--op-text);
    }

    &__opt {
      font-weight: 400;
      color: var(--op-text-secondary);
    }

    &__input {
      width: 100%;
      padding: var(--op-space-3);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      font: inherit;

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }

      &--area {
        resize: vertical;
      }
    }

    &__hint {
      margin: 0;
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
    }

    &__btn {
      justify-self: start;
      display: inline-block;
      padding: var(--op-space-3) var(--op-space-5);
      border: 0;
      border-radius: var(--op-radius);
      background: var(--op-primary);
      color: #fff;
      font-weight: 600;
      text-decoration: none;
      cursor: pointer;

      &:hover:not(:disabled) {
        background: var(--op-primary-dark);
      }
      &:disabled {
        opacity: 0.6;
        cursor: default;
      }
      &:focus-visible {
        outline: 2px solid var(--op-primary-dark);
        outline-offset: 2px;
      }
    }

    &__acuse {
      display: grid;
      gap: var(--op-space-4);
      margin: 0 0 var(--op-space-4);

      div {
        display: grid;
        gap: 4px;
      }

      dt {
        font-size: var(--op-font-sm);
        color: var(--op-text-secondary);
      }

      dd {
        margin: 0;
      }
    }

    /* Solo el énfasis propio de ESTE acuse: el código de verificación es lo
       único que se lleva quien radica, así que pesa más que el número. Lo
       demás —fuente, fondo, color— lo pone `.op-dato`. */
    &__dato--clave {
      font-size: 1.15rem;
      font-weight: 700;
    }

    &__aviso {
      margin: 0;
      padding: var(--op-space-3);
      border-radius: var(--op-radius);
      background: var(--op-primary-light);
      color: var(--op-primary-dark);
      font-size: var(--op-font-sm);

      &--atencion {
        background: var(--op-warning-bg, #fff4e5);
        color: var(--op-warning-fg, #7a4a00);
      }

      &--error {
        background: var(--op-error-bg, #fdecea);
        color: var(--op-error-fg, #8a1c1c);
      }
    }
  }
</style>
