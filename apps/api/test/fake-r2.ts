// Just enough of the R2Bucket API for unit tests: get (with Range), put, head.

const encoder = new TextEncoder();

function parseRange(headers: Headers, size: number): { offset: number; length: number } | undefined {
  const m = /^bytes=(\d*)-(\d*)$/.exec(headers.get("range") ?? "");
  if (!m) return undefined;
  const [, start, end] = m;
  if (start === "") return { offset: size - Number(end), length: Number(end) };
  const offset = Number(start);
  const last = end === "" ? size - 1 : Math.min(Number(end), size - 1);
  return { offset, length: last - offset + 1 };
}

export class FakeR2 {
  readonly objects = new Map<string, Uint8Array>();
  reads = 0;

  putJson(key: string, value: unknown): void {
    this.objects.set(key, encoder.encode(JSON.stringify(value)));
  }

  putText(key: string, text: string): void {
    this.objects.set(key, encoder.encode(text));
  }

  async put(key: string, value: string | Uint8Array): Promise<void> {
    this.objects.set(key, typeof value === "string" ? encoder.encode(value) : value);
  }

  async head(key: string) {
    const bytes = this.objects.get(key);
    return bytes ? { key, size: bytes.byteLength } : null;
  }

  async get(key: string, options?: { range?: Headers }) {
    this.reads += 1;
    const bytes = this.objects.get(key);
    if (!bytes) return null;
    const range = options?.range ? parseRange(options.range, bytes.byteLength) : undefined;
    const slice = range ? bytes.slice(range.offset, range.offset + range.length) : bytes;
    return {
      key,
      size: bytes.byteLength,
      range,
      body: new Response(slice).body,
      text: async () => new TextDecoder().decode(slice),
      json: async () => JSON.parse(new TextDecoder().decode(slice)),
      arrayBuffer: async () => slice.buffer.slice(slice.byteOffset, slice.byteOffset + slice.byteLength),
    };
  }

  asBucket(): R2Bucket {
    return this as unknown as R2Bucket;
  }
}
