import { describe, expect, it, vi } from "vitest";

import { ApiError, withWakeRetry } from "../src/api/client";

describe("waking a sleeping API host (D-026 Q1)", () => {
  it("retries network failures and 502/503/504, then succeeds", async () => {
    const request = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new ApiError(0, "cannot reach the API"))
      .mockRejectedValueOnce(new ApiError(503, "Service Unavailable"))
      .mockResolvedValue("ok");
    await expect(withWakeRetry(request, { delayMs: 0 })).resolves.toBe("ok");
    expect(request).toHaveBeenCalledTimes(3);
  });

  it("does not retry a real error", async () => {
    const request = vi.fn<() => Promise<string>>().mockRejectedValue(new ApiError(404, "nope"));
    await expect(withWakeRetry(request, { delayMs: 0 })).rejects.toThrow("nope");
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("gives up after the last attempt", async () => {
    const request = vi.fn<() => Promise<string>>().mockRejectedValue(new ApiError(502, "down"));
    await expect(withWakeRetry(request, { attempts: 3, delayMs: 0 })).rejects.toThrow("down");
    expect(request).toHaveBeenCalledTimes(3);
  });
});
