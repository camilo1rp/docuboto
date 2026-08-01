import * as server from '../entries/pages/(app)/busqueda/_page.server.js';

export const index = 8;
let component_cache;
export const component = async () => component_cache ??= (await import('../entries/pages/(app)/busqueda/_page.svelte.js')).default;
export { server };
export const server_id = "src/routes/(app)/busqueda/+page.server.js";
export const imports = ["_app/immutable/nodes/8.X3PoA-pl.js","_app/immutable/chunks/CtVBvrjE.js","_app/immutable/chunks/D7g4fVw7.js","_app/immutable/chunks/Cy0ZpuEm.js","_app/immutable/chunks/CrG8ZwFW.js","_app/immutable/chunks/DAQusuWS.js","_app/immutable/chunks/D0OlBrF0.js","_app/immutable/chunks/44tWMpT2.js","_app/immutable/chunks/DiJsxayN.js","_app/immutable/chunks/BEaHNo--.js","_app/immutable/chunks/Dre5_qyR.js","_app/immutable/chunks/DBSzoquX.js","_app/immutable/chunks/4CRy-5tI.js","_app/immutable/chunks/BODgJ0i6.js"];
export const stylesheets = ["_app/immutable/assets/StatusChip.ZerA6BO8.css","_app/immutable/assets/EmptyState.Caay_EWn.css","_app/immutable/assets/Button.CD2zAZPb.css","_app/immutable/assets/FormField.D9RMqt46.css","_app/immutable/assets/DataTable.Bqrf9bq5.css","_app/immutable/assets/8.q9QiihQ4.css"];
export const fonts = [];
