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
  /desclasificaciones — Cola de la SEGUNDA persona (C-2, ADR-039).

  Bajar el nivel de un radicado CLASIFICADO (nivel 3, Ley 1712 art. 18: derechos
  de las personas) no lo decide quien lo pide: lo decide otra persona con
  PERM_RECLASIFICAR crud>=4. Esta es su cola. Quien solicitó puede retirar su
  solicitud pero NO decidirla (las filas `propia` no llevan botones de decisión;
  el backend lo re-valida con 409 `separacion_de_funciones`).

  Aprobar es irreversible en la práctica: la información se difunde. Por eso
  lleva confirmación escalonada (revisar el radicado → teclear su número) y el
  aprobador tiene el enlace al radicado ANTES de decidir: aprobar leyendo solo la
  versión del solicitante es la peor forma de decidir barato.

  D-05: el access_token nunca llega al cliente; todo va por /desclasificaciones/api/*.
-->
<script>
  import { onMount, tick } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { formatearFechaHora } from "$lib/utils/fecha.js";
  import { permissionLevels } from "$lib/stores/auth.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";

  import Modal from "$lib/components/ui/Modal.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";
  import { getNivelChip } from "$lib/utils/nivelSeguridad.js";
  import { puedeDecidir, puedeSolicitar } from "$lib/utils/reserva.js";
  import { aprobacionConfirmada, fraseAprobacion } from "$lib/utils/desclasificacionConfirm.js";

  /** @type {import('./$types').PageData} */
  export let data;

  // ---------------------------------------------------------------------------
  // Permisos (espejo del RBAC; el backend re-valida en cada operación)
  // ---------------------------------------------------------------------------
  $: canDecidir = puedeDecidir($permissionLevels);
  $: canRetirar = puedeSolicitar($permissionLevels);

  const DOC_TYPE_LABELS = /** @type {Record<string,string>} */ ({
    E: "Entrada",
    S: "Salida",
    I: "Interno",
  });
  const MOTIVO_MIN = 10;
  const MOTIVO_MAX = 500;

  // ---------------------------------------------------------------------------
  // Cola
  // ---------------------------------------------------------------------------
  let cola = data.cola?.items ?? [];
  let colaTotal = data.cola?.total ?? 0;
  $: colaError = data.colaError ?? null;
  let colaCargando = false;
  let colaFallo = "";

  async function recargarCola() {
    colaCargando = true;
    colaFallo = "";
    try {
      const res = await fetch("/desclasificaciones/api/cola");
      const cuerpo = await res.json().catch(() => null);
      if (res.ok && cuerpo) {
        cola = cuerpo.items ?? [];
        colaTotal = cuerpo.total ?? cola.length;
      } else {
        colaFallo = mensajeError(cuerpo, "No se pudo actualizar la cola.");
      }
    } catch {
      colaFallo = "No se pudo actualizar la cola.";
    } finally {
      colaCargando = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Aprobar — confirmación escalonada
  // ---------------------------------------------------------------------------
  let modalAprobar = false;
  /** @type {any|null} */
  let fila = null;
  /** 1 = revisar, 2 = teclear el número del radicado. */
  let paso = 1;
  let revisado = false;
  let textoConfirmacion = "";
  let aprobando = false;
  /** @type {HTMLElement | undefined} */
  let tituloEl;

  $: confirmada = aprobacionConfirmada({ revisado, texto: textoConfirmacion, fila });

  /** @param {any} p */
  function abrirAprobar(p) {
    fila = p;
    paso = 1;
    revisado = false;
    textoConfirmacion = "";
    modalAprobar = true;
  }

  function cerrarAprobar() {
    modalAprobar = false;
    tick().then(() => tituloEl?.focus());
  }

  async function aprobar() {
    if (!fila || aprobando || !confirmada) return;
    aprobando = true;
    try {
      const res = await fetch(`/desclasificaciones/api/${encodeURIComponent(fila.id)}/aprobar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmado: true }),
      });
      const result = await res.json().catch(() => ({}));
      if (!res.ok) throw result;
      notifySuccess(
        `Desclasificación aprobada. El radicado ${fila.tracking_number} quedó en nivel ${getNivelChip(result?.nivel_seguridad ?? fila.nivel_nuevo).label}.`,
      );
      cerrarAprobar();
      fila = null;
      recargarCola();
    } catch (/** @type {any} */ err) {
      notifyError(mensajeError(err, "No se pudo aprobar la desclasificación."));
      // Un 409 (ya resuelta, caducada, nivel cambió) deja la fila obsoleta: se cierra
      // y se refresca para no invitar a repetir el intento sobre algo que ya no existe.
      cerrarAprobar();
      recargarCola();
    } finally {
      aprobando = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Rechazar — motivo obligatorio (10..500)
  // ---------------------------------------------------------------------------
  let modalRechazar = false;
  let motivoRechazo = "";
  let motivoError = "";
  let rechazando = false;

  /** @param {any} p */
  function abrirRechazar(p) {
    fila = p;
    motivoRechazo = "";
    motivoError = "";
    modalRechazar = true;
  }

  function cerrarRechazar() {
    modalRechazar = false;
    tick().then(() => tituloEl?.focus());
  }

  async function rechazar() {
    if (!fila || rechazando) return;
    motivoError = "";
    const motivo = motivoRechazo.trim();
    if (motivo.length < MOTIVO_MIN) {
      motivoError = `El motivo del rechazo debe tener al menos ${MOTIVO_MIN} caracteres.`;
      tick().then(() => document.getElementById("rechazo-motivo")?.focus());
      return;
    }
    rechazando = true;
    try {
      const res = await fetch(`/desclasificaciones/api/${encodeURIComponent(fila.id)}/rechazar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resolucion_motivo: motivo }),
      });
      const result = await res.json().catch(() => ({}));
      if (!res.ok) throw result;
      notifySuccess(
        `Solicitud sobre ${fila.tracking_number} rechazada. El nivel del radicado no cambió.`,
      );
      cerrarRechazar();
      fila = null;
      recargarCola();
    } catch (/** @type {any} */ err) {
      notifyError(mensajeError(err, "No se pudo rechazar la solicitud."));
    } finally {
      rechazando = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Retirar la propia solicitud
  // ---------------------------------------------------------------------------
  let retirandoId = "";

  /** @param {any} p */
  async function retirar(p) {
    if (retirandoId) return;
    retirandoId = p.id;
    try {
      const res = await fetch(`/desclasificaciones/api/${encodeURIComponent(p.id)}/retirar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const result = await res.json().catch(() => ({}));
      if (!res.ok) throw result;
      notifySuccess(`Solicitud sobre ${p.tracking_number} retirada.`);
      recargarCola();
    } catch (/** @type {any} */ err) {
      notifyError(mensajeError(err, "No se pudo retirar la solicitud."));
    } finally {
      retirandoId = "";
    }
  }

  /** @param {string} iso */
  const fmt = (iso) => (iso ? formatearFechaHora(iso) : "—");

  onMount(() => {
    setBreadcrumbs([{ label: "Resolver desclasificaciones" }]);
  });
</script>

<svelte:head>
  <title>Resolver desclasificaciones — Docuboto</title>
</svelte:head>

<div class="desclas-page">
  <header class="desclas-page__header">
    <h1 class="desclas-page__title" bind:this={tituloEl} tabindex="-1">
      Resolver desclasificaciones
    </h1>
    <p class="desclas-page__intro">
      Solicitudes para bajar el nivel de radicados <strong>clasificados</strong> (Ley 1712 de 2014, art.
      18). Las decide una persona distinta de quien las pidió.
    </p>
  </header>

  {#if !canDecidir}
    <EmptyState
      icon="fas fa-lock"
      title="Sin permiso de aprobación de desclasificaciones"
      description="Esta pantalla resuelve solicitudes de desclasificación. Su usuario no tiene el permiso de reclasificación con nivel de aprobación. Solicítelo a un administrador de la entidad."
    />
  {:else}
    <section aria-labelledby="cola-titulo">
      <div class="desclas-cola__head">
        <h2 class="desclas-cola__title" id="cola-titulo">
          Pendientes de decidir
          {#if !colaError}<span class="desclas-cola__count">{colaTotal}</span>{/if}
        </h2>
        <Button
          variant="ghost"
          size="sm"
          icon="fas fa-rotate"
          loading={colaCargando}
          on:click={recargarCola}
        >
          Actualizar
        </Button>
      </div>

      {#if colaFallo}
        <p class="desclas-cola__aviso" role="alert">{colaFallo}</p>
      {/if}

      {#if colaError}
        <!-- «No se pudo consultar» NO es «no hay nada». -->
        <EmptyState
          icon={colaError.status === 403 ? "fas fa-lock" : "fas fa-triangle-exclamation"}
          title={colaError.status === 403
            ? "Sin permiso para ver la cola"
            : "No se pudo consultar la cola"}
          description={colaError.status === 403
            ? "Su usuario no tiene el nivel de aprobación de desclasificaciones."
            : `${colaError.detail} Esto no significa que no haya solicitudes pendientes.`}
        />
      {:else if cola.length === 0}
        <EmptyState
          icon="fas fa-check"
          title="No hay desclasificaciones pendientes"
          description="Cuando alguien solicite bajar el nivel de un radicado clasificado, aparecerá aquí."
        />
      {:else}
        <ul class="desclas-cola__list" role="list">
          {#each cola as p (p.id)}
            <li class="desclas-cola__item">
              <div class="desclas-cola__main">
                <code class="desclas-cola__tracking">{p.tracking_number}</code>
                <span class="desclas-cola__tipo"
                  >{DOC_TYPE_LABELS[p.doc_type] ?? p.doc_type ?? ""}</span
                >
                <span class="desclas-cola__subject">{p.subject ?? "—"}</span>
              </div>
              <div class="desclas-cola__meta">
                <StatusChip status="warning" label="Pendiente" dot />
                <span>
                  {getNivelChip(p.nivel_anterior).label} →
                  <strong>{getNivelChip(p.nivel_nuevo).label}</strong>
                </span>
                <span
                  >Solicitada: <time datetime={p.solicitado_at}>{fmt(p.solicitado_at)}</time></span
                >
                <span>Vence: <time datetime={p.vence_at}>{fmt(p.vence_at)}</time></span>
              </div>
              <p class="desclas-cola__motivo">{p.motivo}</p>
              <div class="desclas-cola__actions">
                <a
                  class="desclas-cola__ver"
                  href="/bandeja?radicado={encodeURIComponent(p.radicado_id)}"
                  target="_blank"
                  rel="noopener"
                >
                  <i class="fas fa-up-right-from-square" aria-hidden="true"></i>
                  Ver el radicado {p.tracking_number}
                  <span class="sr-only">(se abre en una pestaña nueva)</span>
                </a>
                {#if p.propia}
                  <p class="desclas-cola__propia" role="note">
                    <i class="fas fa-user-shield" aria-hidden="true"></i>
                    <span>
                      Usted pidió esta desclasificación: <strong>la decide otra persona</strong>.
                    </span>
                  </p>
                  {#if canRetirar}
                    <Button
                      variant="ghost"
                      size="sm"
                      loading={retirandoId === p.id}
                      disabled={Boolean(retirandoId)}
                      on:click={() => retirar(p)}
                    >
                      <i class="fas fa-undo" aria-hidden="true"></i>
                      Retirar mi solicitud
                    </Button>
                  {/if}
                {:else}
                  <Button variant="secondary" size="sm" on:click={() => abrirRechazar(p)}>
                    <i class="fas fa-times-circle" aria-hidden="true"></i>
                    Rechazar
                  </Button>
                  <Button variant="danger" size="sm" on:click={() => abrirAprobar(p)}>
                    <i class="fas fa-unlock-alt" aria-hidden="true"></i>
                    Aprobar…
                  </Button>
                {/if}
              </div>
            </li>
          {/each}
        </ul>
        {#if colaTotal > cola.length}
          <p class="desclas-cola__nota">
            Se muestran las {cola.length} más antiguas de {colaTotal}. Al resolverlas aparecerán las
            siguientes.
          </p>
        {/if}
      {/if}
    </section>
  {/if}
</div>

<!-- =========================================================================
     Modal: aprobar — confirmación escalonada (irreversible en la práctica)
     ========================================================================= -->
<Modal open={modalAprobar} title="Aprobar desclasificación" size="md" on:close={cerrarAprobar}>
  {#if fila}
    <p class="decision__alerta" role="alert">
      <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
      <span>
        Al aprobar, el radicado <strong>{fila.tracking_number}</strong> pasa a
        <strong>{getNivelChip(fila.nivel_nuevo).label}</strong> en este mismo instante y su
        contenido puede conocerse y difundirse.
        <strong>No se puede deshacer en la práctica.</strong>
      </span>
    </p>

    <dl class="decision__dl">
      <dt>Nivel</dt>
      <dd>{getNivelChip(fila.nivel_anterior).label} → {getNivelChip(fila.nivel_nuevo).label}</dd>
      <dt>Motivo de quien solicita</dt>
      <dd>{fila.motivo}</dd>
      {#if fila.causal_reserva}
        <dt>Causal de la reserva resultante</dt>
        <dd>{fila.causal_reserva}</dd>
      {/if}
      {#if fila.fundamento_juridico}
        <dt>Fundamento jurídico</dt>
        <dd>{fila.fundamento_juridico}</dd>
      {/if}
      {#if fila.plazo_reserva_meses}
        <dt>Plazo de la reserva</dt>
        <dd>{fila.plazo_reserva_meses} meses</dd>
      {/if}
    </dl>

    {#if paso === 1}
      <p>
        <a
          class="desclas-cola__ver"
          href="/bandeja?radicado={encodeURIComponent(fila.radicado_id)}"
          target="_blank"
          rel="noopener"
        >
          <i class="fas fa-up-right-from-square" aria-hidden="true"></i>
          Abrir el radicado {fila.tracking_number}
          <span class="sr-only">(se abre en una pestaña nueva)</span>
        </a>
      </p>
      <label class="decision__check">
        <input type="checkbox" bind:checked={revisado} />
        <span>
          Revisé el radicado y su contenido, y considero que ya no requiere protección en el nivel
          actual.
        </span>
      </label>
    {:else}
      <FormField
        id="aprobar-confirmacion"
        label={`Para confirmar, escriba el número del radicado: ${fraseAprobacion(fila)}`}
        type="text"
        bind:value={textoConfirmacion}
        autocomplete="off"
        hint="Debe coincidir exactamente."
      />
    {/if}
  {/if}

  <svelte:fragment slot="footer">
    {#if paso === 1}
      <Button variant="secondary" size="sm" disabled={!revisado} on:click={() => (paso = 2)}>
        Continuar
      </Button>
    {:else}
      <Button
        variant="danger"
        size="sm"
        loading={aprobando}
        disabled={!confirmada || aprobando}
        on:click={aprobar}
      >
        <i class="fas fa-unlock-alt" aria-hidden="true"></i>
        Aprobar desclasificación
      </Button>
    {/if}
    <Button variant="ghost" size="sm" disabled={aprobando} on:click={cerrarAprobar}>
      Cancelar
    </Button>
  </svelte:fragment>
</Modal>

<!-- =========================================================================
     Modal: rechazar — motivo obligatorio
     ========================================================================= -->
<Modal open={modalRechazar} title="Rechazar desclasificación" size="sm" on:close={cerrarRechazar}>
  <p class="decision__info">
    Al rechazar, la solicitud sobre <strong>{fila?.tracking_number ?? ""}</strong> queda cerrada y el
    radicado conserva su nivel. Quien la pidió verá el motivo.
  </p>
  <FormField
    id="rechazo-motivo"
    label="Motivo del rechazo"
    type="textarea"
    required
    rows={4}
    maxlength={MOTIVO_MAX}
    bind:value={motivoRechazo}
    error={motivoError}
    hint={`Entre ${MOTIVO_MIN} y ${MOTIVO_MAX} caracteres (${motivoRechazo.length}/${MOTIVO_MAX}).`}
  />
  <svelte:fragment slot="footer">
    <Button
      variant="primary"
      size="sm"
      loading={rechazando}
      disabled={rechazando}
      on:click={rechazar}
    >
      Rechazar solicitud
    </Button>
    <Button variant="ghost" size="sm" disabled={rechazando} on:click={cerrarRechazar}>
      Cancelar
    </Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .desclas-page {
    padding: var(--op-space-6);
    max-width: var(--op-content-max);
    margin: 0 auto;
  }

  .desclas-page__header {
    margin-bottom: var(--op-space-4);
  }

  .desclas-page__title {
    font-weight: 600;
    color: var(--op-text);
    margin: 0 0 var(--op-space-2);

    &:focus {
      outline: none;
    }
  }

  .desclas-page__intro {
    color: var(--op-text-secondary);
    margin: 0;
  }

  .desclas-cola__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--op-space-3);
    margin-bottom: var(--op-space-3);
  }

  .desclas-cola__title {
    font-size: var(--op-font-lg);
    font-weight: 600;
    margin: 0;
  }

  .desclas-cola__count {
    margin-left: var(--op-space-2);
    padding: 0 var(--op-space-2);
    background: var(--op-primary-light);
    color: var(--op-primary-dark);
    border-radius: var(--op-radius-full);
    font-size: var(--op-font-sm);
  }

  .desclas-cola__aviso {
    color: var(--op-error);
    margin-bottom: var(--op-space-3);
  }

  .desclas-cola__list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
  }

  .desclas-cola__item {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-4);
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);
    min-width: 0;
  }

  .desclas-cola__main,
  .desclas-cola__meta,
  .desclas-cola__actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--op-space-3);
  }

  .desclas-cola__tracking {
    font-weight: 600;
  }

  .desclas-cola__tipo,
  .desclas-cola__meta {
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
  }

  .desclas-cola__subject {
    min-width: 0;
    overflow-wrap: anywhere;
  }

  .desclas-cola__motivo {
    margin: 0;
    overflow-wrap: anywhere;
  }

  .desclas-cola__ver {
    color: var(--op-primary-dark);
    text-decoration: underline;
  }

  .desclas-cola__propia {
    display: flex;
    gap: var(--op-space-2);
    margin: 0;
    padding: var(--op-space-2) var(--op-space-3);
    background: var(--op-primary-light);
    border-radius: var(--op-radius);
    color: var(--op-text);
  }

  .desclas-cola__nota {
    margin-top: var(--op-space-3);
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
  }

  .decision__alerta {
    display: flex;
    gap: var(--op-space-2);
    padding: var(--op-space-3);
    margin-bottom: var(--op-space-3);
    background: var(--op-surface);
    border: 1px solid var(--op-error);
    border-radius: var(--op-radius);
    color: var(--op-text);
  }

  .decision__info {
    margin-bottom: var(--op-space-3);
  }

  .decision__dl {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: var(--op-space-1) var(--op-space-3);
    margin: 0 0 var(--op-space-3);

    dt {
      color: var(--op-text-secondary);
    }

    dd {
      margin: 0;
      overflow-wrap: anywhere;
    }
  }

  .decision__check {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    cursor: pointer;
  }
</style>
