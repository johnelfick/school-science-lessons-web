/* External links in the navigation open in a new tab. */
document.querySelectorAll(".md-nav__link, .md-tabs__link").forEach(function (a) {
  if (a.href && a.href.indexOf("http") === 0 &&
      a.href.indexOf(location.origin) !== 0) {
    a.target = "_blank";
    a.rel = "noopener";
  }
});
