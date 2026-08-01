import { r as redirect } from "../../../chunks/index.js";
import { C as COOKIE_ACCESS_TOKEN, a as COOKIE_REFRESH_TOKEN, r as refreshAccessToken, s as setSessionCookies, c as clearSessionCookies, d as decodeJwt, e as extractSessionUser } from "../../../chunks/auth.server.js";
import { A as API_V1 } from "../../../chunks/config.server.js";
async function load({ cookies, url, fetch }) {
  let accessToken = cookies.get(COOKIE_ACCESS_TOKEN);
  const refreshToken = cookies.get(COOKIE_REFRESH_TOKEN);
  if (!accessToken && refreshToken) {
    const tokenSet = await refreshAccessToken(refreshToken);
    if (tokenSet) {
      setSessionCookies(cookies, tokenSet);
      accessToken = tokenSet.access_token;
    }
  }
  if (!accessToken) {
    clearSessionCookies(cookies);
    const next = encodeURIComponent(url.pathname + url.search);
    redirect(302, `/login?next=${next}`);
  }
  const claims = decodeJwt(accessToken);
  if (!claims) {
    clearSessionCookies(cookies);
    redirect(302, "/login?error=invalid_token");
  }
  const user = extractSessionUser(claims);
  try {
    const meData = await fetch(`${API_V1}/auth/me`, {
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "X-Tenant-Slug": user.tenant_slug ?? ""
      }
    });
    if (meData.ok) {
      const me = await meData.json();
      user.permissions = me.permissions ?? [];
      if (me.name) user.name = me.name;
      if (me.email) user.email = me.email;
    }
  } catch {
    user.permissions = [];
  }
  return { user };
}
export {
  load
};
