// Landing-page theme handling. Loaded in <head> (blocking, tiny, same-origin) so the
// saved/preferred theme is applied before first paint — no flash. Mirrors the app's
// dark-default behaviour and persists the choice in localStorage.
(function () {
  // Mark JS as available so the stylesheet can hide reveal elements pre-animation.
  // Without JS this class never lands, so all content stays visible.
  document.documentElement.classList.add("js");
  try {
    var saved = localStorage.getItem("horyon-theme");
    var prefersLight =
      window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
    var theme = saved || (prefersLight ? "light" : "dark");
    document.documentElement.setAttribute("data-theme", theme);
  } catch (e) {
    /* default markup is already data-theme="dark" */
  }
})();

document.addEventListener("DOMContentLoaded", function () {
  var year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());

  // Reveal-on-scroll: fade/slide elements in as they enter the viewport.
  // Falls back to showing everything if IntersectionObserver is unavailable.
  var revealables = document.querySelectorAll(".reveal");
  if (!("IntersectionObserver" in window)) {
    revealables.forEach(function (el) { el.classList.add("is-visible"); });
  } else {
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            io.unobserve(entry.target);
          }
        });
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.12 }
    );
    revealables.forEach(function (el) { io.observe(el); });
  }

  var btn = document.getElementById("theme-toggle");
  if (!btn) return;
  btn.addEventListener("click", function () {
    var cur =
      document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
    var next = cur === "light" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("horyon-theme", next);
    } catch (e) {
      /* ignore persistence failure */
    }
  });
});
