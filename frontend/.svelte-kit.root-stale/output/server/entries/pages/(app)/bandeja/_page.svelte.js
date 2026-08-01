import { c as create_ssr_component, e as escape, b as add_attribute, d as each, s as subscribe, v as validate_component } from "../../../../chunks/ssr.js";
import { p as permissions } from "../../../../chunks/auth.js";
import { D as Drawer } from "../../../../chunks/Drawer.js";
import { M as Modal } from "../../../../chunks/Modal.js";
import { S as StatusChip } from "../../../../chunks/StatusChip.js";
/* empty css                                                      */
import { E as EmptyState } from "../../../../chunks/EmptyState.js";
import { B as Button } from "../../../../chunks/Button.js";
import { F as FormField } from "../../../../chunks/FormField.js";
const css$1 = {
  code: '.op-userpicker.svelte-1bnmx7j.svelte-1bnmx7j{display:flex;flex-direction:column;gap:var(--op-space-2)}.op-userpicker__label.svelte-1bnmx7j.svelte-1bnmx7j{font-size:var(--op-font-sm);font-weight:var(--op-weight-medium);color:var(--op-text)}.op-userpicker__search.svelte-1bnmx7j.svelte-1bnmx7j{position:relative}.op-userpicker__search-icon.svelte-1bnmx7j.svelte-1bnmx7j{position:absolute;left:var(--op-space-3);top:50%;transform:translateY(-50%);font-size:var(--op-font-xs);color:var(--op-text-secondary);pointer-events:none}.op-userpicker__search-input.svelte-1bnmx7j.svelte-1bnmx7j{font-family:var(--op-font-family);font-size:var(--op-font-sm);color:var(--op-text);background:var(--op-surface);border:1px solid var(--op-border);border-radius:var(--op-radius);height:36px;width:100%;padding:0 var(--op-space-3) 0 var(--op-space-6);outline:none;transition:border-color var(--op-transition), box-shadow var(--op-transition)}.op-userpicker__search-input.svelte-1bnmx7j.svelte-1bnmx7j:focus{border-color:var(--op-primary);box-shadow:0 0 0 3px var(--op-focus-ring)}.op-userpicker__search-input.svelte-1bnmx7j.svelte-1bnmx7j:disabled{background:var(--op-bg);color:var(--op-text-secondary);cursor:not-allowed}.op-userpicker__panels.svelte-1bnmx7j.svelte-1bnmx7j{display:grid;grid-template-columns:1fr 1fr;gap:var(--op-space-3)}@media(max-width: 640px){.op-userpicker__panels.svelte-1bnmx7j.svelte-1bnmx7j{grid-template-columns:1fr}}.op-userpicker__panel.svelte-1bnmx7j.svelte-1bnmx7j{border:1px solid var(--op-border);border-radius:var(--op-radius);background:var(--op-surface);padding:var(--op-space-2);min-height:120px}.op-userpicker__panel-title.svelte-1bnmx7j.svelte-1bnmx7j{font-size:var(--op-font-xs);font-weight:var(--op-weight-bold);text-transform:uppercase;letter-spacing:0.04em;color:var(--op-text-secondary);margin:0 0 var(--op-space-2)}.op-userpicker__empty.svelte-1bnmx7j.svelte-1bnmx7j{font-size:var(--op-font-xs);color:var(--op-text-secondary);margin:var(--op-space-2) 0}.op-userpicker__list.svelte-1bnmx7j.svelte-1bnmx7j{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:var(--op-space-1);max-height:220px;overflow-y:auto}.op-userpicker__item.svelte-1bnmx7j.svelte-1bnmx7j{display:flex;align-items:center;gap:var(--op-space-2);padding:var(--op-space-1) var(--op-space-2);border-radius:var(--op-radius);background:var(--op-bg)}.op-userpicker__order.svelte-1bnmx7j.svelte-1bnmx7j{flex:0 0 auto;width:22px;height:22px;display:inline-flex;align-items:center;justify-content:center;font-size:var(--op-font-xs);font-weight:var(--op-weight-bold);color:var(--op-on-primary);background:var(--op-primary-dark);border-radius:50%}.op-userpicker__user.svelte-1bnmx7j.svelte-1bnmx7j{display:flex;flex-direction:column;min-width:0;flex:1 1 auto}.op-userpicker__username.svelte-1bnmx7j.svelte-1bnmx7j{font-size:var(--op-font-sm);color:var(--op-text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.op-userpicker__email.svelte-1bnmx7j.svelte-1bnmx7j{font-size:var(--op-font-xs);color:var(--op-text-secondary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.op-userpicker__actions.svelte-1bnmx7j.svelte-1bnmx7j{display:flex;gap:var(--op-space-1);flex:0 0 auto}.op-userpicker__btn.svelte-1bnmx7j.svelte-1bnmx7j{position:relative;width:28px;height:28px;display:inline-flex;align-items:center;justify-content:center;font-size:var(--op-font-xs);color:var(--op-text-secondary);background:var(--op-surface);border:1px solid var(--op-border);border-radius:var(--op-radius);cursor:pointer;transition:background var(--op-transition), color var(--op-transition), border-color var(--op-transition)}.op-userpicker__btn.svelte-1bnmx7j.svelte-1bnmx7j::after{content:"";position:absolute;inset:-8px}.op-userpicker__btn.svelte-1bnmx7j.svelte-1bnmx7j:hover:not(:disabled){border-color:var(--op-primary);color:var(--op-primary)}.op-userpicker__btn.svelte-1bnmx7j.svelte-1bnmx7j:focus-visible{outline:2px solid var(--op-primary);outline-offset:2px;box-shadow:0 0 0 3px var(--op-focus-ring)}.op-userpicker__btn.svelte-1bnmx7j.svelte-1bnmx7j:disabled{opacity:0.5;cursor:not-allowed}.op-userpicker__btn--add.svelte-1bnmx7j.svelte-1bnmx7j:hover:not(:disabled){border-color:var(--op-success, var(--op-primary));color:var(--op-success, var(--op-primary))}.op-userpicker__btn--remove.svelte-1bnmx7j.svelte-1bnmx7j:hover:not(:disabled){border-color:var(--op-error);color:var(--op-error)}.op-userpicker__hint.svelte-1bnmx7j.svelte-1bnmx7j{font-size:var(--op-font-xs);color:var(--op-text-secondary);margin:0}.op-userpicker__error.svelte-1bnmx7j.svelte-1bnmx7j{font-size:var(--op-font-xs);color:var(--op-error);margin:0}.op-userpicker--error.svelte-1bnmx7j .op-userpicker__panel.svelte-1bnmx7j{border-color:var(--op-error)}',
  map: '{"version":3,"file":"UserPicker.svelte","sources":["UserPicker.svelte"],"sourcesContent":["<!--\\n  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).\\n  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.\\n  Contacto: aurigadl@gmail.com\\n\\n  Software libre: este archivo se distribuye bajo la Licencia Pública General\\n  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,\\n  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega\\n  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.\\n-->\\n\\n<!--\\n  UserPicker.svelte — Selector ordenado de usuarios (T-08c).\\n\\n  Pensado para construir una cadena de revisión (vistos buenos), donde el\\n  ORDEN importa: el primer revisor decide primero, luego el segundo, etc.\\n\\n  Props:\\n    id        string  — prefijo de id para asociar label/controles (a11y)\\n    label     string  — texto del label visible\\n    users     Array<{id:string; username:string; email?:string; is_active?:boolean}>\\n              — universo de usuarios disponibles (cargado por el padre vía SSR).\\n    value     string[] — ids seleccionados EN ORDEN (bindable). Default [].\\n    disabled  boolean\\n    error     string  — mensaje de error\\n    hint      string  — texto de ayuda\\n    max       number  — máximo de revisores (default 20, paridad backend CadenaCreate)\\n\\n  El componente NO llama al gateway: recibe `users` ya resueltos por el padre\\n  (un proxy SSR que lee la cookie httpOnly — D-05). Solo gestiona la selección.\\n\\n  Accesibilidad:\\n    - búsqueda con label asociado\\n    - cada acción (agregar/subir/bajar/quitar) tiene aria-label descriptivo\\n    - una live region anuncia los cambios de la cadena\\n-->\\n<script>\\n  export let id = \\"userpicker\\";\\n  export let label = \\"\\";\\n  /** @type {Array<{id:string; username:string; email?:string; is_active?:boolean}>} */\\n  export let users = [];\\n  /** @type {string[]} — ids seleccionados, en orden */\\n  export let value = [];\\n  export let disabled = false;\\n  export let error = \\"\\";\\n  export let hint = \\"\\";\\n  export let max = 20;\\n\\n  let query = \\"\\";\\n\\n  // Mapa id → usuario para resolver nombres en la cadena seleccionada.\\n  $: usersById = new Map(users.map((u) => [u.id, u]));\\n\\n  // Disponibles: activos, no seleccionados, que casen con la búsqueda.\\n  $: available = users\\n    .filter((u) => u.is_active !== false && !value.includes(u.id))\\n    .filter((u) => {\\n      if (!query.trim()) return true;\\n      const q = query.trim().toLowerCase();\\n      return (\\n        u.username.toLowerCase().includes(q) || (u.email ?? \\"\\").toLowerCase().includes(q)\\n      );\\n    });\\n\\n  // Cadena seleccionada resuelta a objetos (preserva el orden de `value`).\\n  $: chain = value.map((uid) => usersById.get(uid)).filter(Boolean);\\n\\n  $: atMax = value.length >= max;\\n\\n  // Live region: describe la cadena EN ORDEN para lectores de pantalla. Se recompone\\n  // tras cada cambio (agregar/quitar/subir/bajar) para que el reordenamiento se anuncie.\\n  $: liveMessage =\\n    chain.length === 0\\n      ? \\"Sin revisores seleccionados.\\"\\n      : `Cadena de ${chain.length} revisor(es): ` +\\n        chain.map((u, i) => `${i + 1}. ${u.username}`).join(\\", \\") +\\n        \\".\\";\\n\\n  const hintId = `${id}-hint`;\\n  const errorId = `${id}-error`;\\n  $: describedBy = [hint ? hintId : null, errorId].filter(Boolean).join(\\" \\") || undefined;\\n\\n  function add(uid) {\\n    if (disabled || atMax || value.includes(uid)) return;\\n    value = [...value, uid];\\n  }\\n\\n  function remove(uid) {\\n    if (disabled) return;\\n    value = value.filter((x) => x !== uid);\\n  }\\n\\n  function move(index, delta) {\\n    if (disabled) return;\\n    const target = index + delta;\\n    if (target < 0 || target >= value.length) return;\\n    const next = [...value];\\n    const [item] = next.splice(index, 1);\\n    next.splice(target, 0, item);\\n    value = next;\\n  }\\n<\/script>\\n\\n<div class=\\"op-userpicker\\" class:op-userpicker--error={!!error}>\\n  {#if label}\\n    <span class=\\"op-userpicker__label\\" id=\\"{id}-label\\">{label}</span>\\n  {/if}\\n\\n  <!-- Búsqueda de usuarios disponibles -->\\n  <div class=\\"op-userpicker__search\\">\\n    <label class=\\"sr-only\\" for=\\"{id}-search\\">Buscar usuario por nombre o correo</label>\\n    <span class=\\"op-userpicker__search-icon\\" aria-hidden=\\"true\\">\\n      <i class=\\"fas fa-search\\"></i>\\n    </span>\\n    <input\\n      id=\\"{id}-search\\"\\n      class=\\"op-userpicker__search-input\\"\\n      type=\\"text\\"\\n      bind:value={query}\\n      {disabled}\\n      placeholder=\\"Buscar usuario…\\"\\n      autocomplete=\\"off\\"\\n      aria-describedby={describedBy}\\n    />\\n  </div>\\n\\n  <div class=\\"op-userpicker__panels\\">\\n    <!-- Disponibles -->\\n    <div class=\\"op-userpicker__panel\\">\\n      <p class=\\"op-userpicker__panel-title\\">\\n        Disponibles{#if query.trim()}&nbsp;({available.length}){/if}\\n      </p>\\n      {#if available.length === 0}\\n        <p class=\\"op-userpicker__empty\\">\\n          {query.trim() ? \\"Sin coincidencias.\\" : \\"No hay más usuarios disponibles.\\"}\\n        </p>\\n      {:else}\\n        <ul class=\\"op-userpicker__list\\" aria-labelledby=\\"{id}-label\\">\\n          {#each available as u (u.id)}\\n            <li class=\\"op-userpicker__item\\">\\n              <span class=\\"op-userpicker__user\\">\\n                <span class=\\"op-userpicker__username\\">{u.username}</span>\\n                {#if u.email}\\n                  <span class=\\"op-userpicker__email\\">{u.email}</span>\\n                {/if}\\n              </span>\\n              <button\\n                type=\\"button\\"\\n                class=\\"op-userpicker__btn op-userpicker__btn--add\\"\\n                on:click={() => add(u.id)}\\n                disabled={disabled || atMax}\\n                aria-label={`Agregar a ${u.username} a la cadena`}\\n              >\\n                <i class=\\"fas fa-plus\\" aria-hidden=\\"true\\"></i>\\n              </button>\\n            </li>\\n          {/each}\\n        </ul>\\n      {/if}\\n    </div>\\n\\n    <!-- Cadena seleccionada (ordenada) -->\\n    <div class=\\"op-userpicker__panel\\">\\n      <p class=\\"op-userpicker__panel-title\\" id=\\"{id}-chain-title\\">\\n        Cadena de revisión ({chain.length})\\n      </p>\\n      {#if chain.length === 0}\\n        <p class=\\"op-userpicker__empty\\">Agregue revisores en el orden de revisión.</p>\\n      {:else}\\n        <ol class=\\"op-userpicker__list op-userpicker__list--ordered\\" aria-labelledby=\\"{id}-chain-title\\">\\n          {#each chain as u, i (u.id)}\\n            <li class=\\"op-userpicker__item\\">\\n              <span class=\\"op-userpicker__order\\" aria-hidden=\\"true\\">{i + 1}</span>\\n              <span class=\\"op-userpicker__user\\">\\n                <span class=\\"op-userpicker__username\\">\\n                  <span class=\\"sr-only\\">Posición {i + 1} de {chain.length}: </span>\\n                  {u.username}\\n                </span>\\n                {#if u.email}\\n                  <span class=\\"op-userpicker__email\\">{u.email}</span>\\n                {/if}\\n              </span>\\n              <span class=\\"op-userpicker__actions\\">\\n                <button\\n                  type=\\"button\\"\\n                  class=\\"op-userpicker__btn\\"\\n                  on:click={() => move(i, -1)}\\n                  disabled={disabled || i === 0}\\n                  aria-label={`Subir a ${u.username} a la posición ${i}`}\\n                >\\n                  <i class=\\"fas fa-arrow-up\\" aria-hidden=\\"true\\"></i>\\n                </button>\\n                <button\\n                  type=\\"button\\"\\n                  class=\\"op-userpicker__btn\\"\\n                  on:click={() => move(i, 1)}\\n                  disabled={disabled || i === chain.length - 1}\\n                  aria-label={`Bajar a ${u.username} a la posición ${i + 2}`}\\n                >\\n                  <i class=\\"fas fa-arrow-down\\" aria-hidden=\\"true\\"></i>\\n                </button>\\n                <button\\n                  type=\\"button\\"\\n                  class=\\"op-userpicker__btn op-userpicker__btn--remove\\"\\n                  on:click={() => remove(u.id)}\\n                  {disabled}\\n                  aria-label={`Quitar a ${u.username} de la cadena`}\\n                >\\n                  <i class=\\"fas fa-times\\" aria-hidden=\\"true\\"></i>\\n                </button>\\n              </span>\\n            </li>\\n          {/each}\\n        </ol>\\n      {/if}\\n    </div>\\n  </div>\\n\\n  {#if atMax}\\n    <p id={hintId} class=\\"op-userpicker__hint\\">Máximo {max} revisores.</p>\\n  {:else if hint && !error}\\n    <p id={hintId} class=\\"op-userpicker__hint\\">{hint}</p>\\n  {/if}\\n\\n  <p id={errorId} class=\\"op-userpicker__error\\" aria-live=\\"polite\\" aria-atomic=\\"true\\">\\n    {error || \\"\\"}\\n  </p>\\n\\n  <!-- Live region: estado de la cadena para lectores de pantalla -->\\n  <span class=\\"sr-only\\" aria-live=\\"polite\\" aria-atomic=\\"true\\">{liveMessage}</span>\\n</div>\\n\\n<style lang=\\"scss\\">.op-userpicker {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-2);\\n}\\n.op-userpicker__label {\\n  font-size: var(--op-font-sm);\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-text);\\n}\\n.op-userpicker__search {\\n  position: relative;\\n}\\n.op-userpicker__search-icon {\\n  position: absolute;\\n  left: var(--op-space-3);\\n  top: 50%;\\n  transform: translateY(-50%);\\n  font-size: var(--op-font-xs);\\n  color: var(--op-text-secondary);\\n  pointer-events: none;\\n}\\n.op-userpicker__search-input {\\n  font-family: var(--op-font-family);\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text);\\n  background: var(--op-surface);\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius);\\n  height: 36px;\\n  width: 100%;\\n  padding: 0 var(--op-space-3) 0 var(--op-space-6);\\n  outline: none;\\n  transition: border-color var(--op-transition), box-shadow var(--op-transition);\\n}\\n.op-userpicker__search-input:focus {\\n  border-color: var(--op-primary);\\n  box-shadow: 0 0 0 3px var(--op-focus-ring);\\n}\\n.op-userpicker__search-input:disabled {\\n  background: var(--op-bg);\\n  color: var(--op-text-secondary);\\n  cursor: not-allowed;\\n}\\n.op-userpicker__panels {\\n  display: grid;\\n  grid-template-columns: 1fr 1fr;\\n  gap: var(--op-space-3);\\n}\\n@media (max-width: 640px) {\\n  .op-userpicker__panels {\\n    grid-template-columns: 1fr;\\n  }\\n}\\n.op-userpicker__panel {\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius);\\n  background: var(--op-surface);\\n  padding: var(--op-space-2);\\n  min-height: 120px;\\n}\\n.op-userpicker__panel-title {\\n  font-size: var(--op-font-xs);\\n  font-weight: var(--op-weight-bold);\\n  text-transform: uppercase;\\n  letter-spacing: 0.04em;\\n  color: var(--op-text-secondary);\\n  margin: 0 0 var(--op-space-2);\\n}\\n.op-userpicker__empty {\\n  font-size: var(--op-font-xs);\\n  color: var(--op-text-secondary);\\n  margin: var(--op-space-2) 0;\\n}\\n.op-userpicker__list {\\n  list-style: none;\\n  margin: 0;\\n  padding: 0;\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-1);\\n  max-height: 220px;\\n  overflow-y: auto;\\n}\\n.op-userpicker__item {\\n  display: flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n  padding: var(--op-space-1) var(--op-space-2);\\n  border-radius: var(--op-radius);\\n  background: var(--op-bg);\\n}\\n.op-userpicker__order {\\n  flex: 0 0 auto;\\n  width: 22px;\\n  height: 22px;\\n  display: inline-flex;\\n  align-items: center;\\n  justify-content: center;\\n  font-size: var(--op-font-xs);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-on-primary);\\n  background: var(--op-primary-dark);\\n  border-radius: 50%;\\n}\\n.op-userpicker__user {\\n  display: flex;\\n  flex-direction: column;\\n  min-width: 0;\\n  flex: 1 1 auto;\\n}\\n.op-userpicker__username {\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text);\\n  white-space: nowrap;\\n  overflow: hidden;\\n  text-overflow: ellipsis;\\n}\\n.op-userpicker__email {\\n  font-size: var(--op-font-xs);\\n  color: var(--op-text-secondary);\\n  white-space: nowrap;\\n  overflow: hidden;\\n  text-overflow: ellipsis;\\n}\\n.op-userpicker__actions {\\n  display: flex;\\n  gap: var(--op-space-1);\\n  flex: 0 0 auto;\\n}\\n.op-userpicker__btn {\\n  position: relative;\\n  width: 28px;\\n  height: 28px;\\n  display: inline-flex;\\n  align-items: center;\\n  justify-content: center;\\n  font-size: var(--op-font-xs);\\n  color: var(--op-text-secondary);\\n  background: var(--op-surface);\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius);\\n  cursor: pointer;\\n  transition: background var(--op-transition), color var(--op-transition), border-color var(--op-transition);\\n}\\n.op-userpicker__btn::after {\\n  content: \\"\\";\\n  position: absolute;\\n  inset: -8px;\\n}\\n.op-userpicker__btn:hover:not(:disabled) {\\n  border-color: var(--op-primary);\\n  color: var(--op-primary);\\n}\\n.op-userpicker__btn:focus-visible {\\n  outline: 2px solid var(--op-primary);\\n  outline-offset: 2px;\\n  box-shadow: 0 0 0 3px var(--op-focus-ring);\\n}\\n.op-userpicker__btn:disabled {\\n  opacity: 0.5;\\n  cursor: not-allowed;\\n}\\n.op-userpicker__btn--add:hover:not(:disabled) {\\n  border-color: var(--op-success, var(--op-primary));\\n  color: var(--op-success, var(--op-primary));\\n}\\n.op-userpicker__btn--remove:hover:not(:disabled) {\\n  border-color: var(--op-error);\\n  color: var(--op-error);\\n}\\n.op-userpicker__hint {\\n  font-size: var(--op-font-xs);\\n  color: var(--op-text-secondary);\\n  margin: 0;\\n}\\n.op-userpicker__error {\\n  font-size: var(--op-font-xs);\\n  color: var(--op-error);\\n  margin: 0;\\n}\\n.op-userpicker--error .op-userpicker__panel {\\n  border-color: var(--op-error);\\n}</style>\\n"],"names":[],"mappings":"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"}'
};
const UserPicker = create_ssr_component(($$result, $$props, $$bindings, slots) => {
  let usersById;
  let available;
  let chain;
  let atMax;
  let liveMessage;
  let describedBy;
  let { id = "userpicker" } = $$props;
  let { label = "" } = $$props;
  let { users = [] } = $$props;
  let { value = [] } = $$props;
  let { disabled = false } = $$props;
  let { error = "" } = $$props;
  let { hint = "" } = $$props;
  let { max = 20 } = $$props;
  let query = "";
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  if ($$props.id === void 0 && $$bindings.id && id !== void 0) $$bindings.id(id);
  if ($$props.label === void 0 && $$bindings.label && label !== void 0) $$bindings.label(label);
  if ($$props.users === void 0 && $$bindings.users && users !== void 0) $$bindings.users(users);
  if ($$props.value === void 0 && $$bindings.value && value !== void 0) $$bindings.value(value);
  if ($$props.disabled === void 0 && $$bindings.disabled && disabled !== void 0) $$bindings.disabled(disabled);
  if ($$props.error === void 0 && $$bindings.error && error !== void 0) $$bindings.error(error);
  if ($$props.hint === void 0 && $$bindings.hint && hint !== void 0) $$bindings.hint(hint);
  if ($$props.max === void 0 && $$bindings.max && max !== void 0) $$bindings.max(max);
  $$result.css.add(css$1);
  usersById = new Map(users.map((u) => [u.id, u]));
  available = users.filter((u) => u.is_active !== false && !value.includes(u.id)).filter((u) => {
    if (!query.trim()) return true;
    const q = query.trim().toLowerCase();
    return u.username.toLowerCase().includes(q) || (u.email ?? "").toLowerCase().includes(q);
  });
  chain = value.map((uid) => usersById.get(uid)).filter(Boolean);
  atMax = value.length >= max;
  liveMessage = chain.length === 0 ? "Sin revisores seleccionados." : `Cadena de ${chain.length} revisor(es): ` + chain.map((u, i) => `${i + 1}. ${u.username}`).join(", ") + ".";
  describedBy = [hint ? hintId : null, errorId].filter(Boolean).join(" ") || void 0;
  return `   <div class="${["op-userpicker svelte-1bnmx7j", !!error ? "op-userpicker--error" : ""].join(" ").trim()}">${label ? `<span class="op-userpicker__label svelte-1bnmx7j" id="${escape(id, true) + "-label"}">${escape(label)}</span>` : ``}  <div class="op-userpicker__search svelte-1bnmx7j"><label class="sr-only" for="${escape(id, true) + "-search"}">Buscar usuario por nombre o correo</label> <span class="op-userpicker__search-icon svelte-1bnmx7j" aria-hidden="true" data-svelte-h="svelte-nz0wf"><i class="fas fa-search"></i></span> <input id="${escape(id, true) + "-search"}" class="op-userpicker__search-input svelte-1bnmx7j" type="text" ${disabled ? "disabled" : ""} placeholder="Buscar usuario…" autocomplete="off"${add_attribute("aria-describedby", describedBy, 0)}${add_attribute("value", query, 0)}></div> <div class="op-userpicker__panels svelte-1bnmx7j"> <div class="op-userpicker__panel svelte-1bnmx7j"><p class="op-userpicker__panel-title svelte-1bnmx7j">Disponibles${query.trim() ? ` (${escape(available.length)})` : ``}</p> ${available.length === 0 ? `<p class="op-userpicker__empty svelte-1bnmx7j">${escape(query.trim() ? "Sin coincidencias." : "No hay más usuarios disponibles.")}</p>` : `<ul class="op-userpicker__list svelte-1bnmx7j" aria-labelledby="${escape(id, true) + "-label"}">${each(available, (u) => {
    return `<li class="op-userpicker__item svelte-1bnmx7j"><span class="op-userpicker__user svelte-1bnmx7j"><span class="op-userpicker__username svelte-1bnmx7j">${escape(u.username)}</span> ${u.email ? `<span class="op-userpicker__email svelte-1bnmx7j">${escape(u.email)}</span>` : ``}</span> <button type="button" class="op-userpicker__btn op-userpicker__btn--add svelte-1bnmx7j" ${disabled || atMax ? "disabled" : ""}${add_attribute("aria-label", `Agregar a ${u.username} a la cadena`, 0)}><i class="fas fa-plus" aria-hidden="true"></i></button> </li>`;
  })}</ul>`}</div>  <div class="op-userpicker__panel svelte-1bnmx7j"><p class="op-userpicker__panel-title svelte-1bnmx7j" id="${escape(id, true) + "-chain-title"}">Cadena de revisión (${escape(chain.length)})</p> ${chain.length === 0 ? `<p class="op-userpicker__empty svelte-1bnmx7j" data-svelte-h="svelte-1duvdg5">Agregue revisores en el orden de revisión.</p>` : `<ol class="op-userpicker__list op-userpicker__list--ordered svelte-1bnmx7j" aria-labelledby="${escape(id, true) + "-chain-title"}">${each(chain, (u, i) => {
    return `<li class="op-userpicker__item svelte-1bnmx7j"><span class="op-userpicker__order svelte-1bnmx7j" aria-hidden="true">${escape(i + 1)}</span> <span class="op-userpicker__user svelte-1bnmx7j"><span class="op-userpicker__username svelte-1bnmx7j"><span class="sr-only">Posición ${escape(i + 1)} de ${escape(chain.length)}:</span> ${escape(u.username)}</span> ${u.email ? `<span class="op-userpicker__email svelte-1bnmx7j">${escape(u.email)}</span>` : ``}</span> <span class="op-userpicker__actions svelte-1bnmx7j"><button type="button" class="op-userpicker__btn svelte-1bnmx7j" ${disabled || i === 0 ? "disabled" : ""}${add_attribute("aria-label", `Subir a ${u.username} a la posición ${i}`, 0)}><i class="fas fa-arrow-up" aria-hidden="true"></i></button> <button type="button" class="op-userpicker__btn svelte-1bnmx7j" ${disabled || i === chain.length - 1 ? "disabled" : ""}${add_attribute("aria-label", `Bajar a ${u.username} a la posición ${i + 2}`, 0)}><i class="fas fa-arrow-down" aria-hidden="true"></i></button> <button type="button" class="op-userpicker__btn op-userpicker__btn--remove svelte-1bnmx7j" ${disabled ? "disabled" : ""}${add_attribute("aria-label", `Quitar a ${u.username} de la cadena`, 0)}><i class="fas fa-times" aria-hidden="true"></i> </button></span> </li>`;
  })}</ol>`}</div></div> ${atMax ? `<p${add_attribute("id", hintId, 0)} class="op-userpicker__hint svelte-1bnmx7j">Máximo ${escape(max)} revisores.</p>` : `${hint && !error ? `<p${add_attribute("id", hintId, 0)} class="op-userpicker__hint svelte-1bnmx7j">${escape(hint)}</p>` : ``}`} <p${add_attribute("id", errorId, 0)} class="op-userpicker__error svelte-1bnmx7j" aria-live="polite" aria-atomic="true">${escape(error || "")}</p>  <span class="sr-only" aria-live="polite" aria-atomic="true">${escape(liveMessage)}</span> </div>`;
});
const NIVEL_OPTIONS = [
  { value: "1", label: "1 — Pública" },
  { value: "2", label: "2 — Reservada" },
  { value: "3", label: "3 — Clasificada" }
];
const css = {
  code: ".bandeja.svelte-1v7xjen.svelte-1v7xjen{padding:var(--op-space-6);max-width:1200px;margin:0 auto;display:flex;flex-direction:column;gap:var(--op-space-4)}.bandeja__header.svelte-1v7xjen.svelte-1v7xjen{display:flex;align-items:baseline;gap:var(--op-space-4);flex-wrap:wrap;padding-bottom:var(--op-space-4);border-bottom:1px solid var(--op-border)}.bandeja__title.svelte-1v7xjen.svelte-1v7xjen{font-size:var(--op-font-xl);font-weight:var(--op-weight-bold);color:var(--op-text);margin:0}.bandeja__total.svelte-1v7xjen.svelte-1v7xjen{font-size:var(--op-font-sm);color:var(--op-text-secondary);flex-shrink:0}.bandeja__filters.svelte-1v7xjen.svelte-1v7xjen{background:var(--op-surface);border:1px solid var(--op-border);border-radius:var(--op-radius-lg);padding:var(--op-space-4) var(--op-space-5)}.filters-form.svelte-1v7xjen.svelte-1v7xjen{display:flex;align-items:flex-end;gap:var(--op-space-4);flex-wrap:wrap}.filters-form__fields.svelte-1v7xjen.svelte-1v7xjen{display:flex;gap:var(--op-space-3);flex-wrap:wrap;flex:1}.filters-form__actions.svelte-1v7xjen.svelte-1v7xjen{display:flex;gap:var(--op-space-2);align-items:flex-end;padding-bottom:0;margin-bottom:2px}.filters-note.svelte-1v7xjen.svelte-1v7xjen{font-size:var(--op-font-xs);color:var(--op-text-secondary);margin:var(--op-space-2) 0 0}.filters-note__link.svelte-1v7xjen.svelte-1v7xjen{color:var(--op-primary);text-decoration:underline}.filters-note__link.svelte-1v7xjen.svelte-1v7xjen:hover{color:var(--op-primary-dark)}.filters-note__link.svelte-1v7xjen.svelte-1v7xjen:focus-visible{outline:2px solid var(--op-primary);outline-offset:2px;border-radius:var(--op-radius-sm)}.bandeja__loading.svelte-1v7xjen.svelte-1v7xjen{padding:var(--op-space-6);background:var(--op-surface);border:1px solid var(--op-border);border-radius:var(--op-radius-lg)}.bandeja__table-wrapper.svelte-1v7xjen.svelte-1v7xjen{background:var(--op-surface);border:1px solid var(--op-border);border-radius:var(--op-radius-lg);overflow:hidden}.bandeja-table.svelte-1v7xjen.svelte-1v7xjen{width:100%;border-collapse:collapse;font-size:var(--op-font-sm)}.bandeja-table__th.svelte-1v7xjen.svelte-1v7xjen{padding:var(--op-space-3) var(--op-space-4);text-align:left;font-size:var(--op-font-xs);font-weight:var(--op-weight-bold);color:var(--op-text-secondary);background:var(--op-bg);border-bottom:1px solid var(--op-border);white-space:nowrap}.bandeja-table__th--tracking.svelte-1v7xjen.svelte-1v7xjen{width:160px}.bandeja-table__th--dep.svelte-1v7xjen.svelte-1v7xjen{width:160px}.bandeja-table__th--actions.svelte-1v7xjen.svelte-1v7xjen{width:180px;text-align:right}.bandeja-table__row.svelte-1v7xjen.svelte-1v7xjen{transition:background var(--op-transition)}.bandeja-table__row.svelte-1v7xjen.svelte-1v7xjen:hover{background:var(--op-bg)}.bandeja-table__row--selected.svelte-1v7xjen.svelte-1v7xjen{background:var(--op-primary-light)}.bandeja-table__row.svelte-1v7xjen:not(:last-child) .bandeja-table__td.svelte-1v7xjen{border-bottom:1px solid var(--op-border)}.bandeja-table__td.svelte-1v7xjen.svelte-1v7xjen{padding:var(--op-space-3) var(--op-space-4);vertical-align:middle;color:var(--op-text)}.bandeja-table__td--subject.svelte-1v7xjen.svelte-1v7xjen{max-width:280px}.bandeja-table__td--dep.svelte-1v7xjen.svelte-1v7xjen{max-width:160px}.bandeja-table__td--date.svelte-1v7xjen.svelte-1v7xjen{white-space:nowrap;color:var(--op-text-secondary)}.bandeja-table__td--actions.svelte-1v7xjen.svelte-1v7xjen{text-align:right;white-space:nowrap}.tracking-number.svelte-1v7xjen.svelte-1v7xjen{font-family:monospace;font-size:var(--op-font-xs);font-weight:var(--op-weight-medium);color:var(--op-primary-dark);background:var(--op-primary-light);padding:2px var(--op-space-2);border-radius:var(--op-radius-sm);white-space:nowrap}.doc-type-badge.svelte-1v7xjen.svelte-1v7xjen{display:inline-block;font-size:var(--op-font-xs);font-weight:var(--op-weight-bold);padding:2px var(--op-space-2);border-radius:var(--op-radius-sm);white-space:nowrap}.doc-type-badge--e.svelte-1v7xjen.svelte-1v7xjen{background:var(--op-info-bg);color:var(--op-info-fg)}.doc-type-badge--s.svelte-1v7xjen.svelte-1v7xjen{background:var(--op-warning-bg);color:var(--op-warning-fg)}.doc-type-badge--i.svelte-1v7xjen.svelte-1v7xjen{background:var(--op-bg);color:var(--op-text-secondary);border:1px solid var(--op-border)}.subject-text.svelte-1v7xjen.svelte-1v7xjen{display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.action-btn.svelte-1v7xjen.svelte-1v7xjen{display:inline-flex;align-items:center;gap:var(--op-space-1);font-size:var(--op-font-xs);font-weight:var(--op-weight-medium);padding:3px var(--op-space-2);border-radius:var(--op-radius-sm);border:1px solid transparent;cursor:pointer;transition:background var(--op-transition), color var(--op-transition)}.action-btn.svelte-1v7xjen.svelte-1v7xjen:focus-visible{outline:2px solid var(--op-primary);outline-offset:2px}.action-btn.svelte-1v7xjen.svelte-1v7xjen:disabled{opacity:0.5;cursor:not-allowed}.action-btn--detail.svelte-1v7xjen.svelte-1v7xjen{background:var(--op-bg);color:var(--op-text-secondary);border-color:var(--op-border);margin-right:var(--op-space-1)}.action-btn--detail.svelte-1v7xjen.svelte-1v7xjen:hover:not(:disabled){background:var(--op-primary-light);color:var(--op-primary-dark);border-color:var(--op-primary-light)}.action-btn--advance.svelte-1v7xjen.svelte-1v7xjen{background:var(--op-primary-light);color:var(--op-primary-dark);border-color:var(--op-primary-light)}.action-btn--advance.svelte-1v7xjen.svelte-1v7xjen:hover:not(:disabled){background:var(--op-primary);color:var(--op-surface)}.action-btn--danger.svelte-1v7xjen.svelte-1v7xjen{background:transparent;color:var(--op-error);border-color:var(--op-error)}.action-btn--danger.svelte-1v7xjen.svelte-1v7xjen:hover:not(:disabled){background:var(--op-error);color:var(--op-surface)}.modal-desc.svelte-1v7xjen.svelte-1v7xjen{font-size:var(--op-font-sm);color:var(--op-text);margin:0 0 var(--op-space-4);line-height:var(--op-line-height)}.modal-desc.svelte-1v7xjen strong.svelte-1v7xjen{font-family:monospace;font-weight:var(--op-weight-bold);color:var(--op-primary-dark)}.modal-form.svelte-1v7xjen.svelte-1v7xjen{display:flex;flex-direction:column;gap:var(--op-space-4)}.modal-loader.svelte-1v7xjen.svelte-1v7xjen{display:flex;align-items:center;justify-content:center;gap:var(--op-space-3);padding:var(--op-space-6) 0;font-size:var(--op-font-sm);color:var(--op-text-secondary)}.bandeja-pagination.svelte-1v7xjen.svelte-1v7xjen{display:flex;align-items:center;justify-content:center;gap:var(--op-space-4);padding:var(--op-space-3) var(--op-space-5);border-top:1px solid var(--op-border)}.pagination-btn.svelte-1v7xjen.svelte-1v7xjen{display:flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:var(--op-radius-sm);border:1px solid var(--op-border);background:var(--op-surface);color:var(--op-text-secondary);cursor:pointer;transition:background var(--op-transition)}.pagination-btn.svelte-1v7xjen.svelte-1v7xjen:hover:not(:disabled){background:var(--op-bg);color:var(--op-text)}.pagination-btn.svelte-1v7xjen.svelte-1v7xjen:focus-visible{outline:2px solid var(--op-primary);outline-offset:2px}.pagination-btn.svelte-1v7xjen.svelte-1v7xjen:disabled{opacity:0.4;cursor:not-allowed}.pagination-info.svelte-1v7xjen.svelte-1v7xjen{font-size:var(--op-font-sm);color:var(--op-text-secondary)}.doc-detail.svelte-1v7xjen.svelte-1v7xjen{display:flex;flex-direction:column;gap:var(--op-space-4)}.doc-detail__header.svelte-1v7xjen.svelte-1v7xjen{display:flex;align-items:center;justify-content:space-between;gap:var(--op-space-3);flex-wrap:wrap}.doc-detail__tracking.svelte-1v7xjen.svelte-1v7xjen{font-family:monospace;font-size:var(--op-font-sm);font-weight:var(--op-weight-bold);color:var(--op-primary-dark);background:var(--op-primary-light);padding:var(--op-space-1) var(--op-space-3);border-radius:var(--op-radius-sm);margin:0}.doc-detail__section.svelte-1v7xjen.svelte-1v7xjen{border-top:1px solid var(--op-border);padding-top:var(--op-space-3)}.doc-detail__section-title.svelte-1v7xjen.svelte-1v7xjen{font-size:var(--op-font-xs);font-weight:var(--op-weight-bold);color:var(--op-text-secondary);text-transform:uppercase;letter-spacing:0.5px;margin:0 0 var(--op-space-3)}.doc-detail__subject.svelte-1v7xjen.svelte-1v7xjen{font-size:var(--op-font-sm);color:var(--op-text);margin:0;line-height:var(--op-line-height)}.doc-detail__dl.svelte-1v7xjen.svelte-1v7xjen{display:grid;grid-template-columns:140px 1fr;gap:var(--op-space-2) var(--op-space-3);font-size:var(--op-font-sm);margin:0}.doc-detail__dl.svelte-1v7xjen dt.svelte-1v7xjen{font-weight:var(--op-weight-medium);color:var(--op-text-secondary)}.doc-detail__dl.svelte-1v7xjen dd.svelte-1v7xjen{color:var(--op-text);margin:0;overflow-wrap:break-word}.due-date.svelte-1v7xjen.svelte-1v7xjen{color:var(--op-warning-fg);font-weight:var(--op-weight-medium)}.doc-detail__obs.svelte-1v7xjen.svelte-1v7xjen{font-size:var(--op-font-sm);color:var(--op-text-secondary);margin:0;font-style:italic;line-height:var(--op-line-height);white-space:pre-wrap}.doc-detail__obs--rich.svelte-1v7xjen.svelte-1v7xjen{font-style:normal;color:var(--op-text)}.doc-detail__obs--rich.svelte-1v7xjen p{margin:0 0 var(--op-space-2)}.doc-detail__obs--rich.svelte-1v7xjen h2,.doc-detail__obs--rich.svelte-1v7xjen h3{margin:var(--op-space-2) 0 var(--op-space-1);color:var(--op-text)}.doc-detail__obs--rich.svelte-1v7xjen ul,.doc-detail__obs--rich.svelte-1v7xjen ol{margin:0 0 var(--op-space-2) var(--op-space-5)}.doc-detail__obs--rich.svelte-1v7xjen blockquote{margin:0 0 var(--op-space-2);padding-left:var(--op-space-3);border-left:3px solid var(--op-primary-light)}.doc-detail__obs--rich.svelte-1v7xjen a{color:var(--op-primary);text-decoration:underline}.anexos-list.svelte-1v7xjen.svelte-1v7xjen{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:var(--op-space-2)}.anexos-list__item.svelte-1v7xjen.svelte-1v7xjen{display:flex;align-items:center;gap:var(--op-space-2);font-size:var(--op-font-sm);padding:var(--op-space-2) var(--op-space-3);background:var(--op-bg);border-radius:var(--op-radius-sm);border:1px solid var(--op-border)}.anexos-list__icon.svelte-1v7xjen.svelte-1v7xjen{color:var(--op-text-secondary);flex-shrink:0;font-size:var(--op-font-xs)}.anexos-list__name.svelte-1v7xjen.svelte-1v7xjen{flex:1;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;color:var(--op-text)}.anexos-list__size.svelte-1v7xjen.svelte-1v7xjen{font-size:var(--op-font-xs);color:var(--op-text-secondary);flex-shrink:0}@media(max-width: 768px){.bandeja.svelte-1v7xjen.svelte-1v7xjen{padding:var(--op-space-4)}.bandeja-table__th--dep.svelte-1v7xjen.svelte-1v7xjen,.bandeja-table__td--dep.svelte-1v7xjen.svelte-1v7xjen{display:none}.filters-form.svelte-1v7xjen.svelte-1v7xjen{flex-direction:column;align-items:stretch}.filters-form__actions.svelte-1v7xjen.svelte-1v7xjen{justify-content:flex-start}}",
  map: '{"version":3,"file":"+page.svelte","sources":["+page.svelte"],"sourcesContent":["<!--\\n  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).\\n  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.\\n  Contacto: aurigadl@gmail.com\\n\\n  Software libre: este archivo se distribuye bajo la Licencia Pública General\\n  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,\\n  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega\\n  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.\\n-->\\n\\n<!--\\n  /bandeja — Bandeja unificada de radicados (T-08, RF-UI-03).\\n\\n  Criterio de hecho: filtrar y abrir el detalle (drawer) NO provoca\\n  navegación a otra pantalla. Todo ocurre en esta vista.\\n\\n  Filtros soportados por el backend (/api/v1/documents):\\n    - doc_type: E (Entrada) | S (Salida) | I (Interno)\\n    - status: registered | distributed | in_progress | closed\\n\\n  Filtros NO disponibles en /documents pero sí en /search (T-11):\\n    - date_from, date_to, dest_dept_code, q (texto libre)\\n    → Implementar en T-11; aquí solo los que el backend soporta directamente.\\n\\n  Acciones inline implementadas (T-08, sin T-08b):\\n    - Ver detalle en drawer (sin navegar)\\n    - PATCH /documents/{id} para actualizar estado (distributed / in_progress)\\n      → Solo si el usuario tiene permiso PERM_RADI o PERM_RADI_ENTRADA.\\n    - TODO T-08b: anular, devolver, responder, VoBo.\\n-->\\n<script>\\n  import { onMount, tick } from \\"svelte\\";\\n  import { setBreadcrumbs } from \\"$lib/stores/ui.js\\";\\n  import { permissions } from \\"$lib/stores/auth.js\\";\\n  import { notifyError, notifySuccess } from \\"$lib/stores/notifications.js\\";\\n  // Las llamadas al gateway se realizan a través de los endpoints locales /bandeja/api[/id]\\n  // que leen la cookie httpOnly en el servidor — el token nunca llega al cliente (D-05).\\n  import Drawer from \\"$lib/components/ui/Drawer.svelte\\";\\n  import Modal from \\"$lib/components/ui/Modal.svelte\\";\\n  import StatusChip from \\"$lib/components/ui/StatusChip.svelte\\";\\n  import Loader from \\"$lib/components/ui/Loader.svelte\\";\\n  import EmptyState from \\"$lib/components/ui/EmptyState.svelte\\";\\n  import Button from \\"$lib/components/ui/Button.svelte\\";\\n  import FormField from \\"$lib/components/ui/FormField.svelte\\";\\n  import UserPicker from \\"$lib/components/ui/UserPicker.svelte\\";\\n  import { NIVEL_OPTIONS, getNivelChip } from \\"$lib/utils/nivelSeguridad.js\\";\\n\\n  /** @type {import(\'./$types\').PageData} */\\n  export let data;\\n\\n  // ---------------------------------------------------------------------------\\n  // Estado local de la bandeja\\n  // ---------------------------------------------------------------------------\\n\\n  /** @type {import(\'$lib/types/api.js\').DocumentResponse[]} */\\n  let documents = data.documents ?? [];\\n  let total = data.total ?? 0;\\n  let loading = false;\\n\\n  // Filtros activos (inicializados desde los query params del server load)\\n  let filterDocType = data.initialFilters?.docType ?? \\"\\";\\n  let filterStatus = data.initialFilters?.status ?? \\"\\";\\n  let currentPage = data.initialFilters?.page ?? 1;\\n  const pageSize = 20;\\n\\n  // ---------------------------------------------------------------------------\\n  // Drawer de detalle\\n  // ---------------------------------------------------------------------------\\n  let drawerOpen = false;\\n  /** @type {import(\'$lib/types/api.js\').DocumentResponse|null} */\\n  let selectedDoc = null;\\n  let detailLoading = false;\\n\\n  // ---------------------------------------------------------------------------\\n  // Acciones inline\\n  // ---------------------------------------------------------------------------\\n  let actionLoading = false;\\n\\n  // ---------------------------------------------------------------------------\\n  // T-08b: estado de modales de operador\\n  // ---------------------------------------------------------------------------\\n  let modalAnulacion = false;\\n  let modalDevolver = false;\\n  let modalResponder = false;\\n  let modalVoBo = false;\\n  let modalCrearCadena = false;\\n\\n  // ---------------------------------------------------------------------------\\n  // T-RECLASIFICAR: modal de reclasificación de nivel de seguridad (RF-SEG-08)\\n  // ---------------------------------------------------------------------------\\n  let modalReclasificar = false;\\n  let reclasificarNivel = \\"\\";\\n  let reclasificarMotivo = \\"\\";\\n  let reclasificarMotivoError = \\"\\";\\n  let reclasificarCausal = \\"\\";\\n  let reclasificarFundamento = \\"\\";\\n  let reclasificarPlazo = \\"\\";\\n  let reclasificarLoading = false;\\n  /** @type {HTMLElement | undefined} Título de la página: destino de foco estable. */\\n  let tituloEl;\\n\\n  // Anulacion — causal (req, ≤15 chars), motivo (opt, ≤2000)\\n  let anulCausal = \\"\\";\\n  let anulMotivo = \\"\\";\\n  let anulCausalError = \\"\\";\\n\\n  // Devolver — to_dept (req, ≤100), causal (req, ≤15), comentario (opt)\\n  let devToDept = \\"\\";\\n  let devCausal = \\"\\";\\n  let devComentario = \\"\\";\\n  let devToDeptError = \\"\\";\\n  let devCausalError = \\"\\";\\n\\n  // Responder — todos opcionales en RespuestaRapidaCreate\\n  let respSubject = \\"\\";\\n  let respDestDept = \\"\\";\\n  let respObservations = \\"\\";\\n  let respResponseDays = \\"\\";\\n\\n  // VoBo — aprobar (req), comentario (opt)\\n  let voboAprobarStr = \\"\\"; // \\"true\\" | \\"false\\" | \\"\\" (vacío = sin seleccionar)\\n  let voboComentario = \\"\\";\\n\\n  // T-08c: crear cadena de visto bueno (revisores ordenados)\\n  /** @type {Array<{id:string; username:string; email?:string; is_active?:boolean}>} */\\n  let cadenaUsers = [];\\n  /** @type {string[]} — ids de revisores en orden */\\n  let cadenaRevisores = [];\\n  let cadenaUsersLoading = false;\\n  let cadenaUsersError = \\"\\";\\n\\n  // ---------------------------------------------------------------------------\\n  // Breadcrumbs\\n  // ---------------------------------------------------------------------------\\n  onMount(() => {\\n    setBreadcrumbs([{ label: \\"Bandeja de radicados\\" }]);\\n  });\\n\\n  // ---------------------------------------------------------------------------\\n  // Helpers de presentación\\n  // ---------------------------------------------------------------------------\\n  const DOC_TYPE_LABELS = { E: \\"Entrada\\", S: \\"Salida\\", I: \\"Interno\\" };\\n  const STATUS_LABELS = {\\n    registered: \\"Radicado\\",\\n    distributed: \\"Distribuido\\",\\n    in_progress: \\"En trámite\\",\\n    closed: \\"Cerrado\\",\\n  };\\n  const STATUS_CHIP_MAP = {\\n    registered: \\"info\\",\\n    distributed: \\"pending\\",\\n    in_progress: \\"active\\",\\n    closed: \\"closed\\",\\n  };\\n\\n  /**\\n   * Formatea una fecha ISO 8601 como fecha legible en español.\\n   * @param {string} iso\\n   */\\n  function formatDate(iso) {\\n    if (!iso) return \\"—\\";\\n    return new Date(iso).toLocaleDateString(\\"es-CO\\", {\\n      year: \\"numeric\\",\\n      month: \\"2-digit\\",\\n      day: \\"2-digit\\",\\n    });\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Carga de datos\\n  // ---------------------------------------------------------------------------\\n\\n  /**\\n   * Carga la página actual de radicados con los filtros activos.\\n   * Llama al endpoint local /bandeja/api que lee la cookie httpOnly en el servidor.\\n   * El token nunca alcanza el JS del cliente (D-05).\\n   */\\n  async function loadDocuments() {\\n    loading = true;\\n    try {\\n      const params = new URLSearchParams({\\n        page: String(currentPage),\\n        size: String(pageSize),\\n      });\\n      if (filterDocType) params.set(\\"doc_type\\", filterDocType);\\n      if (filterStatus) params.set(\\"status\\", filterStatus);\\n\\n      const res = await fetch(`/bandeja/api?${params}`);\\n      const result = await res.json();\\n      if (!res.ok) {\\n        throw result;\\n      }\\n      documents = result.documents;\\n      total = result.total;\\n    } catch (err) {\\n      notifyError(err.detail ?? \\"Error al cargar los radicados.\\");\\n    } finally {\\n      loading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Filtros\\n  // ---------------------------------------------------------------------------\\n\\n  function applyFilters() {\\n    currentPage = 1;\\n    loadDocuments();\\n  }\\n\\n  function clearFilters() {\\n    filterDocType = \\"\\";\\n    filterStatus = \\"\\";\\n    currentPage = 1;\\n    loadDocuments();\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Paginación\\n  // ---------------------------------------------------------------------------\\n\\n  $: totalPages = Math.ceil(total / pageSize);\\n\\n  function goToPage(page) {\\n    if (page < 1 || page > totalPages) return;\\n    currentPage = page;\\n    loadDocuments();\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Drawer de detalle\\n  // ---------------------------------------------------------------------------\\n\\n  /**\\n   * Abre el drawer con el detalle completo del radicado.\\n   * Si el item ya está en el listado usamos esos datos; luego cargamos el\\n   * detalle completo (con anexos, metadata) en background.\\n   * Llama al endpoint local /bandeja/api/[id] — el token permanece en el servidor.\\n   * @param {import(\'$lib/types/api.js\').DocumentResponse} doc\\n   */\\n  async function openDrawer(doc) {\\n    selectedDoc = doc;\\n    drawerOpen = true;\\n\\n    // Cargar detalle completo (incluye anexos y metadata)\\n    detailLoading = true;\\n    try {\\n      const res = await fetch(`/bandeja/api/${doc.id}`);\\n      const result = await res.json();\\n      if (!res.ok) throw result;\\n      selectedDoc = result;\\n    } catch (err) {\\n      notifyError(err.detail ?? \\"No se pudo cargar el detalle del radicado.\\");\\n    } finally {\\n      detailLoading = false;\\n    }\\n  }\\n\\n  function closeDrawer() {\\n    drawerOpen = false;\\n    selectedDoc = null;\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // T-08b: helpers para abrir modales (resetean estado del formulario)\\n  // ---------------------------------------------------------------------------\\n\\n  /** Abre el modal de anulación para el radicado actualmente seleccionado. */\\n  function openModalAnulacion() {\\n    anulCausal = \\"\\";\\n    anulMotivo = \\"\\";\\n    anulCausalError = \\"\\";\\n    modalAnulacion = true;\\n  }\\n\\n  /** Abre el modal de devolución para el radicado actualmente seleccionado. */\\n  function openModalDevolver() {\\n    devToDept = \\"\\";\\n    devCausal = \\"\\";\\n    devComentario = \\"\\";\\n    devToDeptError = \\"\\";\\n    devCausalError = \\"\\";\\n    modalDevolver = true;\\n  }\\n\\n  /** Abre el modal de respuesta rápida (Salida vinculada). */\\n  function openModalResponder() {\\n    // Precargar asunto del Entrada como punto de partida\\n    respSubject = selectedDoc?.subject ? `RE: ${selectedDoc.subject}` : \\"\\";\\n    respDestDept = selectedDoc?.origin_dept ?? \\"\\";\\n    respObservations = \\"\\";\\n    respResponseDays = \\"\\";\\n    modalResponder = true;\\n  }\\n\\n  /** Abre el modal de decisión de Visto Bueno. */\\n  function openModalVoBo() {\\n    voboAprobarStr = \\"\\"; // sin preselección — el operador elige explícitamente\\n    voboComentario = \\"\\";\\n    modalVoBo = true;\\n  }\\n\\n  /**\\n   * Abre el modal de anulación desde la fila de la tabla sin abrir el drawer.\\n   * @param {import(\'$lib/types/api.js\').DocumentResponse} doc\\n   */\\n  function openAnulacionFromRow(doc) {\\n    selectedDoc = doc; // target del modal\\n    openModalAnulacion();\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // T-08b: funciones de acción (llaman a los endpoints proxy SSR)\\n  // ---------------------------------------------------------------------------\\n\\n  /**\\n   * Solicita la anulación del radicado seleccionado.\\n   * POST /bandeja/api/[id]/anulacion → /api/v1/documents/{id}/anulacion\\n   */\\n  async function requestAnulacion() {\\n    if (!selectedDoc || actionLoading) return;\\n\\n    // Validación cliente\\n    anulCausalError = \\"\\";\\n    if (!anulCausal.trim()) {\\n      anulCausalError = \\"La causal es obligatoria.\\";\\n      return;\\n    }\\n    if (anulCausal.trim().length > 15) {\\n      anulCausalError = \\"Máximo 15 caracteres.\\";\\n      return;\\n    }\\n\\n    actionLoading = true;\\n    try {\\n      const res = await fetch(`/bandeja/api/${selectedDoc.id}/anulacion`, {\\n        method: \\"POST\\",\\n        headers: { \\"Content-Type\\": \\"application/json\\" },\\n        body: JSON.stringify({\\n          causal: anulCausal.trim(),\\n          ...(anulMotivo.trim() ? { motivo: anulMotivo.trim() } : {}),\\n        }),\\n      });\\n      const result = await res.json();\\n      if (!res.ok) throw result;\\n\\n      modalAnulacion = false;\\n      notifySuccess(\\"Solicitud de anulación registrada correctamente.\\");\\n      // Recargar lista para reflejar el cambio de estado\\n      await loadDocuments();\\n      if (drawerOpen) closeDrawer();\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"No se pudo registrar la solicitud de anulación.\\");\\n    } finally {\\n      actionLoading = false;\\n    }\\n  }\\n\\n  /**\\n   * Devuelve el radicado seleccionado en flujo.\\n   * POST /bandeja/api/[id]/devolver → /api/v1/workflows/{id}/devolver\\n   */\\n  async function devolver() {\\n    if (!selectedDoc || actionLoading) return;\\n\\n    // Validación cliente\\n    devToDeptError = \\"\\";\\n    devCausalError = \\"\\";\\n    let valid = true;\\n    if (!devToDept.trim()) {\\n      devToDeptError = \\"La dependencia destino es obligatoria.\\";\\n      valid = false;\\n    } else if (devToDept.trim().length > 100) {\\n      devToDeptError = \\"Máximo 100 caracteres.\\";\\n      valid = false;\\n    }\\n    if (!devCausal.trim()) {\\n      devCausalError = \\"La causal es obligatoria.\\";\\n      valid = false;\\n    } else if (devCausal.trim().length > 15) {\\n      devCausalError = \\"Máximo 15 caracteres.\\";\\n      valid = false;\\n    }\\n    if (!valid) return;\\n\\n    actionLoading = true;\\n    try {\\n      const res = await fetch(`/bandeja/api/${selectedDoc.id}/devolver`, {\\n        method: \\"POST\\",\\n        headers: { \\"Content-Type\\": \\"application/json\\" },\\n        body: JSON.stringify({\\n          to_dept: devToDept.trim(),\\n          causal: devCausal.trim(),\\n          ...(devComentario.trim() ? { comentario: devComentario.trim() } : {}),\\n        }),\\n      });\\n      const result = await res.json();\\n      if (!res.ok) throw result;\\n\\n      modalDevolver = false;\\n      notifySuccess(\\"Radicado devuelto correctamente.\\");\\n      await loadDocuments();\\n      if (drawerOpen) closeDrawer();\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"No se pudo devolver el radicado.\\");\\n    } finally {\\n      actionLoading = false;\\n    }\\n  }\\n\\n  /**\\n   * Crea un radicado de Salida vinculado al Entrada seleccionado.\\n   * POST /bandeja/api/[id]/respuesta → /api/v1/documents/{id}/respuesta\\n   */\\n  async function responder() {\\n    if (!selectedDoc || actionLoading) return;\\n\\n    actionLoading = true;\\n    try {\\n      const days = parseInt(respResponseDays, 10);\\n      const res = await fetch(`/bandeja/api/${selectedDoc.id}/respuesta`, {\\n        method: \\"POST\\",\\n        headers: { \\"Content-Type\\": \\"application/json\\" },\\n        body: JSON.stringify({\\n          ...(respSubject.trim() ? { subject: respSubject.trim() } : {}),\\n          ...(respDestDept.trim() ? { dest_dept: respDestDept.trim() } : {}),\\n          ...(respObservations.trim() ? { observations: respObservations.trim() } : {}),\\n          ...(!isNaN(days) && days > 0 ? { response_days: days } : {}),\\n        }),\\n      });\\n      const result = await res.json();\\n      if (!res.ok) throw result;\\n\\n      modalResponder = false;\\n      const newTracking = result.tracking_number ?? \\"\\";\\n      notifySuccess(\\n        newTracking\\n          ? `Respuesta radicada: ${newTracking}`\\n          : \\"Radicado de respuesta creado correctamente.\\",\\n      );\\n      await loadDocuments();\\n      if (drawerOpen) closeDrawer();\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"No se pudo crear el radicado de respuesta.\\");\\n    } finally {\\n      actionLoading = false;\\n    }\\n  }\\n\\n  /**\\n   * Registra la decisión del usuario sobre un Visto Bueno pendiente.\\n   * POST /bandeja/api/[id]/vobo → /api/v1/workflows/{id}/vistos-buenos/decidir\\n   */\\n  async function voboDecidir() {\\n    if (!selectedDoc || actionLoading) return;\\n    if (!voboAprobarStr) {\\n      notifyError(\\"Seleccione una decisión antes de confirmar.\\");\\n      return;\\n    }\\n\\n    actionLoading = true;\\n    try {\\n      const aprobar = voboAprobarStr === \\"true\\";\\n      const res = await fetch(`/bandeja/api/${selectedDoc.id}/vobo`, {\\n        method: \\"POST\\",\\n        headers: { \\"Content-Type\\": \\"application/json\\" },\\n        body: JSON.stringify({\\n          aprobar,\\n          ...(voboComentario.trim() ? { comentario: voboComentario.trim() } : {}),\\n        }),\\n      });\\n      const result = await res.json();\\n      if (!res.ok) throw result;\\n\\n      modalVoBo = false;\\n      notifySuccess(aprobar ? \\"Visto bueno aprobado.\\" : \\"Visto bueno rechazado.\\");\\n      await loadDocuments();\\n      if (drawerOpen) closeDrawer();\\n    } catch (err) {\\n      notifyError(err?.detail ?? \\"No se pudo registrar la decisión del visto bueno.\\");\\n    } finally {\\n      actionLoading = false;\\n    }\\n  }\\n\\n  /**\\n   * Abre el modal de creación de cadena de VoBo y carga los usuarios del tenant\\n   * vía el proxy SSR (D-05 — el token vive en la cookie httpOnly, no en el cliente).\\n   * T-08c.\\n   */\\n  async function openModalCrearCadena() {\\n    cadenaRevisores = [];\\n    cadenaUsersError = \\"\\";\\n    modalCrearCadena = true;\\n    // Recargar siempre: el universo de usuarios puede cambiar entre aperturas.\\n    cadenaUsersLoading = true;\\n    try {\\n      const res = await fetch(\\"/bandeja/api/users?size=100\\");\\n      const result = await res.json();\\n      if (!res.ok) throw result;\\n      cadenaUsers = result.users ?? [];\\n      if (cadenaUsers.length === 0) {\\n        cadenaUsersError = \\"No hay usuarios disponibles para conformar la cadena.\\";\\n      }\\n    } catch (err) {\\n      cadenaUsers = [];\\n      cadenaUsersError = err?.detail ?? \\"No se pudo cargar la lista de usuarios.\\";\\n    } finally {\\n      cadenaUsersLoading = false;\\n    }\\n  }\\n\\n  /**\\n   * Crea la cadena de vistos buenos (revisores ordenados) sobre el radicado.\\n   * POST /bandeja/api/[id]/vobo-cadena → /api/v1/workflows/{id}/vistos-buenos\\n   * T-08c.\\n   */\\n  async function crearCadena() {\\n    if (!selectedDoc || actionLoading) return;\\n    if (cadenaRevisores.length === 0) {\\n      cadenaUsersError = \\"Seleccione al menos un revisor.\\";\\n      return;\\n    }\\n\\n    actionLoading = true;\\n    cadenaUsersError = \\"\\";\\n    try {\\n      const res = await fetch(`/bandeja/api/${selectedDoc.id}/vobo-cadena`, {\\n        method: \\"POST\\",\\n        headers: { \\"Content-Type\\": \\"application/json\\" },\\n        body: JSON.stringify({ revisores: cadenaRevisores }),\\n      });\\n      const result = await res.json();\\n      if (!res.ok) throw result;\\n\\n      modalCrearCadena = false;\\n      notifySuccess(`Cadena de visto bueno creada (${cadenaRevisores.length} revisor(es)).`);\\n      await loadDocuments();\\n      if (drawerOpen) closeDrawer();\\n    } catch (err) {\\n      // Errores de envío → toast (coherente con las otras acciones de operador).\\n      // cadenaUsersError se reserva para validación local / carga de usuarios.\\n      notifyError(err?.detail ?? \\"No se pudo crear la cadena de visto bueno.\\");\\n    } finally {\\n      actionLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // T-RECLASIFICAR: reclasificar nivel de seguridad (RF-SEG-08, Ley 1712)\\n  // ---------------------------------------------------------------------------\\n\\n  /**\\n   * Abre el modal de reclasificación para el radicado actualmente en el drawer.\\n   * Cierra el drawer ANTES para no apilar focus traps (A11y — mismo patrón que firmas).\\n   */\\n  function openModalReclasificar() {\\n    if (!selectedDoc) return;\\n    // Preseleccionar el nivel actual del radicado (o \\"1\\" si no tiene)\\n    reclasificarNivel = String(selectedDoc.nivel_seguridad ?? \\"1\\");\\n    reclasificarMotivo = \\"\\";\\n    reclasificarMotivoError = \\"\\";\\n    reclasificarCausal = \\"\\";\\n    reclasificarFundamento = \\"\\";\\n    reclasificarPlazo = \\"\\";\\n    // Cerrar drawer ANTES de abrir modal — evitar apilar focus traps (A11y)\\n    drawerOpen = false;\\n    modalReclasificar = true;\\n  }\\n\\n  /**\\n   * Cierra el modal de reclasificación devolviendo el foco al título de la página.\\n   * El disparador (\\"Reclasificar nivel\\") vivía en el drawer ya desmontado, por lo que\\n   * el retorno de foco nativo del Modal caería en <body>; lo movemos al h1 (patrón firmas).\\n   */\\n  function cerrarModalReclasificar() {\\n    modalReclasificar = false;\\n    tick().then(() => tituloEl?.focus());\\n  }\\n\\n  /**\\n   * Envía la reclasificación al proxy SSR.\\n   * POST /bandeja/api/[id]/security-level → PATCH /api/v1/documents/{id}/security-level.\\n   * Valida en cliente antes de enviar; traduce 404/403/422 a mensajes claros.\\n   */\\n  async function reclasificar() {\\n    if (!selectedDoc || reclasificarLoading) return;\\n\\n    reclasificarMotivoError = \\"\\";\\n    const nivelInt = parseInt(reclasificarNivel, 10);\\n\\n    if (!reclasificarNivel || ![1, 2, 3].includes(nivelInt)) {\\n      notifyError(\\"Seleccione un nivel de seguridad válido.\\");\\n      return;\\n    }\\n    if (!reclasificarMotivo.trim()) {\\n      reclasificarMotivoError = \\"El motivo de la reclasificación es obligatorio.\\";\\n      return;\\n    }\\n\\n    reclasificarLoading = true;\\n    try {\\n      /** @type {Record<string, unknown>} */\\n      const reqBody = {\\n        nivel_seguridad: nivelInt,\\n        motivo: reclasificarMotivo.trim(),\\n      };\\n      // Campos opcionales solo cuando nivel >= 2 (reservada/clasificada)\\n      if (nivelInt >= 2) {\\n        if (reclasificarCausal.trim()) reqBody.causal_reserva = reclasificarCausal.trim();\\n        if (reclasificarFundamento.trim())\\n          reqBody.fundamento_juridico = reclasificarFundamento.trim();\\n        if (reclasificarPlazo.trim()) {\\n          const plazoInt = parseInt(reclasificarPlazo, 10);\\n          if (!isNaN(plazoInt) && plazoInt >= 0) reqBody.plazo_reserva_meses = plazoInt;\\n        }\\n      }\\n\\n      const res = await fetch(`/bandeja/api/${encodeURIComponent(selectedDoc.id)}/security-level`, {\\n        method: \\"POST\\",\\n        headers: { \\"Content-Type\\": \\"application/json\\" },\\n        body: JSON.stringify(reqBody),\\n      });\\n      const result = await res.json();\\n\\n      if (res.ok) {\\n        notifySuccess(\\"Nivel de seguridad reclasificado correctamente.\\");\\n        // Actualizar el nivel en el detalle local para reflejar el cambio inmediato\\n        selectedDoc = { ...selectedDoc, nivel_seguridad: /** @type {1|2|3} */ (nivelInt) };\\n        cerrarModalReclasificar();\\n      } else if (res.status === 403) {\\n        notifyError(\\n          result.detail ?? \\"No tiene el nivel de acceso para asignar ese nivel de seguridad.\\",\\n        );\\n        cerrarModalReclasificar();\\n      } else if (res.status === 404) {\\n        notifyError(\\n          result.detail ?? \\"No fue posible reclasificar el radicado o no tiene acceso a él.\\",\\n        );\\n        cerrarModalReclasificar();\\n      } else {\\n        notifyError(result.detail ?? `Error ${res.status} al reclasificar el nivel de seguridad.`);\\n      }\\n    } catch {\\n      notifyError(\\"No se pudo conectar al servidor de documentos.\\");\\n    } finally {\\n      reclasificarLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Acciones inline (T-08 scope — sin T-08b)\\n  // ---------------------------------------------------------------------------\\n\\n  /**\\n   * Avanza el estado de un radicado (solo transiciones válidas).\\n   * Solo disponible si el usuario tiene permisos de operador.\\n   * Llama al endpoint local /bandeja/api/[id] — el backend re-valida RBAC.\\n   * @param {string} docId\\n   * @param {\'distributed\'|\'in_progress\'} newStatus\\n   */\\n  async function advanceStatus(docId, newStatus) {\\n    if (actionLoading) return;\\n    actionLoading = true;\\n    try {\\n      const res = await fetch(`/bandeja/api/${docId}`, {\\n        method: \\"PATCH\\",\\n        headers: { \\"Content-Type\\": \\"application/json\\" },\\n        body: JSON.stringify({ status: newStatus }),\\n      });\\n      const updated = await res.json();\\n      if (!res.ok) throw updated;\\n      // Actualizar en la lista local para evitar reload completo\\n      documents = documents.map((d) => (d.id === docId ? { ...d, status: updated.status } : d));\\n      // Actualizar en el drawer si está abierto\\n      if (selectedDoc?.id === docId) {\\n        selectedDoc = { ...selectedDoc, status: updated.status };\\n      }\\n      notifySuccess(`Estado actualizado a \\"${STATUS_LABELS[newStatus] ?? newStatus}\\".`);\\n    } catch (err) {\\n      notifyError(err.detail ?? \\"No se pudo actualizar el estado.\\");\\n    } finally {\\n      actionLoading = false;\\n    }\\n  }\\n\\n  // ---------------------------------------------------------------------------\\n  // Autorización (la UI filtra; el backend re-valida — D-05, ADR-013/020)\\n  // Los permisos en $permissions vienen de /auth/me (layout.server.js).\\n  // D-07: \'GESTIONAR_RADICADO\' y \'RADICAR_ENTRADA\' no existen en el catálogo\\n  // RBAC real (migración 004/005/007). Mapeados a los permisos reales:\\n  //   PERM_RADI            — permiso base de radicación (acceso general a radicados)\\n  //   PERM_RADI_ENTRADA    — radicar documentos de entrada\\n  //   PERM_SOL_ANULAR      — solicitar anulación de radicado (migración 007)\\n  //   PERM_RESPUESTA_VINCULADA — crear respuesta vinculada Salida RE: (migración 007)\\n  //   PERM_RADI_SALIDA     — radicar documentos de salida (cubre crear respuesta)\\n  // El backend re-valida RBAC en cada endpoint; la UI no es fuente de verdad.\\n  // ---------------------------------------------------------------------------\\n  $: canManage = $permissions.includes(\\"PERM_RADI\\") || $permissions.includes(\\"PERM_RADI_ENTRADA\\");\\n\\n  // T-08b: permisos para acciones de operador\\n  $: canAnular = $permissions.includes(\\"PERM_SOL_ANULAR\\");\\n  $: canDevolver = $permissions.includes(\\"PERM_RADI\\");\\n  $: canResponder =\\n    $permissions.includes(\\"PERM_RESPUESTA_VINCULADA\\") || $permissions.includes(\\"PERM_RADI_SALIDA\\");\\n  $: canVoBo = $permissions.includes(\\"PERM_RADI\\");\\n  // Pre-computed para evitar condiciones largas en template (printWidth)\\n  $: canDevolverInDrawer =\\n    canDevolver && (selectedDoc?.status === \\"in_progress\\" || selectedDoc?.status === \\"distributed\\");\\n  $: canReclasificar = $permissions.includes(\\"PERM_RECLASIFICAR\\");\\n  // Nivel seleccionado como entero (para mostrar/ocultar campos opcionales de reserva en template)\\n  $: nivelReclasificarInt = parseInt(reclasificarNivel, 10) || 0;\\n<\/script>\\n\\n<svelte:head>\\n  <title>Bandeja de radicados — OrpycaMCP</title>\\n</svelte:head>\\n\\n<div class=\\"bandeja\\">\\n  <!-- -----------------------------------------------------------------------\\n    Encabezado\\n  ----------------------------------------------------------------------- -->\\n  <header class=\\"bandeja__header\\">\\n    <h1 class=\\"bandeja__title\\" bind:this={tituloEl} tabindex=\\"-1\\">Bandeja de radicados</h1>\\n    <span class=\\"bandeja__total\\" aria-live=\\"polite\\">\\n      {#if !loading}\\n        {total.toLocaleString(\\"es-CO\\")} radicado{total !== 1 ? \\"s\\" : \\"\\"}\\n      {/if}\\n    </span>\\n  </header>\\n\\n  <!-- -----------------------------------------------------------------------\\n    Filtros\\n  ----------------------------------------------------------------------- -->\\n  <section class=\\"bandeja__filters\\" aria-label=\\"Filtros de la bandeja\\">\\n    <form\\n      class=\\"filters-form\\"\\n      on:submit|preventDefault={applyFilters}\\n      role=\\"search\\"\\n      aria-label=\\"Filtrar radicados\\"\\n    >\\n      <div class=\\"filters-form__fields\\">\\n        <FormField\\n          label=\\"Tipo\\"\\n          type=\\"select\\"\\n          id=\\"filter-type\\"\\n          bind:value={filterDocType}\\n          options={[\\n            { value: \\"\\", label: \\"Todos los tipos\\" },\\n            { value: \\"E\\", label: \\"Entrada\\" },\\n            { value: \\"S\\", label: \\"Salida\\" },\\n            { value: \\"I\\", label: \\"Interno\\" },\\n          ]}\\n        />\\n\\n        <FormField\\n          label=\\"Estado\\"\\n          type=\\"select\\"\\n          id=\\"filter-status\\"\\n          bind:value={filterStatus}\\n          options={[\\n            { value: \\"\\", label: \\"Todos los estados\\" },\\n            { value: \\"registered\\", label: \\"Radicado\\" },\\n            { value: \\"distributed\\", label: \\"Distribuido\\" },\\n            { value: \\"in_progress\\", label: \\"En trámite\\" },\\n            { value: \\"closed\\", label: \\"Cerrado\\" },\\n          ]}\\n        />\\n      </div>\\n\\n      <div class=\\"filters-form__actions\\">\\n        <Button type=\\"submit\\" variant=\\"primary\\" size=\\"sm\\">\\n          <i class=\\"fas fa-filter\\" aria-hidden=\\"true\\"></i>\\n          Filtrar\\n        </Button>\\n        {#if filterDocType || filterStatus}\\n          <Button type=\\"button\\" variant=\\"ghost\\" size=\\"sm\\" on:click={clearFilters}>\\n            <i class=\\"fas fa-times\\" aria-hidden=\\"true\\"></i>\\n            Limpiar\\n          </Button>\\n        {/if}\\n      </div>\\n    </form>\\n\\n    <!-- Nota: filtros por fecha y dependencia disponibles en Búsqueda avanzada (T-11) -->\\n    <p class=\\"filters-note\\">\\n      Para filtrar por fecha, dependencia o texto libre usa\\n      <a href=\\"/busqueda\\" class=\\"filters-note__link\\">Búsqueda avanzada</a>.\\n    </p>\\n  </section>\\n\\n  <!-- -----------------------------------------------------------------------\\n    Error de carga inicial\\n  ----------------------------------------------------------------------- -->\\n  {#if data.loadError && documents.length === 0}\\n    <EmptyState\\n      icon=\\"fas fa-exclamation-circle\\"\\n      title=\\"Error al cargar la bandeja\\"\\n      description={data.loadError}\\n    >\\n      <Button variant=\\"primary\\" on:click={loadDocuments}>Reintentar</Button>\\n    </EmptyState>\\n  {:else}\\n    <!-- =====================================================================\\n      Tabla de radicados\\n    ===================================================================== -->\\n    <section class=\\"bandeja__table-wrapper\\" aria-label=\\"Lista de radicados\\">\\n      {#if loading}\\n        <div class=\\"bandeja__loading\\" aria-live=\\"polite\\">\\n          <Loader type=\\"skeleton\\" lines={5} label=\\"Cargando radicados…\\" />\\n        </div>\\n      {:else if documents.length === 0}\\n        <EmptyState\\n          icon=\\"fas fa-inbox\\"\\n          title=\\"Sin radicados\\"\\n          description=\\"No hay radicados que coincidan con los filtros aplicados.\\"\\n        >\\n          {#if filterDocType || filterStatus}\\n            <Button variant=\\"ghost\\" on:click={clearFilters}>Limpiar filtros</Button>\\n          {/if}\\n        </EmptyState>\\n      {:else}\\n        <table class=\\"bandeja-table\\" aria-label=\\"Radicados\\">\\n          <thead>\\n            <tr>\\n              <th scope=\\"col\\" class=\\"bandeja-table__th bandeja-table__th--tracking\\">Radicado</th>\\n              <th scope=\\"col\\" class=\\"bandeja-table__th\\">Tipo</th>\\n              <th scope=\\"col\\" class=\\"bandeja-table__th\\">Asunto</th>\\n              <th scope=\\"col\\" class=\\"bandeja-table__th bandeja-table__th--dep\\"\\n                >Dependencia destino</th\\n              >\\n              <th scope=\\"col\\" class=\\"bandeja-table__th\\">Fecha</th>\\n              <th scope=\\"col\\" class=\\"bandeja-table__th\\">Estado</th>\\n              <th scope=\\"col\\" class=\\"bandeja-table__th bandeja-table__th--actions\\">\\n                <span class=\\"sr-only\\">Acciones</span>\\n              </th>\\n            </tr>\\n          </thead>\\n          <tbody>\\n            {#each documents as doc (doc.id)}\\n              <tr\\n                class=\\"bandeja-table__row\\"\\n                class:bandeja-table__row--selected={selectedDoc?.id === doc.id}\\n              >\\n                <td class=\\"bandeja-table__td\\">\\n                  <span class=\\"tracking-number\\">{doc.tracking_number}</span>\\n                </td>\\n                <td class=\\"bandeja-table__td\\">\\n                  <span class=\\"doc-type-badge doc-type-badge--{doc.doc_type.toLowerCase()}\\">\\n                    {DOC_TYPE_LABELS[doc.doc_type] ?? doc.doc_type}\\n                  </span>\\n                </td>\\n                <td class=\\"bandeja-table__td bandeja-table__td--subject\\">\\n                  <span class=\\"subject-text\\" title={doc.subject}>{doc.subject}</span>\\n                </td>\\n                <td class=\\"bandeja-table__td bandeja-table__td--dep\\">\\n                  {doc.dest_dept ?? \\"—\\"}\\n                </td>\\n                <td class=\\"bandeja-table__td bandeja-table__td--date\\">\\n                  <time datetime={doc.registered_at}>{formatDate(doc.registered_at)}</time>\\n                </td>\\n                <td class=\\"bandeja-table__td\\">\\n                  <StatusChip\\n                    status={STATUS_CHIP_MAP[doc.status] ?? \\"neutral\\"}\\n                    label={STATUS_LABELS[doc.status] ?? doc.status}\\n                    dot={true}\\n                  />\\n                </td>\\n                <td class=\\"bandeja-table__td bandeja-table__td--actions\\">\\n                  <button\\n                    class=\\"action-btn action-btn--detail\\"\\n                    type=\\"button\\"\\n                    aria-label=\\"Ver detalle de radicado {doc.tracking_number}\\"\\n                    on:click={() => openDrawer(doc)}\\n                  >\\n                    <i class=\\"fas fa-eye\\" aria-hidden=\\"true\\"></i>\\n                    Detalle\\n                  </button>\\n\\n                  <!-- Acción inline: avanzar estado (T-08 básico) -->\\n                  {#if canManage}\\n                    {#if doc.status === \\"registered\\"}\\n                      <button\\n                        class=\\"action-btn action-btn--advance\\"\\n                        type=\\"button\\"\\n                        aria-label=\\"Distribuir radicado {doc.tracking_number}\\"\\n                        disabled={actionLoading}\\n                        on:click={() => advanceStatus(doc.id, \\"distributed\\")}\\n                      >\\n                        <i class=\\"fas fa-paper-plane\\" aria-hidden=\\"true\\"></i>\\n                        Distribuir\\n                      </button>\\n                    {:else if doc.status === \\"distributed\\"}\\n                      <button\\n                        class=\\"action-btn action-btn--advance\\"\\n                        type=\\"button\\"\\n                        aria-label=\\"Iniciar trámite del radicado {doc.tracking_number}\\"\\n                        disabled={actionLoading}\\n                        on:click={() => advanceStatus(doc.id, \\"in_progress\\")}\\n                      >\\n                        <i class=\\"fas fa-play\\" aria-hidden=\\"true\\"></i>\\n                        Iniciar trámite\\n                      </button>\\n                    {/if}\\n                  {/if}\\n                  <!-- T-08b: Anular inline; otras acciones en el drawer de detalle -->\\n                  {#if canAnular && doc.status !== \\"closed\\" && doc.status !== \\"cancelled\\"}\\n                    <button\\n                      class=\\"action-btn action-btn--danger\\"\\n                      type=\\"button\\"\\n                      aria-label=\\"Solicitar anulación del radicado {doc.tracking_number}\\"\\n                      disabled={actionLoading}\\n                      on:click={() => openAnulacionFromRow(doc)}\\n                    >\\n                      <i class=\\"fas fa-ban\\" aria-hidden=\\"true\\"></i>\\n                      Anular\\n                    </button>\\n                  {/if}\\n                </td>\\n              </tr>\\n            {/each}\\n          </tbody>\\n        </table>\\n\\n        <!-- ==============================================================\\n          Paginación\\n        ============================================================== -->\\n        {#if totalPages > 1}\\n          <nav class=\\"bandeja-pagination\\" aria-label=\\"Paginación de radicados\\">\\n            <button\\n              class=\\"pagination-btn\\"\\n              type=\\"button\\"\\n              aria-label=\\"Página anterior\\"\\n              disabled={currentPage <= 1 || loading}\\n              on:click={() => goToPage(currentPage - 1)}\\n            >\\n              <i class=\\"fas fa-chevron-left\\" aria-hidden=\\"true\\"></i>\\n            </button>\\n\\n            <span class=\\"pagination-info\\" aria-live=\\"polite\\" aria-atomic=\\"true\\">\\n              Página {currentPage} de {totalPages}\\n            </span>\\n\\n            <button\\n              class=\\"pagination-btn\\"\\n              type=\\"button\\"\\n              aria-label=\\"Página siguiente\\"\\n              disabled={currentPage >= totalPages || loading}\\n              on:click={() => goToPage(currentPage + 1)}\\n            >\\n              <i class=\\"fas fa-chevron-right\\" aria-hidden=\\"true\\"></i>\\n            </button>\\n          </nav>\\n        {/if}\\n      {/if}\\n    </section>\\n  {/if}\\n</div>\\n\\n<!-- =========================================================================\\n  Drawer de detalle del radicado\\n  No navega a otra pantalla: la URL no cambia, el foco queda en el drawer.\\n========================================================================= -->\\n<Drawer\\n  open={drawerOpen}\\n  title={selectedDoc ? selectedDoc.tracking_number : \\"Detalle del radicado\\"}\\n  side=\\"right\\"\\n  width=\\"480px\\"\\n  on:close={closeDrawer}\\n>\\n  {#if detailLoading}\\n    <Loader type=\\"skeleton\\" lines={8} label=\\"Cargando detalle…\\" />\\n  {:else if selectedDoc}\\n    <article class=\\"doc-detail\\" aria-label=\\"Detalle del radicado {selectedDoc.tracking_number}\\">\\n      <!-- Encabezado del detalle -->\\n      <header class=\\"doc-detail__header\\">\\n        <h3 class=\\"doc-detail__tracking\\">{selectedDoc.tracking_number}</h3>\\n        <StatusChip\\n          status={STATUS_CHIP_MAP[selectedDoc.status] ?? \\"neutral\\"}\\n          label={STATUS_LABELS[selectedDoc.status] ?? selectedDoc.status}\\n          dot={true}\\n        />\\n      </header>\\n\\n      <!-- Asunto -->\\n      <section class=\\"doc-detail__section\\" aria-labelledby=\\"detail-subject-heading\\">\\n        <h4 id=\\"detail-subject-heading\\" class=\\"doc-detail__section-title\\">Asunto</h4>\\n        <p class=\\"doc-detail__subject\\">{selectedDoc.subject}</p>\\n      </section>\\n\\n      <!-- Metadatos principales -->\\n      <section class=\\"doc-detail__section\\" aria-labelledby=\\"detail-meta-heading\\">\\n        <h4 id=\\"detail-meta-heading\\" class=\\"doc-detail__section-title\\">Información</h4>\\n        <dl class=\\"doc-detail__dl\\">\\n          <dt>Tipo</dt>\\n          <dd>{DOC_TYPE_LABELS[selectedDoc.doc_type] ?? selectedDoc.doc_type}</dd>\\n\\n          <dt>Fecha de radicación</dt>\\n          <dd>\\n            <time datetime={selectedDoc.registered_at}>{formatDate(selectedDoc.registered_at)}</time\\n            >\\n          </dd>\\n\\n          {#if selectedDoc.due_date}\\n            <dt>Fecha de vencimiento</dt>\\n            <dd>\\n              <time datetime={selectedDoc.due_date} class=\\"due-date\\">{selectedDoc.due_date}</time>\\n            </dd>\\n          {/if}\\n\\n          <dt>Dependencia origen</dt>\\n          <dd>{selectedDoc.origin_dept ?? \\"—\\"}</dd>\\n\\n          <dt>Dependencia destino</dt>\\n          <dd>{selectedDoc.dest_dept ?? \\"—\\"}</dd>\\n\\n          {#if selectedDoc.sender_name}\\n            <dt>Remitente</dt>\\n            <dd>{selectedDoc.sender_name}</dd>\\n          {/if}\\n\\n          {#if selectedDoc.sender_entity}\\n            <dt>Entidad remitente</dt>\\n            <dd>{selectedDoc.sender_entity}</dd>\\n          {/if}\\n\\n          <dt>Páginas</dt>\\n          <dd>{selectedDoc.pages}</dd>\\n\\n          {#if selectedDoc.doc_class}\\n            <dt>Clase documental</dt>\\n            <dd>{selectedDoc.doc_class}</dd>\\n          {/if}\\n\\n          {#if selectedDoc.nivel_seguridad}\\n            {@const nivelChip = getNivelChip(selectedDoc.nivel_seguridad)}\\n            <dt>Nivel de seguridad</dt>\\n            <dd>\\n              <StatusChip status={nivelChip.status} label={nivelChip.label} dot />\\n            </dd>\\n          {/if}\\n        </dl>\\n      </section>\\n\\n      <!-- Observaciones / cuerpo -->\\n      {#if selectedDoc.observations}\\n        <section class=\\"doc-detail__section\\" aria-labelledby=\\"detail-obs-heading\\">\\n          <h4 id=\\"detail-obs-heading\\" class=\\"doc-detail__section-title\\">\\n            {selectedDoc.doc_type === \\"E\\" ? \\"Observaciones\\" : \\"Cuerpo del documento\\"}\\n          </h4>\\n          {#if selectedDoc.doc_type === \\"S\\" || selectedDoc.doc_type === \\"I\\"}\\n            <!-- Cuerpo rico (T-12b): observations_html ya viene saneado del server (D-05). -->\\n            <div class=\\"doc-detail__obs doc-detail__obs--rich\\">\\n              {@html selectedDoc.observations_html ?? \\"\\"}\\n            </div>\\n          {:else}\\n            <!-- Entrada: texto plano; Svelte lo escapa, white-space conserva saltos. -->\\n            <p class=\\"doc-detail__obs\\">{selectedDoc.observations}</p>\\n          {/if}\\n        </section>\\n      {/if}\\n\\n      <!-- Anexos -->\\n      {#if selectedDoc.anexos?.length > 0}\\n        <section class=\\"doc-detail__section\\" aria-labelledby=\\"detail-anexos-heading\\">\\n          <h4 id=\\"detail-anexos-heading\\" class=\\"doc-detail__section-title\\">\\n            Anexos ({selectedDoc.anexos.length})\\n          </h4>\\n          <ul class=\\"anexos-list\\" role=\\"list\\">\\n            {#each selectedDoc.anexos as anexo (anexo.id)}\\n              <li class=\\"anexos-list__item\\">\\n                <i class=\\"fas fa-paperclip anexos-list__icon\\" aria-hidden=\\"true\\"></i>\\n                <span class=\\"anexos-list__name\\" title={anexo.filename}>{anexo.filename}</span>\\n                {#if anexo.file_size}\\n                  <span class=\\"anexos-list__size\\">\\n                    ({(anexo.file_size / 1024).toFixed(1)} KB)\\n                  </span>\\n                {/if}\\n              </li>\\n            {/each}\\n          </ul>\\n        </section>\\n      {/if}\\n    </article>\\n  {/if}\\n\\n  <!-- Footer de acciones del drawer -->\\n  <svelte:fragment slot=\\"footer\\">\\n    {#if selectedDoc && canManage}\\n      {#if selectedDoc.status === \\"registered\\"}\\n        <Button\\n          variant=\\"primary\\"\\n          size=\\"sm\\"\\n          disabled={actionLoading}\\n          on:click={() => advanceStatus(selectedDoc.id, \\"distributed\\")}\\n        >\\n          <i class=\\"fas fa-paper-plane\\" aria-hidden=\\"true\\"></i>\\n          Distribuir\\n        </Button>\\n      {:else if selectedDoc.status === \\"distributed\\"}\\n        <Button\\n          variant=\\"primary\\"\\n          size=\\"sm\\"\\n          disabled={actionLoading}\\n          on:click={() => advanceStatus(selectedDoc.id, \\"in_progress\\")}\\n        >\\n          <i class=\\"fas fa-play\\" aria-hidden=\\"true\\"></i>\\n          Iniciar trámite\\n        </Button>\\n      {/if}\\n      <!-- T-08b: acciones de operador -->\\n      {#if canResponder && selectedDoc.doc_type === \\"E\\"}\\n        <Button\\n          variant=\\"secondary\\"\\n          size=\\"sm\\"\\n          disabled={actionLoading}\\n          on:click={openModalResponder}\\n        >\\n          <i class=\\"fas fa-reply\\" aria-hidden=\\"true\\"></i>\\n          Responder\\n        </Button>\\n      {/if}\\n      {#if canDevolverInDrawer}\\n        <Button variant=\\"secondary\\" size=\\"sm\\" disabled={actionLoading} on:click={openModalDevolver}>\\n          <i class=\\"fas fa-undo\\" aria-hidden=\\"true\\"></i>\\n          Devolver\\n        </Button>\\n      {/if}\\n      {#if canVoBo && selectedDoc.status === \\"in_progress\\"}\\n        <Button variant=\\"secondary\\" size=\\"sm\\" disabled={actionLoading} on:click={openModalVoBo}>\\n          <i class=\\"fas fa-check-double\\" aria-hidden=\\"true\\"></i>\\n          VoBo\\n        </Button>\\n        <Button\\n          variant=\\"secondary\\"\\n          size=\\"sm\\"\\n          disabled={actionLoading}\\n          on:click={openModalCrearCadena}\\n        >\\n          <i class=\\"fas fa-users\\" aria-hidden=\\"true\\"></i>\\n          Cadena VoBo\\n        </Button>\\n      {/if}\\n      {#if canAnular && selectedDoc.status !== \\"closed\\" && selectedDoc.status !== \\"cancelled\\"}\\n        <Button variant=\\"danger\\" size=\\"sm\\" disabled={actionLoading} on:click={openModalAnulacion}>\\n          <i class=\\"fas fa-ban\\" aria-hidden=\\"true\\"></i>\\n          Anular\\n        </Button>\\n      {/if}\\n    {/if}\\n    {#if selectedDoc && canReclasificar}\\n      <Button\\n        variant=\\"secondary\\"\\n        size=\\"sm\\"\\n        disabled={actionLoading}\\n        on:click={openModalReclasificar}\\n      >\\n        <i class=\\"fas fa-shield-alt\\" aria-hidden=\\"true\\"></i>\\n        Reclasificar nivel\\n      </Button>\\n    {/if}\\n    <Button variant=\\"ghost\\" size=\\"sm\\" on:click={closeDrawer}>Cerrar</Button>\\n  </svelte:fragment>\\n</Drawer>\\n\\n<!-- =========================================================================\\n  T-08b: Modales de acciones de operador\\n  Cada modal pide confirmación explícita antes de la acción (ADR-020 §3).\\n  Los endpoints proxy leen el token de la cookie httpOnly (nunca el cliente).\\n========================================================================= -->\\n\\n<!-- Modal: Solicitar anulación -->\\n<Modal\\n  open={modalAnulacion}\\n  title=\\"Solicitar anulación\\"\\n  size=\\"sm\\"\\n  on:close={() => (modalAnulacion = false)}\\n>\\n  <p class=\\"modal-desc\\">\\n    Solicita la anulación del radicado\\n    <strong>{selectedDoc?.tracking_number ?? \\"\\"}</strong>. La anulación requiere aprobación\\n    posterior por un supervisor.\\n  </p>\\n  <div class=\\"modal-form\\">\\n    <FormField\\n      id=\\"anul-causal\\"\\n      label=\\"Causal\\"\\n      type=\\"text\\"\\n      bind:value={anulCausal}\\n      required={true}\\n      placeholder=\\"Ej. DUPLICADO\\"\\n      hint=\\"Código de causal, máx. 15 caracteres.\\"\\n      error={anulCausalError}\\n    />\\n    <FormField\\n      id=\\"anul-motivo\\"\\n      label=\\"Motivo (opcional)\\"\\n      type=\\"textarea\\"\\n      bind:value={anulMotivo}\\n      placeholder=\\"Descripción detallada de la razón de anulación…\\"\\n      rows={3}\\n    />\\n  </div>\\n  <svelte:fragment slot=\\"footer\\">\\n    <Button variant=\\"danger\\" size=\\"sm\\" loading={actionLoading} on:click={requestAnulacion}>\\n      Solicitar anulación\\n    </Button>\\n    <Button\\n      variant=\\"ghost\\"\\n      size=\\"sm\\"\\n      disabled={actionLoading}\\n      on:click={() => (modalAnulacion = false)}\\n    >\\n      Cancelar\\n    </Button>\\n  </svelte:fragment>\\n</Modal>\\n\\n<!-- Modal: Devolver en flujo -->\\n<Modal\\n  open={modalDevolver}\\n  title=\\"Devolver radicado\\"\\n  size=\\"sm\\"\\n  on:close={() => (modalDevolver = false)}\\n>\\n  <p class=\\"modal-desc\\">\\n    Devuelve el radicado\\n    <strong>{selectedDoc?.tracking_number ?? \\"\\"}</strong> a otra dependencia dentro del flujo.\\n  </p>\\n  <div class=\\"modal-form\\">\\n    <FormField\\n      id=\\"dev-to-dept\\"\\n      label=\\"Dependencia destino\\"\\n      type=\\"text\\"\\n      bind:value={devToDept}\\n      required={true}\\n      placeholder=\\"Nombre o código de la dependencia\\"\\n      hint=\\"Máx. 100 caracteres.\\"\\n      error={devToDeptError}\\n    />\\n    <FormField\\n      id=\\"dev-causal\\"\\n      label=\\"Causal\\"\\n      type=\\"text\\"\\n      bind:value={devCausal}\\n      required={true}\\n      placeholder=\\"Ej. INCOMPLETO\\"\\n      hint=\\"Código de causal, máx. 15 caracteres.\\"\\n      error={devCausalError}\\n    />\\n    <FormField\\n      id=\\"dev-comentario\\"\\n      label=\\"Comentario (opcional)\\"\\n      type=\\"textarea\\"\\n      bind:value={devComentario}\\n      placeholder=\\"Información adicional para la dependencia destino…\\"\\n      rows={3}\\n    />\\n  </div>\\n  <svelte:fragment slot=\\"footer\\">\\n    <Button variant=\\"primary\\" size=\\"sm\\" loading={actionLoading} on:click={devolver}>\\n      <i class=\\"fas fa-undo\\" aria-hidden=\\"true\\"></i>\\n      Devolver\\n    </Button>\\n    <Button\\n      variant=\\"ghost\\"\\n      size=\\"sm\\"\\n      disabled={actionLoading}\\n      on:click={() => (modalDevolver = false)}\\n    >\\n      Cancelar\\n    </Button>\\n  </svelte:fragment>\\n</Modal>\\n\\n<!-- Modal: Responder (crear Salida vinculada) -->\\n<Modal\\n  open={modalResponder}\\n  title=\\"Responder radicado\\"\\n  size=\\"md\\"\\n  on:close={() => (modalResponder = false)}\\n>\\n  <p class=\\"modal-desc\\">\\n    Crea un radicado de Salida vinculado a\\n    <strong>{selectedDoc?.tracking_number ?? \\"\\"}</strong>. Todos los campos son opcionales; el\\n    backend genera el número de radicado.\\n  </p>\\n  <div class=\\"modal-form\\">\\n    <FormField\\n      id=\\"resp-subject\\"\\n      label=\\"Asunto de la respuesta\\"\\n      type=\\"text\\"\\n      bind:value={respSubject}\\n      placeholder=\\"Asunto del radicado de salida\\"\\n    />\\n    <FormField\\n      id=\\"resp-dest-dept\\"\\n      label=\\"Dependencia destino\\"\\n      type=\\"text\\"\\n      bind:value={respDestDept}\\n      placeholder=\\"Dependencia que recibirá la respuesta\\"\\n    />\\n    <FormField\\n      id=\\"resp-observations\\"\\n      label=\\"Observaciones\\"\\n      type=\\"textarea\\"\\n      bind:value={respObservations}\\n      placeholder=\\"Observaciones para el radicado de respuesta…\\"\\n      rows={3}\\n    />\\n    <FormField\\n      id=\\"resp-days\\"\\n      label=\\"Días de respuesta\\"\\n      type=\\"text\\"\\n      bind:value={respResponseDays}\\n      placeholder=\\"Ej. 15\\"\\n      hint=\\"Número de días hábiles de plazo para la respuesta.\\"\\n    />\\n  </div>\\n  <svelte:fragment slot=\\"footer\\">\\n    <Button variant=\\"primary\\" size=\\"sm\\" loading={actionLoading} on:click={responder}>\\n      <i class=\\"fas fa-reply\\" aria-hidden=\\"true\\"></i>\\n      Crear respuesta\\n    </Button>\\n    <Button\\n      variant=\\"ghost\\"\\n      size=\\"sm\\"\\n      disabled={actionLoading}\\n      on:click={() => (modalResponder = false)}\\n    >\\n      Cancelar\\n    </Button>\\n  </svelte:fragment>\\n</Modal>\\n\\n<!-- Modal: Decisión de Visto Bueno -->\\n<Modal open={modalVoBo} title=\\"Visto bueno\\" size=\\"sm\\" on:close={() => (modalVoBo = false)}>\\n  <p class=\\"modal-desc\\">\\n    Registra tu decisión sobre el radicado\\n    <strong>{selectedDoc?.tracking_number ?? \\"\\"}</strong>.\\n  </p>\\n  <div class=\\"modal-form\\">\\n    <FormField\\n      id=\\"vobo-decision\\"\\n      label=\\"Decisión\\"\\n      type=\\"select\\"\\n      bind:value={voboAprobarStr}\\n      required={true}\\n      placeholder=\\"Seleccione una decisión…\\"\\n      options={[\\n        { value: \\"true\\", label: \\"Aprobar\\" },\\n        { value: \\"false\\", label: \\"Rechazar\\" },\\n      ]}\\n    />\\n    <FormField\\n      id=\\"vobo-comentario\\"\\n      label=\\"Comentario (opcional)\\"\\n      type=\\"textarea\\"\\n      bind:value={voboComentario}\\n      placeholder=\\"Justificación de la decisión…\\"\\n      rows={3}\\n    />\\n  </div>\\n  <svelte:fragment slot=\\"footer\\">\\n    <Button\\n      variant=\\"primary\\"\\n      size=\\"sm\\"\\n      loading={actionLoading}\\n      disabled={actionLoading || !voboAprobarStr}\\n      on:click={voboDecidir}\\n    >\\n      <i class=\\"fas fa-check-double\\" aria-hidden=\\"true\\"></i>\\n      Confirmar decisión\\n    </Button>\\n    <Button variant=\\"ghost\\" size=\\"sm\\" disabled={actionLoading} on:click={() => (modalVoBo = false)}>\\n      Cancelar\\n    </Button>\\n  </svelte:fragment>\\n</Modal>\\n\\n<!-- Modal: Crear cadena de Visto Bueno (T-08c) -->\\n<Modal\\n  open={modalCrearCadena}\\n  title=\\"Crear cadena de visto bueno\\"\\n  size=\\"md\\"\\n  on:close={() => (modalCrearCadena = false)}\\n>\\n  <p class=\\"modal-desc\\">\\n    Define la secuencia ordenada de revisores para el radicado\\n    <strong>{selectedDoc?.tracking_number ?? \\"\\"}</strong>. Cada revisor da su visto bueno en el\\n    orden establecido.\\n  </p>\\n  {#if cadenaUsersLoading}\\n    <div class=\\"modal-loader\\">\\n      <Loader />\\n      <span>Cargando usuarios…</span>\\n    </div>\\n  {:else}\\n    <div class=\\"modal-form\\">\\n      <UserPicker\\n        id=\\"cadena-vobo\\"\\n        label=\\"Revisores (en orden de revisión)\\"\\n        users={cadenaUsers}\\n        bind:value={cadenaRevisores}\\n        disabled={actionLoading}\\n        error={cadenaUsersError}\\n        hint=\\"Use la búsqueda para agregar revisores; ordénelos con las flechas.\\"\\n        max={20}\\n      />\\n    </div>\\n  {/if}\\n  <svelte:fragment slot=\\"footer\\">\\n    <Button\\n      variant=\\"primary\\"\\n      size=\\"sm\\"\\n      loading={actionLoading}\\n      disabled={actionLoading || cadenaUsersLoading || cadenaRevisores.length === 0}\\n      on:click={crearCadena}\\n    >\\n      <i class=\\"fas fa-users\\" aria-hidden=\\"true\\"></i>\\n      Crear cadena\\n    </Button>\\n    <Button\\n      variant=\\"ghost\\"\\n      size=\\"sm\\"\\n      disabled={actionLoading}\\n      on:click={() => (modalCrearCadena = false)}\\n    >\\n      Cancelar\\n    </Button>\\n  </svelte:fragment>\\n</Modal>\\n\\n<!-- Modal: Reclasificar nivel de seguridad (RF-SEG-08, Ley 1712) -->\\n<Modal\\n  open={modalReclasificar}\\n  title=\\"Reclasificar nivel de seguridad\\"\\n  size=\\"md\\"\\n  on:close={cerrarModalReclasificar}\\n>\\n  <p class=\\"modal-desc\\">\\n    Reclasifica el nivel de seguridad del radicado\\n    <strong>{selectedDoc?.tracking_number ?? \\"\\"}</strong>. El motivo es obligatorio como acto de\\n    reserva motivado (Ley 1712 de 2014).\\n  </p>\\n  <div class=\\"modal-form\\">\\n    <FormField\\n      id=\\"reclasificar-nivel\\"\\n      label=\\"Nivel de seguridad\\"\\n      type=\\"select\\"\\n      bind:value={reclasificarNivel}\\n      required={true}\\n      options={NIVEL_OPTIONS}\\n      hint=\\"Al elegir Reservada o Clasificada se solicitan datos adicionales de reserva.\\"\\n    />\\n    <FormField\\n      id=\\"reclasificar-motivo\\"\\n      label=\\"Motivo\\"\\n      type=\\"textarea\\"\\n      bind:value={reclasificarMotivo}\\n      required={true}\\n      rows={4}\\n      placeholder=\\"Explique el acto de reserva motivado que justifica este nivel de seguridad…\\"\\n      error={reclasificarMotivoError}\\n      hint=\\"Campo obligatorio. Registre el fundamento que justifica la clasificación.\\"\\n    />\\n    <!--\\n      aria-live=\\"polite\\": anuncia a lectores de pantalla la aparición de los campos\\n      opcionales de reserva cuando el nivel cambia a Reservada (2) o Clasificada (3).\\n      WCAG 4.1.3 — los cambios de contenido dinámico deben ser perceptibles.\\n    -->\\n    <div aria-live=\\"polite\\">\\n      {#if nivelReclasificarInt >= 2}\\n        <div class=\\"modal-form\\">\\n          <FormField\\n            id=\\"reclasificar-causal\\"\\n            label=\\"Causal de reserva (opcional)\\"\\n            type=\\"text\\"\\n            bind:value={reclasificarCausal}\\n            placeholder=\\"Ej. Información de seguridad nacional\\"\\n            hint=\\"Máx. 200 caracteres.\\"\\n          />\\n          <FormField\\n            id=\\"reclasificar-fundamento\\"\\n            label=\\"Fundamento jurídico (opcional)\\"\\n            type=\\"text\\"\\n            bind:value={reclasificarFundamento}\\n            placeholder=\\"Ej. Ley 1712/2014, art. 19\\"\\n            hint=\\"Máx. 300 caracteres.\\"\\n          />\\n          <FormField\\n            id=\\"reclasificar-plazo\\"\\n            label=\\"Plazo de reserva en meses (opcional)\\"\\n            type=\\"text\\"\\n            bind:value={reclasificarPlazo}\\n            placeholder=\\"Ej. 60\\"\\n            hint=\\"Número entero entre 0 y 600 meses.\\"\\n          />\\n        </div>\\n      {/if}\\n    </div>\\n  </div>\\n  <svelte:fragment slot=\\"footer\\">\\n    <Button\\n      variant=\\"primary\\"\\n      size=\\"sm\\"\\n      loading={reclasificarLoading}\\n      disabled={reclasificarLoading}\\n      on:click={reclasificar}\\n    >\\n      <i class=\\"fas fa-shield-alt\\" aria-hidden=\\"true\\"></i>\\n      Confirmar reclasificación\\n    </Button>\\n    <Button\\n      variant=\\"ghost\\"\\n      size=\\"sm\\"\\n      disabled={reclasificarLoading}\\n      on:click={cerrarModalReclasificar}\\n    >\\n      Cancelar\\n    </Button>\\n  </svelte:fragment>\\n</Modal>\\n\\n<style lang=\\"scss\\">.bandeja {\\n  padding: var(--op-space-6);\\n  max-width: 1200px;\\n  margin: 0 auto;\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-4);\\n}\\n\\n.bandeja__header {\\n  display: flex;\\n  align-items: baseline;\\n  gap: var(--op-space-4);\\n  flex-wrap: wrap;\\n  padding-bottom: var(--op-space-4);\\n  border-bottom: 1px solid var(--op-border);\\n}\\n\\n.bandeja__title {\\n  font-size: var(--op-font-xl);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text);\\n  margin: 0;\\n}\\n\\n.bandeja__total {\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text-secondary);\\n  flex-shrink: 0;\\n}\\n\\n.bandeja__filters {\\n  background: var(--op-surface);\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius-lg);\\n  padding: var(--op-space-4) var(--op-space-5);\\n}\\n\\n.filters-form {\\n  display: flex;\\n  align-items: flex-end;\\n  gap: var(--op-space-4);\\n  flex-wrap: wrap;\\n}\\n\\n.filters-form__fields {\\n  display: flex;\\n  gap: var(--op-space-3);\\n  flex-wrap: wrap;\\n  flex: 1;\\n}\\n\\n.filters-form__actions {\\n  display: flex;\\n  gap: var(--op-space-2);\\n  align-items: flex-end;\\n  padding-bottom: 0;\\n  margin-bottom: 2px;\\n}\\n\\n.filters-note {\\n  font-size: var(--op-font-xs);\\n  color: var(--op-text-secondary);\\n  margin: var(--op-space-2) 0 0;\\n}\\n\\n.filters-note__link {\\n  color: var(--op-primary);\\n  text-decoration: underline;\\n}\\n.filters-note__link:hover {\\n  color: var(--op-primary-dark);\\n}\\n.filters-note__link:focus-visible {\\n  outline: 2px solid var(--op-primary);\\n  outline-offset: 2px;\\n  border-radius: var(--op-radius-sm);\\n}\\n\\n.bandeja__loading {\\n  padding: var(--op-space-6);\\n  background: var(--op-surface);\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius-lg);\\n}\\n\\n.bandeja__table-wrapper {\\n  background: var(--op-surface);\\n  border: 1px solid var(--op-border);\\n  border-radius: var(--op-radius-lg);\\n  overflow: hidden;\\n}\\n\\n.bandeja-table {\\n  width: 100%;\\n  border-collapse: collapse;\\n  font-size: var(--op-font-sm);\\n}\\n.bandeja-table__th {\\n  padding: var(--op-space-3) var(--op-space-4);\\n  text-align: left;\\n  font-size: var(--op-font-xs);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text-secondary);\\n  background: var(--op-bg);\\n  border-bottom: 1px solid var(--op-border);\\n  white-space: nowrap;\\n}\\n.bandeja-table__th--tracking {\\n  width: 160px;\\n}\\n.bandeja-table__th--dep {\\n  width: 160px;\\n}\\n.bandeja-table__th--actions {\\n  width: 180px;\\n  text-align: right;\\n}\\n.bandeja-table__row {\\n  transition: background var(--op-transition);\\n}\\n.bandeja-table__row:hover {\\n  background: var(--op-bg);\\n}\\n.bandeja-table__row--selected {\\n  background: var(--op-primary-light);\\n}\\n.bandeja-table__row:not(:last-child) .bandeja-table__td {\\n  border-bottom: 1px solid var(--op-border);\\n}\\n.bandeja-table__td {\\n  padding: var(--op-space-3) var(--op-space-4);\\n  vertical-align: middle;\\n  color: var(--op-text);\\n}\\n.bandeja-table__td--subject {\\n  max-width: 280px;\\n}\\n.bandeja-table__td--dep {\\n  max-width: 160px;\\n}\\n.bandeja-table__td--date {\\n  white-space: nowrap;\\n  color: var(--op-text-secondary);\\n}\\n.bandeja-table__td--actions {\\n  text-align: right;\\n  white-space: nowrap;\\n}\\n\\n.tracking-number {\\n  font-family: monospace;\\n  font-size: var(--op-font-xs);\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-primary-dark);\\n  background: var(--op-primary-light);\\n  padding: 2px var(--op-space-2);\\n  border-radius: var(--op-radius-sm);\\n  white-space: nowrap;\\n}\\n\\n.doc-type-badge {\\n  display: inline-block;\\n  font-size: var(--op-font-xs);\\n  font-weight: var(--op-weight-bold);\\n  padding: 2px var(--op-space-2);\\n  border-radius: var(--op-radius-sm);\\n  white-space: nowrap;\\n}\\n.doc-type-badge--e {\\n  background: var(--op-info-bg);\\n  color: var(--op-info-fg);\\n}\\n.doc-type-badge--s {\\n  background: var(--op-warning-bg);\\n  color: var(--op-warning-fg);\\n}\\n.doc-type-badge--i {\\n  background: var(--op-bg);\\n  color: var(--op-text-secondary);\\n  border: 1px solid var(--op-border);\\n}\\n\\n.subject-text {\\n  display: block;\\n  overflow: hidden;\\n  white-space: nowrap;\\n  text-overflow: ellipsis;\\n}\\n\\n.action-btn {\\n  display: inline-flex;\\n  align-items: center;\\n  gap: var(--op-space-1);\\n  font-size: var(--op-font-xs);\\n  font-weight: var(--op-weight-medium);\\n  padding: 3px var(--op-space-2);\\n  border-radius: var(--op-radius-sm);\\n  border: 1px solid transparent;\\n  cursor: pointer;\\n  transition: background var(--op-transition), color var(--op-transition);\\n}\\n.action-btn:focus-visible {\\n  outline: 2px solid var(--op-primary);\\n  outline-offset: 2px;\\n}\\n.action-btn:disabled {\\n  opacity: 0.5;\\n  cursor: not-allowed;\\n}\\n.action-btn--detail {\\n  background: var(--op-bg);\\n  color: var(--op-text-secondary);\\n  border-color: var(--op-border);\\n  margin-right: var(--op-space-1);\\n}\\n.action-btn--detail:hover:not(:disabled) {\\n  background: var(--op-primary-light);\\n  color: var(--op-primary-dark);\\n  border-color: var(--op-primary-light);\\n}\\n.action-btn--advance {\\n  background: var(--op-primary-light);\\n  color: var(--op-primary-dark);\\n  border-color: var(--op-primary-light);\\n}\\n.action-btn--advance:hover:not(:disabled) {\\n  background: var(--op-primary);\\n  color: var(--op-surface);\\n}\\n.action-btn--danger {\\n  background: transparent;\\n  color: var(--op-error);\\n  border-color: var(--op-error);\\n}\\n.action-btn--danger:hover:not(:disabled) {\\n  background: var(--op-error);\\n  color: var(--op-surface);\\n}\\n\\n.modal-desc {\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text);\\n  margin: 0 0 var(--op-space-4);\\n  line-height: var(--op-line-height);\\n}\\n.modal-desc strong {\\n  font-family: monospace;\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-primary-dark);\\n}\\n\\n.modal-form {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-4);\\n}\\n\\n.modal-loader {\\n  display: flex;\\n  align-items: center;\\n  justify-content: center;\\n  gap: var(--op-space-3);\\n  padding: var(--op-space-6) 0;\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text-secondary);\\n}\\n\\n.bandeja-pagination {\\n  display: flex;\\n  align-items: center;\\n  justify-content: center;\\n  gap: var(--op-space-4);\\n  padding: var(--op-space-3) var(--op-space-5);\\n  border-top: 1px solid var(--op-border);\\n}\\n\\n.pagination-btn {\\n  display: flex;\\n  align-items: center;\\n  justify-content: center;\\n  width: 32px;\\n  height: 32px;\\n  border-radius: var(--op-radius-sm);\\n  border: 1px solid var(--op-border);\\n  background: var(--op-surface);\\n  color: var(--op-text-secondary);\\n  cursor: pointer;\\n  transition: background var(--op-transition);\\n}\\n.pagination-btn:hover:not(:disabled) {\\n  background: var(--op-bg);\\n  color: var(--op-text);\\n}\\n.pagination-btn:focus-visible {\\n  outline: 2px solid var(--op-primary);\\n  outline-offset: 2px;\\n}\\n.pagination-btn:disabled {\\n  opacity: 0.4;\\n  cursor: not-allowed;\\n}\\n\\n.pagination-info {\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text-secondary);\\n}\\n\\n.doc-detail {\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-4);\\n}\\n\\n.doc-detail__header {\\n  display: flex;\\n  align-items: center;\\n  justify-content: space-between;\\n  gap: var(--op-space-3);\\n  flex-wrap: wrap;\\n}\\n\\n.doc-detail__tracking {\\n  font-family: monospace;\\n  font-size: var(--op-font-sm);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-primary-dark);\\n  background: var(--op-primary-light);\\n  padding: var(--op-space-1) var(--op-space-3);\\n  border-radius: var(--op-radius-sm);\\n  margin: 0;\\n}\\n\\n.doc-detail__section {\\n  border-top: 1px solid var(--op-border);\\n  padding-top: var(--op-space-3);\\n}\\n\\n.doc-detail__section-title {\\n  font-size: var(--op-font-xs);\\n  font-weight: var(--op-weight-bold);\\n  color: var(--op-text-secondary);\\n  text-transform: uppercase;\\n  letter-spacing: 0.5px;\\n  margin: 0 0 var(--op-space-3);\\n}\\n\\n.doc-detail__subject {\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text);\\n  margin: 0;\\n  line-height: var(--op-line-height);\\n}\\n\\n.doc-detail__dl {\\n  display: grid;\\n  grid-template-columns: 140px 1fr;\\n  gap: var(--op-space-2) var(--op-space-3);\\n  font-size: var(--op-font-sm);\\n  margin: 0;\\n}\\n.doc-detail__dl dt {\\n  font-weight: var(--op-weight-medium);\\n  color: var(--op-text-secondary);\\n}\\n.doc-detail__dl dd {\\n  color: var(--op-text);\\n  margin: 0;\\n  overflow-wrap: break-word;\\n}\\n\\n.due-date {\\n  color: var(--op-warning-fg);\\n  font-weight: var(--op-weight-medium);\\n}\\n\\n.doc-detail__obs {\\n  font-size: var(--op-font-sm);\\n  color: var(--op-text-secondary);\\n  margin: 0;\\n  font-style: italic;\\n  line-height: var(--op-line-height);\\n  white-space: pre-wrap;\\n}\\n\\n.doc-detail__obs--rich {\\n  font-style: normal;\\n  color: var(--op-text);\\n}\\n.doc-detail__obs--rich :global(p) {\\n  margin: 0 0 var(--op-space-2);\\n}\\n.doc-detail__obs--rich :global(h2),\\n.doc-detail__obs--rich :global(h3) {\\n  margin: var(--op-space-2) 0 var(--op-space-1);\\n  color: var(--op-text);\\n}\\n.doc-detail__obs--rich :global(ul),\\n.doc-detail__obs--rich :global(ol) {\\n  margin: 0 0 var(--op-space-2) var(--op-space-5);\\n}\\n.doc-detail__obs--rich :global(blockquote) {\\n  margin: 0 0 var(--op-space-2);\\n  padding-left: var(--op-space-3);\\n  border-left: 3px solid var(--op-primary-light);\\n}\\n.doc-detail__obs--rich :global(a) {\\n  color: var(--op-primary);\\n  text-decoration: underline;\\n}\\n\\n.anexos-list {\\n  list-style: none;\\n  padding: 0;\\n  margin: 0;\\n  display: flex;\\n  flex-direction: column;\\n  gap: var(--op-space-2);\\n}\\n\\n.anexos-list__item {\\n  display: flex;\\n  align-items: center;\\n  gap: var(--op-space-2);\\n  font-size: var(--op-font-sm);\\n  padding: var(--op-space-2) var(--op-space-3);\\n  background: var(--op-bg);\\n  border-radius: var(--op-radius-sm);\\n  border: 1px solid var(--op-border);\\n}\\n\\n.anexos-list__icon {\\n  color: var(--op-text-secondary);\\n  flex-shrink: 0;\\n  font-size: var(--op-font-xs);\\n}\\n\\n.anexos-list__name {\\n  flex: 1;\\n  overflow: hidden;\\n  white-space: nowrap;\\n  text-overflow: ellipsis;\\n  color: var(--op-text);\\n}\\n\\n.anexos-list__size {\\n  font-size: var(--op-font-xs);\\n  color: var(--op-text-secondary);\\n  flex-shrink: 0;\\n}\\n\\n@media (max-width: 768px) {\\n  .bandeja {\\n    padding: var(--op-space-4);\\n  }\\n  .bandeja-table__th--dep, .bandeja-table__td--dep {\\n    display: none;\\n  }\\n  .filters-form {\\n    flex-direction: column;\\n    align-items: stretch;\\n  }\\n  .filters-form__actions {\\n    justify-content: flex-start;\\n  }\\n}</style>\\n"],"names":[],"mappings":"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"}'
};
const pageSize = 20;
function formatDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("es-CO", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  });
}
const Page = create_ssr_component(($$result, $$props, $$bindings, slots) => {
  let totalPages;
  let canManage;
  let canAnular;
  let nivelReclasificarInt;
  let $permissions, $$unsubscribe_permissions;
  $$unsubscribe_permissions = subscribe(permissions, (value) => $permissions = value);
  let { data } = $$props;
  let documents = data.documents ?? [];
  let total = data.total ?? 0;
  let loading = false;
  let filterDocType = data.initialFilters?.docType ?? "";
  let filterStatus = data.initialFilters?.status ?? "";
  let currentPage = data.initialFilters?.page ?? 1;
  let drawerOpen = false;
  let selectedDoc = null;
  let actionLoading = false;
  let modalAnulacion = false;
  let modalDevolver = false;
  let modalResponder = false;
  let modalVoBo = false;
  let modalCrearCadena = false;
  let modalReclasificar = false;
  let reclasificarNivel = "";
  let reclasificarMotivo = "";
  let reclasificarMotivoError = "";
  let reclasificarCausal = "";
  let reclasificarFundamento = "";
  let reclasificarPlazo = "";
  let reclasificarLoading = false;
  let tituloEl;
  let anulCausal = "";
  let anulMotivo = "";
  let anulCausalError = "";
  let devToDept = "";
  let devCausal = "";
  let devComentario = "";
  let devToDeptError = "";
  let devCausalError = "";
  let respSubject = "";
  let respDestDept = "";
  let respObservations = "";
  let respResponseDays = "";
  let voboAprobarStr = "";
  let voboComentario = "";
  let cadenaUsers = [];
  let cadenaRevisores = [];
  let cadenaUsersError = "";
  const DOC_TYPE_LABELS = { E: "Entrada", S: "Salida", I: "Interno" };
  const STATUS_LABELS = {
    registered: "Radicado",
    distributed: "Distribuido",
    in_progress: "En trámite",
    closed: "Cerrado"
  };
  const STATUS_CHIP_MAP = {
    registered: "info",
    distributed: "pending",
    in_progress: "active",
    closed: "closed"
  };
  if ($$props.data === void 0 && $$bindings.data && data !== void 0) $$bindings.data(data);
  $$result.css.add(css);
  let $$settled;
  let $$rendered;
  let previous_head = $$result.head;
  do {
    $$settled = true;
    $$result.head = previous_head;
    totalPages = Math.ceil(total / pageSize);
    canManage = $permissions.includes("PERM_RADI") || $permissions.includes("PERM_RADI_ENTRADA");
    canAnular = $permissions.includes("PERM_SOL_ANULAR");
    $permissions.includes("PERM_RADI");
    $permissions.includes("PERM_RESPUESTA_VINCULADA") || $permissions.includes("PERM_RADI_SALIDA");
    $permissions.includes("PERM_RADI");
    $permissions.includes("PERM_RECLASIFICAR");
    nivelReclasificarInt = parseInt(reclasificarNivel, 10) || 0;
    $$rendered = `   ${$$result.head += `<!-- HEAD_svelte-annf7p_START -->${$$result.title = `<title>Bandeja de radicados — OrpycaMCP</title>`, ""}<!-- HEAD_svelte-annf7p_END -->`, ""} <div class="bandeja svelte-1v7xjen"> <header class="bandeja__header svelte-1v7xjen"><h1 class="bandeja__title svelte-1v7xjen" tabindex="-1"${add_attribute("this", tituloEl, 0)} data-svelte-h="svelte-eurnc2">Bandeja de radicados</h1> <span class="bandeja__total svelte-1v7xjen" aria-live="polite">${`${escape(total.toLocaleString("es-CO"))} radicado${escape(total !== 1 ? "s" : "")}`}</span></header>  <section class="bandeja__filters svelte-1v7xjen" aria-label="Filtros de la bandeja"><form class="filters-form svelte-1v7xjen" role="search" aria-label="Filtrar radicados"><div class="filters-form__fields svelte-1v7xjen">${validate_component(FormField, "FormField").$$render(
      $$result,
      {
        label: "Tipo",
        type: "select",
        id: "filter-type",
        options: [
          { value: "", label: "Todos los tipos" },
          { value: "E", label: "Entrada" },
          { value: "S", label: "Salida" },
          { value: "I", label: "Interno" }
        ],
        value: filterDocType
      },
      {
        value: ($$value) => {
          filterDocType = $$value;
          $$settled = false;
        }
      },
      {}
    )} ${validate_component(FormField, "FormField").$$render(
      $$result,
      {
        label: "Estado",
        type: "select",
        id: "filter-status",
        options: [
          { value: "", label: "Todos los estados" },
          { value: "registered", label: "Radicado" },
          {
            value: "distributed",
            label: "Distribuido"
          },
          {
            value: "in_progress",
            label: "En trámite"
          },
          { value: "closed", label: "Cerrado" }
        ],
        value: filterStatus
      },
      {
        value: ($$value) => {
          filterStatus = $$value;
          $$settled = false;
        }
      },
      {}
    )}</div> <div class="filters-form__actions svelte-1v7xjen">${validate_component(Button, "Button").$$render(
      $$result,
      {
        type: "submit",
        variant: "primary",
        size: "sm"
      },
      {},
      {
        default: () => {
          return `<i class="fas fa-filter" aria-hidden="true"></i>
          Filtrar`;
        }
      }
    )} ${filterDocType || filterStatus ? `${validate_component(Button, "Button").$$render(
      $$result,
      {
        type: "button",
        variant: "ghost",
        size: "sm"
      },
      {},
      {
        default: () => {
          return `<i class="fas fa-times" aria-hidden="true"></i>
            Limpiar`;
        }
      }
    )}` : ``}</div></form>  <p class="filters-note svelte-1v7xjen" data-svelte-h="svelte-12r9d93">Para filtrar por fecha, dependencia o texto libre usa
      <a href="/busqueda" class="filters-note__link svelte-1v7xjen">Búsqueda avanzada</a>.</p></section>  ${data.loadError && documents.length === 0 ? `${validate_component(EmptyState, "EmptyState").$$render(
      $$result,
      {
        icon: "fas fa-exclamation-circle",
        title: "Error al cargar la bandeja",
        description: data.loadError
      },
      {},
      {
        default: () => {
          return `${validate_component(Button, "Button").$$render($$result, { variant: "primary" }, {}, {
            default: () => {
              return `Reintentar`;
            }
          })}`;
        }
      }
    )}` : ` <section class="bandeja__table-wrapper svelte-1v7xjen" aria-label="Lista de radicados">${`${documents.length === 0 ? `${validate_component(EmptyState, "EmptyState").$$render(
      $$result,
      {
        icon: "fas fa-inbox",
        title: "Sin radicados",
        description: "No hay radicados que coincidan con los filtros aplicados."
      },
      {},
      {
        default: () => {
          return `${filterDocType || filterStatus ? `${validate_component(Button, "Button").$$render($$result, { variant: "ghost" }, {}, {
            default: () => {
              return `Limpiar filtros`;
            }
          })}` : ``}`;
        }
      }
    )}` : `<table class="bandeja-table svelte-1v7xjen" aria-label="Radicados"><thead data-svelte-h="svelte-1fg8byq"><tr><th scope="col" class="bandeja-table__th bandeja-table__th--tracking svelte-1v7xjen">Radicado</th> <th scope="col" class="bandeja-table__th svelte-1v7xjen">Tipo</th> <th scope="col" class="bandeja-table__th svelte-1v7xjen">Asunto</th> <th scope="col" class="bandeja-table__th bandeja-table__th--dep svelte-1v7xjen">Dependencia destino</th> <th scope="col" class="bandeja-table__th svelte-1v7xjen">Fecha</th> <th scope="col" class="bandeja-table__th svelte-1v7xjen">Estado</th> <th scope="col" class="bandeja-table__th bandeja-table__th--actions svelte-1v7xjen"><span class="sr-only">Acciones</span></th></tr></thead> <tbody>${each(documents, (doc) => {
      return `<tr class="${[
        "bandeja-table__row svelte-1v7xjen",
        selectedDoc?.id === doc.id ? "bandeja-table__row--selected" : ""
      ].join(" ").trim()}"><td class="bandeja-table__td svelte-1v7xjen"><span class="tracking-number svelte-1v7xjen">${escape(doc.tracking_number)}</span></td> <td class="bandeja-table__td svelte-1v7xjen"><span class="${"doc-type-badge doc-type-badge--" + escape(doc.doc_type.toLowerCase(), true) + " svelte-1v7xjen"}">${escape(DOC_TYPE_LABELS[doc.doc_type] ?? doc.doc_type)} </span></td> <td class="bandeja-table__td bandeja-table__td--subject svelte-1v7xjen"><span class="subject-text svelte-1v7xjen"${add_attribute("title", doc.subject, 0)}>${escape(doc.subject)}</span></td> <td class="bandeja-table__td bandeja-table__td--dep svelte-1v7xjen">${escape(doc.dest_dept ?? "—")}</td> <td class="bandeja-table__td bandeja-table__td--date svelte-1v7xjen"><time${add_attribute("datetime", doc.registered_at, 0)}>${escape(formatDate(doc.registered_at))}</time></td> <td class="bandeja-table__td svelte-1v7xjen">${validate_component(StatusChip, "StatusChip").$$render(
        $$result,
        {
          status: STATUS_CHIP_MAP[doc.status] ?? "neutral",
          label: STATUS_LABELS[doc.status] ?? doc.status,
          dot: true
        },
        {},
        {}
      )}</td> <td class="bandeja-table__td bandeja-table__td--actions svelte-1v7xjen"><button class="action-btn action-btn--detail svelte-1v7xjen" type="button" aria-label="${"Ver detalle de radicado " + escape(doc.tracking_number, true)}"><i class="fas fa-eye" aria-hidden="true"></i>
                    Detalle</button>  ${canManage ? `${doc.status === "registered" ? `<button class="action-btn action-btn--advance svelte-1v7xjen" type="button" aria-label="${"Distribuir radicado " + escape(doc.tracking_number, true)}" ${""}><i class="fas fa-paper-plane" aria-hidden="true"></i>
                        Distribuir
                      </button>` : `${doc.status === "distributed" ? `<button class="action-btn action-btn--advance svelte-1v7xjen" type="button" aria-label="${"Iniciar trámite del radicado " + escape(doc.tracking_number, true)}" ${""}><i class="fas fa-play" aria-hidden="true"></i>
                        Iniciar trámite
                      </button>` : ``}`}` : ``}  ${canAnular && doc.status !== "closed" && doc.status !== "cancelled" ? `<button class="action-btn action-btn--danger svelte-1v7xjen" type="button" aria-label="${"Solicitar anulación del radicado " + escape(doc.tracking_number, true)}" ${""}><i class="fas fa-ban" aria-hidden="true"></i>
                      Anular
                    </button>` : ``}</td> </tr>`;
    })}</tbody></table>  ${totalPages > 1 ? `<nav class="bandeja-pagination svelte-1v7xjen" aria-label="Paginación de radicados"><button class="pagination-btn svelte-1v7xjen" type="button" aria-label="Página anterior" ${currentPage <= 1 || loading ? "disabled" : ""}><i class="fas fa-chevron-left" aria-hidden="true"></i></button> <span class="pagination-info svelte-1v7xjen" aria-live="polite" aria-atomic="true">Página ${escape(currentPage)} de ${escape(totalPages)}</span> <button class="pagination-btn svelte-1v7xjen" type="button" aria-label="Página siguiente" ${currentPage >= totalPages || loading ? "disabled" : ""}><i class="fas fa-chevron-right" aria-hidden="true"></i></button></nav>` : ``}`}`}</section>`}</div>  ${validate_component(Drawer, "Drawer").$$render(
      $$result,
      {
        open: drawerOpen,
        title: "Detalle del radicado",
        side: "right",
        width: "480px"
      },
      {},
      {
        footer: () => {
          return ` ${``} ${``} ${validate_component(Button, "Button").$$render($$result, { variant: "ghost", size: "sm" }, {}, {
            default: () => {
              return `Cerrar`;
            }
          })} `;
        },
        default: () => {
          return `${`${``}`} `;
        }
      }
    )}   ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: modalAnulacion,
        title: "Solicitar anulación",
        size: "sm"
      },
      {},
      {
        footer: () => {
          return `${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "danger",
              size: "sm",
              loading: actionLoading
            },
            {},
            {
              default: () => {
                return `Solicitar anulación`;
              }
            }
          )} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "ghost",
              size: "sm",
              disabled: actionLoading
            },
            {},
            {
              default: () => {
                return `Cancelar`;
              }
            }
          )} `;
        },
        default: () => {
          return `<p class="modal-desc svelte-1v7xjen">Solicita la anulación del radicado
    <strong class="svelte-1v7xjen">${escape("")}</strong>. La anulación requiere aprobación
    posterior por un supervisor.</p> <div class="modal-form svelte-1v7xjen">${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "anul-causal",
              label: "Causal",
              type: "text",
              required: true,
              placeholder: "Ej. DUPLICADO",
              hint: "Código de causal, máx. 15 caracteres.",
              error: anulCausalError,
              value: anulCausal
            },
            {
              value: ($$value) => {
                anulCausal = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "anul-motivo",
              label: "Motivo (opcional)",
              type: "textarea",
              placeholder: "Descripción detallada de la razón de anulación…",
              rows: 3,
              value: anulMotivo
            },
            {
              value: ($$value) => {
                anulMotivo = $$value;
                $$settled = false;
              }
            },
            {}
          )}</div>`;
        }
      }
    )}  ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: modalDevolver,
        title: "Devolver radicado",
        size: "sm"
      },
      {},
      {
        footer: () => {
          return `${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "primary",
              size: "sm",
              loading: actionLoading
            },
            {},
            {
              default: () => {
                return `<i class="fas fa-undo" aria-hidden="true"></i>
      Devolver`;
              }
            }
          )} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "ghost",
              size: "sm",
              disabled: actionLoading
            },
            {},
            {
              default: () => {
                return `Cancelar`;
              }
            }
          )} `;
        },
        default: () => {
          return `<p class="modal-desc svelte-1v7xjen">Devuelve el radicado
    <strong class="svelte-1v7xjen">${escape("")}</strong> a otra dependencia dentro del flujo.</p> <div class="modal-form svelte-1v7xjen">${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "dev-to-dept",
              label: "Dependencia destino",
              type: "text",
              required: true,
              placeholder: "Nombre o código de la dependencia",
              hint: "Máx. 100 caracteres.",
              error: devToDeptError,
              value: devToDept
            },
            {
              value: ($$value) => {
                devToDept = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "dev-causal",
              label: "Causal",
              type: "text",
              required: true,
              placeholder: "Ej. INCOMPLETO",
              hint: "Código de causal, máx. 15 caracteres.",
              error: devCausalError,
              value: devCausal
            },
            {
              value: ($$value) => {
                devCausal = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "dev-comentario",
              label: "Comentario (opcional)",
              type: "textarea",
              placeholder: "Información adicional para la dependencia destino…",
              rows: 3,
              value: devComentario
            },
            {
              value: ($$value) => {
                devComentario = $$value;
                $$settled = false;
              }
            },
            {}
          )}</div>`;
        }
      }
    )}  ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: modalResponder,
        title: "Responder radicado",
        size: "md"
      },
      {},
      {
        footer: () => {
          return `${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "primary",
              size: "sm",
              loading: actionLoading
            },
            {},
            {
              default: () => {
                return `<i class="fas fa-reply" aria-hidden="true"></i>
      Crear respuesta`;
              }
            }
          )} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "ghost",
              size: "sm",
              disabled: actionLoading
            },
            {},
            {
              default: () => {
                return `Cancelar`;
              }
            }
          )} `;
        },
        default: () => {
          return `<p class="modal-desc svelte-1v7xjen">Crea un radicado de Salida vinculado a
    <strong class="svelte-1v7xjen">${escape("")}</strong>. Todos los campos son opcionales; el
    backend genera el número de radicado.</p> <div class="modal-form svelte-1v7xjen">${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "resp-subject",
              label: "Asunto de la respuesta",
              type: "text",
              placeholder: "Asunto del radicado de salida",
              value: respSubject
            },
            {
              value: ($$value) => {
                respSubject = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "resp-dest-dept",
              label: "Dependencia destino",
              type: "text",
              placeholder: "Dependencia que recibirá la respuesta",
              value: respDestDept
            },
            {
              value: ($$value) => {
                respDestDept = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "resp-observations",
              label: "Observaciones",
              type: "textarea",
              placeholder: "Observaciones para el radicado de respuesta…",
              rows: 3,
              value: respObservations
            },
            {
              value: ($$value) => {
                respObservations = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "resp-days",
              label: "Días de respuesta",
              type: "text",
              placeholder: "Ej. 15",
              hint: "Número de días hábiles de plazo para la respuesta.",
              value: respResponseDays
            },
            {
              value: ($$value) => {
                respResponseDays = $$value;
                $$settled = false;
              }
            },
            {}
          )}</div>`;
        }
      }
    )}  ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: modalVoBo,
        title: "Visto bueno",
        size: "sm"
      },
      {},
      {
        footer: () => {
          return `${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "primary",
              size: "sm",
              loading: actionLoading,
              disabled: !voboAprobarStr
            },
            {},
            {
              default: () => {
                return `<i class="fas fa-check-double" aria-hidden="true"></i>
      Confirmar decisión`;
              }
            }
          )} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "ghost",
              size: "sm",
              disabled: actionLoading
            },
            {},
            {
              default: () => {
                return `Cancelar`;
              }
            }
          )} `;
        },
        default: () => {
          return `<p class="modal-desc svelte-1v7xjen">Registra tu decisión sobre el radicado
    <strong class="svelte-1v7xjen">${escape("")}</strong>.</p> <div class="modal-form svelte-1v7xjen">${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "vobo-decision",
              label: "Decisión",
              type: "select",
              required: true,
              placeholder: "Seleccione una decisión…",
              options: [
                { value: "true", label: "Aprobar" },
                { value: "false", label: "Rechazar" }
              ],
              value: voboAprobarStr
            },
            {
              value: ($$value) => {
                voboAprobarStr = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "vobo-comentario",
              label: "Comentario (opcional)",
              type: "textarea",
              placeholder: "Justificación de la decisión…",
              rows: 3,
              value: voboComentario
            },
            {
              value: ($$value) => {
                voboComentario = $$value;
                $$settled = false;
              }
            },
            {}
          )}</div>`;
        }
      }
    )}  ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: modalCrearCadena,
        title: "Crear cadena de visto bueno",
        size: "md"
      },
      {},
      {
        footer: () => {
          return `${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "primary",
              size: "sm",
              loading: actionLoading,
              disabled: cadenaRevisores.length === 0
            },
            {},
            {
              default: () => {
                return `<i class="fas fa-users" aria-hidden="true"></i>
      Crear cadena`;
              }
            }
          )} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "ghost",
              size: "sm",
              disabled: actionLoading
            },
            {},
            {
              default: () => {
                return `Cancelar`;
              }
            }
          )} `;
        },
        default: () => {
          return `<p class="modal-desc svelte-1v7xjen">Define la secuencia ordenada de revisores para el radicado
    <strong class="svelte-1v7xjen">${escape("")}</strong>. Cada revisor da su visto bueno en el
    orden establecido.</p> ${`<div class="modal-form svelte-1v7xjen">${validate_component(UserPicker, "UserPicker").$$render(
            $$result,
            {
              id: "cadena-vobo",
              label: "Revisores (en orden de revisión)",
              users: cadenaUsers,
              disabled: actionLoading,
              error: cadenaUsersError,
              hint: "Use la búsqueda para agregar revisores; ordénelos con las flechas.",
              max: 20,
              value: cadenaRevisores
            },
            {
              value: ($$value) => {
                cadenaRevisores = $$value;
                $$settled = false;
              }
            },
            {}
          )}</div>`}`;
        }
      }
    )}  ${validate_component(Modal, "Modal").$$render(
      $$result,
      {
        open: modalReclasificar,
        title: "Reclasificar nivel de seguridad",
        size: "md"
      },
      {},
      {
        footer: () => {
          return `${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "primary",
              size: "sm",
              loading: reclasificarLoading,
              disabled: reclasificarLoading
            },
            {},
            {
              default: () => {
                return `<i class="fas fa-shield-alt" aria-hidden="true"></i>
      Confirmar reclasificación`;
              }
            }
          )} ${validate_component(Button, "Button").$$render(
            $$result,
            {
              variant: "ghost",
              size: "sm",
              disabled: reclasificarLoading
            },
            {},
            {
              default: () => {
                return `Cancelar`;
              }
            }
          )} `;
        },
        default: () => {
          return `<p class="modal-desc svelte-1v7xjen">Reclasifica el nivel de seguridad del radicado
    <strong class="svelte-1v7xjen">${escape("")}</strong>. El motivo es obligatorio como acto de
    reserva motivado (Ley 1712 de 2014).</p> <div class="modal-form svelte-1v7xjen">${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "reclasificar-nivel",
              label: "Nivel de seguridad",
              type: "select",
              required: true,
              options: NIVEL_OPTIONS,
              hint: "Al elegir Reservada o Clasificada se solicitan datos adicionales de reserva.",
              value: reclasificarNivel
            },
            {
              value: ($$value) => {
                reclasificarNivel = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "reclasificar-motivo",
              label: "Motivo",
              type: "textarea",
              required: true,
              rows: 4,
              placeholder: "Explique el acto de reserva motivado que justifica este nivel de seguridad…",
              error: reclasificarMotivoError,
              hint: "Campo obligatorio. Registre el fundamento que justifica la clasificación.",
              value: reclasificarMotivo
            },
            {
              value: ($$value) => {
                reclasificarMotivo = $$value;
                $$settled = false;
              }
            },
            {}
          )}  <div aria-live="polite">${nivelReclasificarInt >= 2 ? `<div class="modal-form svelte-1v7xjen">${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "reclasificar-causal",
              label: "Causal de reserva (opcional)",
              type: "text",
              placeholder: "Ej. Información de seguridad nacional",
              hint: "Máx. 200 caracteres.",
              value: reclasificarCausal
            },
            {
              value: ($$value) => {
                reclasificarCausal = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "reclasificar-fundamento",
              label: "Fundamento jurídico (opcional)",
              type: "text",
              placeholder: "Ej. Ley 1712/2014, art. 19",
              hint: "Máx. 300 caracteres.",
              value: reclasificarFundamento
            },
            {
              value: ($$value) => {
                reclasificarFundamento = $$value;
                $$settled = false;
              }
            },
            {}
          )} ${validate_component(FormField, "FormField").$$render(
            $$result,
            {
              id: "reclasificar-plazo",
              label: "Plazo de reserva en meses (opcional)",
              type: "text",
              placeholder: "Ej. 60",
              hint: "Número entero entre 0 y 600 meses.",
              value: reclasificarPlazo
            },
            {
              value: ($$value) => {
                reclasificarPlazo = $$value;
                $$settled = false;
              }
            },
            {}
          )}</div>` : ``}</div></div>`;
        }
      }
    )}`;
  } while (!$$settled);
  $$unsubscribe_permissions();
  return $$rendered;
});
export {
  Page as default
};
