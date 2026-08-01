import * as server from '../entries/pages/(app)/dashboard/_page.server.js';

export const index = 9;
let component_cache;
export const component = async () => component_cache ??= (await import('../entries/pages/(app)/dashboard/_page.svelte.js')).default;
export { server };
export const server_id = "src/routes/(app)/dashboard/+page.server.js";
export const imports = ["_app/immutable/nodes/9.OpFj2eZ-.js","_app/immutable/chunks/CtVBvrjE.js","_app/immutable/chunks/D7g4fVw7.js","_app/immutable/chunks/BEaHNo--.js","_app/immutable/chunks/o__dw3S6.js","_app/immutable/chunks/Cy0ZpuEm.js","_app/immutable/chunks/CrG8ZwFW.js","_app/immutable/chunks/4CRy-5tI.js","_app/immutable/chunks/DAQusuWS.js","_app/immutable/chunks/D1wMasOE.js","_app/immutable/chunks/BODgJ0i6.js","_app/immutable/chunks/TuimYN54.js","_app/immutable/chunks/DiJsxayN.js","_app/immutable/chunks/44tWMpT2.js","_app/immutable/chunks/iu-Zm2jR.js"];
export const stylesheets = ["_app/immutable/assets/Button.CD2zAZPb.css","_app/immutable/assets/Card.D7ogqzaE.css","_app/immutable/assets/EmptyState.Caay_EWn.css","_app/immutable/assets/StatusChip.ZerA6BO8.css","_app/immutable/assets/Loader.Dp1KZ9ni.css","_app/immutable/assets/9.DIYPCu9D.css"];
export const fonts = [];
