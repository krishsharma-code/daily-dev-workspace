# sse-parser

A dependency-free, spec-correct parser for `text/event-stream` — Server-Sent
Events. One ES module that runs unchanged in the browser and in Node, so you can
read a stream you fetched yourself instead of handing it to `EventSource`.

`EventSource` is all-or-nothing: GET only, no custom headers or body, no access
to `id:` or `retry:`, and no way to test it without a live server. Reaching for
`response.text().split("\n\n")` is worse — it breaks on `\r\n`, on comments, on
multi-line `data:` fields, and on the thing that actually bites in production:
a single event arriving split across two chunk reads.

This parser is a character-level state machine over the WHATWG HTML event-stream
format, so a stream cut at any byte still parses to exactly the same events.

```js
import { streamSSE, parseSSE, SSEParser } from "./sse-parser.js";

// 1. Stream from fetch — POST, auth headers, an abort signal, all allowed.
const res = await fetch("/api/chat", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ prompt: "hello" }),
});

for await (const event of streamSSE(res.body)) {
  if (event.type === "token") append(event.data);
  else if (event.type === "done") break;
}

// 2. Parse a body you already hold.
parseSSE("event: tick\nid: 7\ndata: {\"n\":1}\n\n");
// [{ type: "tick", data: '{"n":1}', id: "7" }]

// 3. Drive it yourself, chunk by chunk.
const parser = new SSEParser({ onComment: (c) => console.log("comment:", c) });
for (const event of parser.feed(chunk)) render(event);
for (const event of parser.end()) render(event);
parser.lastEventId; // send back as Last-Event-ID when reconnecting
parser.retry;       // server-requested reconnection delay, or null
```

## API

| Export | Purpose |
| --- | --- |
| `new SSEParser(options?)` | The state machine. Options: `onEvent`, `onComment`, `onRetry`. |
| `parser.feed(chunk)` | Feed a `string`, `Uint8Array`, `ArrayBuffer` or view. Returns the events this chunk completed (usually `[]`). |
| `parser.end()` | End of stream. Returns the events the final bytes completed. |
| `parser.flush()` | Non-spec: dispatch an event the stream never terminated. |
| `parser.reset()` | Drop buffered state, keep the callbacks. |
| `parser.lastEventId` / `parser.retry` | Last `id:`, and last valid `retry:` in ms (`null` if never sent). |
| `parseSSE(text, options?)` | Parse a complete body. `{ flush: true }` keeps an unterminated trailing event. |
| `streamSSE(source, options?)` | Async generator over a `ReadableStream` or any (async) iterable of chunks. |
| `DEFAULT_RETRY_MS` | `3000` — the spec's reconnection delay when the server never sent one. |

Each event is `{ type, data, id }`: `type` is `"message"` unless an `event:`
field named it, `data` is the `data:` lines joined with `\n`, and `id` is the
last event id in force at dispatch.

## Behaviour worth knowing

- **Every terminator form.** `\n`, `\r\n` and a lone `\r` all end a line — and a
  `\r\n` torn in half by a chunk boundary is still one terminator.
- **Multi-line data.** `data: a` + `data: b` dispatches `"a\nb"`. Exactly one
  space after the colon is stripped, so `data:  x` keeps its second space.
- **Comments are not events.** A line starting with `:` is reported to
  `onComment` and never dispatched — that is how keep-alives stay invisible.
- **`id:` persists.** It carries across events, is cleared by a bare `id:` line,
  and is ignored entirely if it contains a NUL.
- **`retry:` must be digits.** `retry: 1500` sets 1500 ms; `retry: 15ab` and
  `retry: -5` are ignored and leave the previous value alone.
- **Empty dispatches.** A blank line with no buffered data dispatches nothing,
  but it still clears a pending `event:` name — the spec's quirk, kept here.
- **Encoding.** A leading BOM is dropped and multi-byte characters split across
  chunks decode correctly, because the `TextDecoder` is fed in streaming mode.
- **End of stream.** An event that was never followed by a blank line is
  discarded, as the spec requires. Use `flush()` / `parseSSE(text, { flush: true })`
  when you are holding a body from a sloppy server.

## Try it

```bash
cd web-frontend/sse-parser
npm test                      # 40 tests, node --test, no dependencies

python3 -m http.server 8000   # then open http://localhost:8000/
```

`index.html` is a playground: paste a stream and parse it whole, or press
**Stream in chunks** to watch the same bytes arrive in deliberately awkward
slices — mid-line, mid-`\r\n`, mid-field — and still produce the same events.
(A static server is needed because browsers block ES module imports from
`file://`.)

## Files

| File | What it is |
| --- | --- |
| `sse-parser.js` | The parser, `parseSSE` and `streamSSE`. |
| `sse-parser.test.mjs` | The suite, including every-byte-boundary split checks. |
| `index.html`, `sse-parser.css` | Runnable demo. |
