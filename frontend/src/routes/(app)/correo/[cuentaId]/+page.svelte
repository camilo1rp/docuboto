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
  /correo/[cuentaId] — Bandeja de un buzón institucional (E19 Increment 2).

  **La pantalla que hace visible la reserva.** Varias personas de correspondencia
  trabajan el mismo buzón; el backend impide que dos radiquen el mismo correo,
  pero si la pantalla no lo mostrara, el segundo lo descubriría pulsando y
  llevándose un error que no esperaba. Aquí un correo ocupado se ve ocupado.

  El Orfeo legacy tenía la lista pero no esto: allí un correo ya radicado seguía
  apareciendo con una «R» roja, sin bloqueo, sin aviso y sin recarga.

  **Aviso a las demás pantallas.** Un sondeo cada pocos segundos pide solo el
  RESUMEN de la bandeja —cuántos pendientes, cuántos ocupados y la marca de la
  última novedad— y la lista se recarga únicamente si esa marca cambió. Se hizo
  así, y no con una conexión permanente, porque resuelve el problema real con lo
  que el proyecto ya tiene: es el mismo patrón honesto de `/admin/interoperabilidad`.
  Cuando el sondeo falla se DICE, en vez de dejar una pantalla que parece viva y
  lleva minutos congelada.
-->
<script>
  import { onDestroy, onMount } from "svelte";
  import { invalidateAll } from "$app/navigation";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { vigilarInactividad } from "$lib/utils/inactividad.js";
  import { notifyError, notifySuccess, notifyWarning } from "$lib/stores/notifications.js";
  import { codigoError, mensajeError } from "$lib/utils/apiError.js";
  import Button from "$lib/components/ui/Button.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  $: correos = data.correos ?? [];
  $: correosError = data.correosError ?? null;

  /**
   * La bandeja no se pudo leer — no existe el buzón, o no está al alcance de
   * quien mira.
   *
   * Lo que esto apaga importa: sin ello, el sondeo seguía preguntando cada 12 s,
   * fallaba con el mismo 404 y la pantalla anunciaba «no se está pudiendo
   * comprobar si hay novedades, actualícela a mano antes de radicar» — sobre una
   * bandeja que no existe y en la que no se puede radicar nada. Un error de
   * autorización presentado como un fallo transitorio del sondeo: el mismo
   * patrón que hizo que `/envios` presentara un 403 como «no hay registros».
   */
  $: bandejaInaccesible = Boolean(correosError);
  $: cuenta = data.cuenta ?? null;
  $: yo = data.yo ?? "";

  // Los dos vocabularios que se ELIGEN al radicar, con la misma forma que en
  // `/radicar`: la dependencia se identifica por su CÓDIGO de organigrama y el
  // tipo documental por su código de catálogo. Se leen `d.codigo` y `t.nombre`
  // —los nombres reales de cada router, que no comparten convención— sin valor
  // de cortesía detrás: un `?? algo` convertiría el próximo cambio de contrato
  // en un desplegable de «undefined» en vez de en un fallo visible.
  $: dependenciaOpciones = (data.dependencias ?? []).map((d) => ({
    value: String(d.codigo ?? d.id),
    label: `${d.sigla ? d.sigla + " — " : ""}${d.nombre}`,
  }));
  $: tipoOpciones = (data.tiposDocumentales ?? []).map((t) => ({
    value: String(t.code ?? t.id),
    label: t.nombre,
  }));

  /**
   * Preselección de la dependencia a partir de la que la cuenta trae escrita.
   *
   * `email_cuenta.dependencia_destino` es texto libre (columna heredada), así
   * que se casa por nombre o sigla contra el organigrama. **Si no casa no se
   * preselecciona nada**: preseleccionar la primera de la lista pondría un
   * destino que nadie eligió en un acto que no se deshace.
   */
  function codigoPorNombre(escrito) {
    const buscado = String(escrito ?? "")
      .trim()
      .toLowerCase();
    if (!buscado) return "";
    const hallado = (data.dependencias ?? []).find(
      (d) =>
        String(d.nombre ?? "")
          .trim()
          .toLowerCase() === buscado ||
        String(d.sigla ?? "")
          .trim()
          .toLowerCase() === buscado,
    );
    return hallado ? String(hallado.codigo ?? hallado.id) : "";
  }

  const SONDEO_MS = 12000;
  // Umbral de inactividad REAL de la persona, muy por debajo del de Keycloak
  // (30 min en producción): el sondeo tiene que parar con margen para que el
  // temporizador del servidor llegue a expirar de verdad.
  const INACTIVIDAD_MS = 5 * 60 * 1000;

  let sondeo = null;
  // Se anuncia en pantalla cuando el sondeo se detiene por inactividad: una
  // bandeja que deja de actualizarse en silencio se lee como una bandeja sin
  // novedades, que es lo contrario de lo que pasa.
  let sondeoPausadoPorInactividad = false;
  let marcaConocida = null;
  let sondeoCaido = false;
  let hayNovedades = false;

  let trabajando = "";
  let sincronizando = false;
  /** Correo abierto para radicar, ya reservado. */
  let abierto = null;
  let formulario = null;
  let descartando = null;
  let motivoDescarte = "";

  function esMio(correo) {
    return Boolean(yo) && correo.reservado_vigente && correo.reservado_por === yo;
  }

  /**
   * Reservado por otra persona.
   *
   * Si no se pudo resolver la identidad interna (`yo` vacío) NO se afirma que
   * sea de otro: se muestra como reservado sin dueño conocido y se deja
   * intentar, porque el backend es quien decide. Deshabilitar por no saber
   * bloquearía la ventanilla; darlo por propio escondería una reserva ajena.
   */
  function esDeOtro(correo) {
    return Boolean(yo) && correo.reservado_vigente && correo.reservado_por !== yo;
  }

  function estaReservadoPorAlguien(correo) {
    return correo.reservado_vigente && !esMio(correo);
  }

  function fecha(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("es-CO");
  }

  // ── Sondeo ──────────────────────────────────────────────────────────────
  async function mirarSiHayNovedades() {
    try {
      const res = await fetch(`/correo/${data.cuentaId}/api/estado`);
      if (!res.ok) {
        sondeoCaido = true;
        return;
      }
      sondeoCaido = false;
      const estado = await res.json();
      const marca = estado.ultima_novedad ?? null;
      if (marcaConocida === null) {
        marcaConocida = marca;
        return;
      }
      if (marca !== marcaConocida) {
        marcaConocida = marca;
        // Si el operador tiene un correo abierto, NO se recarga por debajo: se
        // avisa y decide él. Recargar mientras completa un formulario le
        // borraría lo escrito, y eso es peor que enterarse un minuto más tarde.
        if (abierto || descartando) hayNovedades = true;
        else await invalidateAll();
      }
    } catch {
      sondeoCaido = true;
    }
  }

  async function recargarAhora() {
    hayNovedades = false;
    await invalidateAll();
  }

  // ── Acciones ────────────────────────────────────────────────────────────
  async function accion(correo, nombre, cuerpo = {}) {
    const res = await fetch(`/correo/${data.cuentaId}/api/${correo.id}?accion=${nombre}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cuerpo),
    });
    const datos = await res.json().catch(() => ({}));
    return { ok: res.ok, datos, status: res.status };
  }

  async function abrir(correo) {
    trabajando = correo.id;
    try {
      const { ok, datos } = await accion(correo, "reservar");
      if (!ok) {
        // 409 no es una avería: es la reserva funcionando. Se recarga para que
        // la lista muestre quién lo tiene.
        notifyWarning(mensajeError(datos, `No se pudo abrir (${codigoError(datos)}).`));
        await recargarAhora();
        return;
      }
      abierto = correo;
      formulario = {
        subject: correo.asunto ?? "",
        sender_name: correo.remitente_nombre ?? "",
        dest_dept_code: codigoPorNombre(cuenta?.dependencia_destino),
        doc_class: "",
      };
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      trabajando = "";
    }
  }

  async function cerrarSinRadicar() {
    const correo = abierto;
    abierto = null;
    formulario = null;
    // Cerrar el formulario suelta la reserva. Sin esto, mirar un correo y
    // decidir que no es para uno lo dejaba bloqueado quince minutos para toda
    // la ventanilla — y la única salida era esperar a que caducara.
    if (correo) await soltar(correo, { avisar: false });
    await recargarAhora();
  }

  /**
   * Suelta la reserva propia. No decide nada sobre el correo.
   *
   * Best-effort a propósito: si la petición falla, el peor caso es que la
   * reserva siga puesta hasta que caduque sola, que es exactamente lo que pasaba
   * antes de que esto existiera. Convertirlo en un error bloquearía el cierre
   * del formulario por un problema que se resuelve solo.
   */
  async function soltar(correo, { avisar = true } = {}) {
    try {
      const { ok, datos } = await accion(correo, "liberar");
      if (!ok && avisar) {
        notifyWarning(mensajeError(datos, `No se pudo soltar (${codigoError(datos)}).`));
        return;
      }
      if (avisar) await recargarAhora();
    } catch {
      if (avisar) notifyError("No se pudo conectar al servidor.");
    }
  }

  async function radicar() {
    if (!abierto) return;
    trabajando = abierto.id;
    try {
      const { ok, datos } = await accion(abierto, "radicar", formulario);
      if (!ok) {
        notifyError(mensajeError(datos, `No se pudo radicar (${codigoError(datos)}).`));
        return;
      }
      notifySuccess(
        `Radicado ${datos.tracking_number}` +
          (datos.anexos ? ` con ${datos.anexos} anexo(s)` : "") +
          (datos.movido_a ? `. El correo se movió a «${datos.movido_a}».` : "."),
      );
      abierto = null;
      formulario = null;
      await recargarAhora();
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      trabajando = "";
    }
  }

  async function confirmarDescarte() {
    if (!descartando) return;
    trabajando = descartando.id;
    try {
      const { ok, datos } = await accion(descartando, "descartar", { motivo: motivoDescarte });
      if (!ok) {
        notifyError(mensajeError(datos, `No se pudo descartar (${codigoError(datos)}).`));
        return;
      }
      notifySuccess("El correo se apartó de la bandeja.");
      descartando = null;
      motivoDescarte = "";
      await recargarAhora();
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      trabajando = "";
    }
  }

  async function pedirDescarte(correo) {
    // Descartar exige tener el correo abierto, igual que radicar: es una
    // decisión sobre una comunicación oficial recibida.
    trabajando = correo.id;
    const { ok, datos } = await accion(correo, "reservar");
    trabajando = "";
    if (!ok) {
      notifyWarning(mensajeError(datos, "Otro funcionario está trabajando este correo."));
      await recargarAhora();
      return;
    }
    descartando = correo;
    motivoDescarte = "";
  }

  async function sincronizar() {
    sincronizando = true;
    try {
      const res = await fetch(`/correo/${data.cuentaId}/api/sincronizar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const datos = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(mensajeError(datos, `No se pudo consultar el buzón (${codigoError(datos)}).`));
        return;
      }
      if (datos.nuevos === 0) notifySuccess(`No hay correos nuevos en «${datos.carpeta}».`);
      else notifySuccess(`${datos.nuevos} correo(s) nuevo(s) en la bandeja.`);
      if (datos.hay_mas) {
        // El tope se DECLARA en vez de recortar en silencio: sin decirlo,
        // «trajo 50» sería indistinguible de «eso era todo».
        notifyWarning("Quedan más correos sin traer en el buzón. Vuelva a consultar para seguir.");
      }
      await recargarAhora();
    } catch {
      notifyError("No se pudo conectar al servidor.");
    } finally {
      sincronizando = false;
    }
  }

  onMount(() => {
    setBreadcrumbs([
      { label: "Administración", href: "/admin" },
      { label: "Ingesta de correo", href: "/admin/correo" },
      { label: cuenta?.nombre ?? "Bandeja" },
    ]);
    // Sin bandeja que vigilar no se sondea: preguntaría cada 12 s por algo que
    // ya se sabe que no se alcanza.
    if (!bandejaInaccesible) sondeo = setInterval(mirarSiHayNovedades, SONDEO_MS);

    // El sondeo PARA sin interacción real de la persona. No es un cierre de
    // sesión desde el navegador —ese control vive en Keycloak y ahí debe
    // quedarse—: es dejar de FINGIR actividad. Cada sondeo pasa por el
    // `handle` del servidor, dispara el refresco transparente del token y
    // reinicia el contador de inactividad de Keycloak, así que una pestaña
    // olvidada mantenía la sesión viva hasta el tope absoluto de 10 h con la
    // pantalla desbloqueada. El servidor tenía la política correcta y esta
    // pantalla la desactivaba sin que nadie lo hubiera decidido.
    const dejarDeVigilar = vigilarInactividad({
      msSinActividad: INACTIVIDAD_MS,
      alInactivo: () => {
        if (sondeo) clearInterval(sondeo);
        sondeo = null;
        sondeoPausadoPorInactividad = true;
      },
      alActivo: () => {
        sondeoPausadoPorInactividad = false;
        if (!bandejaInaccesible && !sondeo) {
          // Al volver, una pasada inmediata: si no, la primera novedad tardaría
          // hasta 12 s en aparecer justo cuando alguien acaba de mirar.
          mirarSiHayNovedades();
          sondeo = setInterval(mirarSiHayNovedades, SONDEO_MS);
        }
      },
    });

    // El intervalo se limpia en el RETORNO de onMount y no en onDestroy:
    // `onDestroy` corre también en SSR, donde no hay temporizador que limpiar.
    return () => {
      clearInterval(sondeo);
      dejarDeVigilar();
    };
  });

  onDestroy(() => {});
</script>

<svelte:head>
  <title>Bandeja de {cuenta?.nombre ?? "correo"} — OrpycaMCP</title>
</svelte:head>

<div class="bandeja">
  <header class="bandeja__header">
    <div>
      <h1 class="bandeja__title">{cuenta?.nombre ?? "Bandeja de correo"}</h1>
      <p class="bandeja__subtitle">
        {#if cuenta}
          Correos de <code class="op-dato">{cuenta.direccion}</code> pendientes de radicar, leídos
          de la carpeta <strong>{cuenta.carpeta_origen}</strong>.
        {:else}
          Correos pendientes de radicar.
        {/if}
      </p>
    </div>
    <Button
      icon="fas fa-inbox-in"
      loading={sincronizando}
      disabled={bandejaInaccesible}
      on:click={sincronizar}
    >
      Consultar el buzón
    </Button>
  </header>

  {#if hayNovedades}
    <div class="bandeja__novedades" role="status">
      <span>
        La bandeja cambió mientras trabajaba: otro funcionario radicó o tomó algún correo.
      </span>
      <Button size="sm" variant="ghost" on:click={recargarAhora}>Actualizar la lista</Button>
    </div>
  {/if}

  {#if sondeoPausadoPorInactividad && !bandejaInaccesible}
    <div class="bandeja__aviso" role="status">
      <span>
        La comprobación automática de novedades está <strong>en pausa</strong> por inactividad. Se reanuda
        sola al usar la pantalla. Sin esta pausa, una pestaña olvidada mantendría la sesión abierta indefinidamente.
      </span>
    </div>
  {/if}

  {#if sondeoCaido && !bandejaInaccesible}
    <div class="bandeja__aviso" role="status">
      <span>
        No se está pudiendo comprobar si hay novedades, así que esta lista puede estar
        desactualizada. Actualícela a mano antes de radicar.
      </span>
    </div>
  {/if}

  {#if correosError}
    <EmptyState
      icon="fas fa-triangle-exclamation"
      title={correosError.status === 403
        ? "Sin permiso para ver esta bandeja"
        : "No se pudo cargar la bandeja"}
      description="{correosError.detail} Esto no significa que la bandeja esté vacía: no se pudo preguntar."
    />
  {:else if correos.length === 0}
    <EmptyState
      icon="fas fa-inbox"
      title="No hay correos pendientes"
      description="Cuando alguien mueva correspondencia a la carpeta de trabajo del buzón, aparecerá aquí al consultar."
      action="Consultar el buzón"
    />
  {:else}
    <ul class="correos" role="list">
      {#each correos as correo (correo.id)}
        <li class="correo" class:correo--ocupado={esDeOtro(correo)}>
          <div class="correo__cabecera">
            <span class="correo__asunto">{correo.asunto || "(correo sin asunto)"}</span>
            {#if correo.num_adjuntos > 0}
              <span class="correo__chip">
                <i class="fas fa-paperclip" aria-hidden="true"></i>
                {correo.num_adjuntos}
              </span>
            {/if}
          </div>

          <p class="correo__meta">
            De <strong>{correo.remitente_nombre || correo.remitente_email || "—"}</strong>
            {#if correo.remitente_email}
              <code class="op-dato">{correo.remitente_email}</code>
            {/if}
            · recibido el {fecha(correo.fecha_recibo)}
          </p>

          {#if estaReservadoPorAlguien(correo)}
            <!--
              Lo que Orfeo no mostraba. Sin esto, el segundo funcionario descubre
              que el correo está tomado pulsando «Abrir» y llevándose un error
              que no esperaba.
            -->
            <p class="correo__ocupado">
              <i class="fas fa-user-lock" aria-hidden="true"></i>
              {esDeOtro(correo)
                ? "Otro funcionario lo está trabajando."
                : "Está reservado. Si no es suyo, no podrá abrirlo."}
            </p>
          {:else if esMio(correo)}
            <p class="correo__mio">
              <i class="fas fa-user-check" aria-hidden="true"></i>
              Lo tiene usted abierto.
              <button type="button" class="correo__soltar" on:click={() => soltar(correo)}>
                Soltarlo
              </button>
            </p>
          {/if}

          <div class="correo__acciones">
            <Button
              size="sm"
              icon="fas fa-folder-open"
              disabled={esDeOtro(correo)}
              loading={trabajando === correo.id}
              on:click={() => abrir(correo)}
            >
              Abrir y radicar
            </Button>
            <Button
              size="sm"
              variant="ghost"
              icon="fas fa-ban"
              disabled={esDeOtro(correo)}
              on:click={() => pedirDescarte(correo)}
            >
              No radicar
            </Button>
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<!-- Radicar el correo abierto -->
<Modal open={Boolean(abierto)} title="Radicar el correo" size="lg" on:close={cerrarSinRadicar}>
  {#if abierto && formulario}
    <div class="form">
      <p class="form__nota">
        Se creará un documento de <strong>Entrada</strong> con número oficial. Sus
        {abierto.num_adjuntos} anexo(s) y el mensaje original se conservarán, y el correo se moverá a
        <strong>{cuenta?.carpeta_radicados ?? "la carpeta de radicados"}</strong>.
      </p>
      <p class="form__nota">
        Recibido en el buzón el <strong>{fecha(abierto.fecha_recibo)}</strong>. Esa fecha queda
        registrada como la de recibo de la comunicación, distinta de la fecha de radicación.
      </p>

      <label class="form__campo">
        <span>Asunto</span>
        <input class="input" bind:value={formulario.subject} />
        <small>Llega del correo; corríjalo si el remitente lo escribió mal.</small>
      </label>
      <label class="form__campo">
        <span>Remitente</span>
        <input class="input" bind:value={formulario.sender_name} />
      </label>
      <label class="form__campo">
        <span>Dependencia competente</span>
        <div class="select is-fullwidth">
          <select bind:value={formulario.dest_dept_code}>
            <option value="">— seleccionar —</option>
            {#each dependenciaOpciones as opcion (opcion.value)}
              <option value={opcion.value}>{opcion.label}</option>
            {/each}
          </select>
        </div>
        <small>A quién se reparte para su trámite. Se elige del organigrama.</small>
      </label>
      <label class="form__campo">
        <span>Tipo documental <em>(opcional)</em></span>
        <div class="select is-fullwidth">
          <select bind:value={formulario.doc_class}>
            <option value="">— sin clasificar —</option>
            {#each tipoOpciones as opcion (opcion.value)}
              <option value={opcion.value}>{opcion.label}</option>
            {/each}
          </select>
        </div>
        <small>Gobierna la retención según la TRD; puede tipificarse después.</small>
      </label>
    </div>
  {/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={cerrarSinRadicar} disabled={Boolean(trabajando)}>
      Cancelar
    </Button>
    <Button loading={Boolean(trabajando)} on:click={radicar}>Radicar</Button>
  </svelte:fragment>
</Modal>

<!-- Descartar -->
<Modal
  open={Boolean(descartando)}
  title="No radicar este correo"
  on:close={() => {
    descartando = null;
  }}
>
  <div class="form">
    <p class="form__nota">
      El correo se apartará de la bandeja sin crear ningún radicado. Queda registrado quién lo
      decidió y por qué: es una decisión sobre una comunicación recibida.
    </p>
    <label class="form__campo">
      <span>Motivo</span>
      <input class="input" bind:value={motivoDescarte} placeholder="Boletín publicitario" />
      <small>Mínimo cinco caracteres.</small>
    </label>
  </div>
  <svelte:fragment slot="footer">
    <Button
      variant="ghost"
      on:click={() => {
        descartando = null;
      }}
    >
      Cancelar
    </Button>
    <Button
      variant="danger"
      disabled={motivoDescarte.trim().length < 5}
      loading={Boolean(trabajando)}
      on:click={confirmarDescarte}
    >
      Apartar de la bandeja
    </Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .bandeja {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
  }

  .bandeja__header {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: flex-start;
    gap: var(--op-space-4);
  }

  .bandeja__title {
    font-size: var(--op-font-xl);
    font-weight: 700;
  }

  .bandeja__subtitle {
    color: var(--op-text-secondary);
  }

  .bandeja__novedades,
  .bandeja__aviso {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--op-space-3);
    padding: var(--op-space-3) var(--op-space-4);
    border-radius: var(--op-radius);
  }

  .bandeja__novedades {
    background: var(--op-info-bg);
    color: var(--op-info-fg);
  }

  .bandeja__aviso {
    background: var(--op-warning-bg);
    color: var(--op-warning-fg);
  }

  .correos {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
  }

  .correo {
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-4);
  }

  .correo--ocupado {
    background: var(--op-bg);
    opacity: 0.75;
  }

  .correo__cabecera {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--op-space-2);
  }

  .correo__asunto {
    font-weight: 700;
  }

  .correo__chip {
    font-size: var(--op-font-xs);
    padding: 0.1rem 0.5rem;
    border-radius: var(--op-radius-full);
    border: 1px solid var(--op-border);
    color: var(--op-text-secondary);
  }

  .correo__meta {
    margin-top: var(--op-space-2);
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
  }

  .correo__ocupado,
  .correo__mio {
    margin-top: var(--op-space-2);
    font-size: var(--op-font-sm);
  }

  .correo__ocupado {
    color: var(--op-warning-fg);
  }

  .correo__mio {
    color: var(--op-info-fg);
  }

  .correo__soltar {
    margin-left: var(--op-space-2);
    padding: 0;
    border: 0;
    background: none;
    color: inherit;
    font: inherit;
    text-decoration: underline;
    cursor: pointer;
  }

  .correo__acciones {
    display: flex;
    flex-wrap: wrap;
    gap: var(--op-space-2);
    margin-top: var(--op-space-3);
  }

  .form {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);
  }

  .form__nota {
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
  }

  .form__campo {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-1);

    > span {
      font-weight: 600;
    }

    small {
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
    }
  }
</style>
