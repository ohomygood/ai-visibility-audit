(function(){
  var pref = localStorage.getItem("aiv_lang") || ((navigator.language||"").toLowerCase().indexOf("zh")===0 ? "zh" : "en");
  window.setLang = function(l){ document.body.className = "lang-" + l; document.documentElement.lang = l==="zh"?"zh-CN":"en"; localStorage.setItem("aiv_lang", l); };
  document.addEventListener("DOMContentLoaded", function(){ setLang(pref);
    var c = window.AIV_CONFIG || {};
    document.querySelectorAll("[data-href]").forEach(function(a){ a.href = c[a.getAttribute("data-href")] || "#"; });
    document.querySelectorAll("[data-text]").forEach(function(a){ a.textContent = c[a.getAttribute("data-text")] || ""; });
  });
})();
