// AnalystOS web server: Next.js behind a thin Node HTTP server that records each request's real
// socket address in an internal header (overwriting anything the client sent). The /api proxy route
// forwards it to the API as X-AOS-Client-Addr with the shared AOS_PROXY_SECRET, so the API can refuse
// single-user local mode to browsers on other machines. Plain `next start` has no access to the
// socket address, and X-Forwarded-For from a client can be forged.
//
//   node server.mjs [--dev] [-p 3000] [-H 127.0.0.1]     (env: PORT, HOSTNAME, API_URL, AOS_PROXY_SECRET)
import { createServer } from "node:http";
import next from "next";

const argv = process.argv.slice(2);
const arg = (short, long) => {
  const i = argv.findIndex((a) => a === short || a === long);
  return i >= 0 ? argv[i + 1] : undefined;
};
const dev = argv.includes("--dev");
const port = Number(arg("-p", "--port") ?? process.env.PORT ?? 3000);
const hostname = arg("-H", "--hostname") ?? process.env.HOSTNAME ?? "127.0.0.1";
const PEER_HEADER = "x-aos-web-peer";

if (!process.env.AOS_PROXY_SECRET) {
  console.warn(
    "[analystos-web] AOS_PROXY_SECRET is not set: the API cannot tell remote browsers from local ones. " +
      "Keep this server on 127.0.0.1 (the default) or set the same AOS_PROXY_SECRET for web and API.",
  );
}

const app = next({ dev, hostname, port });
const handle = app.getRequestHandler();
await app.prepare();

createServer((req, res) => {
  req.headers[PEER_HEADER] = (req.socket.remoteAddress ?? "").replace(/^::ffff:/, "");
  handle(req, res);
}).listen(port, hostname, () => {
  console.log(`[analystos-web] ${dev ? "dev" : "production"} server on http://${hostname}:${port} (API ${process.env.API_URL ?? "http://127.0.0.1:8000"})`);
});
