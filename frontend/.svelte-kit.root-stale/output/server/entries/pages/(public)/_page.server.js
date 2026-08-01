import { r as redirect } from "../../../chunks/index.js";
async function load({ locals }) {
  if (locals.user) {
    redirect(302, "/dashboard");
  }
  return {};
}
export {
  load
};
