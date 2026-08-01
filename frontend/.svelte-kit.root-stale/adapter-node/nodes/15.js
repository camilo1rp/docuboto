import * as server from '../entries/pages/(app)/reportes/_page.server.js';

export const index = 15;
let component_cache;
export const component = async () => component_cache ??= (await import('../entries/pages/(app)/reportes/_page.svelte.js')).default;
export { server };
export const server_id = "src/routes/(app)/reportes/+page.server.js";
export const imports = ["_app/immutable/nodes/15.B3r0XA3q.js","_app/immutable/chunks/CtVBvrjE.js","_app/immutable/chunks/D7g4fVw7.js","_app/immutable/chunks/D0QH3NT1.js","_app/immutable/chunks/Cy0ZpuEm.js","_app/immutable/chunks/CrG8ZwFW.js","_app/immutable/chunks/DAQusuWS.js","_app/immutable/chunks/D0OlBrF0.js","_app/immutable/chunks/iu-Zm2jR.js","_app/immutable/chunks/BEaHNo--.js","_app/immutable/chunks/DiJsxayN.js","_app/immutable/chunks/Dre5_qyR.js","_app/immutable/chunks/DBSzoquX.js","_app/immutable/chunks/4CRy-5tI.js","_app/immutable/chunks/BODgJ0i6.js"];
export const stylesheets = ["_app/immutable/assets/Button.CD2zAZPb.css","_app/immutable/assets/Loader.Dp1KZ9ni.css","_app/immutable/assets/EmptyState.Caay_EWn.css","_app/immutable/assets/FormField.D9RMqt46.css","_app/immutable/assets/DataTable.Bqrf9bq5.css","_app/immutable/assets/15.DqUsIowq.css"];
export const fonts = [];
