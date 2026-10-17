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

    const datos = {
      acepta_tratamiento: true,
      politica_version: politica.version,
      sitio_web: sitioWeb,
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

    enviando = true;
    try {
      const firma =
        JSON.stringify(datos) +
        Array.from(archivos ?? [], (a) => `${a.name}:${a.size}:${a.lastModified}`).join("|");
      const res = await fetch("/radicacion/api", {
        method: "POST",
        body: cuerpo,
        headers: { "Idempotency-Key": claveParaEnvio(firma) },
      });
      const resultado = await res.json().catch(() => null);
      if (!res.ok) {
        if (codigoError(resultado) === "politica_desactualizada") await recargarPolitica();
        error = mensajeError(resultado, "No se pudo radicar en este momento.");
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

        <button class="vent__btn" type="submit" disabled={enviando || sinPolitica}>
          {enviando ? "Radicando…" : "Radicar documento"}
        </button>
      </form>
    {/if}
  </div>
</div>

<style lang="scss">
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
