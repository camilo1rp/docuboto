<!--
  Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
  Contacto: aurigadl@gmail.com

  Software libre: este archivo se distribuye bajo la Licencia Pública General
  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
-->

<!--
  Verificación pública de una firma por su código (ADR-054 §2, §4). Sin sesión.

  Responde la pregunta de un tercero —«¿esta firma es válida, quién la hizo,
  cuándo, con qué sello de tiempo, y está acreditada?»— sin el documento.
  Reglas de honestidad que este componente no negocia:
    - `acreditado` se muestra SIEMPRE: «válida» no es «cualificada».
    - Una constancia de firma simple no se pinta ni válida ni inválida.
    - Un error de red no se presenta como firma inválida.

  Props:
    codigo        string        — el código consultado (o tecleado)
    verificacion  object|null   — PublicSignatureVerification
    mensaje       string|null   — por qué no hay veredicto (código, 404 neutro, 503)
-->
<script>
  import { formatearFechaHora } from "$lib/utils/fecha.js";
  import { veredicto, motivoEnPalabras } from "$lib/utils/verificacionFirma.js";

  export let codigo = "";
  /** @type {import('$lib/types/api.js').PublicSignatureVerification|null} */
  export let verificacion = null;
  /** @type {string|null} */
  export let mensaje = null;

  $: v = verificacion ? veredicto(verificacion) : null;
  /** @param {string|null|undefined} iso */
  const fecha = (iso) => (iso ? formatearFechaHora(iso) : "—");
</script>

<div class="verif">
  <div class="verif__card">
    <a class="verif__volver" href="/">← Inicio</a>
    <h1 class="verif__title">Comprobar una firma</h1>
    <p class="verif__lead">
      Escriba el código de la firma que acompaña al documento. No necesita cuenta. La comprobación
      dice si la firma es válida y quién la hizo; no muestra el documento.
    </p>

    <form class="verif__form" method="GET" action="/firma">
      <label class="verif__label" for="codigo">Código de la firma</label>
      <input
        id="codigo"
        name="codigo"
        class="verif__input"
        type="text"
        value={codigo}
        placeholder="Por ejemplo: 3f2c1a9e-4b7d-4e2a-9c1f-0a1b2c3d4e5f"
        autocomplete="off"
        spellcheck="false"
      />
      <button class="verif__btn" type="submit">Comprobar</button>
    </form>

    {#if mensaje}
      <p class="verif__error" role="alert">{mensaje}</p>
    {/if}

    {#if verificacion && v}
      <section class="verif__resultado" aria-live="polite">
        <p
          class="verif__veredicto"
          class:verif__veredicto--ok={v.tono === "ok"}
          class:verif__veredicto--mal={v.tono === "mal"}
          role="status"
        >
          {v.titulo}
        </p>
        {#if motivoEnPalabras(verificacion.motivo)}
          <p class="verif__nota">{motivoEnPalabras(verificacion.motivo)}</p>
        {/if}
        <dl class="verif__dl">
          <div>
            <dt>Clase</dt>
            <dd>
              {verificacion.kind === "electronic_signature"
                ? "Firma electrónica (XAdES)"
                : "Constancia de firma simple"}
            </dd>
          </div>
          <div>
            <dt>Acreditación</dt>
            <dd>
              {verificacion.acreditado
                ? "Acreditada ante ONAC"
                : "No acreditada (ONAC): no es firma digital del art. 28 de la Ley 527 de 1999"}
            </dd>
          </div>
          <div>
            <dt>Firmante</dt>
            <dd>{verificacion.firmante_nombre ?? "No consta en el catálogo de usuarios"}</dd>
          </div>
          <div>
            <dt>Firmado en</dt>
            <dd>{fecha(verificacion.firmado_en)}</dd>
          </div>
          {#if verificacion.kind === "electronic_signature"}
            <div>
              <dt>Sello de tiempo</dt>
              <dd>
                {verificacion.sello_tiempo
                  ? fecha(verificacion.sello_tiempo)
                  : "Sin sello de tiempo verificado"}
              </dd>
            </div>
            {#if verificacion.nivel}
              <div>
                <dt>Nivel</dt>
                <dd>{verificacion.nivel}</dd>
              </div>
            {/if}
            {#if verificacion.revocacion_status}
              <div>
                <dt>Certificado del firmante</dt>
                <dd>{verificacion.revocacion_status}</dd>
              </div>
            {/if}
          {/if}
          <div>
            <dt>Huella del documento</dt>
            <dd><code class="verif__mono">{verificacion.huella_documento}</code></dd>
          </div>
        </dl>
        <p class="verif__nota">
          Compare la huella con la del documento que tiene en la mano (SHA-256). El documento
          firmado completo lo entrega la entidad a quien tenga permiso para verlo.
        </p>
      </section>
    {/if}
  </div>
</div>

<style lang="scss">
  .verif {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--op-space-5) var(--op-space-4);
    background: linear-gradient(160deg, var(--op-brand-900), var(--op-brand-700));

    &__card {
      width: 100%;
      max-width: 640px;
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
      display: flex;
      flex-direction: column;
      gap: var(--op-space-2);
    }

    &__label {
      font-weight: 600;
      color: var(--op-text);
    }

    &__input {
      padding: var(--op-space-3);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      font-family: var(--op-font-family-mono);

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }

    &__btn {
      align-self: flex-start;
      margin-top: var(--op-space-2);
      padding: var(--op-space-3) var(--op-space-5);
      border: 0;
      border-radius: var(--op-radius);
      background: var(--op-primary);
      color: #fff;
      font-weight: 600;
      cursor: pointer;

      &:hover {
        background: var(--op-primary-dark);
      }
      &:focus-visible {
        outline: 2px solid var(--op-primary-dark);
        outline-offset: 2px;
      }
    }

    &__error {
      margin-top: var(--op-space-4);
      padding: var(--op-space-3);
      border-radius: var(--op-radius);
      background: var(--op-error-bg, #fdecea);
      color: var(--op-error-fg, #8a1c1c);
    }

    &__resultado {
      margin-top: var(--op-space-5);
      padding-top: var(--op-space-4);
      border-top: 1px solid var(--op-border);
    }

    &__veredicto {
      margin: 0 0 var(--op-space-3);
      font-size: var(--op-font-lg);
      font-weight: 600;
      color: var(--op-text);

      &--ok {
        color: var(--op-success-fg);
      }
      &--mal {
        color: var(--op-error-fg, #8a1c1c);
      }
    }

    &__dl {
      margin: 0;
      display: grid;
      gap: var(--op-space-3);

      div {
        display: grid;
        gap: 2px;
      }

      dt {
        font-size: var(--op-font-sm);
        color: var(--op-text-secondary);
      }

      dd {
        margin: 0;
        color: var(--op-text);
        overflow-wrap: anywhere;
      }
    }

    &__mono {
      font-family: var(--op-font-family-mono);
    }

    &__nota {
      margin: var(--op-space-3) 0 0;
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
    }
  }
</style>
