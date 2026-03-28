import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const proxyTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8999";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 4173,
    proxy: {
      "/v1": {
        target: proxyTarget,
        changeOrigin: true,
      },
    },
  },
});
