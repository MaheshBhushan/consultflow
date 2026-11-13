import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, formatCost, formatLatency, inputLabel, type InputSource } from "./api";

describe("formatting", () => {
  it("formats cost with micro-dollar precision", () => {
    expect(formatCost("0.002100")).toBe("$0.002100");
    expect(formatCost(0)).toBe("$0.000000");
  });

  it("formats latency in ms below one second and seconds above", () => {
    expect(formatLatency(42)).toBe("42 ms");
    expect(formatLatency(1530)).toBe("1.53 s");
  });

  it("labels inputs by filename, then subject", () => {
    const base = { id: 3, kind: "email", meta: {} } as unknown as InputSource;
    expect(inputLabel({ ...base, filename: "a.eml" })).toBe("#3 email: a.eml");
    expect(inputLabel({ ...base, filename: null, meta: { subject: "Hi" } })).toBe("#3 email: Hi");
    expect(inputLabel({ ...base, filename: null })).toBe("#3 email: pasted email");
  });
});

describe("api client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("turns FastAPI validation errors into readable ApiErrors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: [{ msg: "bad step" }, { msg: "bad model" }] }), { status: 422 })),
    );
    const err = await api.listClients().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.message).toBe("bad step; bad model");
  });

  it("uploads files as multipart form data", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    await api.uploadInput(7, new File(["hello"], "note.txt", { type: "text/plain" }));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/clients/7/inputs/upload");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBeInstanceOf(File);
  });
});
