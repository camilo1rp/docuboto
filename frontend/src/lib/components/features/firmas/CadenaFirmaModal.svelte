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
  CadenaFirmaModal.svelte — abrir una cadena de firmantes EN ORDEN sobre un
  objeto que no es un borrador (E06, ADR-016, RF-FIR-04).

  Por qué existe: el proxy `/firmas/api/requests` estaba escrito y probado, y
  NINGUNA vista lo llamaba. `/borradores` tiene su propio circuito —visto bueno
  más firma sobre el texto que se está redactando—, pero los otros dos tipos que
  el backend admite, `expediente` y `radicado`, no tenían por dónde abrirse una
  cadena. Y no es una capacidad hipotética: el propio sembrado del entorno de
  pruebas crea las dos, y sobre el expediente dice literalmente que «es donde
  una entidad la usa de verdad». Una capacidad construida, probada, sembrada y
  sin puerta.

  Diferencia con el circuito del borrador, y por qué son dos cosas: allí se
  componen turnos de VISTO BUENO y uno de FIRMA sobre un texto que aún se puede
  editar. Aquí todos los turnos son de firma: lo que se firma ya está fijado
  —un expediente, un radicado— y no hay texto que revisar antes de producirlo.

  El catálogo de personas se pide a `/borradores/api/firmantes`, que ya es
  genérico por dentro (`GET /auth/users/pickable`, gateado con PERM_RADI) y solo
  vive bajo esa ruta por dónde nació. Se REUTILIZA en vez de duplicarlo: dos
  proxies contra el mismo endpoint se desincronizan, y en este proyecto eso ya
  ha costado más de una vez.

  Props:
    open        boolean — visibilidad
    objetoTipo  string  — 'expediente' | 'radicado'
    objetoId    string  — UUID del objeto
    titulo      string  — título descriptivo de la cadena

  Eventos:
    on:close    — cerrado sin crear
    on:created  — cadena creada; detail = SolicitudResponse
-->
<script>
  import { createEventDispatcher } from "svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";

  export let open = false;
  export let objetoTipo = "";
  export let objetoId = "";
  export let titulo = "";

  const dispatch = createEventDispatcher();

  /** @type {any[]} Catálogo de personas elegibles. */
  let personas = [];
  /** @type {string[]} Firmantes EN ORDEN; la posición es el turno. */
  let firmantes = [];
  let error = "";
  let cargando = false;
  let creando = false;
  /** @type {any|null} Cadena ya abierta sobre este objeto, si la hay. */
  let cadenaAbierta = null;

  let ultimoObjeto = "";
  $: if (open && objetoId && `${objetoTipo}:${objetoId}` !== ultimoObjeto) {
    ultimoObjeto = `${objetoTipo}:${objetoId}`;
    preparar();
  }

  async function preparar() {
    error = "";
    firmantes = [];
    cadenaAbierta = null;
    cargando = true;
    await Promise.all([cargarPersonas(), cargarCadena()]);
    cargando = false;
  }

  async function cargarPersonas() {
    try {
      const res = await fetch("/borradores/api/firmantes");
      const data = await res.json().catch(() => []);
      if (!res.ok) throw new Error(mensajeError(data, "No se pudo cargar el listado."));
      personas = Array.isArray(data) ? data : (data?.items ?? []);
    } catch (err) {
      personas = [];
      error = err instanceof Error ? err.message : String(err);
    }
  }

  /**
   * Cadena viva sobre este objeto, si existe.
   *
   * Solo interesa la PENDIENTE: una firmada o rechazada ya no gobierna nada, y
   * enseñarla como si mandara confundiría sobre qué falta. El 404 es la
   * respuesta normal —no hay ninguna abierta— y no un fallo que anunciar.
   */
  async function cargarCadena() {
    try {
      const q = new URLSearchParams({ objeto_tipo: objetoTipo, objeto_id: objetoId });
      const res = await fetch(`/firmas/api/requests?${q}`);
      if (!res.ok) return;
      const data = await res.json().catch(() => null);
      cadenaAbierta = data?.estado === "pendiente" ? data : null;
    } catch {
      cadenaAbierta = null;
    }
  }

  function nombreDe(id) {
    const p = personas.find((x) => (x.id ?? x.user_id) === id);
    return p?.nombre ?? p?.name ?? p?.username ?? id;
  }

  function agregarTurno() {
    firmantes = [...firmantes, ""];
  }

  function quitarTurno(idx) {
    firmantes = firmantes.filter((_, i) => i !== idx);
  }

  $: completos = firmantes.length > 0 && firmantes.every((f) => f);
  // La misma persona dos veces firmaría dos turnos del mismo acto: el segundo
  // no añade nada y la cadena aparenta más respaldo del que tiene.
  $: repetidos = new Set(firmantes.filter(Boolean)).size !== firmantes.filter(Boolean).length;
  $: puedeCrear = completos && !repetidos && !creando && !cadenaAbierta;

  async function crear() {
    if (!puedeCrear) return;
    creando = true;
    error = "";
    try {
      const res = await fetch("/firmas/api/requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          objeto_tipo: objetoTipo,
          objeto_id: objetoId,
          titulo,
          firmantes,
        }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(mensajeError(data, "No se pudo abrir la cadena de firmas."));
      dispatch("created", data);
      open = false;
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      creando = false;
    }
  }
</script>

<Modal {open} title="Cadena de firmas" size="md" on:close={() => dispatch("close")}>
  <div class="cadena">
    <p class="cadena__objeto">
      <i class="fas fa-file-signature" aria-hidden="true"></i>
      {titulo || objetoId}
    </p>

    {#if cargando}
      <p class="cadena__status" aria-live="polite">Cargando…</p>
    {:else if cadenaAbierta}
      <p class="cadena__aviso" role="status">
        Este objeto ya tiene una cadena abierta con {cadenaAbierta.items?.length ?? 0} turno(s). Hay que
        resolverla o rechazarla antes de abrir otra.
      </p>
      <ol class="cadena__turnos">
        {#each cadenaAbierta.items ?? [] as turno (turno.orden)}
          <li>
            <strong>{turno.orden}.</strong>
            {nombreDe(turno.firmante_id)} — {turno.estado}
          </li>
        {/each}
      </ol>
    {:else}
      <p class="cadena__ayuda">
        Cada persona firma <strong>cuando le toca</strong>: el turno 2 no se abre hasta que el 1
        esté resuelto. Todos los turnos son de firma.
      </p>

      {#each firmantes as _, idx (idx)}
        <div class="cadena__turno">
          <span class="cadena__orden">{idx + 1}</span>
          <select
            class="cadena__select"
            aria-label={`Firmante del turno ${idx + 1}`}
            bind:value={firmantes[idx]}
          >
            <option value="">Seleccione…</option>
            {#each personas as p (p.id ?? p.user_id)}
              <option value={p.id ?? p.user_id}>{nombreDe(p.id ?? p.user_id)}</option>
            {/each}
          </select>
          <Button variant="ghost" size="sm" on:click={() => quitarTurno(idx)}>
            <i class="fas fa-trash" aria-hidden="true"></i>
            <span class="sr-only">Quitar turno {idx + 1}</span>
          </Button>
        </div>
      {/each}

      <Button variant="ghost" size="sm" on:click={agregarTurno}>
        <i class="fas fa-plus" aria-hidden="true"></i> Añadir firmante
      </Button>

      {#if repetidos}
        <p class="cadena__error" role="alert">
          Hay una persona repetida: cada firmante ocupa un solo turno.
        </p>
      {/if}
    {/if}

    {#if error}
      <p class="cadena__error" role="alert">{error}</p>
    {/if}
  </div>

  <svelte:fragment slot="footer">
    {#if !cadenaAbierta}
      <Button variant="primary" loading={creando} disabled={!puedeCrear} on:click={crear}>
        <i class="fas fa-users" aria-hidden="true"></i> Abrir cadena
      </Button>
    {/if}
    <Button variant="ghost" on:click={() => dispatch("close")}>Cerrar</Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .cadena {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
    margin-top: var(--op-space-3);

    &__objeto {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
      margin: 0;
    }

    &__ayuda,
    &__status {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__aviso {
      font-size: var(--op-font-sm);
      color: var(--op-warning-fg);
      background: var(--op-warning-bg);
      border: 1px solid var(--op-warning);
      border-radius: var(--op-radius-sm);
      padding: var(--op-space-3);
      margin: 0;
    }

    &__turnos {
      margin: 0;
      padding-left: var(--op-space-4);
      font-size: var(--op-font-sm);
      color: var(--op-text);
    }

    &__turno {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      flex-wrap: wrap;
    }

    &__orden {
      font-weight: var(--op-weight-medium);
      color: var(--op-text-secondary);
      min-width: 1.5rem;
    }

    &__select {
      flex: 1 1 12rem;
      min-width: 0;
      font-family: var(--op-font-family);
      font-size: var(--op-font-sm);
      padding: var(--op-space-2);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius-sm);
      background: var(--op-surface);
      color: var(--op-text);
    }

    &__error {
      font-size: var(--op-font-sm);
      color: var(--op-error);
      margin: 0;
    }
  }
</style>
