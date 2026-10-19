import { authFetch } from "@/api/client";
import { getAccessToken, setAccessToken } from "@/api/token";

describe("authFetch", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setAccessToken(null);
  });

  it("renews the run-out token and fetches the voiced reply again", async () => {
    setAccessToken("old");
    const seen: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url === "/api/auth/refresh") {
          return new Response(JSON.stringify({ access_token: "new" }), { status: 200 });
        }
        const auth = new Headers(init?.headers).get("Authorization") ?? "";
        seen.push(auth);
        return new Response("mp3", { status: auth === "Bearer new" ? 200 : 401 });
      }),
    );
    const res = await authFetch("/api/media/tts/reply.mp3");
    expect(res.status).toBe(200);
    expect(seen).toEqual(["Bearer old", "Bearer new"]);
    expect(getAccessToken()).toBe("new");
  });

  it("gives back the refusal when the session cannot be renewed", async () => {
    setAccessToken("old");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 401 })),
    );
    expect((await authFetch("/api/media/tts/reply.mp3")).status).toBe(401);
  });
});
