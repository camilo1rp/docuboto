<!--
  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
  Contacto: aurigadl@gmail.com

  Software libre: este archivo se distribuye bajo la Licencia Pública General
  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

  Cascada geográfica DIVIPOLA (E01, RF-RAD-05).

  **Por qué el municipio se ELIGE y no se escribe.** Mientras fue texto libre,
  «Bogotá», «BOGOTA» y «Bogota D.C.» eran tres municipios distintos para
  cualquier consulta, cualquier reporte y cualquier exportación — la misma
  plaga de duplicados del Orfeo original, vista desde el destino en vez del
  sujeto. Una restricción de unicidad no la ataja, porque las tres escrituras
  SON distintas y todas pasan.

  Se emiten el código y el nombre, pero **el que manda es el código**: el
  nombre que se guarda lo resuelve el backend contra el catálogo, así que da
  igual lo que esta pantalla muestre.
-->
<script>
  import { createEventDispatcher, onMount } from "svelte";

  import FormField from "$lib/components/ui/FormField.svelte";
  import { cargarDepartamentos, cargarMunicipios } from "$lib/utils/divipola.js";

  /** Código DIVIPOLA de 2 dígitos, o "" si no se ha elegido. */
  export let departamentoCodigo = "";
  /** Código DIVIPOLA de 5 dígitos, o "" si no se ha elegido. */
  export let municipioCodigo = "";
  export let disabled = false;
  /** Prefijo de los `id` — dos cascadas en la misma página no pueden repetirlos. */
  export let idPrefijo = "divipola";

  const dispatch = createEventDispatcher();

  let departamentos = [];
  let municipios = [];
  let cargandoMunicipios = false;
  let error = "";

  onMount(async () => {
    try {
      departamentos = await cargarDepartamentos();
      // Si se entra con un municipio ya elegido —al editar el contacto de un
      // destinatario que ya lo tenía— hay que poblar la segunda lista o el
      // valor quedaría seleccionado sobre una lista vacía y se perdería al
      // primer render.
      if (departamentoCodigo) await traerMunicipios(departamentoCodigo);
    } catch {
      error = "No se pudo cargar el catálogo geográfico.";
    }
  });

  async function traerMunicipios(codigo) {
    cargandoMunicipios = true;
    try {
      municipios = await cargarMunicipios(codigo);
      error = "";
    } catch {
      municipios = [];
      error = "No se pudieron cargar los municipios.";
    }
    cargandoMunicipios = false;
  }

  async function alCambiarDepartamento() {
    // **El municipio anterior se limpia siempre.** Dejarlo colgando del nuevo
    // departamento es la incoherencia que una cascada mal programada produce
    // en silencio; aquí además la rechazaría la base, con un mensaje que no
    // dice qué pasó.
    municipioCodigo = "";
    municipios = [];
    if (departamentoCodigo) await traerMunicipios(departamentoCodigo);
    emitir();
  }

  function emitir() {
    const dep = departamentos.find((d) => d.codigo === departamentoCodigo);
    const mun = municipios.find((m) => m.codigo === municipioCodigo);
    dispatch("cambio", {
      departamento_codigo: departamentoCodigo || null,
      municipio_codigo: municipioCodigo || null,
      departamento: dep?.nombre ?? null,
      municipio: mun?.nombre ?? null,
    });
  }

  $: opcionesDepartamento = [
    { value: "", label: "Sin especificar" },
    ...departamentos.map((d) => ({ value: d.codigo, label: d.nombre })),
  ];
  $: opcionesMunicipio = [
    { value: "", label: cargandoMunicipios ? "Cargando…" : "Sin especificar" },
    ...municipios.map((m) => ({ value: m.codigo, label: m.nombre })),
  ];
</script>

<FormField
  id="{idPrefijo}-departamento"
  name="departamento_codigo"
  type="select"
  label="Departamento"
  disabled={disabled || departamentos.length === 0}
  options={opcionesDepartamento}
  bind:value={departamentoCodigo}
  on:change={alCambiarDepartamento}
/>

<FormField
  id="{idPrefijo}-municipio"
  name="municipio_codigo"
  type="select"
  label="Municipio"
  disabled={disabled || !departamentoCodigo || cargandoMunicipios}
  hint={departamentoCodigo ? "" : "Elige primero el departamento."}
  options={opcionesMunicipio}
  bind:value={municipioCodigo}
  on:change={emitir}
/>

{#if error}
  <p class="divipola__error" role="alert">
    {error} Puedes seguir sin municipio: el envío físico solo exige la dirección.
  </p>
{/if}

<style lang="scss">
  .divipola__error {
    color: var(--op-error);
    font-size: var(--op-font-sm);
  }
</style>
