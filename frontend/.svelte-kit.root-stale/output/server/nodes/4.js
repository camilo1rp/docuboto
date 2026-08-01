import * as server from '../entries/pages/(app)/admin/plantillas/_page.server.js';

export const index = 4;
let component_cache;
export const component = async () => component_cache ??= (await import('../entries/pages/(app)/admin/plantillas/_page.svelte.js')).default;
export { server };
export const server_id = "src/routes/(app)/admin/plantillas/+page.server.js";
export const imports = ["_app/immutable/nodes/4.B5mm-00s.js","_app/immutable/chunks/CtVBvrjE.js","_app/immutable/chunks/D7g4fVw7.js","_app/immutable/chunks/BEaHNo--.js","_app/immutable/chunks/DAQusuWS.js","_app/immutable/chunks/CrG8ZwFW.js","_app/immutable/chunks/4CRy-5tI.js","_app/immutable/chunks/D0OlBrF0.js","_app/immutable/chunks/BMfcl3EY.js","_app/immutable/chunks/Dre5_qyR.js","_app/immutable/chunks/44tWMpT2.js","_app/immutable/chunks/DiJsxayN.js","_app/immutable/chunks/CU8Cfp9h.js","_app/immutable/chunks/B6NFP72y.js"];
export const stylesheets = ["_app/immutable/assets/Button.CD2zAZPb.css","_app/immutable/assets/Modal.CvfFDgXS.css","_app/immutable/assets/FormField.D9RMqt46.css","_app/immutable/assets/StatusChip.ZerA6BO8.css","_app/immutable/assets/EmptyState.Caay_EWn.css","_app/immutable/assets/RichEditor.BruD9wqi.css","_app/immutable/assets/4.BOfx9ZMA.css"];
export const fonts = [];
