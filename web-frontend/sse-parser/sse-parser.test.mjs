import test from "node:test";
import assert from "node:assert/strict";

import {
  SSEParser,
  parseSSE,
  streamSSE,
  DEFAULT_RETRY_MS,
} from "./sse-parser.js";

/* ---------------------------------------------------------------- */
/* Basic field handling                                              */
/* ---------------------------------------------------------------- */

test("a data line dispatches a message event", () => {
  assert.deepEqual(parseSSE("data: hello\n\n"), [
    { type: "message", data: "hello", id: "" },
  ]);
});

test("multi-line data is joined with newlines", () => {
  const events = parseSSE("data: line one\ndata: line two\ndata: line three\n\n");
  assert.equal(events.length, 1);
  assert.equal(events[0].data, "line one\nline two\nline three");
});

test("event: names the event and does not leak into the next one", () => {
  const events = parseSSE("event: token\ndata: a\n\ndata: b\n\n");
  assert.deepEqual(events, [
    { type: "token", data: "a", id: "" },
    { type: "message", data: "b", id: "" },
  ]);
});

test("only one leading space after the colon is stripped", () => {
  assert.equal(parseSSE("data:  two spaces\n\n")[0].data, " two spaces");
  assert.equal(parseSSE("data:no space\n\n")[0].data, "no space");
  assert.equal(parseSSE("data:\tx\n\n")[0].data, "\tx");
});

test("a field with no colon has an empty value", () => {
  const events = parseSSE("data\n\n");
  assert.equal(events.length, 1);
  assert.equal(events[0].data, "");
});

test("data: with no value dispatches an empty string, not nothing", () => {
  const events = parseSSE("data:\n\n");
  assert.deepEqual(events, [{ type: "message", data: "", id: "" }]);
});

test("a blank line with no data dispatches nothing", () => {
  assert.deepEqual(parseSSE("\n\n\n"), []);
});

test("a blank line with no data still clears a pending event type", () => {
  const events = parseSSE("event: stale\n\ndata: x\n\n");
  assert.deepEqual(events, [{ type: "message", data: "x", id: "" }]);
});

test("comment lines are ignored and reported to onComment", () => {
  const comments = [];
  const events = parseSSE(": keep-alive\n: second\ndata: x\n\n", {
    onComment: (c) => comments.push(c),
  });
  assert.equal(events.length, 1);
  assert.deepEqual(comments, [" keep-alive", " second"]);
});

test("unknown fields are ignored", () => {
  const events = parseSSE("foo: bar\ndata: x\n\n");
  assert.deepEqual(events, [{ type: "message", data: "x", id: "" }]);
});

test("a field name is case-sensitive", () => {
  const events = parseSSE("Data: nope\n\n");
  assert.deepEqual(events, []);
});

/* ---------------------------------------------------------------- */
/* id and retry                                                      */
/* ---------------------------------------------------------------- */

test("id persists across events and is reported on each", () => {
  const events = parseSSE("id: 7\ndata: a\n\ndata: b\n\n");
  assert.deepEqual(events.map((e) => e.id), ["7", "7"]);
});

test("an id containing a NUL is ignored", () => {
  const events = parseSSE("id: 1\nid: bad\u0000id\ndata: a\n\n");
  assert.equal(events[0].id, "1");
});

test("an empty id resets the last event id", () => {
  const events = parseSSE("id: 9\ndata: a\n\nid\ndata: b\n\n");
  assert.deepEqual(events.map((e) => e.id), ["9", ""]);
});

test("retry is accepted only when all ASCII digits", () => {
  const parser = new SSEParser();
  parser.feed("retry: 1500\n");
  assert.equal(parser.retry, 1500);

  parser.feed("retry: 12ab\n");
  assert.equal(parser.retry, 1500, "invalid retry leaves the old value");

  parser.feed("retry: -5\n");
  assert.equal(parser.retry, 1500);

  parser.feed("retry: \n");
  assert.equal(parser.retry, 1500);
});

test("retry starts as null and is reported to onRetry", () => {
  const seen = [];
  const parser = new SSEParser({ onRetry: (ms) => seen.push(ms) });
  assert.equal(parser.retry, null);
  assert.equal(DEFAULT_RETRY_MS, 3000);
  parser.feed("retry: 250\nretry: x\n");
  assert.deepEqual(seen, [250]);
});

test("lastEventId is exposed after parsing", () => {
  const parser = new SSEParser();
  parser.feed("id: abc\ndata: x\n\n");
  assert.equal(parser.lastEventId, "abc");
});

/* ---------------------------------------------------------------- */
/* Line terminators                                                  */
/* ---------------------------------------------------------------- */

test("CRLF, CR and LF are all line terminators", () => {
  assert.equal(parseSSE("data: a\r\n\r\n")[0].data, "a");
  assert.equal(parseSSE("data: b\r\r")[0].data, "b");
  assert.equal(parseSSE("data: c\n\n")[0].data, "c");
});

test("mixed terminators in one stream", () => {
  const events = parseSSE("data: a\r\n\r\ndata: b\r\rdata: c\n\n");
  assert.deepEqual(events.map((e) => e.data), ["a", "b", "c"]);
});

test("a CRLF split across chunks still terminates one line", () => {
  const parser = new SSEParser();
  assert.deepEqual(parser.feed("data: hi\r"), []);
  assert.deepEqual(parser.feed("\n\r"), []);
  const events = parser.feed("\n");
  assert.deepEqual(events, [{ type: "message", data: "hi", id: "" }]);
});

test("a trailing CR at end of stream terminates its line", () => {
  const parser = new SSEParser();
  parser.feed("data: a\r");
  parser.feed("\r");
  assert.equal(parser.end().length, 1);
});

/* ---------------------------------------------------------------- */
/* Chunk boundaries                                                  */
/* ---------------------------------------------------------------- */

test("events split at arbitrary byte positions parse identically", () => {
  const stream =
    ": hello\n" +
    "event: token\n" +
    "id: 42\n" +
    "data: first\n" +
    "data: second\n\n" +
    "data: plain\n\n";
  const whole = parseSSE(stream);

  for (let cut = 1; cut < stream.length; cut++) {
    const parser = new SSEParser();
    const events = [
      ...parser.feed(stream.slice(0, cut)),
      ...parser.feed(stream.slice(cut)),
      ...parser.end(),
    ];
    assert.deepEqual(events, whole, `split at ${cut}`);
  }
});

test("every byte boundary of a byte stream parses identically", () => {
  const bytes = new TextEncoder().encode("id: 1\ndata: a\ndata: b\n\ndata: c\n\n");
  const expected = parseSSE(new TextDecoder().decode(bytes));

  for (let cut = 1; cut < bytes.length; cut++) {
    const parser = new SSEParser();
    const events = [
      ...parser.feed(bytes.subarray(0, cut)),
      ...parser.feed(bytes.subarray(cut)),
      ...parser.end(),
    ];
    assert.deepEqual(events, expected, `byte split at ${cut}`);
  }
});

test("a multi-byte character split across chunks decodes correctly", () => {
  const bytes = new TextEncoder().encode("data: ₹1,00,000 ✓\n\n");
  const parser = new SSEParser();
  const events = [];
  for (const byte of bytes) events.push(...parser.feed(new Uint8Array([byte])));
  events.push(...parser.end());
  assert.deepEqual(events, [{ type: "message", data: "₹1,00,000 ✓", id: "" }]);
});

test("feed accepts ArrayBuffer and typed-array views", () => {
  const bytes = new TextEncoder().encode("data: x\n\n");
  const buffer = bytes.buffer.slice(0);

  for (const chunk of [
    buffer,
    new DataView(buffer),
    new Uint8Array(buffer),
    new Uint16Array(0),
  ]) {
    const parser = new SSEParser();
    const events = [...parser.feed(chunk), ...parser.end()];
    const expected = chunk.byteLength === 0 ? 0 : 1;
    assert.equal(events.length, expected, `${chunk.constructor.name} chunk`);
  }
});

test("feed rejects a chunk it cannot read", () => {
  const parser = new SSEParser();
  assert.throws(() => parser.feed(42), TypeError);
});

/* ---------------------------------------------------------------- */
/* BOM, end of stream, flush                                         */
/* ---------------------------------------------------------------- */

test("a leading UTF-8 BOM is stripped", () => {
  assert.deepEqual(parseSSE("\uFEFFdata: x\n\n"), [
    { type: "message", data: "x", id: "" },
  ]);
});

test("a BOM arriving in its own chunk is stripped", () => {
  const parser = new SSEParser();
  parser.feed("\uFEFF");
  assert.deepEqual(parser.feed("data: x\n\n"), [
    { type: "message", data: "x", id: "" },
  ]);
});

test("a BOM that is not at the start is kept as data", () => {
  assert.equal(parseSSE("data: a\uFEFFb\n\n")[0].data, "a\uFEFFb");
});

test("an event the stream never terminated is discarded", () => {
  assert.deepEqual(parseSSE("data: dangling\n"), []);
  assert.deepEqual(parseSSE("data: dangling"), []);
});

test("flush() dispatches the unterminated trailing event", () => {
  assert.deepEqual(parseSSE("data: dangling", { flush: true }), [
    { type: "message", data: "dangling", id: "" },
  ]);
});

test("flush() on an empty buffer dispatches nothing", () => {
  const parser = new SSEParser();
  assert.deepEqual(parser.flush(), []);
});

test("onEvent fires once per dispatched event", () => {
  const seen = [];
  const parser = new SSEParser({ onEvent: (e) => seen.push(e.data) });
  parser.feed("data: a\n\ndata: b\n\n");
  assert.deepEqual(seen, ["a", "b"]);
});

test("reset() clears buffered state but keeps callbacks", () => {
  const seen = [];
  const parser = new SSEParser({ onEvent: (e) => seen.push(e.data) });
  parser.feed("id: 3\ndata: half");
  parser.reset();
  assert.equal(parser.lastEventId, "");
  assert.deepEqual(parser.feed("data: fresh\n\n"), [
    { type: "message", data: "fresh", id: "" },
  ]);
  assert.deepEqual(seen, ["fresh"]);
});

/* ---------------------------------------------------------------- */
/* streamSSE                                                         */
/* ---------------------------------------------------------------- */

async function collect(iterable) {
  const out = [];
  for await (const item of iterable) out.push(item);
  return out;
}

test("streamSSE reads a ReadableStream", async () => {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode("event: token\ndata: he"));
      controller.enqueue(encoder.encode("llo\n\ndata: bye\n\n"));
      controller.close();
    },
  });
  assert.deepEqual(await collect(streamSSE(body)), [
    { type: "token", data: "hello", id: "" },
    { type: "message", data: "bye", id: "" },
  ]);
});

test("streamSSE reads an async iterable of strings", async () => {
  async function* chunks() {
    yield "data: a\n";
    yield "\n";
    yield "data: b\n\n";
  }
  assert.deepEqual((await collect(streamSSE(chunks()))).map((e) => e.data), [
    "a",
    "b",
  ]);
});

test("streamSSE reads a plain iterable", async () => {
  assert.deepEqual((await collect(streamSSE(["data: a\n\n"]))).length, 1);
});

test("streamSSE forwards comments and retry to the caller", async () => {
  const comments = [];
  const retries = [];
  await collect(
    streamSSE([": ping\n", "retry: 500\n", "data: x\n\n"], {
      onComment: (c) => comments.push(c),
      onRetry: (ms) => retries.push(ms),
    }),
  );
  assert.deepEqual(comments, [" ping"]);
  assert.deepEqual(retries, [500]);
});

test("streamSSE stops reading when the consumer breaks out", async () => {
  let cancelled = false;
  const encoder = new TextEncoder();
  let pushed = 0;
  const body = new ReadableStream({
    pull(controller) {
      if (pushed++ > 50) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(`data: ${pushed}\n\n`));
    },
    cancel() {
      cancelled = true;
    },
  });

  for await (const event of streamSSE(body)) {
    assert.equal(event.data, "1");
    break;
  }
  assert.equal(cancelled, true, "the stream is cancelled on early exit");
});

test("streamSSE rejects a source it cannot read", async () => {
  await assert.rejects(async () => {
    for await (const _ of streamSSE(42)) void _;
  }, TypeError);
});
