// Generates src/api/schema.d.ts from the backend OpenAPI schema.
// Source: OPENAPI_URL (default http://localhost:8000/api/openapi.json); if the API is not
// running, the schema is exported directly from the backend code with `uv run`.
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const url = process.env.OPENAPI_URL ?? "http://localhost:8000/api/openapi.json";
const out = resolve("src/api/schema.d.ts");
const backendDir = resolve("../backend");

async function fetchSchema() {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(3000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    console.log(`OpenAPI: ${url}`);
    return await res.text();
  } catch (err) {
    console.log(`API недоступен (${err.message}), экспортирую схему из кода бэкенда`);
    return execFileSync("uv", ["run", "python", "-m", "app.openapi_export"], {
      cwd: backendDir,
      encoding: "utf-8",
      shell: process.platform === "win32",
      maxBuffer: 16 * 1024 * 1024,
    });
  }
}

const schema = await fetchSchema();
const dir = mkdtempSync(join(tmpdir(), "openapi-"));
const file = join(dir, "openapi.json");
writeFileSync(file, schema);
try {
  execFileSync("npx", ["openapi-typescript", file, "-o", out], {
    stdio: "inherit",
    shell: process.platform === "win32",
  });
} finally {
  rmSync(dir, { recursive: true, force: true });
}
console.log(`Типы записаны в ${out}`);
