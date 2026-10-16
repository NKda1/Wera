#!/usr/bin/env node
// `pnpm dev`: runs the agent service (Python, via uv) and the web app together.
// Both get the same random AGENT_TOKEN, so only this web app can talk to this agent.
// Ctrl+C stops both; if either one exits, the other is stopped too.

import { spawn, spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const port = process.env.AGENT_PORT || "8787";
const env = {
  ...process.env,
  AGENT_TOKEN: process.env.AGENT_TOKEN || randomBytes(24).toString("hex"),
  AGENT_PORT: port,
  AGENT_URL: `http://127.0.0.1:${port}`,
};

if (spawnSync("uv", ["--version"], { stdio: "ignore" }).status !== 0) {
  console.error("WERA's agent service needs uv (https://docs.astral.sh/uv/). Install it, then run `pnpm dev` again.");
  process.exit(1);
}

const children = [];
let stopping = false;

function start(name, color, command, args, cwd) {
  const child = spawn(command, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
  const tag = `\x1b[${color}m[${name}]\x1b[0m `;
  for (const stream of [child.stdout, child.stderr]) {
    let buffer = "";
    stream.on("data", (chunk) => {
      buffer += chunk;
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) process.stdout.write(tag + line + "\n");
    });
  }
  child.on("exit", (code) => {
    if (!stopping) {
      console.log(`${tag}exited (${code ?? "signal"}), stopping WERA.`);
      stop(code ?? 1);
    }
  });
  children.push(child);
}

function stop(code = 0) {
  stopping = true;
  for (const child of children) if (child.exitCode === null) child.kill("SIGTERM");
  setTimeout(() => process.exit(code), 300);
}

process.on("SIGINT", () => stop(0));
process.on("SIGTERM", () => stop(0));

start("agent", "32", "uv", ["run", "--project", "services/agent", "wera", "serve"], root);
start("web", "36", process.execPath, [fileURLToPath(new URL("../apps/web/node_modules/next/dist/bin/next", import.meta.url)), "dev"], new URL("../apps/web/", import.meta.url));
