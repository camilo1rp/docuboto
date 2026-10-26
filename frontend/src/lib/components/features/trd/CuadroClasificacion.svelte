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
  CuadroClasificacion.svelte — pestaña «Cuadro» de /admin/trd (C-4, ADR-040).

  Cuadro de clasificación DERIVADO del organigrama y de la TRD vigente: fondo >
  sección > subsección > serie > subserie. Solo lectura. Los avisos van arriba
  (lo que no se pudo ubicar o cambió tras convalidar) y las series «sin ubicar»
  al final: nada se omite en silencio. Va por el BFF /admin/trd/api/cuadro.

  `canBorradores` (USUA_PERM_TRD): el interruptor «Incluir borradores» solo se
  ofrece con el permiso; el backend responde 403 sin él.
-->
<script>
  import { onMount } from "svelte";
  import { mensajeError } from "$lib/utils/apiError.js";
  import { aplanarCuadro, textoAviso, motivoSinSeccion } from "$lib/utils/cuadro.js";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";

  export let canBorradores = false;

  let segun = "convalidado";
  let incluirBorradores = false;
  let incluirVacias = false;

  let cargando = false;
  /** @type {string|null} */
  let error = null;
  /** @type {any} */
  let cuadro = null;
  let peticion = 0;

  $: filas = aplanarCuadro(cuadro?.secciones ?? []);

  async function cargar() {
    const mia = ++peticion;
    cargando = true;
    error = null;
    const params = new URLSearchParams({ segun, incluir_vacias: String(incluirVacias) });
    if (canBorradores && incluirBorradores) params.set("incluir_borradores", "true");
    try {
      const res = await fetch(`/admin/trd/api/cuadro?${params}`);
      const cuerpo = await res.json().catch(() => ({}));
      if (mia !== peticion) return;
      if (!res.ok) {
        cuadro = null;
        error =
          res.status === 403
            ? "Sin permiso para ver el cuadro con esas opciones."
            : mensajeError(cuerpo, "No se pudo cargar el cuadro de clasificación.");
        return;
      }
      cuadro = cuerpo;
    } catch {
      if (mia !== peticion) return;
      cuadro = null;
      error = "No se pudo conectar al servidor.";
    } finally {
      if (mia === peticion) cargando = false;
    }
  }

  onMount(cargar);

  const ETIQUETA_KIND = {
    seccion: "Sección",
    subseccion: "Subsección",
    serie: "Serie",
    subserie: "Subserie",
  };
  /** @param {any} f */
  function fases(f) {
    const d = f.dato;
    if (f.kind === "seccion" || f.kind === "subseccion") return "";
    return `${d.archivo_gestion_years ?? "—"}a / ${d.archivo_central_years ?? "—"}a`;
  }
</script>

<section aria-labelledby="cuadro-titulo" class="cuadro">
  <h2 id="cuadro-titulo" class="cuadro__titulo">Cuadro de clasificación</h2>
  <p class="cuadro__nota">
    Se deriva del organigrama y de las series vigentes; no se edita aquí. Para cambiar dónde queda
    una serie, cambie su dependencia productora en una versión nueva.
  </p>

  <div class="cuadro__controles">
    <label class="cuadro__control" for="cuadro-segun">
      Ubicar según
      <select id="cuadro-segun" class="select" bind:value={segun} on:change={cargar}>
        <option value="convalidado">Lo que aprobó el Consejo (convalidado)</option>
        <option value="vivo">El organigrama actual (vivo)</option>
      </select>
    </label>
    <label class="cuadro__check">
      <input type="checkbox" bind:checked={incluirVacias} on:change={cargar} />
      Incluir secciones sin series
    </label>
    {#if canBorradores}
      <label class="cuadro__check">
        <input type="checkbox" bind:checked={incluirBorradores} on:change={cargar} />
        Incluir borradores
      </label>
    {/if}
  </div>

  <div aria-live="polite">
    {#if cargando}
      <Loader />
    {:else if error}
      <p class="cuadro__error" role="alert">{error}</p>
    {:else if cuadro}
      {#if cuadro.avisos?.length}
        <ul class="cuadro__avisos" aria-label="Avisos del cuadro">
          {#each cuadro.avisos as aviso, i (i)}
            <li class="cuadro__aviso">
              <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
              {textoAviso(aviso)}
            </li>
          {/each}
        </ul>
      {/if}

      <p class="cuadro__fondo">
        <strong>Fondo:</strong>
        {cuadro.fondo?.nombre ?? "Sin nombre configurado"}{#if cuadro.fondo?.sigla}
          ({cuadro.fondo.sigla}){/if}
      </p>

      {#if filas.length === 0}
        <EmptyState
          icon="fas fa-folder-tree"
          title="Cuadro vacío"
          description="Ninguna sección del organigrama tiene series vigentes. Active «Incluir secciones sin series» para ver la estructura."
        />
      {:else}
        <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
        <div
          class="op-table-scroll"
          role="region"
          aria-label="Cuadro de clasificación"
          tabindex="0"
        >
          <table class="table is-fullwidth cuadro__tabla" aria-label="Cuadro de clasificación">
            <thead>
              <tr>
                <th scope="col">Nivel</th>
                <th scope="col">Código</th>
                <th scope="col">Nombre</th>
                <th scope="col">Retención (gest./cent.)</th>
                <th scope="col">Estado</th>
              </tr>
            </thead>
            <tbody>
              {#each filas as f (f.key)}
                <tr class="cuadro__fila cuadro__fila--{f.kind}">
                  <td>{ETIQUETA_KIND[f.kind]}</td>
                  <td>
                    <code
                      >{f.kind === "seccion" || f.kind === "subseccion"
                        ? (f.dato.sigla ?? f.dato.codigo)
                        : f.dato.code}</code
                    >
                  </td>
                  <td>
                    <span class="cuadro__indent" style="--depth: {f.depth}">{f.etiqueta}</span>
                    {#if f.kind === "serie" && f.dato.compartida}
                      <span class="cuadro__marca">compartida</span>
                    {/if}
                    {#if f.kind === "serie" && f.dato.ruta_difiere_de_la_convalidada}
                      <span class="cuadro__marca cuadro__marca--aviso">
                        ubicación distinta de la convalidada
                      </span>
                    {/if}
                    {#if (f.kind === "seccion" || f.kind === "subseccion") && f.dato.activo === false}
                      <span class="cuadro__marca cuadro__marca--aviso">inactiva</span>
                    {/if}
                  </td>
                  <td>{fases(f)}</td>
                  <td>{f.kind === "serie" || f.kind === "subserie" ? f.dato.estado : ""}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}

      {#if cuadro.sin_seccion?.length}
        <h3 class="cuadro__subtitulo">Sin ubicar ({cuadro.sin_seccion.length})</h3>
        <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
        <div
          class="op-table-scroll"
          role="region"
          aria-label="Series sin ubicar en el cuadro"
          tabindex="0"
        >
          <table class="table is-fullwidth" aria-label="Series sin ubicar en el cuadro">
            <thead>
              <tr>
                <th scope="col">Código</th>
                <th scope="col">Nombre</th>
                <th scope="col">Estado</th>
                <th scope="col">Motivo</th>
              </tr>
            </thead>
            <tbody>
              {#each cuadro.sin_seccion as s (`${s.code}#${s.version}`)}
                <tr>
                  <td><code>{s.code}</code> v{s.version}</td>
                  <td>{s.name}</td>
                  <td>{s.estado}</td>
                  <td>{motivoSinSeccion(s.motivo)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    {/if}
  </div>
</section>

<style lang="scss">
  .cuadro {
    &__titulo {
      font-size: var(--op-font-lg);
      font-weight: 600;
      color: var(--op-text);
    }
    &__nota {
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
      margin-bottom: var(--op-space-3);
    }
    &__controles {
      display: flex;
      flex-wrap: wrap;
      gap: var(--op-space-4);
      align-items: flex-end;
      margin-bottom: var(--op-space-4);
    }
    &__control {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
      font-size: var(--op-font-sm);
      color: var(--op-text);
    }
    &__check {
      display: flex;
      gap: var(--op-space-2);
      align-items: center;
      font-size: var(--op-font-sm);
      color: var(--op-text);
    }
    &__error {
      color: var(--op-error);
    }
    &__avisos {
      list-style: none;
      margin: 0 0 var(--op-space-4);
      padding: var(--op-space-3);
      background: var(--op-bg);
      border: 1px solid var(--op-warning);
      border-radius: var(--op-radius);
    }
    &__aviso {
      display: flex;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-text);
      i {
        color: var(--op-warning);
        margin-top: 2px;
      }
    }
    &__fondo {
      margin-bottom: var(--op-space-3);
      color: var(--op-text);
    }
    &__indent {
      padding-left: calc(var(--depth, 0) * 1.25rem);
    }
    &__fila--seccion {
      font-weight: 600;
    }
    &__marca {
      margin-left: var(--op-space-2);
      font-size: var(--op-font-xs, 0.75rem);
      color: var(--op-text-secondary);
      &--aviso {
        color: var(--op-warning);
      }
    }
    &__subtitulo {
      margin: var(--op-space-5, 1.5rem) 0 var(--op-space-2);
      font-size: var(--op-font-md, 1rem);
      color: var(--op-text);
    }
  }
</style>
