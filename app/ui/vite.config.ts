import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import tailwindcss from "@tailwindcss/vite";

// base stays relative: Home Assistant serves the App under a per-install
// Ingress prefix, so absolute asset URLs resolve against the domain root and
// never reach this app.
export default defineConfig({
  base: "./",
  plugins: [vue(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: { outDir: "dist", emptyOutDir: true },
  server: { proxy: { "/manager": "http://127.0.0.1:8099" } },
});
