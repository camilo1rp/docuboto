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
  SellosArchivo.svelte — faceta «Sellos de archivo» de /admin/preservacion
  (C-6, ADR-042). Solo lectura: resumen por situación + listado paginado.

  - La faceta solo se monta con USUA_PERM_ADMIN >= 3 (la página decide).
  - Una fila de objeto reservado SE MUESTRA como «Reservado»: el sello hay que
    vigilarlo aunque no se pueda nombrar el objeto.
  - 403 se dice «Sin permiso», nunca «No hay sellos»: un error no es un vacío.
  - El total sale de X-Total-Count (el BFF lo reenvía); si falta, no se inventa.
-->
<script>
  import { formatearFechaHora } from "$lib/utils/fecha.js";
  import { mensajeError } from "$lib/utils/apiError.js";
  import {
    SITUACIONES,
    SITUACION_LABELS,
    ESTADO_LABELS,
    FUENTE_REVOCACION_LABELS,
    textoUltimoAviso,
    textoObjeto,
    textoDias,
    huellaCorta,
  } from "$lib/utils/sellos.js";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import Button from "$lib/components/ui/Button.svelte";

  /**
   * Datos de la carga SSR: { resumen: {data,error}, lista: {data,error} }
   * @type {any}
   */
  export let inicial = null;

  const TAM = 20;

  let resumen = inicial?.resumen?.data ?? null;
  let resumenError = inicial?.resumen?.error ?? null;
  let filas = inicial?.lista?.data?.items ?? [];
  let total = inicial?.lista?.data?.total ?? null;
  /** @type {{status?: number, detail?: string, code?: string}|null} */
  let listaError = inicial?.lista?.error ?? null;

  /** @type {string[]} */
  let situaciones = [];
  let pagina = 1;
  let cargando = false;
  let peticion = 0;

  $: sinPermiso = listaError?.status === 403 || resumenError?.status === 403;
  $: hayMas = total != null ? pagina * TAM < total : filas.length === TAM;

  async function recargar() {
    const mia = ++peticion;
    cargando = true;
    const qs = new URLSearchParams({ page: String(pagina), size: String(TAM) });
    for (const s of situaciones) qs.append("situacion", s);
    try {
      const [lres, rres] = await Promise.all([
        fetch(`/admin/preservacion/api/sellos-archivo?${qs}`),
        fetch("/admin/preservacion/api/sellos-archivo/resumen"),
      ]);
      if (mia !== peticion) return;
      if (lres.ok) {
        filas = await lres.json().catch(() => []);
        const t = Number(lres.headers.get("X-Total-Count"));
        total = Number.isFinite(t) && lres.headers.has("X-Total-Count") ? t : null;
        listaError = null;
      } else {
        const cuerpo = await lres.json().catch(() => ({}));
        filas = [];
        total = null;
        listaError = {
          status: lres.status,
          detail: mensajeError(cuerpo, `Error ${lres.status}`),
        };
      }
      if (rres.ok) {
        resumen = await rres.json().catch(() => resumen);
        resumenError = null;
      } else {
        resumenError = { status: rres.status, detail: `Error ${rres.status}` };
      }
    } catch {
      if (mia !== peticion) return;
      listaError = { status: 503, detail: "No se pudo conectar al servidor." };
    } finally {
      if (mia === peticion) cargando = false;
    }
  }

  /** @param {string} s */
  function alternar(s) {
    situaciones = situaciones.includes(s)
      ? situaciones.filter((x) => x !== s)
      : [...situaciones, s];
    pagina = 1;
    recargar();
  }
  function irA(n) {
    pagina = n;
    recargar();
  }
  const fmt = (iso) => (iso ? formatearFechaHora(iso) : "—");

  /** @param {string} s */
  function chipDe(s) {
    return s === "vencido" || s === "rechazado" ? "error" : "warning";
  }
</script>

<section aria-labelledby="sellos-titulo" class="sellos">
  <h2 id="sellos-titulo" class="op-card-title">Sellos de archivo (ArchiveTimeStamp)</h2>
  <p class="sellos__nota">
    <i class="fas fa-circle-info" aria-hidden="true"></i>
    Solo visibilidad. La cadena de sellos de las firmas XAdES-LTA se renueva sola; aquí se ven los que
    necesitan atención{#if resumen?.margen_dias != null}
      (margen de aviso: {resumen.margen_dias} días){/if}.
  </p>

  {#if sinPermiso}
    <EmptyState
      icon="fas fa-lock"
      title="Sin permiso"
      description="Consultar los sellos de archivo exige el permiso de administración con nivel 3."
    />
  {:else}
    {#if resumenError}
      <p class="sellos__error" role="alert">
        No se pudo cargar el resumen: {mensajeError(resumenError, "intente de nuevo.")}
      </p>
    {:else if resumen}
      <ul class="sellos__resumen" aria-label="Resumen de sellos por situación">
        {#each SITUACIONES as s (s)}
          <li>
            <button
              type="button"
              class="sellos__tile"
              class:sellos__tile--on={situaciones.includes(s)}
              aria-pressed={situaciones.includes(s)}
              on:click={() => alternar(s)}
            >
              <span class="sellos__tile-n">{resumen[s] ?? 0}</span>
              <span class="sellos__tile-l">{SITUACION_LABELS[s]}</span>
            </button>
          </li>
        {/each}
      </ul>
    {/if}

    <div aria-live="polite">
      {#if cargando}
        <Loader />
      {:else if listaError}
        <EmptyState
          icon="fas fa-exclamation-triangle"
          title="No se pudo cargar el listado"
          description={listaError.detail ?? "Intente recargar la página."}
        />
      {:else if filas.length === 0}
        <EmptyState
          icon="fas fa-circle-check"
          title="Sin sellos que atender"
          description={situaciones.length
            ? "Ningún sello está en las situaciones elegidas."
            : "No hay sellos por vencer, vencidos ni con novedades."}
        />
      {:else}
        <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
        <div
          class="sellos__tabla op-table-scroll"
          role="region"
          aria-label="Sellos de archivo"
          tabindex="0"
        >
          <table class="table is-fullwidth is-hoverable">
            <caption class="is-sr-only">Sellos de archivo que necesitan atención</caption>
            <thead>
              <tr>
                <th scope="col">Objeto</th>
                <th scope="col">Sello</th>
                <th scope="col">Situación</th>
                <th scope="col">Vence</th>
                <th scope="col">TSA</th>
                <th scope="col">Último aviso</th>
              </tr>
            </thead>
            <tbody>
              {#each filas as f (`${f.firma_id}-${f.ordinal}`)}
                <tr>
                  <td class:sellos__reservado={f.objeto_reservado}>{textoObjeto(f)}</td>
                  <td>
                    #{f.ordinal}
                    <StatusChip
                      status={f.estado === "caducado"
                        ? "error"
                        : f.estado === "vigente"
                          ? "success"
                          : "warning"}
                      label={ESTADO_LABELS[f.estado] ?? f.estado}
                      dot
                    />
                    <span class="sellos__sub">Generado {fmt(f.gen_time)}</span>
                  </td>
                  <td>
                    {#each f.situaciones ?? [] as s (s)}
                      <StatusChip status={chipDe(s)} label={SITUACION_LABELS[s] ?? s} dot />
                    {/each}
                  </td>
                  <td>
                    {fmt(f.vence_at)}
                    <span class="sellos__sub">{textoDias(f.dias_restantes)}</span>
                  </td>
                  <td>
                    <code title={f.tsa_cert_fingerprint ?? ""}
                      >{huellaCorta(f.tsa_cert_fingerprint)}</code
                    >
                    <span class="sellos__sub">
                      Revocación: {FUENTE_REVOCACION_LABELS[f.tsa_revocacion_fuente] ??
                        f.tsa_revocacion_fuente ??
                        "—"}{#if f.revocacion_tsa_previa === false}
                        · sin evidencia previa{/if}
                    </span>
                  </td>
                  <td>{textoUltimoAviso(f.ultimo_aviso)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>

        <nav class="sellos__pag" aria-label="Paginación de sellos">
          <Button variant="ghost" size="sm" disabled={pagina <= 1} on:click={() => irA(pagina - 1)}
            >Anterior</Button
          >
          <span>
            Página {pagina}{#if total != null}
              de {Math.max(1, Math.ceil(total / TAM))} ({total} sello{total === 1 ? "" : "s"}){/if}
          </span>
          <Button variant="ghost" size="sm" disabled={!hayMas} on:click={() => irA(pagina + 1)}
            >Siguiente</Button
          >
        </nav>
      {/if}
    </div>
  {/if}
</section>

<style lang="scss">
  .sellos {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);

    &__nota {
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
    }
    &__error {
      color: var(--op-error);
    }
    &__resumen {
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: var(--op-space-3);
    }
    &__tile {
      width: 100%;
      min-height: 44px;
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: var(--op-space-1);
      padding: var(--op-space-3);
      background: var(--op-surface);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      color: var(--op-text);
      cursor: pointer;

      &--on {
        background: var(--op-primary-light);
        border-color: var(--op-primary);
      }
    }
    &__tile-n {
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-bold);
    }
    &__tile-l {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
    }
    &__sub {
      display: block;
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
    }
    &__reservado {
      font-style: italic;
      color: var(--op-text-secondary);
    }
    &__pag {
      display: flex;
      align-items: center;
      gap: var(--op-space-3);
      justify-content: flex-end;
    }
  }
</style>
