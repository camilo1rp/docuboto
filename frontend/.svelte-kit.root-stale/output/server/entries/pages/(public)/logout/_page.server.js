import { r as redirect } from "../../../../chunks/index.js";
import { a as COOKIE_REFRESH_TOKEN, k as revokeSession, c as clearSessionCookies } from "../../../../chunks/auth.server.js";
import { b as KEYCLOAK_LOGOUT_URL, K as KEYCLOAK_CLIENT_ID } from "../../../../chunks/config.js";
async function load({ cookies, url }) {
  await performLogout(cookies, url);
}
const actions = {
  default: async ({ cookies, url }) => {
    await performLogout(cookies, url);
  }
};
async function performLogout(cookies, url) {
  const refreshToken = cookies.get(COOKIE_REFRESH_TOKEN);
  if (refreshToken) {
    await revokeSession(refreshToken);
  }
  clearSessionCookies(cookies);
  const postLogoutUri = `${url.origin}/login`;
  const keycloakLogoutUrl = new URL(KEYCLOAK_LOGOUT_URL);
  keycloakLogoutUrl.searchParams.set("client_id", KEYCLOAK_CLIENT_ID);
  keycloakLogoutUrl.searchParams.set("post_logout_redirect_uri", postLogoutUri);
  redirect(302, keycloakLogoutUrl.toString());
}
export {
  actions,
  load
};
