// In-memory stand-ins for KV, rate limiters, Analytics Engine, Durable Objects and the Cache API.
import { UsageCounter } from "../src/usage";

export class FakeKV {
  readonly values = new Map<string, { value: string; metadata?: unknown }>();
  reads = 0;
  failing = false;

  async get(name: string, type?: "json" | "text" | { type?: string }): Promise<unknown> {
    this.reads += 1;
    if (this.failing) throw new Error("KV unavailable");
    const entry = this.values.get(name);
    if (!entry) return null;
    const asJson = type === "json" || (typeof type === "object" && type?.type === "json");
    return asJson ? JSON.parse(entry.value) : entry.value;
  }

  async put(name: string, value: string, options?: { metadata?: unknown }): Promise<void> {
    this.values.set(name, { value, metadata: options?.metadata });
  }

  async delete(name: string): Promise<void> {
    this.values.delete(name);
  }

  async list(options: { prefix?: string; cursor?: string } = {}) {
    const keys = [...this.values.entries()]
      .filter(([name]) => name.startsWith(options.prefix ?? ""))
      .sort(([a], [b]) => (a < b ? -1 : 1))
      .map(([name, { metadata }]) => ({ name, ...(metadata === undefined ? {} : { metadata }) }));
    return { keys, list_complete: true, cacheStatus: null };
  }

  json(name: string): unknown {
    const entry = this.values.get(name);
    return entry ? JSON.parse(entry.value) : undefined;
  }

  asKV(): KVNamespace {
    return this as unknown as KVNamespace;
  }
}

export class FakeLimiter {
  readonly calls: string[] = [];
  constructor(public allow = true) {}
  async limit({ key }: { key: string }) {
    this.calls.push(key);
    return { success: this.allow };
  }
  asBinding(): RateLimit {
    return this as unknown as RateLimit;
  }
}

export class FakeAnalytics {
  readonly points: AnalyticsEngineDataPoint[] = [];
  writeDataPoint(point: AnalyticsEngineDataPoint) {
    this.points.push(point);
  }
  asBinding(): AnalyticsEngineDataset {
    return this as unknown as AnalyticsEngineDataset;
  }
}

/** Durable Object storage: the key-value subset UsageCounter uses, with lexicographic list ranges. */
export class FakeStorage {
  readonly data = new Map<string, unknown>();
  async get<T>(key: string): Promise<T | undefined> {
    return this.data.get(key) as T | undefined;
  }
  async put(key: string, value: unknown): Promise<void> {
    this.data.set(key, structuredClone(value));
  }
  async delete(keys: string | string[]): Promise<boolean | number> {
    const list = Array.isArray(keys) ? keys : [keys];
    let n = 0;
    for (const k of list) if (this.data.delete(k)) n++;
    return Array.isArray(keys) ? n : n > 0;
  }
  async list<T>(options: { prefix?: string; start?: string; end?: string } = {}): Promise<Map<string, T>> {
    const out = new Map<string, T>();
    for (const key of [...this.data.keys()].sort()) {
      if (options.prefix && !key.startsWith(options.prefix)) continue;
      if (options.start && key < options.start) continue;
      if (options.end && key >= options.end) continue;
      out.set(key, this.data.get(key) as T);
    }
    return out;
  }
}

/** A namespace of real UsageCounter instances over FakeStorage, addressed by name like idFromName. */
export class FakeCounterNamespace {
  readonly objects = new Map<string, { counter: UsageCounter; storage: FakeStorage }>();
  requests = 0;

  idFromName(name: string) {
    return { name, toString: () => name } as unknown as DurableObjectId;
  }

  get(id: DurableObjectId) {
    const name = (id as unknown as { name: string }).name;
    let entry = this.objects.get(name);
    if (!entry) {
      const storage = new FakeStorage();
      const counter = new UsageCounter({ storage } as unknown as DurableObjectState, {} as Env);
      entry = { counter, storage };
      this.objects.set(name, entry);
    }
    const { counter } = entry;
    return {
      fetch: async (input: string, init?: RequestInit) => {
        this.requests += 1;
        return counter.fetch(new Request(input, init));
      },
    } as unknown as DurableObjectStub;
  }

  asBinding(): DurableObjectNamespace {
    return this as unknown as DurableObjectNamespace;
  }
}

/** Collects waitUntil promises so a test can await the metering that runs after the response. */
export class FakeContext {
  readonly pending: Promise<unknown>[] = [];
  waitUntil(p: Promise<unknown>) {
    this.pending.push(p);
  }
  passThroughOnException() {}
  props = {};
  async settle() {
    while (this.pending.length) await Promise.all(this.pending.splice(0));
  }
  asContext(): ExecutionContext {
    return this as unknown as ExecutionContext;
  }
}

/** caches.default over a Map, keyed by URL like the real Cache API (GET only). */
export class FakeCache {
  readonly entries = new Map<string, { body: Uint8Array; headers: Headers }>();
  matches = 0;
  async match(request: Request): Promise<Response | undefined> {
    this.matches += 1;
    const hit = this.entries.get(request.url);
    return hit ? new Response(hit.body.slice(), { headers: hit.headers }) : undefined;
  }
  async put(request: Request, response: Response): Promise<void> {
    this.entries.set(request.url, { body: new Uint8Array(await response.arrayBuffer()), headers: new Headers(response.headers) });
  }
}
