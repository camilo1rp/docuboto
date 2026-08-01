import * as server from '../entries/pages/(public)/login/callback/_page.server.js';

export const index = 19;
let component_cache;
export const component = async () => component_cache ??= (await import('../entries/pages/(public)/login/callback/_page.svelte.js')).default;
export { server };
export const server_id = "src/routes/(public)/login/callback/+page.server.js";
export const imports = ["_app/immutable/nodes/19.BJBACp0R.js","_app/immutable/chunks/CtVBvrjE.js","_app/immutable/chunks/D7g4fVw7.js"];
export const stylesheets = ["_app/immutable/assets/19.Dl-x2dX-.css"];
export const fonts = [];
