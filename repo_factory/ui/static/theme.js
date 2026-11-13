/* The theme before the first paint (UI-v4.md 1.2, AP): Settings › Appearance keeps two choices
   in localStorage. The base, auto, light or dark: light and dark set html[data-theme], auto leaves
   it to the OS (prefers-color-scheme in repo-factory.css). The sidebar theme sets html[data-look]
   (static/looks.css); a custom one also sets its worked-out colours as --c-* on html. Loaded in
   <head>, before the stylesheets apply, by the app, the classic page and the loop page.
   The columns too (UX-PLAN.md 4.4, UX-7): the widths the splitters set (repo-factory.layout), clamped to
   this window, so the panes never jump on load; app.js's splitters use the same apply(). */
"use strict";
(function () {
  const LOOKS = ["plum", "paper", "harbor", "reef", "fern", "clay", "graphite", "rosewood", "brass", "midnight", "contrast", "custom"];
  const html = document.documentElement;
  let t = null, l = null, c = null;
  try { t = localStorage.getItem("repo-factory.theme"); l = localStorage.getItem("repo-factory.look"); c = localStorage.getItem("repo-factory.look.custom"); } catch (e) { /* storage off: auto */ }
  if (t === "light" || t === "dark") html.setAttribute("data-theme", t);
  let vars = null;
  try { vars = c ? JSON.parse(c).vars : null; } catch (e) { vars = null; }
  for (const k in vars || {}) if (/^--c-[a-z0-9-]+$/.test(k) && /^(#[0-9a-f]{6}|light|dark)$/.test(vars[k])) html.style.setProperty(k, vars[k]);
  if (LOOKS.includes(l) && (l !== "custom" || vars)) html.setAttribute("data-look", l);

  /* repo-factory.layout: {nav, detail, icons}, per browser, nothing on the server. The navigator is
     168 to 400 px (56 as icons), the list at least 320, the detail at least 360 and at most 70% of
     the window (60% under 1180 px, where the navigator is a drawer); with no width set, repo-factory.css's
     defaults apply (224 px, 41%). A window too narrow for what was set shrinks the detail to its
     least, then shows the navigator as icons; the stored widths come back as soon as they fit. */
  const KEY = "repo-factory.layout", NAV = { min: 168, max: 400, icons: 56, def: 224 }, DET = { min: 360 }, LIST = 320, SPLIT = 6;
  const read = () => { try { const x = JSON.parse(localStorage.getItem(KEY) || "{}"); return x && typeof x === "object" ? x : {}; } catch (e) { return {}; } };
  const save = x => { try { if (x && Object.keys(x).length) localStorage.setItem(KEY, JSON.stringify(x)); else localStorage.removeItem(KEY); } catch (e) { /* storage off: this page only */ } };
  const num = v => typeof v === "number" && isFinite(v) ? v : null;
  function apply(x) {
    x = x || read();
    const W = innerWidth, wide = W >= 1180, mid = W >= 760 && !wide, st = html.style;
    let icons = wide && x.icons === true, nav = wide ? (icons ? NAV.icons : Math.max(NAV.min, Math.min(NAV.max, num(x.nav) || NAV.def))) : 0;
    let det = num(x.detail) != null ? Math.max(DET.min, Math.min(x.detail, W * (wide ? 0.7 : 0.6))) : null;
    if (wide && det != null && nav + det + LIST + 2 * SPLIT > W) {
      det = Math.max(DET.min, W - nav - LIST - 2 * SPLIT);
      if (nav + det + LIST + 2 * SPLIT > W && !icons) { icons = true; nav = NAV.icons; det = Math.max(DET.min, Math.min(x.detail, W - nav - LIST - 2 * SPLIT)); }
    }
    if (mid && det != null) det = Math.max(DET.min, Math.min(det, W - LIST - SPLIT));
    if (wide && (num(x.nav) != null || icons)) st.setProperty("--nav-w", nav + "px"); else st.removeProperty("--nav-w");
    if (det != null && W >= 760) st.setProperty("--detail-w", Math.round(det) + "px"); else st.removeProperty("--detail-w");
    html.classList.toggle("nav-icons", icons);
    return { nav, det, icons, wide, mid };
  }
  apply();
  window.factoryLayout = { read, save, apply, NAV, DET, LIST, SPLIT };
})();
