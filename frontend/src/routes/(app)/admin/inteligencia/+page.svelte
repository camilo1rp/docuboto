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
  /admin/inteligencia — Proveedor, niveles y retiro (ADR-045 §4).

  Pasar a ppi exige una declaración que nombra al proveedor y a sus
  subencargados tal como vienen en la declaración firmada. Habilitar
  RESERVADA o CLASIFICADA es otro acto, con la ruta de ese nivel.
  Volver a local no borra nada: la confirmación ofrece retirar los datos
  en el mismo acto, y en local se muestra cuántos radicados quedan.
-->
<script>
  import { onMount } from "svelte";
  import { invalidateAll } from "$app/navigation";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  import Button from "$lib/components/ui/Button.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import { codigoError, mensajeError } from "$lib/utils/apiError.js";
  import {
    ETIQUETA_NIVEL,
    declaracionNivel,
    declaracionProveedor,
    etiquetaHosting,
    etiquetaRetencion,
    exigidosNivel,
    exigidosProveedor,
    filaDe,
    fraseInventario,
    rutaAdmitida,
  } from "$lib/utils/inteligencia.js";

  /** @type {import('./$types').PageData} */
  export let data;

  const EXTRA = [2, 3];

  let borrador = "local";
  let meta = false;
  let declaracion = "";
  /** @type {Record<number, string>} */
  let declaraciones = { 2: "", 3: "" };
  let visto = "";
  let saving = false;
  /** @type {null | "local" | "retiro" | "nivel"} */
  let dialogo = null;
  let retirarConLocal = false;
  /** @type {number | null} */
  let nivelPendiente = null;

  $: config = data.config;
  $: firma = config
    ? `${config.provider}|${config.levels}|${config.send_metadata}|${config.suspended}`
    : "";
  $: if (firma !== visto) {
    visto = firma;
    borrador = config?.provider === "ppi" ? "ppi" : "local";
    meta = config?.send_metadata === true;
    if (data.capabilities?.provider) {
      declaracion = declaracionProveedor(data.capabilities);
      declaraciones = {
        2: declaracionNivel(data.capabilities, 2),
        3: declaracionNivel(data.capabilities, 3),
      };
    }
  }

  $: eligiendoPpi = borrador === "ppi" && config?.provider !== "ppi";
  $: redeclararPublica = config?.provider === "ppi" && config?.suspended === true;
  $: pideDeclaracion = eligiendoPpi || (redeclararPublica && borrador === "ppi");
  $: puedeAplicar =
    !!config &&
    !saving &&
    ((borrador === "local" && config.provider === "ppi") ||
      (pideDeclaracion && !!data.capabilities?.provider && declaracion.trim().length > 0));

  onMount(() => {
    setBreadcrumbs([{ label: "Administración", href: "/admin" }, { label: "Inteligencia" }]);
  });

  /**
   * @param {any} body
   * @param {string} fallback
   */
  function textoError(body, fallback) {
    /** @type {Record<string, string>} */
    const mapa = {
      declaracion_requerida: "Falta la declaración del administrador.",
      declaracion_incompleta: "La declaración no nombra lo que declara el proveedor.",
      bandera_apagada: "Encienda la capa de conocimiento antes de usar el proveedor.",
      politica_no_publicada:
        "Publique la política de tratamiento de datos antes de transmitir.",
      capacidades_no_disponibles: "No se pudo leer la declaración del proveedor.",
      capacidades_invalidas: "La declaración del proveedor no se pudo comprobar.",
      declaracion_distinta:
        "La declaración del proveedor cambió. Léala de nuevo y declare otra vez.",
      nivel_no_declarado: "El proveedor no declara la ruta de ese nivel.",
      ruta_no_admitida: "Esa ruta conserva o entrena con los datos.",
      proveedor_local: "Habilitar un nivel exige que la entidad use el proveedor.",
      purga_no_aceptada: "El proveedor no aceptó el borrado. No quedó asiento.",
      declaracion_no_registrada:
        "El cambio quedó guardado, pero la pasarela no registró la declaración. Vuelva a enviarla.",
    };
    const base = mapa[codigoError(body)] ?? mensajeError(body, fallback);
    const anidado = body?.detail;
    const faltan = Array.isArray(anidado?.faltan)
      ? anidado.faltan
      : Array.isArray(body?.faltan)
        ? body.faltan
        : [];
    return faltan.length ? `${base} Falta: ${faltan.join(", ")}.` : base;
  }

  /**
   * @param {string} url
   * @param {string} method
   * @param {object | null} payload
   */
  async function pedir(url, method, payload) {
    /** @type {RequestInit} */
    const init = { method };
    if (payload) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(payload);
    }
    const res = await fetch(url, init);
    const body = await res.json().catch(() => ({}));
    return { ok: res.ok, body };
  }

  function cerrar() {
    dialogo = null;
    nivelPendiente = null;
    retirarConLocal = false;
  }

  async function aplicarProveedor() {
    if (borrador === "local" && config?.provider === "ppi") {
      retirarConLocal = false;
      dialogo = "local";
      return;
    }
    await guardarProveedor(false);
  }

  /** @param {boolean} retirar */
  async function guardarProveedor(retirar) {
    saving = true;
    try {
      /** @type {{provider: string, transmission_statement?: string}} */
      const payload = { provider: borrador };
      if (borrador === "ppi") payload.transmission_statement = declaracion.trim();
      const puesto = await pedir("/admin/inteligencia/api", "PUT", payload);
      if (!puesto.ok) {
        notifyError(textoError(puesto.body, "No se pudo cambiar el proveedor."));
        return;
      }
      if (retirar) {
        const baja = await pedir("/admin/inteligencia/api/retiro", "POST", null);
        if (!baja.ok) {
          notifyError(
            "El proveedor quedó en el índice local, pero el borrado no fue aceptado. " +
              textoError(baja.body, "Vuelva a pedir el retiro."),
          );
          await invalidateAll();
          return;
        }
      }
      notifySuccess(
        borrador === "ppi"
          ? "La entidad transmite por el proveedor. Quedó habilitada PÚBLICA."
          : retirar
            ? "La entidad usa el índice local y se pidió el borrado de los datos."
            : "La entidad usa el índice local. Los datos ya transmitidos siguen en el proveedor.",
      );
      cerrar();
      await invalidateAll();
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      saving = false;
    }
  }

  async function guardarMeta() {
    saving = true;
    try {
      const puesto = await pedir("/admin/inteligencia/api", "PUT", { send_metadata: meta });
      if (!puesto.ok) {
        notifyError(textoError(puesto.body, "No se pudo cambiar el envío de metadatos."));
        return;
      }
      notifySuccess(
        meta
          ? "Los metadatos de las plantillas se transmiten."
          : "Los metadatos de las plantillas dejan de transmitirse.",
      );
      await invalidateAll();
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      saving = false;
    }
  }

  /** @param {number} nivel */
  function pedirBaja(nivel) {
    nivelPendiente = nivel;
    dialogo = "nivel";
  }

  /** @param {number} nivel @param {boolean} enabled */
  async function guardarNivel(nivel, enabled) {
    saving = true;
    try {
      /** @type {{level: number, enabled: boolean, statement?: string}} */
      const payload = { level: nivel, enabled };
      if (enabled) payload.statement = (declaraciones[nivel] ?? "").trim();
      const puesto = await pedir("/admin/inteligencia/api/niveles", "PUT", payload);
      if (!puesto.ok) {
        notifyError(textoError(puesto.body, "No se pudo cambiar el nivel."));
        return;
      }
      const purga = puesto.body?.purge;
      if (!enabled && purga === "pending") {
        notifyError(
          `${ETIQUETA_NIVEL[nivel]} quedó deshabilitada, ` +
            "pero el borrado en el proveedor sigue pendiente.",
        );
      } else {
        notifySuccess(
          enabled
            ? `${ETIQUETA_NIVEL[nivel]} queda habilitada.`
            : `${ETIQUETA_NIVEL[nivel]} queda deshabilitada ` +
              "y se pidió el borrado de lo transmitido.",
        );
      }
      cerrar();
      await invalidateAll();
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      saving = false;
    }
  }

  async function retirar() {
    saving = true;
    try {
      const baja = await pedir("/admin/inteligencia/api/retiro", "POST", null);
      if (!baja.ok) {
        notifyError(textoError(baja.body, "El proveedor no aceptó el borrado."));
        return;
      }
      notifySuccess("Se pidió el borrado de todo lo que el proveedor conserva de la entidad.");
      cerrar();
      await invalidateAll();
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      saving = false;
    }
  }

  async function confirmar() {
    if (dialogo === "local") await guardarProveedor(retirarConLocal);
    else if (dialogo === "retiro") await retirar();
    else if (dialogo === "nivel" && nivelPendiente) await guardarNivel(nivelPendiente, false);
  }

  /**
   * @param {number} nivel
   */
  function habilitado(nivel) {
    return Array.isArray(config?.levels) && config.levels.includes(nivel);
  }

  /**
   * @param {string} id
   */
  function nombreSub(id) {
    const sub = (data.capabilities?.subprocessors ?? []).find((fila) => fila.id === id);
    return sub ? `${sub.name} (${id})` : id;
  }

  const NUMERO_NIVEL = { public: 1, reserved: 2, classified: 3 };

  /** @param {string} level */
  function etiquetaFila(level) {
    return ETIQUETA_NIVEL[NUMERO_NIVEL[level]] ?? level;
  }
</script>

<svelte:head>
  <title>Inteligencia — Docuboto</title>
</svelte:head>

<div class="intel">
  <header class="intel__header">
    <h1>Inteligencia</h1>
    <p>
      Elija si la entidad transmite radicados a un proveedor y qué niveles salen. Volver al índice
      local no borra lo ya transmitido.
    </p>
  </header>

  {#if data.configError}
    <p class="intel__error" role="alert">{data.configError}</p>
  {:else if config}
    <p class="intel__estado" role="status" data-testid="estado">
      {#if config.suspended}
        El envío está suspendido. Vuelva a declarar cada nivel habilitado.
      {:else if config.provider === "ppi"}
        El envío está activo.
      {:else}
        La entidad usa el índice local. No se transmiten radicados.
      {/if}
    </p>

    <section class="intel__card" aria-labelledby="intel-proveedor">
      <h2 id="intel-proveedor">Proveedor</h2>
      <fieldset class="intel__radios">
        <legend class="sr-only">Proveedor de inteligencia</legend>
        <label>
          <input type="radio" name="proveedor" value="local" bind:group={borrador} />
          Índice local
        </label>
        <label>
          <input type="radio" name="proveedor" value="ppi" bind:group={borrador} />
          Proveedor PPI
        </label>
      </fieldset>

      {#if config.provider === "local"}
        {#if data.inventoryError}
          <p class="intel__error" role="alert" data-testid="inventario-error">
            {data.inventoryError}
          </p>
        {:else if data.inventory}
          <p data-testid="inventario">{fraseInventario(data.inventory)}</p>
        {/if}
      {/if}

      {#if data.capabilitiesError}
        <p class="intel__error" role="alert" data-testid="capabilities-error">
          {data.capabilitiesError}
          Sin esa declaración no puede pasar al proveedor ni habilitar un nivel.
        </p>
      {:else if data.capabilities?.provider}
        <div class="intel__scroll">
          <table class="intel__rutas">
            <caption>Subencargados y ruta de cada nivel, según la declaración firmada.</caption>
            <thead>
              <tr>
                <th scope="col">Nivel</th>
                <th scope="col">Dónde corre</th>
                <th scope="col">Modelos</th>
                <th scope="col">Subencargados</th>
                <th scope="col">Retención</th>
              </tr>
            </thead>
            <tbody>
              {#each data.capabilities.routing ?? [] as fila (fila.level)}
                <tr>
                  <td>{etiquetaFila(fila.level)}</td>
                  <td>{etiquetaHosting(fila.hosting)}</td>
                  <td>{fila.model_aliases.join(", ")}</td>
                  <td>
                    {fila.subprocessors.length
                      ? fila.subprocessors.map(nombreSub).join(", ")
                      : "Ninguno"}
                  </td>
                  <td>
                    {etiquetaRetencion(fila.prompt_and_output_retention)}
                    {fila.trains_on_customer_data ? " Entrena con los datos." : " No entrena."}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <p>
          Proveedor: {data.capabilities.provider.name} ({data.capabilities.provider.id}).
          {#if data.capabilities.subprocessors?.length}
            Subencargados:
            {data.capabilities.subprocessors.map((sub) => `${sub.name} (${sub.id})`).join(", ")}.
          {/if}
        </p>
      {/if}

      {#if pideDeclaracion && data.capabilities?.provider}
        <label class="intel__campo" for="declaracion-proveedor">
          Declaración
          <textarea
            id="declaracion-proveedor"
            maxlength="4000"
            rows="4"
            bind:value={declaracion}
            data-testid="declaracion-proveedor"
          ></textarea>
        </label>
        <p class="intel__hint">
          Tiene que incluir, tal cual:
          {exigidosProveedor(data.capabilities).join(", ")}.
        </p>
      {/if}

      <Button on:click={aplicarProveedor} disabled={!puedeAplicar} loading={saving}>
        {redeclararPublica && borrador === "ppi"
          ? "Volver a declarar PÚBLICA"
          : "Aplicar proveedor"}
      </Button>
    </section>

    <section class="intel__card" aria-labelledby="intel-meta">
      <h2 id="intel-meta">Metadatos de plantilla</h2>
      <p>Es todo o nada. Apagado, los radicados salen sin los metadatos de las plantillas.</p>
      <label>
        <input type="checkbox" bind:checked={meta} />
        Enviar los metadatos de las plantillas
      </label>
      <Button
        variant="secondary"
        on:click={guardarMeta}
        disabled={saving || meta === (config.send_metadata === true)}
      >
        Aplicar metadatos
      </Button>
    </section>

    {#if config.provider === "ppi"}
      <section class="intel__card" aria-labelledby="intel-niveles">
        <h2 id="intel-niveles">Niveles</h2>
        <p>
          PÚBLICA queda habilitada al pasar al proveedor. RESERVADA y CLASIFICADA son otro acto.
        </p>
        <ul class="intel__niveles">
          <li>
            <strong>PÚBLICA</strong>
            {habilitado(1) ? "habilitada" : "no habilitada"}.
          </li>
          {#each EXTRA as nivel (nivel)}
            {@const fila = filaDe(data.capabilities, nivel)}
            {@const admitida = rutaAdmitida(fila)}
            <li>
              <strong>{ETIQUETA_NIVEL[nivel]}</strong>
              {habilitado(nivel) ? "habilitada" : "no habilitada"}.
              {#if !fila}
                <p>El proveedor no declara la ruta de {ETIQUETA_NIVEL[nivel]}.</p>
              {:else if !admitida}
                <p>
                  La ruta {etiquetaHosting(fila.hosting)} conserva o entrena con los datos. No se
                  puede habilitar.
                </p>
              {:else if habilitado(nivel)}
                {#if config.suspended}
                  <label class="intel__campo" for="declaracion-{nivel}">
                    Declaración de {ETIQUETA_NIVEL[nivel]}
                    <textarea
                      id="declaracion-{nivel}"
                      maxlength="4000"
                      rows="3"
                      bind:value={declaraciones[nivel]}
                    ></textarea>
                  </label>
                  <p class="intel__hint">
                    Tiene que incluir, tal cual:
                    {(exigidosNivel(data.capabilities, nivel) ?? []).join(", ")}.
                  </p>
                  <Button on:click={() => guardarNivel(nivel, true)} disabled={saving}>
                    Volver a declarar {ETIQUETA_NIVEL[nivel]}
                  </Button>
                {/if}
                <Button variant="danger" on:click={() => pedirBaja(nivel)} disabled={saving}>
                  Deshabilitar {ETIQUETA_NIVEL[nivel]}
                </Button>
              {:else}
                <label class="intel__campo" for="declaracion-{nivel}">
                  Declaración de {ETIQUETA_NIVEL[nivel]}
                  <textarea
                    id="declaracion-{nivel}"
                    maxlength="4000"
                    rows="3"
                    bind:value={declaraciones[nivel]}
                  ></textarea>
                </label>
                <p class="intel__hint">
                  Tiene que incluir, tal cual:
                  {(exigidosNivel(data.capabilities, nivel) ?? []).join(", ")}.
                </p>
                <Button
                  on:click={() => guardarNivel(nivel, true)}
                  disabled={saving || !(declaraciones[nivel] ?? "").trim()}
                >
                  Habilitar {ETIQUETA_NIVEL[nivel]}
                </Button>
              {/if}
            </li>
          {/each}
        </ul>
      </section>
    {/if}

    <section class="intel__card" aria-labelledby="intel-retiro">
      <h2 id="intel-retiro">Retiro de datos</h2>
      <p>
        Pide al proveedor el borrado de todo lo de la entidad. No cambia el proveedor. El asiento
        queda solo si el proveedor acepta.
      </p>
      <Button variant="danger" on:click={() => (dialogo = "retiro")} disabled={saving}>
        Retirar los datos
      </Button>
    </section>
  {/if}
</div>

<Modal
  open={dialogo !== null}
  title={dialogo === "nivel" ? "Deshabilitar el nivel" : "Confirmar"}
  on:close={cerrar}
>
  {#if dialogo === "local"}
    <p>
      La entidad pasa al índice local. Las llamadas se cortan. Lo ya transmitido sigue en el
      proveedor hasta que lo retire.
    </p>
    <label>
      <input type="checkbox" bind:checked={retirarConLocal} data-testid="retirar-tambien" />
      Retirar también los datos
    </label>
  {:else if dialogo === "retiro"}
    <p>Se pide el borrado de todo lo que el proveedor conserva de esta entidad.</p>
  {:else if dialogo === "nivel"}
    <p>
      {ETIQUETA_NIVEL[nivelPendiente ?? 0]} deja de transmitirse y se ordena el borrado de lo ya
      enviado de ese nivel.
    </p>
  {/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={cerrar} disabled={saving}>Cancelar</Button>
    <Button variant="danger" on:click={confirmar} loading={saving}>Confirmar</Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .intel {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
    max-width: 52rem;

    &__header h1 {
      margin: 0 0 var(--op-space-2);
    }

    &__header p,
    &__card p {
      margin: 0;
    }

    &__estado {
      margin: 0;
      font-weight: var(--op-weight-bold);
    }

    &__error {
      margin: 0;
      color: var(--op-danger, #9b2c2c);
    }

    &__card {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-3);
      padding: var(--op-space-4);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      background: var(--op-surface);

      h2 {
        margin: 0;
        font-size: var(--op-font-sm);
      }
    }

    &__radios,
    &__niveles {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-2);
      margin: 0;
      padding: 0;
      border: 0;
    }

    &__niveles {
      list-style: none;
    }

    &__campo {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
      min-width: 0;

      textarea {
        width: 100%;
        max-width: 100%;
        box-sizing: border-box;
      }
    }

    &__hint {
      font-size: var(--op-font-sm);
      overflow-wrap: anywhere;
    }

    &__scroll {
      overflow-x: auto;
      max-width: 100%;
    }

    &__rutas {
      width: 100%;
      border-collapse: collapse;

      th,
      td {
        text-align: left;
        padding: var(--op-space-2);
        border-bottom: 1px solid var(--op-border);
        vertical-align: top;
      }

      caption {
        text-align: left;
        margin-bottom: var(--op-space-2);
      }
    }
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
</style>
