// The page's entry: the shell (App.svelte) draws the side nav, the notice and the page the
// address names. The tokens (colours, light and dark) are static/repo-factory.css, loaded first.
import { mount } from "svelte";
import "./app.css";
import App from "./App.svelte";

export const shell = mount(App, { target: document.body });
