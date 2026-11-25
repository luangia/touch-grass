/** Drop the throwaway e2e database after the local run (see serve.py). */
import { execFileSync } from "node:child_process";

export default function teardown(): void {
  execFileSync(
    "uv",
    ["run", "--project", "../api", "python", "serve.py", "--drop"],
    {
      stdio: "inherit",
    },
  );
}
