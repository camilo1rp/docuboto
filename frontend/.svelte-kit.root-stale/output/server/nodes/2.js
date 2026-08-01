import * as server from '../entries/pages/(app)/_layout.server.js';

export const index = 2;
let component_cache;
export const component = async () => component_cache ??= (await import('../entries/pages/(app)/_layout.svelte.js')).default;
export { server };
export const server_id = "src/routes/(app)/+layout.server.js";
export const imports = ["_app/immutable/nodes/2.B8KDD6-O.js","_app/immutable/chunks/CtVBvrjE.js","_app/immutable/chunks/D7g4fVw7.js","_app/immutable/chunks/4CRy-5tI.js","_app/immutable/chunks/CrG8ZwFW.js","_app/immutable/chunks/D0QH3NT1.js","_app/immutable/chunks/Cy0ZpuEm.js","_app/immutable/chunks/BEaHNo--.js","_app/immutable/chunks/o__dw3S6.js","_app/immutable/chunks/DAQusuWS.js","_app/immutable/chunks/D1wMasOE.js","_app/immutable/chunks/BODgJ0i6.js","_app/immutable/chunks/iu-Zm2jR.js","_app/immutable/chunks/D0OlBrF0.js"];
export const stylesheets = ["_app/immutable/assets/Button.CD2zAZPb.css","_app/immutable/assets/Loader.Dp1KZ9ni.css","_app/immutable/assets/2.LE_ZhbFi.css"];
export const fonts = [];
