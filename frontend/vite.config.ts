import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// API_PORT lets the dev proxy follow the backend if 8000 is taken: API_PORT=8077 npm run dev
const apiPort = process.env.API_PORT ?? "8000";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": `http://127.0.0.1:${apiPort}` } },
});
