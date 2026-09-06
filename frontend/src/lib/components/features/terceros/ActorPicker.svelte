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
  ActorPicker — buscar, reutilizar y añadir ACTORES de un radicado (E01, RF-RAD-05).

  Sirve a los dos roles: el **remitente** de una Entrada y los **destinatarios**
  de una Salida. Antes solo existía para destinatarios, y esa asimetría era la
  causa de los duplicados: el remitente de una Entrada se tecleaba a mano en dos
  columnas de texto libre, así que el directorio solo podía aprender de las
  Salidas — y de esas tampoco, porque el vínculo no llegaba a persistirse.
  De 182 radicados, 104 eran Entradas.

  **Buscar antes que crear, y por eso el buscador va primero.** El Orfeo del que
  deriva este sistema acabó con el mismo señor treinta veces en el catálogo, con
  direcciones distintas y variaciones de tildes. Esos duplicados no se crean por
  saltarse una restricción: se crean por **no encontrar lo que ya está**. Aquí el
  alta solo aparece cuando la búsqueda no dio con nadie, y la búsqueda ignora
  tildes y mayúsculas.

  **El contacto se puede corregir para ESTE envío**, que es lo que pasa de
  verdad al radicar: se busca a alguien y se le ajusta el correo o la dirección.
  Ese ajuste **no toca la ficha del catálogo** —y se dice en pantalla—, porque
  vale para este envío y no dice nada sobre dónde vive esa persona en general.

  **El canal es una condición material.** Sin correo no hay envío electrónico y
  sin dirección no hay envío físico, así que la opción no se ofrece cuando falta
  el dato: ofrecerla haría descubrir el límite chocando con el backend, y aquí
  el dato se puede completar en el mismo sitio.
-->
<script>
  import { createEventDispatcher } from "svelte";

  import CascadaDivipola from "$lib/components/features/terceros/CascadaDivipola.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import { notifyError } from "$lib/stores/notifications.js";
  import { mensajeError } from "$lib/utils/apiError.js";

  /** Actores ya elegidos, con su contacto congelado para ESTE radicado. */
  export let destinatarios = [];
  export let disabled = false;
  /** `remitente` (quien lo envía) o `destinatario` (a quién va). */
  export let rol = "destinatario";
  /** Cuántos admite. Un radicado tiene UN remitente y puede tener varios
      destinatarios; el tope se aplica ocultando el buscador, no rechazando
      después, para que el límite se vea antes de chocar con él. */
  export let max = 99;

  $: esRemitente = rol === "remitente";
  $: rotulo = esRemitente ? "remitente" : "destinatario";
  $: completo = destinatarios.length >= max;

  const dispatch = createEventDispatcher();

  const TIPOS = [
    { value: "ciudadano", label: "Ciudadano" },
    { value: "empresa", label: "Empresa" },
    { value: "entidad_publica", label: "Entidad pública" },
    { value: "usuario_interno", label: "Usuario interno" },
    { value: "direccion_libre", label: "Dirección libre (sin identificar)" },
  ];

  const CANALES = [
    { value: "", label: "Sin decidir" },
    { value: "email", label: "Correo electrónico" },
    { value: "fisico", label: "Físico" },
    { value: "ambos", label: "Ambos" },
  ];

  let consulta = "";
  let buscando = false;
  let resultados = [];
  let buscado = false;

  let modal = null; // "alta" | "contacto"
  let enviando = false;
  let errorModal = "";
  let editandoIndice = null;

  // Geografía DIVIPOLA. Se guarda el CÓDIGO, no el nombre: el nombre que
  // acaba en la base lo resuelve el backend contra el catálogo, así que es
  // indiferente cómo lo muestre esta pantalla.
  let fDepartamentoCod = "";
  let fMunicipioCod = "";
  let cDepartamentoCod = "";
  let cMunicipioCod = "";
  let cDepartamento = null;
  let cMunicipio = null;

  // Alta de un tercero nuevo.
  let fTipo = "ciudadano";
  let fNombre = "";
  let fEntidad = "";
  let fTipoDoc = "";
  let fNumDoc = "";
  let fEmail = "";
  let fDireccion = "";

  // Corrección del contacto para este envío.
  let cEmail = "";
  let cDireccion = "";
  let cTelefono = "";

  async function buscar() {
    buscando = true;
    buscado = false;
    try {
      const res = await fetch(`/radicar/api/terceros?q=${encodeURIComponent(consulta)}`);
      const body = await res.json().catch(() => []);
      resultados = res.ok && Array.isArray(body) ? body : [];
      if (!res.ok) notifyError(mensajeError(body, `Error ${res.status}`));
    } catch {
      resultados = [];
      notifyError("No se pudo buscar en el catálogo de terceros.");
    } finally {
      buscando = false;
      buscado = true;
    }
  }

  function yaElegido(tercero) {
    return destinatarios.some((d) => d.tercero_id === tercero.id);
  }

  function anadir(tercero) {
    // El contacto arranca con el de la ficha; se corrige aparte si hace falta.
    destinatarios = [
      ...destinatarios,
      {
        tercero_id: tercero.id,
        rol,
        nombre: tercero.nombre,
        entidad: tercero.entidad,
        email: tercero.email,
        telefono: tercero.telefono,
        direccion: tercero.direccion,
        municipio: tercero.municipio,
        departamento: tercero.departamento,
        municipio_codigo: tercero.municipio_codigo ?? null,
        departamento_codigo: tercero.departamento_codigo ?? null,
        canal: "",
      },
    ];
    dispatch("cambio", destinatarios);
  }

  function quitar(indice) {
    destinatarios = destinatarios.filter((_, i) => i !== indice);
    dispatch("cambio", destinatarios);
  }

  function abrirAlta() {
    modal = "alta";
    errorModal = "";
    fTipo = "ciudadano";
    fNombre = consulta;
    fEntidad = "";
    fTipoDoc = "";
    fNumDoc = "";
    fEmail = "";
    fDireccion = "";
    fDepartamentoCod = "";
    fMunicipioCod = "";
  }

  async function crearTercero() {
    enviando = true;
    errorModal = "";
    try {
      const res = await fetch("/radicar/api/terceros", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tipo: fTipo,
          nombre: fNombre,
          entidad: fEntidad || null,
          tipo_documento: fTipoDoc || null,
          numero_documento: fNumDoc || null,
          email: fEmail || null,
          direccion: fDireccion || null,
          departamento_codigo: fDepartamentoCod || null,
          municipio_codigo: fMunicipioCod || null,
        }),
      });
      const body = await res.json().catch(() => ({}));
      enviando = false;
      if (!res.ok) {
        errorModal = mensajeError(body, `Error ${res.status}`);
        return;
      }
      // El alta es idempotente por documento: si ya existía, se DICE en vez de
      // dejar creer que se creó una ficha nueva.
      if (body.creado === false) {
        notifyError(
          `Ya existía «${body.nombre}» con ese documento: se reutiliza esa ficha en vez de crear otra.`,
        );
      }
      modal = null;
      anadir(body);
    } catch {
      enviando = false;
      errorModal = "No se pudo conectar con el servidor.";
    }
  }

  function abrirContacto(indice) {
    modal = "contacto";
    editandoIndice = indice;
    errorModal = "";
    const d = destinatarios[indice];
    cEmail = d.email ?? "";
    cDireccion = d.direccion ?? "";
    cTelefono = d.telefono ?? "";
    cDepartamentoCod = d.departamento_codigo ?? "";
    cMunicipioCod = d.municipio_codigo ?? "";
  }

  function guardarContacto() {
    const copia = [...destinatarios];
    copia[editandoIndice] = {
      ...copia[editandoIndice],
      email: cEmail || null,
      direccion: cDireccion || null,
      telefono: cTelefono || null,
      departamento_codigo: cDepartamentoCod || null,
      municipio_codigo: cMunicipioCod || null,
      // Los nombres viajan solo para poder MOSTRARLOS en la lista mientras se
      // compone el radicado. Lo que se congela lo resuelve el backend desde el
      // código, así que un nombre desactualizado aquí no ensucia el registro.
      departamento: cDepartamento ?? copia[editandoIndice].departamento,
      municipio: cMunicipio ?? copia[editandoIndice].municipio,
    };
    destinatarios = copia;
    modal = null;
    dispatch("cambio", destinatarios);
  }

  /** Qué canales son posibles con el contacto de ESTE envío. */
  function canalesPosibles(d) {
    const tieneEmail = Boolean((d.email ?? "").trim());
    const tieneDireccion = Boolean((d.direccion ?? "").trim());
    return CANALES.filter((c) => {
      if (c.value === "email") return tieneEmail;
      if (c.value === "fisico") return tieneDireccion;
      if (c.value === "ambos") return tieneEmail && tieneDireccion;
      return true;
    });
  }

  /** Qué dato falta para el canal elegido, o cadena vacía. */
  function faltaParaCanal(d) {
    if (!d.canal) return "";
    const faltas = [];
    if ((d.canal === "email" || d.canal === "ambos") && !(d.email ?? "").trim()) {
      faltas.push("un correo electrónico");
    }
    if ((d.canal === "fisico" || d.canal === "ambos") && !(d.direccion ?? "").trim()) {
      faltas.push("una dirección física");
    }
    return faltas.join(" y ");
  }
</script>

<div class="dest">
  <p class="dest__intro">
    {#if esRemitente}Toda Entrada tiene <strong>un remitente</strong>, y buscarlo en el directorio
      es lo que evita que la misma persona acabe treinta veces en el catálogo.{:else}Un radicado de
      salida puede tener <strong>uno o varios destinatarios</strong>.{/if} Busca antes de crear: la mayoría
    de los duplicados aparecen por no encontrar la ficha que ya existe.
  </p>

  <!--
    Con el cupo lleno el buscador desaparece en vez de rechazar después: un
    remitente es UNO, y dejar buscar para luego negar hace descubrir el límite
    chocando con él.
  -->
  {#if !completo}
    <div class="dest__buscador">
      <FormField
        id="tercero-buscar"
        name="q"
        label={esRemitente ? "Buscar remitente" : "Buscar destinatario"}
        bind:value={consulta}
        {disabled}
        hint="Por nombre o entidad. No distingue tildes ni mayúsculas."
      />
      <Button variant="secondary" loading={buscando} {disabled} on:click={buscar}>Buscar</Button>
    </div>
  {:else}
    <p class="dest__intro">
      Ya hay {rotulo} para este radicado. Quítalo si quieres elegir otro.
    </p>
  {/if}

  {#if buscado}
    {#if resultados.length === 0}
      <EmptyState
        icon="fas fa-user-slash"
        title="No hay ninguna ficha que coincida"
        description="Si de verdad no está en el catálogo, regístralo. Antes prueba con menos palabras: buscar «Pérez» encuentra más que «José Pérez Gómez»."
      />
      {#if !disabled}
        <Button variant="primary" icon="fas fa-user-plus" on:click={abrirAlta}>
          Registrar un tercero nuevo
        </Button>
      {/if}
    {:else}
      <ul class="dest__resultados">
        {#each resultados as t (t.id)}
          <li>
            <span class="dest__nombre">{t.nombre}</span>
            {#if t.numero_documento}
              <span class="dest__doc">{t.tipo_documento} {t.numero_documento}</span>
            {/if}
            {#if t.entidad}<span class="dest__sub">{t.entidad}</span>{/if}
            {#if yaElegido(t)}
              <span class="dest__sub">Ya está en la lista</span>
            {:else if !disabled}
              <Button size="sm" variant="ghost" on:click={() => anadir(t)}>Añadir</Button>
            {/if}
          </li>
        {/each}
      </ul>
      {#if !disabled}
        <Button size="sm" variant="ghost" on:click={abrirAlta}>
          Ninguno es: registrar uno nuevo
        </Button>
      {/if}
    {/if}
  {/if}

  {#if destinatarios.length > 0}
    <h4 class="dest__titulo">
      {esRemitente ? "Remitente" : "Destinatarios"} de este radicado ({destinatarios.length})
    </h4>
    <ul class="dest__lista">
      {#each destinatarios as d, i (d.tercero_id + (d.direccion ?? ""))}
        <li class="dest__item">
          <div class="dest__item-datos">
            <strong>{d.nombre}</strong>
            {#if d.entidad}<span class="dest__sub"> · {d.entidad}</span>{/if}
            <br />
            <span class="dest__sub">
              {d.email || "sin correo"} · {d.direccion || "sin dirección física"}
            </span>
          </div>
          <div class="dest__item-canal">
            <FormField
              id="canal-{i}"
              name="canal-{i}"
              type="select"
              label="Envío"
              bind:value={destinatarios[i].canal}
              options={canalesPosibles(d)}
              {disabled}
              on:change={() => dispatch("cambio", destinatarios)}
            />
          </div>
          {#if !disabled}
            <div class="dest__item-acciones">
              <Button size="sm" variant="ghost" on:click={() => abrirContacto(i)}>
                Corregir contacto
              </Button>
              <Button size="sm" variant="ghost" on:click={() => quitar(i)}>Quitar</Button>
            </div>
          {/if}
          {#if faltaParaCanal(d)}
            <p class="dest__aviso" role="alert">
              Marcado para envío <strong>{d.canal}</strong> y le falta {faltaParaCanal(d)}.
              Complétalo aquí o cambia el canal: sin ese dato el envío no se puede hacer.
            </p>
          {/if}
          {#if !(d.email ?? "").trim() && !(d.direccion ?? "").trim()}
            <p class="dest__aviso" role="status">
              Sin correo ni dirección no se le puede enviar por ningún canal.
            </p>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</div>

<Modal
  open={modal === "alta"}
  title="Registrar un tercero"
  size="sm"
  on:close={() => (modal = null)}
>
  <FormField
    id="t-tipo"
    name="tipo"
    type="select"
    label="Tipo"
    bind:value={fTipo}
    options={TIPOS}
  />
  <FormField id="t-nombre" name="nombre" label="Nombre" required bind:value={fNombre} />
  <FormField id="t-entidad" name="entidad" label="Entidad (opcional)" bind:value={fEntidad} />
  <FormField
    id="t-tipodoc"
    name="tipo_documento"
    label="Tipo de documento"
    bind:value={fTipoDoc}
    hint="Con documento, el sistema impide que esta persona se duplique. Sin él, no puede."
  />
  <FormField id="t-numdoc" name="numero_documento" label="Número" bind:value={fNumDoc} />
  <FormField id="t-email" name="email" label="Correo electrónico" bind:value={fEmail} />
  <FormField id="t-direccion" name="direccion" label="Dirección física" bind:value={fDireccion} />
  <CascadaDivipola
    idPrefijo="t"
    bind:departamentoCodigo={fDepartamentoCod}
    bind:municipioCodigo={fMunicipioCod}
  />
  {#if (fTipoDoc && !fNumDoc) || (!fTipoDoc && fNumDoc)}
    <p class="dest__aviso" role="alert">
      El tipo y el número de documento van juntos o ninguno: uno sin el otro no identifica a nadie.
    </p>
  {/if}
  {#if errorModal}<p class="dest__error" role="alert">{errorModal}</p>{/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modal = null)}>Cancelar</Button>
    <Button
      variant="primary"
      loading={enviando}
      disabled={fNombre.trim().length < 2 || Boolean(fTipoDoc) !== Boolean(fNumDoc)}
      on:click={crearTercero}
    >
      Registrar
    </Button>
  </svelte:fragment>
</Modal>

<Modal
  open={modal === "contacto"}
  title="Contacto para este envío"
  size="sm"
  on:close={() => (modal = null)}
>
  <p class="dest__aviso">
    Lo que cambies aquí vale <strong>solo para este radicado</strong> y queda congelado con él: no modifica
    la ficha del catálogo. Es lo que permite enviar a una dirección concreta sin que cambie lo que dicen
    los envíos anteriores.
  </p>
  <FormField id="c-email" name="email" label="Correo electrónico" bind:value={cEmail} />
  <FormField id="c-direccion" name="direccion" label="Dirección física" bind:value={cDireccion} />
  <FormField id="c-telefono" name="telefono" label="Teléfono" bind:value={cTelefono} />
  <CascadaDivipola
    idPrefijo="c"
    bind:departamentoCodigo={cDepartamentoCod}
    bind:municipioCodigo={cMunicipioCod}
    on:cambio={(e) => {
      cDepartamento = e.detail.departamento;
      cMunicipio = e.detail.municipio;
    }}
  />
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={() => (modal = null)}>Cancelar</Button>
    <Button variant="primary" on:click={guardarContacto}>Usar estos datos</Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .dest__intro,
  .dest__sub {
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
  }
  .dest__buscador {
    display: flex;
    align-items: flex-end;
    gap: var(--op-space-2);
    margin-bottom: var(--op-space-3);
  }
  .dest__resultados,
  .dest__lista {
    list-style: none;
    margin: var(--op-space-2) 0;
  }
  .dest__resultados li {
    display: flex;
    align-items: center;
    gap: var(--op-space-2);
    padding: var(--op-space-2);
    border-bottom: 1px solid var(--op-border);
    font-size: var(--op-font-sm);
  }
  .dest__nombre {
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
  }
  .dest__doc {
    font-family: var(--op-font-family-mono);
    font-size: var(--op-font-xs);
    color: var(--op-text-secondary);
  }
  .dest__titulo {
    margin-top: var(--op-space-4);
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
  }
  .dest__item {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    gap: var(--op-space-3);
    padding: var(--op-space-3);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    margin-bottom: var(--op-space-2);
  }
  .dest__item-datos {
    flex: 1 1 16rem;
  }
  .dest__item-canal {
    flex: 0 0 12rem;
  }
  .dest__item-acciones {
    display: flex;
    flex-wrap: wrap;
    gap: var(--op-space-1);
  }
  .dest__aviso {
    flex: 1 1 100%;
    margin-top: var(--op-space-2);
    padding: var(--op-space-2);
    border-left: 3px solid var(--op-warning);
    background: var(--op-bg);
    color: var(--op-text);
    font-size: var(--op-font-sm);
  }
  .dest__error {
    color: var(--op-error);
    font-size: var(--op-font-sm);
    margin: var(--op-space-2) 0;
  }
</style>
