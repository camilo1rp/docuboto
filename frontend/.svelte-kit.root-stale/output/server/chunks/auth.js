import { d as derived, w as writable } from "./index2.js";
import { h as get_store_value } from "./ssr.js";
const sessionUser = writable(null);
const roles = derived(sessionUser, ($u) => $u?.roles ?? []);
const permissions = derived(sessionUser, ($u) => $u?.permissions ?? []);
const tenantSlug = derived(sessionUser, ($u) => $u?.tenant_slug ?? null);
derived(sessionUser, ($u) => $u !== null);
function can(permission) {
  return get_store_value(permissions).includes(permission);
}
function initSession(user) {
  sessionUser.set(user);
}
export {
  can as c,
  initSession as i,
  permissions as p,
  roles as r,
  sessionUser as s,
  tenantSlug as t
};
