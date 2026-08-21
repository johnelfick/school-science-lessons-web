/* Copy buttons on the corrections page: click to copy the exact line. */
document.addEventListener("click", function (e) {
  var b = e.target.closest(".ssl-copy");
  if (!b) return;
  navigator.clipboard.writeText(b.getAttribute("data-copy")).then(function () {
    var old = b.textContent;
    b.textContent = "Copied ✓";
    b.classList.add("ssl-copy--done");
    setTimeout(function () {
      b.textContent = old;
      b.classList.remove("ssl-copy--done");
    }, 1500);
  });
});

/* External links in the navigation open in a new tab. */
document.querySelectorAll(".md-nav__link, .md-tabs__link").forEach(function (a) {
  if (a.href && a.href.indexOf("http") === 0 &&
      a.href.indexOf(location.origin) !== 0) {
    a.target = "_blank";
    a.rel = "noopener";
  }
});
