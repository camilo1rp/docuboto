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
  HistorialAcceso — quién leyó y quién descargó este radicado (RF-BUS-10;
  Ley 594/2000 art. 22).

  **Ver la ficha y llevarse el documento son hechos distintos**, y la pantalla
  los distingue con palabras, no con un código de acción: «consultó la ficha» no
  es lo mismo que «descargó un anexo», y en una reclamación por fuga de
  información la diferencia es toda.

  Se carga bajo demanda, al desplegar. Un radicado muy consultado acumula
  cientos de asientos, y traerlos al abrir cada ficha pagaría esa consulta en
  todas las aperturas para una pregunta que casi nadie hace.

  **Y dice un nombre, no un identificador.** El asiento guarda el id interno de
  quien accedió —que es lo correcto: un `username` cambia y un asiento no se
  toca nunca—, pero una sección titulada «quién ha visto este radicado» que
  contesta con un UUID no contesta. El nombre se resuelve en el backend; si no
  se puede resolver (un asiento del propio sistema, un usuario dado de baja del
  catálogo) se cae al identificador, que dice poco pero no miente.

  **Si está truncado, lo dice.** Un historial de acceso incompleto presentado
  como completo es una afirmación falsa sobre quién ha visto un documento.
-->
<script>
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";

  export let radicadoId = "";
  /** Base del proxy, por si otra pantalla lo reutiliza. */
  export let basePath = "/bandeja/api";

  const TAMANO = 20;

  let abierto = false;
  let cargado = false;
  let cargando = false;
  let error = "";
  /** @type {any[]} */
  let entradas = [];
  let total = 0;

  const ETIQUETA = {
    "document.radicado_consultado": "consultó la ficha",
    "document.anexo_descargado": "descargó un anexo",
  };

  async function cargar() {
    cargando = true;
    error = "";
    try {
      const res = await fetch(`${basePath}/${radicadoId}/historial-acceso?size=${TAMANO}`);
      const cuerpo = await res.json();
      if (!res.ok) throw cuerpo;
      entradas = Array.isArray(cuerpo) ? cuerpo : [];
      total = Number(res.headers.get("X-Total-Count") ?? entradas.length);
      cargado = true;
    } catch (e) {
      error = mensajeError(e, "No se pudo leer el historial de acceso.");
    } finally {
      cargando = false;
    }
  }

  function alternar() {
    abierto = !abierto;
    if (abierto && !cargado && !cargando) cargar();
  }

  /** @param {string} iso */
  function cuando(iso) {
    try {
      return new Date(iso).toLocaleString("es-CO");
    } catch {
      return iso;
    }
  }
</script>

<div class="hist">
  <button type="button" class="hist__toggle" on:click={alternar} aria-expanded={abierto}>
    <i class="fas fa-{abierto ? 'chevron-down' : 'chevron-right'}" aria-hidden="true"></i>
    Quién ha visto este radicado
  </button>

  {#if abierto}
    {#if cargando}
      <p class="hist__tenue" aria-live="polite">Consultando la pista de acceso…</p>
    {:else if error}
      <!-- Nunca «no hay accesos» ante un fallo: son cosas distintas, y
           confundirlas afirmaría que nadie lo ha visto cuando lo que pasa es
           que no se ha podido comprobar. -->
      <p class="hist__error" role="alert">
        {error} Esto <strong>no</strong> significa que nadie lo haya consultado.
      </p>
    {:else if entradas.length === 0}
      <EmptyState
        icon="fas fa-eye-slash"
        title="Todavía no lo ha consultado nadie"
        description="Se registran las lecturas de la ficha y las descargas de anexos hechas por personas, no las del propio sistema."
      />
    {:else}
      <ul class="hist__lista">
        {#each entradas as e (e.ts + e.action + (e.actor ?? ""))}
          <li class="hist__item">
            <span class="hist__accion">{ETIQUETA[e.action] ?? e.action}</span>
            <span class="hist__tenue">
              {cuando(e.ts)} · {e.actor_nombre ?? e.actor ?? "sin actor"} · vía {e.canal}
            </span>
          </li>
        {/each}
      </ul>
      {#if total > entradas.length}
        <p class="hist__tenue">
          Se muestran los {entradas.length} más recientes de <strong>{total}</strong>. El resto está
          en Administración → Auditoría.
        </p>
      {/if}
    {/if}
  {/if}
</div>

<style lang="scss">
  .hist {
    &__toggle {
      background: none;
      border: 0;
      cursor: pointer;
      color: var(--op-text);
      font: inherit;
      padding: 0;
      display: flex;
      align-items: center;
      gap: var(--op-space-1);
    }

    &__lista {
      list-style: none;
      margin: var(--op-space-2) 0 0;
      padding: 0;
    }

    &__item {
      display: flex;
      flex-direction: column;
      padding: var(--op-space-1) 0;
      border-bottom: 1px solid var(--op-border);
    }

    &__accion {
      font-weight: var(--op-weight-medium);
    }

    &__tenue {
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
      margin: var(--op-space-1) 0 0;
    }

    &__error {
      color: var(--op-error);
      margin: var(--op-space-2) 0 0;
    }
  }
</style>
