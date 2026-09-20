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
  /admin/terceros — el directorio y su administración (E01, RF-RAD-05).

  **Por qué existe.** Hasta ahora un tercero solo podía NACER, como efecto
  colateral de elegir un destinatario al radicar. No había dónde listarlos,
  revisarlos ni consolidar los que sobraban: `/admin` tenía veinte secciones y
  ninguna era esta. El Orfeo del que deriva el sistema acabó con el mismo señor
  treinta veces en el catálogo.

  **Los duplicados van arriba, y separados por eje.** No valen lo mismo:
  coincidir en documento de identidad es certeza —son la misma persona— y
  coincidir en nombre es SOSPECHA, porque puede haber dos Juan Pérez. Por eso
  la pantalla los muestra y nunca fusiona sola.

  **Fusionar no borra ni reescribe.** Los radicados de la ficha absorbida siguen
  nombrándola: el día del envío existían dos fichas y se usó una, y decir lo
  contrario falsificaría un acto ya ocurrido (lo impone un trigger de la base).
  Lo que la fusión añade es lo que se supo después, y el superviviente hereda
  el historial al leerlo. Se dice en pantalla antes de confirmar, porque quien
  fusiona tiene derecho a saber qué NO va a pasar.
-->
<script>
  import Button from "$lib/components/ui/Button.svelte";
  import Card from "$lib/components/ui/Card.svelte";
  import DataTable from "$lib/components/ui/DataTable.svelte";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  import { mensajeError } from "$lib/utils/apiError.js";

  /** @type {import('./$types').PageData} */
  export let data;

  let terceros = data.terceros ?? [];
  let duplicados = data.duplicados ?? [];
  let consulta = "";
  let buscando = false;

  const COLUMNAS = [
    { key: "nombre", label: "Nombre" },
    { key: "entidad", label: "Entidad" },
    { key: "numero_documento", label: "Documento" },
    { key: "email", label: "Correo" },
    { key: "municipio", label: "Municipio" },
  ];

  async function buscar() {
    buscando = true;
    try {
      const res = await fetch(
        `/admin/terceros/api?accion=buscar&q=${encodeURIComponent(consulta)}`,
      );
      const cuerpo = await res.json();
      if (!res.ok) throw cuerpo;
      terceros = cuerpo;
    } catch (e) {
      notifyError(mensajeError(e, "No se pudo consultar el directorio."));
    } finally {
      buscando = false;
    }
  }

  async function recargarDuplicados() {
    try {
      const res = await fetch("/admin/terceros/api?accion=duplicados");
      const cuerpo = await res.json();
      if (res.ok) duplicados = cuerpo;
    } catch {
      // El listado principal no depende de esto: un fallo aquí no lo tumba.
    }
  }

  // --- Ficha: dónde aparece --------------------------------------------------
  let drawerAbierto = false;
  /** @type {any} */
  let fichaAbierta = null;
  let radicados = [];
  let cargandoRadicados = false;

  async function verFicha(/** @type {any} */ t) {
    fichaAbierta = t;
    drawerAbierto = true;
    radicados = [];
    cargandoRadicados = true;
    try {
      const res = await fetch(`/admin/terceros/api?accion=radicados&id=${t.id}`);
      const cuerpo = await res.json();
      if (res.ok) radicados = cuerpo;
    } catch {
      notifyError("No se pudo leer en qué radicados aparece esta ficha.");
    } finally {
      cargandoRadicados = false;
    }
  }

  // --- Fusión: acto irreversible --------------------------------------------
  // La frase tecleada nombra el acto Y el objeto. Un «sí» genérico confirma
  // «una acción», no ESTA: quien lo teclea de memoria no se entera de a quién
  // está consolidando.
  const FRASE = "fusionar";
  let modalFusion = false;
  /** @type {any} */ let superviviente = null;
  /** @type {any} */ let absorbido = null;
  let fraseTecleada = "";
  let fusionando = false;

  function abrirFusion(/** @type {any} */ vive, /** @type {any} */ muere) {
    superviviente = vive;
    absorbido = muere;
    fraseTecleada = "";
    modalFusion = true;
  }

  async function confirmarFusion() {
    if (fraseTecleada.trim().toLowerCase() !== FRASE) return;
    fusionando = true;
    try {
      const res = await fetch(`/admin/terceros/api?accion=fusionar&id=${superviviente.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ absorbe_id: absorbido.id }),
      });
      const cuerpo = await res.json();
      if (!res.ok) throw cuerpo;
      notifySuccess(
        `«${absorbido.nombre}» se consolidó en «${superviviente.nombre}». ` +
          `${cuerpo.vinculos_heredados} radicado(s) pasan a verse desde la ficha superviviente.`,
      );
      modalFusion = false;
      await Promise.all([recargarDuplicados(), buscar()]);
    } catch (e) {
      notifyError(mensajeError(e, "No se pudo completar la fusión."));
    } finally {
      fusionando = false;
    }
  }
</script>

<svelte:head><title>Directorio de terceros — OrpycaMCP</title></svelte:head>

<div class="terceros">
  <h1 class="terceros__titulo">Directorio de terceros</h1>
  <p class="terceros__sub">
    Remitentes y destinatarios del catálogo de la entidad. Buscar antes de crear es lo que evita que
    la misma persona acabe repetida: la búsqueda ignora tildes y mayúsculas.
  </p>

  <!-- Duplicados primero: es lo accionable -->
  {#if data.duplicadoError}
    <Card>
      <p class="terceros__aviso" role="alert">
        No se pudo revisar si hay fichas duplicadas ({data.duplicadoError}). Esto
        <strong>no</strong> significa que no las haya.
      </p>
    </Card>
  {:else if duplicados.length > 0}
    <Card>
      <h2 class="terceros__h2">Posibles duplicados ({duplicados.length})</h2>
      <p class="terceros__sub">
        Coincidir en <strong>documento</strong> es certeza: son la misma persona. Coincidir solo en
        <strong>nombre</strong> es una sospecha — puede haber dos personas homónimas. Por eso nada se
        fusiona solo.
      </p>
      {#each duplicados as g (g.eje + g.clave)}
        <div class="terceros__grupo">
          <p class="terceros__grupo-eje">
            {g.eje === "documento" ? "Mismo documento" : "Mismo nombre"}
            <code class="terceros__clave">{g.clave}</code>
          </p>
          <ul class="terceros__fichas">
            {#each g.fichas as f, i (f.id)}
              <li class="terceros__ficha">
                <span>
                  <strong>{f.nombre}</strong>
                  {#if f.entidad}<span class="terceros__tenue"> · {f.entidad}</span>{/if}
                  {#if f.numero_documento}
                    <span class="terceros__tenue"> · {f.numero_documento}</span>
                  {/if}
                </span>
                <span class="terceros__acciones">
                  <Button variant="ghost" size="sm" on:click={() => verFicha(f)}>Ver</Button>
                  {#if i > 0}
                    <Button variant="ghost" size="sm" on:click={() => abrirFusion(g.fichas[0], f)}>
                      Consolidar en la primera
                    </Button>
                  {/if}
                </span>
              </li>
            {/each}
          </ul>
        </div>
      {/each}
    </Card>
  {/if}

  <Card>
    <div class="terceros__buscador">
      <FormField
        id="dir-buscar"
        label="Buscar en el directorio"
        bind:value={consulta}
        hint="Por nombre o entidad. No distingue tildes ni mayúsculas."
      />
      <Button variant="secondary" loading={buscando} on:click={buscar}>Buscar</Button>
    </div>

    {#if data.terceroError}
      <p class="terceros__aviso" role="alert">
        No se pudo cargar el directorio ({data.terceroError}).
      </p>
    {:else if terceros.length === 0}
      <EmptyState
        icon="fas fa-address-book"
        title="No hay fichas que coincidan"
        description="Prueba con menos palabras: buscar «Pérez» encuentra más que «José Pérez Gómez»."
      />
    {:else}
      <DataTable columns={COLUMNAS} rows={terceros} on:rowclick={(e) => verFicha(e.detail)} />
    {/if}
  </Card>
</div>

<Drawer open={drawerAbierto} title="Ficha del directorio" on:close={() => (drawerAbierto = false)}>
  {#if fichaAbierta}
    <h3 class="terceros__h2">{fichaAbierta.nombre}</h3>
    {#if fichaAbierta.entidad}<p class="terceros__tenue">{fichaAbierta.entidad}</p>{/if}
    <p class="terceros__sub">
      Estos son los datos <strong>vigentes</strong> de la ficha. Lo que se usó en cada envío quedó congelado
      en su radicado y no cambia aunque la ficha se corrija.
    </p>

    <h4 class="terceros__h2">Dónde aparece</h4>
    {#if cargandoRadicados}
      <p class="terceros__tenue">Consultando…</p>
    {:else if radicados.length === 0}
      <p class="terceros__tenue">Todavía no aparece en ningún radicado.</p>
    {:else}
      <ul class="terceros__fichas">
        {#each radicados as r (r.tracking_number + r.rol)}
          <li class="terceros__ficha">
            <span><code>{r.tracking_number}</code> · {r.subject}</span>
            <span class="terceros__tenue">{r.rol}</span>
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
</Drawer>

<Modal
  open={modalFusion}
  title="Consolidar fichas duplicadas"
  on:close={() => (modalFusion = false)}
>
  {#if superviviente && absorbido}
    <p class="terceros__aviso">
      <strong>«{absorbido.nombre}»</strong> quedará marcada como la misma persona que
      <strong>«{superviviente.nombre}»</strong>, y dejará de aparecer al radicar.
    </p>
    <p class="terceros__sub">
      Lo que <strong>no</strong> ocurre: los radicados de la ficha absorbida seguirán nombrándola, porque
      el día de cada envío existían dos fichas y se usó una — reescribirlo daría información errónea sobre
      un acto ya ocurrido. El historial pasa a verse desde la ficha superviviente.
    </p>
    <p class="terceros__sub"><strong>No hay «deshacer fusión».</strong></p>
    <FormField
      id="fusion-frase"
      label={`Escribe «${FRASE}» para confirmar`}
      bind:value={fraseTecleada}
    />
  {/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modalFusion = false)}>Cancelar</Button>
    <Button
      variant="primary"
      loading={fusionando}
      disabled={fraseTecleada.trim().toLowerCase() !== FRASE}
      on:click={confirmarFusion}
    >
      Consolidar
    </Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .terceros {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-4);

    &__titulo {
      font-weight: var(--op-weight-bold);
      margin: 0;
    }

    &__h2 {
      font-size: var(--op-font-md);
      font-weight: var(--op-weight-bold);
      margin: var(--op-space-3) 0 var(--op-space-1);
    }

    &__sub {
      color: var(--op-text-secondary);
      margin: 0 0 var(--op-space-2);
    }

    &__tenue {
      color: var(--op-text-secondary);
    }

    &__aviso {
      margin: 0 0 var(--op-space-2);
    }

    &__buscador {
      display: flex;
      gap: var(--op-space-2);
      align-items: flex-end;
      margin-bottom: var(--op-space-3);
    }

    &__grupo {
      border-top: 1px solid var(--op-border);
      padding-top: var(--op-space-2);
      margin-top: var(--op-space-2);
    }

    &__grupo-eje {
      font-weight: var(--op-weight-medium);
      margin: 0 0 var(--op-space-1);
    }

    &__clave {
      color: var(--op-text-secondary);
      font-family: var(--op-font-family-mono);
    }

    &__fichas {
      list-style: none;
      margin: 0;
      padding: 0;
    }

    &__ficha {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-1) 0;
      flex-wrap: wrap;
    }

    &__acciones {
      display: flex;
      gap: var(--op-space-1);
      flex-shrink: 0;
    }
  }
</style>
