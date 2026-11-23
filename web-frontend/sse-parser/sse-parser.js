/**
 * sse-parser — a dependency-free, spec-correct Server-Sent Events parser.
 *
 * Runs unchanged in the browser and in Node (ES module, no globals touched).
 *
 * Why not `EventSource`? It is all-or-nothing: you cannot read a stream you
 * fetched yourself (POST requests, custom headers, `fetch` with an auth token),
 * you cannot see `id:` / `retry:` fields, and you cannot test it without a
 * server. Why not `split("\n\n")`? Because it breaks on `\r\n`, on comments,
 * on multi-line `data:` fields, and — most often — on chunk boundaries, where a
 * single event arrives split across two reads.
 *
 * This parser is a character-level state machine over the WHATWG HTML "Server-
 * sent events" event-stream format:
 *
 *  - `\n`, `\r\n` and a lone `\r` all terminate a line, even when the `\r` ends
 *    one chunk and the `\n` starts the next;
 *  - `data:` fields accumulate and join with `\n`; exactly one leading space
 *    after the colon is stripped;
 *  - a blank line dispatches, a line starting with `:` is a comment;
 *  - `id:` persists across events (and is ignored if it contains a NUL);
 *  - `retry:` is honoured only when it is all ASCII digits;
 *  - unknown fields are ignored;
 *  - a leading UTF-8 BOM is stripped, and multi-byte characters split across
 *    chunks are decoded correctly;
 *  - an event the stream ended without terminating is discarded, as the spec
 *    requires.
 *
 * @example
 * const res = await fetch("/api/chat", { method: "POST", body });
 * for await (const event of streamSSE(res.body)) {
 *   if (event.type === "token") render(event.data);
 * }
 */

/** Reconnection time used when the stream never sent a valid `retry:` field. */
export const DEFAULT_RETRY_MS = 3000;

const DIGITS = /^[0-9]+$/;
const BOM = 0xfeff;

/**
 * One dispatched event.
 *
 * @typedef {object} SSEEvent
 * @property {string} type  Event name; `"message"` when no `event:` was sent.
 * @property {string} data  Joined `data:` lines, without the trailing newline.
 * @property {string} id    Last event id in force when this event dispatched.
 */

/** Turn a chunk of any binary-ish shape into bytes. */
function toBytes(chunk) {
  if (chunk instanceof Uint8Array) return chunk;
  if (chunk instanceof ArrayBuffer) return new Uint8Array(chunk);
  if (ArrayBuffer.isView(chunk)) {
    return new Uint8Array(chunk.buffer, chunk.byteOffset, chunk.byteLength);
  }
  throw new TypeError(
    "sse-parser: feed() expects a string, Uint8Array, ArrayBuffer or ArrayBufferView",
  );
}

export class SSEParser {
  /**
   * @param {object} [options]
   * @param {(event: SSEEvent) => void} [options.onEvent]   Called per event.
   * @param {(comment: string) => void} [options.onComment] Called per `:` line.
   * @param {(ms: number) => void} [options.onRetry]        Called per valid `retry:`.
   */
  constructor(options = {}) {
    this.onEvent = options.onEvent ?? null;
    this.onComment = options.onComment ?? null;
    this.onRetry = options.onRetry ?? null;
    this.reset();
  }

  /** Forget all buffered state; keeps the callbacks. */
  reset() {
    this._buffer = ""; // text after the last line terminator
    this._data = ""; // accumulated `data:` payload, each line ending in "\n"
    this._eventType = "";
    this._lastEventId = "";
    this._retry = null;
    this._atStart = true; // still before the first character (BOM window)
    this._decoder = null; // TextDecoder, created on first binary chunk
    this._sink = null; // array collecting events for the current call
    return this;
  }

  /** Last `id:` seen. Send it back as `Last-Event-ID` when reconnecting. */
  get lastEventId() {
    return this._lastEventId;
  }

  /** Server-requested reconnection time in ms, or `null` if never sent. */
  get retry() {
    return this._retry;
  }

  /**
   * Feed the next chunk of the stream.
   *
   * @param {string|Uint8Array|ArrayBuffer|ArrayBufferView} chunk
   * @returns {SSEEvent[]} events completed by this chunk (often none)
   */
  feed(chunk) {
    const events = [];
    this._sink = events;

    let text;
    if (typeof chunk === "string") {
      text = chunk;
    } else {
      this._decoder ??= new TextDecoder("utf-8");
      text = this._decoder.decode(toBytes(chunk), { stream: true });
    }

    if (this._atStart && text.length > 0) {
      if (text.charCodeAt(0) === BOM) text = text.slice(1);
      this._atStart = false;
    }

    if (text.length > 0) {
      this._buffer += text;
      this._scan(false);
    }

    this._sink = null;
    return events;
  }

  /**
   * Signal end of stream. A trailing `\r` still terminates its line, but an
   * event that was never followed by a blank line is discarded (spec behaviour).
   *
   * @returns {SSEEvent[]} events completed by the end of the stream
   */
  end() {
    const events = [];
    this._sink = events;

    if (this._decoder) {
      const tail = this._decoder.decode();
      if (tail.length > 0) {
        this._atStart = false;
        this._buffer += tail;
      }
      this._decoder = null;
    }

    this._scan(true);
    this._buffer = "";
    this._data = "";
    this._eventType = "";

    this._sink = null;
    return events;
  }

  /**
   * Non-spec convenience: treat whatever is buffered as if a blank line
   * followed it, and dispatch the pending event. Useful when you hold a whole
   * response body that a sloppy server forgot to terminate.
   *
   * @returns {SSEEvent[]}
   */
  flush() {
    const events = [];
    this._sink = events;

    const rest = this._buffer;
    this._buffer = "";
    if (rest.length > 0) this._line(rest);
    this._dispatch();

    this._sink = null;
    return events;
  }

  /* ---------------------------------------------------------------- */
  /* Internals                                                         */
  /* ---------------------------------------------------------------- */

  /** Split the buffer into lines, honouring every terminator form. */
  _scan(final) {
    const buf = this._buffer;
    let pos = 0;

    while (pos < buf.length) {
      let end = pos;
      while (end < buf.length) {
        const c = buf.charCodeAt(end);
        if (c === 10 || c === 13) break;
        end++;
      }

      if (end === buf.length) break; // no terminator yet: keep it buffered

      const line = buf.slice(pos, end);

      if (buf.charCodeAt(end) === 13) {
        // A `\r` may be the first half of a `\r\n` split across two chunks:
        // hold it back until we can see the next character.
        if (end + 1 === buf.length && !final) break;
        pos = end + 1;
        if (buf.charCodeAt(pos) === 10) pos++;
      } else {
        pos = end + 1;
      }

      this._line(line);
    }

    this._buffer = buf.slice(pos);
  }

  /** Apply one complete line of the event stream. */
  _line(line) {
    if (line === "") {
      this._dispatch();
      return;
    }
    if (line.charCodeAt(0) === 58) {
      // ':comment'
      this.onComment?.(line.slice(1));
      return;
    }

    const colon = line.indexOf(":");
    let field;
    let value;
    if (colon === -1) {
      field = line;
      value = "";
    } else {
      field = line.slice(0, colon);
      value = line.slice(colon + 1);
      if (value.charCodeAt(0) === 32) value = value.slice(1); // one space only
    }

    switch (field) {
      case "data":
        this._data += value + "\n";
        break;
      case "event":
        this._eventType = value;
        break;
      case "id":
        if (!value.includes("\u0000")) this._lastEventId = value;
        break;
      case "retry":
        if (DIGITS.test(value)) {
          this._retry = Number(value);
          this.onRetry?.(this._retry);
        }
        break;
      default:
        break; // unknown field: ignored
    }
  }

  /** Blank line seen: dispatch if anything was collected. */
  _dispatch() {
    if (this._data === "") {
      this._eventType = ""; // spec: an empty dispatch still clears the type
      return;
    }

    const data = this._data.endsWith("\n")
      ? this._data.slice(0, -1)
      : this._data;
    const event = {
      type: this._eventType || "message",
      data,
      id: this._lastEventId,
    };

    this._data = "";
    this._eventType = "";

    this._sink?.push(event);
    this.onEvent?.(event);
  }
}

/**
 * Parse a complete event stream held in a string.
 *
 * @param {string} text
 * @param {object} [options]
 * @param {boolean} [options.flush] Dispatch an unterminated trailing event
 *   instead of discarding it (non-spec; off by default).
 * @param {(comment: string) => void} [options.onComment]
 * @param {(ms: number) => void} [options.onRetry]
 * @returns {SSEEvent[]}
 */
export function parseSSE(text, options = {}) {
  const parser = new SSEParser(options);
  const events = parser.feed(text);
  if (options.flush) events.push(...parser.flush());
  events.push(...parser.end());
  return events;
}

/**
 * Consume a stream of chunks as events.
 *
 * @param {ReadableStream|AsyncIterable<Uint8Array|string>|Iterable<Uint8Array|string>} source
 * @param {{ onComment?: (c: string) => void, onRetry?: (ms: number) => void }} [options]
 * @returns {AsyncGenerator<SSEEvent>}
 */
export async function* streamSSE(source, options = {}) {
  const parser = new SSEParser(options);

  for await (const chunk of iterateChunks(source)) {
    for (const event of parser.feed(chunk)) yield event;
  }
  for (const event of parser.end()) yield event;
}

/** Normalise ReadableStream / async iterable / iterable into one shape. */
async function* iterateChunks(source) {
  if (source && typeof source.getReader === "function") {
    const reader = source.getReader();
    let done = false;
    try {
      while (true) {
        const result = await reader.read();
        if (result.done) {
          done = true;
          return;
        }
        if (result.value != null) yield result.value;
      }
    } finally {
      if (!done) {
        try {
          await reader.cancel();
        } catch {
          /* already closed or errored */
        }
      }
      reader.releaseLock?.();
    }
  }

  if (source && typeof source[Symbol.asyncIterator] === "function") {
    for await (const chunk of source) if (chunk != null) yield chunk;
    return;
  }

  if (source && typeof source[Symbol.iterator] === "function") {
    for (const chunk of source) if (chunk != null) yield chunk;
    return;
  }

  throw new TypeError(
    "sse-parser: streamSSE() expects a ReadableStream or an (async) iterable of chunks",
  );
}
