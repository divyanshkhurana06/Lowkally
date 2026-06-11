/** Agent base URL for server-side proxy routes (Render/Vercel/production). */
export function agentApiUrl(): string {
  const raw = (process.env.API_URL || "http://127.0.0.1:8080").trim();
  if (raw.startsWith("http://") || raw.startsWith("https://")) {
    return raw.replace(/\/$/, "");
  }
  return `https://${raw.replace(/\/$/, "")}`;
}
