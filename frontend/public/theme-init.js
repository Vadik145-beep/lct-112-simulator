// Applies the saved theme before the first paint to avoid a flash. A separate file rather
// than an inline script: the Content-Security-Policy allows scripts from this origin only.
try {
  var t = localStorage.getItem("theme");
  var dark = t === "dark" || (t !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  if (dark) document.documentElement.classList.add("dark");
} catch (e) {}
