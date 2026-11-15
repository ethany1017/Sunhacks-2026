import { defineConfig } from "vite";

export default defineConfig({
  build: { chunkSizeWarningLimit: 4000, target: "es2022" },
  server: { port: 5173, host: "0.0.0.0" },
});
