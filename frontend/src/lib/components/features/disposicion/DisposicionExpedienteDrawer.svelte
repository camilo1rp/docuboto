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
  DisposicionExpedienteDrawer.svelte — Muestra la disposición final del
  expediente Y LA REGLA TRD que la justifica (RF-MET-08/E04, Fase 4 cierre
  desfase API↔UI).

  Es de SOLO LECTURA: la disposición del expediente no se fija con un PATCH
  propio — se materializa automáticamente al cerrar (POST .../close, que ya
  tiene su propio botón/Modal de confirmación en la página). Este Drawer
  responde a la pregunta que el archivista necesita antes de decidir cerrar:
  "¿qué regla se aplicará y de dónde sale el plazo?".

  Dos escenarios, verificados contra el backend:
    - CERRADO/TRANSFERIDO: `expediente.disposition` ya es la
      `RetentionSchedule` calculada en el momento del cierre
      (`ArchiveService.close_expediente` → `compute_retention`) — se muestra
      la regla YA APLICADA con sus fechas reales.
    - ABIERTO con `trd_serie_id`: aún no hay disposición materializada (se
      calcula al cerrar); se muestra la REGLA de la serie (`trdSerieDetail`,
      GET /trd/{serie_id}) como vista previa, sin inventar una fecha que
      todavía no existe.
    - ABIERTO sin `trd_serie_id`: no hay serie asignada — se declara
      explícitamente, no se oculta ni se disfraza de "0".

  Props:
    open            boolean
    expediente      ExpedienteResponse | null
    trdSerieDetail  TrdSerieResponse | null — null también si la serie existe
                    pero la faceta 6 del load() falló (ver +page.server.js)

  Eventos:
    on:close
-->
<script>
  import { createEventDispatcher } from "svelte";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import Button from "$lib/components/ui/Button.svelte";

  export let open = false;
  /** @type {any} */
  export let expediente = null;
  /** @type {any} */
  export let trdSerieDetail = null;

  const dispatch = createEventDispatcher();

  /** @param {string|null|undefined} iso */
  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleDateString("es-CO", { year: "numeric", month: "long", day: "2-digit" });
  }

  $: isClosedOrTransferred =
    expediente?.status === "closed" || expediente?.status === "transferred";
  $: hasMaterializedDisposition = isClosedOrTransferred && !!expediente?.disposition;
  $: hasTrdSerieId = !!expediente?.trd_serie_id;

  function close() {
    dispatch("close");
  }
</script>

<Drawer
  {open}
  title={expediente ? `Disposición final — ${expediente.code}` : "Disposición final"}
  side="right"
  width="440px"
  on:close={close}
>
  {#if expediente}
    {#if hasMaterializedDisposition}
      <!-- Escenario 1: regla YA aplicada al cierre -->
      <section class="disp-section" aria-labelledby="disp-applied-heading">
        <h3 id="disp-applied-heading" class="disp-section__title">
          <i class="fas fa-check-circle" aria-hidden="true"></i>
          Disposición aplicada al cierre
        </h3>
        <dl class="disp-dl">
          <dt>Regla</dt>
          <dd>
            {expediente.disposition.disposition_label ??
              expediente.disposition.disposition_code ??
              "—"}
          </dd>
          <dt>Serie TRD</dt>
          <dd>
            {expediente.disposition.code ?? "—"}
            {#if trdSerieDetail?.name}
              — {trdSerieDetail.name}
            {/if}
          </dd>
          <dt>Fecha de cierre</dt>
          <dd>{fmtDate(expediente.disposition.closed_at)}</dd>
          <dt>Fin archivo de gestión</dt>
          <dd>
            {fmtDate(expediente.disposition.fin_archivo_gestion)}
            {#if expediente.disposition.archivo_gestion_years != null}
              <span class="disp-dl__sub">
                ({expediente.disposition.archivo_gestion_years} año{expediente.disposition
                  .archivo_gestion_years !== 1
                  ? "s"
                  : ""} desde el cierre)
              </span>
            {/if}
          </dd>
          <dt>Fin archivo central</dt>
          <dd>
            {fmtDate(expediente.disposition.fin_archivo_central)}
            {#if expediente.disposition.archivo_central_years != null}
              <span class="disp-dl__sub">
                ({expediente.disposition.archivo_central_years} año{expediente.disposition
                  .archivo_central_years !== 1
                  ? "s"
                  : ""} adicionales)
              </span>
            {/if}
          </dd>
        </dl>
        <p class="disp-source">
          <i class="fas fa-circle-info" aria-hidden="true"></i>
          Esta regla se calculó y fijó de forma definitiva en el momento del cierre del expediente ({fmtDate(
            expediente.closed_at,
          )}) — no cambia aunque la serie TRD se modifique después.
        </p>
      </section>
    {:else if hasTrdSerieId && trdSerieDetail}
      <!-- Escenario 2: expediente ABIERTO con serie asignada — vista previa de la regla -->
      <section class="disp-section" aria-labelledby="disp-preview-heading">
        <h3 id="disp-preview-heading" class="disp-section__title">
          <i class="fas fa-eye" aria-hidden="true"></i>
          Regla que se aplicará al cerrar
        </h3>
        <p class="disp-preview-note">
          El expediente está <strong>abierto</strong>: la disposición final aún no se ha calculado —
          se materializa automáticamente al cerrarlo. Esto es la REGLA de la serie asignada, no una
          fecha definitiva.
        </p>
        <dl class="disp-dl">
          <dt>Serie TRD</dt>
          <dd>{trdSerieDetail.code} — {trdSerieDetail.name}</dd>
          <dt>Archivo de gestión</dt>
          <dd>
            {trdSerieDetail.archivo_gestion_years ?? trdSerieDetail.retention_years ?? "—"} año(s) desde
            el cierre
          </dd>
          {#if trdSerieDetail.archivo_central_years != null}
            <dt>Archivo central</dt>
            <dd>{trdSerieDetail.archivo_central_years} año(s) adicionales</dd>
          {/if}
          <dt>Disposición final (código AGN)</dt>
          <dd>{trdSerieDetail.disposition ?? "—"}</dd>
        </dl>
        <p class="disp-source">
          <i class="fas fa-circle-info" aria-hidden="true"></i>
          El código de disposición se muestra tal como está fijado en la serie TRD; la etiqueta legible
          (p. ej. "Conservación Total") se calcula al momento del cierre, no antes.
        </p>
      </section>
    {:else if hasTrdSerieId && !trdSerieDetail}
      <!-- Escenario 3: hay serie pero la faceta de detalle falló al cargar — NO es "sin serie" -->
      <div class="disp-warning" role="alert">
        <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
        <p>
          El expediente tiene una serie TRD asignada (<code>{expediente.trd_serie_id}</code>), pero
          no se pudo cargar su detalle en este momento. No se puede mostrar la regla de retención —
          reintente más tarde; esto NO significa que el expediente carezca de serie.
        </p>
      </div>
    {:else}
      <!-- Escenario 4: sin serie TRD asignada, de verdad -->
      <div class="disp-empty" role="note">
        <i class="fas fa-circle-info" aria-hidden="true"></i>
        <p>
          Este expediente no tiene una serie TRD asignada todavía. Sin serie no existe una regla de
          retención que calcular al cierre — asigne una desde "Editar metadatos" antes de cerrar el
          expediente si aplica una TRD.
        </p>
      </div>
    {/if}
  {/if}

  <svelte:fragment slot="footer">
    <Button variant="ghost" size="sm" on:click={close}>Cerrar</Button>
  </svelte:fragment>
</Drawer>

<style lang="scss">
  .disp-section {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
  }

  .disp-section__title {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    font-size: var(--op-font-md);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
    margin: 0;

    i {
      color: var(--op-primary-dark);
    }
  }

  .disp-preview-note {
    margin: 0;
    font-size: var(--op-font-sm);
    color: var(--op-text-secondary);
    background: var(--op-bg);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-3);
  }

  .disp-dl {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: var(--op-space-2) var(--op-space-3);
    margin: 0;

    dt {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      font-weight: var(--op-weight-medium);
    }

    dd {
      margin: 0;
      font-size: var(--op-font-sm);
      color: var(--op-text);
    }
  }

  .disp-dl__sub {
    color: var(--op-text-secondary);
    font-size: var(--op-font-xs);
  }

  .disp-source {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
    margin: 0;

    i {
      color: var(--op-info);
      margin-top: 2px;
      flex-shrink: 0;
    }
  }

  .disp-warning,
  .disp-empty {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-2);
    border-radius: var(--op-radius);
    padding: var(--op-space-3);
    font-size: var(--op-font-sm);

    p {
      margin: 0;
      line-height: 1.5;
    }
  }

  .disp-warning {
    background: var(--op-warning-bg, var(--op-bg));
    border: 1px solid var(--op-warning);
    color: var(--op-text);

    i {
      color: var(--op-warning);
      margin-top: 2px;
      flex-shrink: 0;
    }

    code {
      font-size: var(--op-font-xs);
    }
  }

  .disp-empty {
    background: var(--op-bg);
    border: 1px solid var(--op-border);
    color: var(--op-text-secondary);

    i {
      color: var(--op-info);
      margin-top: 2px;
      flex-shrink: 0;
    }
  }
</style>
