import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../../../packages/channel-gateway-server/channel_gateway_server/static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8310",
      "/healthz": "http://127.0.0.1:8310",
    },
  },
});
