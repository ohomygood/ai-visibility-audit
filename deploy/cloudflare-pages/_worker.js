// Cloudflare Pages advanced-mode worker: the production *.pages.dev host 301-redirects to the custom domain
// (path + query kept); every other host, incl. per-deploy preview hosts, is served from the static assets.
// build_static.sh copies this only when AIV_SITE_URL is a custom domain (never redirects pages.dev to itself).
const CANONICAL = "__AIV_DOMAIN__";
const OLD_HOSTS = ["ai-visibility-audit-bvb.pages.dev"];
export default {
  async fetch(req, env) {
    const u = new URL(req.url);
    if (OLD_HOSTS.includes(u.hostname) && CANONICAL !== u.hostname) {
      u.protocol = "https:"; u.hostname = CANONICAL; u.port = "";
      return Response.redirect(u.toString(), 301);
    }
    return env.ASSETS.fetch(req);
  },
};
