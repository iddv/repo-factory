// Svelte 5 with runes everywhere: Svelte 4 syntax ($:, export let) is a compile error.
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

export default {
  preprocess: vitePreprocess(),
  compilerOptions: { runes: true },
};
