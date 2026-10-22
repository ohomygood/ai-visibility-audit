// Placeholders — fill in after approval (domain, Cloudflare, Stripe). Do not commit real secrets here.
window.AIV_CONFIG = {
  API_BASE: "",                                            // "" = same origin (local dev server); prod: Render API URL (unchanged by the custom domain)
  SITE_URL: "https://__AIV_DOMAIN__",                      // custom domain https://audit.aiseenlab.com (build: AIV_SITE_URL)
  // Live Stripe Payment Link, $19 USD one-off. Adaptive Pricing is always on for Payment Links: checkout may show local currency.
  // Always append ?client_reference_id=<audit_id>[_<base32 store domain>] (the API's pay_url) so the payment maps
  // to the audit and the order can be rebuilt from Stripe alone after the server's disk is wiped.
  STRIPE_PAYMENT_LINK: "https://buy.stripe.com/fZu00k2l84Zz9YibSt0Ny02",
  CONTACT_EMAIL: "go_with_prob@mail.grokbot.com",          // support + refunds
};

window.AIV_payUrl = function(auditId, email){
  if(!/^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}(_[a-z2-7]{1,176})?$/.test(auditId || "")) return null;   // never sell without an audit to unlock
  var u = window.AIV_CONFIG.STRIPE_PAYMENT_LINK + "?client_reference_id=" + encodeURIComponent(auditId);
  if(email && /^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/.test(email)) u += "&prefilled_email=" + encodeURIComponent(email);
  return u;
};
