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
  /admin/correo/oauth/callback — Retorno de Google o Microsoft tras el
  consentimiento (E19).

  **Por qué el canje ocurre aquí y no en un endpoint público del backend.** El
  proveedor redirige el navegador del administrador a esta dirección; si la
  recibiera directamente el backend, haría falta una ruta sin autenticar
  publicada en el gateway, y toda la protección recaería en el `state`. Al
  aterrizar en la aplicación, la sesión del administrador ya está establecida y
  el canje viaja como una llamada autenticada más.

  **De qué cuenta se trata** lo recuerda `sessionStorage`, porque el proveedor
  redirige a una dirección FIJA —no admite comodines en la ruta— y al volver hay
  que saberlo. No es un canal de confianza y no necesita serlo: el backend
  comprueba que el `state` pertenezca a esa cuenta y a ese usuario, así que
  manipularlo solo consigue que el canje falle.

  El código de autorización llega en la URL. Se canjea **una sola vez** y se
  limpia la marca en cuanto se usa: volver a cargar esta página con el mismo
  código no repite nada, porque el `state` ya se consumió del lado del servidor.
-->
<script>
  import { onMount } from "svelte";
  import { page } from "$app/stores";
  import { goto } from "$app/navigation";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  import { codigoError, mensajeError } from "$lib/utils/apiError.js";
  import Card from "$lib/components/ui/Card.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";

  const CLAVE = "orpyca:oauth-correo-cuenta";

  /** @type {"canjeando" | "hecho" | "error"} */
  let estado = "canjeando";
  let detalle = "";

  async function canjear() {
    const parametros = $page.url.searchParams;
    const code = parametros.get("code") ?? "";
    const state = parametros.get("state") ?? "";
    // El proveedor informa el rechazo del usuario por aquí. No es una avería:
    // alguien pulsó «cancelar», y decirlo así evita que se busque un fallo que
    // no existe.
    const rechazo = parametros.get("error");

    const cuentaId = sessionStorage.getItem(CLAVE) ?? "";
    sessionStorage.removeItem(CLAVE);

    if (rechazo) {
      estado = "error";
      detalle =
        rechazo === "access_denied"
          ? "La autorización se canceló. El buzón sigue sin acceso concedido."
          : `El proveedor rechazó la autorización (${rechazo}).`;
      return;
    }
    if (!code || !state) {
      estado = "error";
      detalle = "La respuesta del proveedor llegó incompleta. Vuelva a iniciar la autorización.";
      return;
    }
    if (!cuentaId) {
      estado = "error";
      detalle =
        "No se pudo determinar qué buzón se estaba autorizando. Esto pasa si la autorización " +
        "se abrió en otra pestaña o en otro navegador. Vuelva a iniciarla desde el buzón.";
      return;
    }

    try {
      const res = await fetch(`/admin/correo/api/cuentas/${cuentaId}/oauth/canjear`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, state }),
      });
      if (res.status === 204) {
        estado = "hecho";
        notifySuccess("Acceso al buzón autorizado.");
        await goto("/admin/correo", { replaceState: true });
        return;
      }
      const cuerpo = await res.json().catch(() => ({}));
      estado = "error";
      detalle = mensajeError(
        cuerpo,
        `No se pudo completar la autorización (${codigoError(cuerpo)}).`,
      );
      notifyError(detalle);
    } catch {
      estado = "error";
      detalle = "No se pudo conectar al servidor.";
      notifyError(detalle);
    }
  }

  onMount(() => {
    setBreadcrumbs([
      { label: "Administración", href: "/admin" },
      { label: "Ingesta de correo", href: "/admin/correo" },
      { label: "Autorización" },
    ]);
    canjear();
  });
</script>

<svelte:head>
  <title>Autorizando el acceso al buzón — OrpycaMCP</title>
</svelte:head>

<Card title="Autorización del buzón">
  {#if estado === "canjeando"}
    <div class="callback__esperando">
      <Loader />
      <p>Completando la autorización con el proveedor…</p>
    </div>
  {:else if estado === "hecho"}
    <p>Autorización completada. Volviendo a la configuración de correo…</p>
  {:else}
    <div class="callback__fallo">
      <p>{detalle}</p>
      <Button href="/admin/correo">Volver a la ingesta de correo</Button>
    </div>
  {/if}
</Card>

<style lang="scss">
  /*
    Los hijos directos son el indicador y el párrafo; el texto nunca cuelga
    suelto de un contenedor flex en fila.
  */
  .callback__esperando,
  .callback__fallo {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--op-space-3);
  }
</style>
