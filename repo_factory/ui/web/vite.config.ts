// The page's build. `npm run build` writes ../static/app/: hashed files and .vite/manifest.json,
// which routes/static.py reads to link them from app.html. No CDN, no inline script, no polyfill
// (the page's CSP is script-src 'self').
//
// `npm run dev` serves on 127.0.0.1:5173 only. Open http://127.0.0.1:5173/: the page is a running
// repo-factory's own (REPO_FACTORY_DEV_TARGET, default http://127.0.0.1:8790) with its built module
// swapped for these sources and hot reload; api/, static/ and auth/ are proxied there with Origin
// rewritten, so the server's same-origin check passes.
/// <reference types="vitest/config" />
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig, type Plugin } from "vite";

const target = (process.env.REPO_FACTORY_DEV_TARGET || "http://127.0.0.1:8790").replace(/\/$/, "");
const proxied = { target, changeOrigin: true, headers: { origin: target } };

function devPage(): Plugin {
  return {
    name: "repo-factory-dev-page",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const path = (req.url || "").split("?")[0];
        if (path !== "/" && path !== "/index.html") return next();
        try {
          const html = await (await fetch(`${target}/`)).text();
          const page = html
            .replace(/<script type="module" src="[^"]*\/app\/[^"]*"><\/script>\n?/, "")
            .replace(/<link rel="stylesheet" href="[^"]*\/app\/[^"]*">\n?/g, "")
            .replace("</body>", '<script type="module" src="/src/main.ts"></script>\n</body>');
          res.setHeader("content-type", "text/html; charset=utf-8");
          res.end(await server.transformIndexHtml(path, page));
        } catch (e) {
          res.statusCode = 502;
          res.end(`no repo-factory at ${target} (${e}); start one: repo-factory ui`);
        }
      });
    },
  };
}

export default defineConfig({
  plugins: [svelte(), devPage()],
  // component tests mount in jsdom: Svelte's browser build, not its server one
  resolve: process.env.VITEST ? { conditions: ["browser"] } : undefined,
  base: "./",
  build: {
    outDir: "../static/app",
    emptyOutDir: true,
    assetsDir: "",
    manifest: true,
    modulePreload: { polyfill: false },
    target: "es2022",
    sourcemap: false,
    rollupOptions: {
      input: "src/main.ts",
      output: {
        entryFileNames: "app-[hash].js",
        chunkFileNames: "[name]-[hash].js",
        assetFileNames: "[name]-[hash][extname]",
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": proxied,
      "/static": proxied,
      "/auth": proxied,
      "/help": proxied,
    },
  },
  preview: { host: "127.0.0.1", port: 4173, strictPort: true },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
    // a busy runner loads a component in seconds, not milliseconds: this is the ceiling
    testTimeout: 20_000,
  },
});
