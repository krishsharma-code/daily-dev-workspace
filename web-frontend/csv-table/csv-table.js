/**
 * csv-table — RFC 4180 CSV parsing and serialising, with no dependencies.
 *
 * Reads and writes delimited text the way spreadsheets and exports actually
 * behave: quoted fields containing delimiters and newlines, doubled quotes,
 * CRLF or LF line endings, a leading byte-order mark, and delimiters other
 * than a comma. Runs unchanged in the browser and in Node — no DOM, no build
 * step.
 */

const BOM = "\uFEFF";
const CANDIDATE_DELIMITERS = [",", ";", "\t", "|"];
const NAMED_DELIMITERS = new Map([
  ["comma", ","],
  ["semicolon", ";"],
  ["tab", "\t"],
  ["pipe", "|"],
]);

/** Thrown when the input is not well-formed CSV. */
export class CsvParseError extends Error {
  /**
   * @param {string} message
   * @param {{line?: number, column?: number}} [position]
   */
  constructor(message, { line = 1, column = 1 } = {}) {
    super(`${message} (line ${line}, column ${column})`);
    this.name = "CsvParseError";
    this.line = line;
    this.column = column;
  }
}

function describeValue(value) {
  if (value === null) return "null";
  if (value === undefined) return "undefined";
  if (typeof value === "string") return `"${value}"`;
  if (Array.isArray(value)) return "an array";
  if (typeof value === "object") return "an object";
  return String(value);
}

function resolveDelimiter(delimiter) {
  if (typeof delimiter !== "string" || delimiter.length === 0) {
    throw new TypeError(`delimiter must be a non-empty string, received ${describeValue(delimiter)}`);
  }
  const named = NAMED_DELIMITERS.get(delimiter.toLowerCase());
  const resolved = named ?? delimiter;
  if (resolved.length !== 1) {
    throw new RangeError(`delimiter must be a single character, received ${describeValue(delimiter)}`);
  }
  if (resolved === '"' || resolved === "\r" || resolved === "\n") {
    throw new RangeError(`delimiter cannot be a quote or line break, received ${describeValue(delimiter)}`);
  }
  return resolved;
}

function resolveQuote(quote) {
  if (typeof quote !== "string" || quote.length !== 1) {
    throw new TypeError(`quote must be a single character, received ${describeValue(quote)}`);
  }
  return quote;
}

function resolveOptions(options) {
  if (options === null || typeof options !== "object" || Array.isArray(options)) {
    throw new TypeError(`options must be an object, received ${describeValue(options)}`);
  }
  return options;
}

function assertBoolean(value, name) {
  if (typeof value !== "boolean") {
    throw new TypeError(`${name} must be a boolean, received ${describeValue(value)}`);
  }
}

/**
 * Count the fields on each non-blank record, ignoring delimiters that sit
 * inside quoted fields. Used only to score candidate delimiters.
 *
 * @param {string} source
 * @param {string} delimiter
 * @param {string} quote
 * @returns {number[]} one entry per non-blank record
 */
function countFieldsPerRecord(source, delimiter, quote) {
  const counts = [];
  let fields = 1;
  let inQuotes = false;
  let atFieldStart = true;
  let hasContent = false;
  let sawDelimiter = false;

  for (let i = 0; i < source.length; i += 1) {
    const ch = source[i];

    if (inQuotes) {
      if (ch === quote) {
        if (source[i + 1] === quote) {
          i += 1;
        } else {
          inQuotes = false;
        }
      }
      continue;
    }

    if (ch === quote && atFieldStart) {
      inQuotes = true;
      atFieldStart = false;
      hasContent = true;
      continue;
    }

    if (ch === delimiter) {
      fields += 1;
      sawDelimiter = true;
      atFieldStart = true;
      continue;
    }

    if (ch === "\r" || ch === "\n") {
      if (ch === "\r" && source[i + 1] === "\n") i += 1;
      if (hasContent || sawDelimiter) counts.push(fields);
      fields = 1;
      atFieldStart = true;
      hasContent = false;
      sawDelimiter = false;
      continue;
    }

    atFieldStart = false;
    hasContent = true;
  }

  if (hasContent || sawDelimiter) counts.push(fields);
  return counts;
}

/**
 * Work out which delimiter a CSV document most likely uses.
 *
 * Each candidate is scored by how consistently it splits records into the same
 * number of fields, so a comma inside quoted prose cannot outvote a real tab.
 * Falls back to `","` when no candidate splits anything.
 *
 * @param {string} text
 * @param {{quote?: string}} [options]
 * @returns {string} one of `","`, `";"`, `"\t"`, `"|"`
 */
export function detectDelimiter(text, options = {}) {
  const { quote = '"' } = resolveOptions(options);
  const source = String(text ?? "").replace(BOM, "");
  const quoteChar = resolveQuote(quote);

  let best = { delimiter: ",", score: -1 };

  for (const candidate of CANDIDATE_DELIMITERS) {
    const counts = countFieldsPerRecord(source, candidate, quoteChar);
    if (counts.length === 0) continue;

    const tally = new Map();
    for (const count of counts) tally.set(count, (tally.get(count) ?? 0) + 1);

    let modeFields = 0;
    let modeRows = 0;
    for (const [fields, rows] of tally) {
      if (rows > modeRows || (rows === modeRows && fields > modeFields)) {
        modeFields = fields;
        modeRows = rows;
      }
    }
    // A candidate that never splits a record into two fields is not a delimiter.
    if (modeFields < 2) continue;

    const consistency = modeRows / counts.length;
    const score = consistency * 1000 + modeFields;
    if (score > best.score) best = { delimiter: candidate, score };
  }

  return best.delimiter;
}

/**
 * Parse CSV text into rows of raw strings.
 *
 * @param {string} text
 * @param {object} [options]
 * @param {string} [options.delimiter="auto"] Delimiter, or `"auto"` to detect it.
 * @param {string} [options.quote='"'] Quote character.
 * @param {boolean} [options.skipEmptyLines=true] Drop records with no content.
 * @param {boolean} [options.trim=false] Trim surrounding whitespace on unquoted fields.
 * @param {boolean} [options.strict=false] Throw on malformed input instead of recovering.
 * @returns {{delimiter: string, rows: string[][]}}
 */
export function parseRows(text, options = {}) {
  const {
    delimiter = "auto",
    quote = '"',
    skipEmptyLines = true,
    trim = false,
    strict = false,
  } = resolveOptions(options);

  assertBoolean(skipEmptyLines, "skipEmptyLines");
  assertBoolean(trim, "trim");
  assertBoolean(strict, "strict");
  const quoteChar = resolveQuote(quote);
  const source = String(text ?? "").replace(BOM, "");
  const resolvedDelimiter =
    delimiter === "auto" ? detectDelimiter(source, { quote: quoteChar }) : resolveDelimiter(delimiter);

  /** @type {string[][]} */
  const rows = [];
  let row = [];
  let field = "";
  let fieldWasQuoted = false;
  let inQuotes = false;
  let line = 1;
  let column = 1;

  const pushField = () => {
    const value = trim && !fieldWasQuoted ? field.trim() : field;
    row.push(value);
    field = "";
    fieldWasQuoted = false;
  };

  const pushRow = () => {
    pushField();
    const isBlank = row.length === 1 && row[0] === "";
    if (!(skipEmptyLines && isBlank)) rows.push(row);
    row = [];
  };

  for (let i = 0; i < source.length; i += 1) {
    const ch = source[i];

    if (inQuotes) {
      if (ch === quoteChar) {
        if (source[i + 1] === quoteChar) {
          field += quoteChar;
          i += 1;
          column += 2;
          continue;
        }
        inQuotes = false;
        column += 1;
        continue;
      }
      field += ch;
      if (ch === "\n") {
        line += 1;
        column = 1;
      } else {
        column += 1;
      }
      continue;
    }

    if (ch === quoteChar && field === "") {
      inQuotes = true;
      fieldWasQuoted = true;
      column += 1;
      continue;
    }

    if (ch === quoteChar) {
      if (strict) {
        throw new CsvParseError("Unexpected quote character inside an unquoted field", { line, column });
      }
      field += ch;
      column += 1;
      continue;
    }

    if (ch === resolvedDelimiter) {
      pushField();
      column += 1;
      continue;
    }

    if (ch === "\r" || ch === "\n") {
      if (ch === "\r" && source[i + 1] === "\n") i += 1;
      pushRow();
      line += 1;
      column = 1;
      continue;
    }

    field += ch;
    column += 1;
  }

  if (inQuotes) {
    throw new CsvParseError("Unterminated quoted field", { line, column });
  }
  if (field !== "" || fieldWasQuoted || row.length > 0) pushRow();

  return { delimiter: resolvedDelimiter, rows };
}

/** Make header names unique and non-empty. */
function dedupeHeaders(headerRow, { trim = true } = {}) {
  const seen = new Map();
  return headerRow.map((raw, index) => {
    const name = trim ? raw.trim() : raw;
    const base = name === "" ? `column${index + 1}` : name;
    const count = (seen.get(base) ?? 0) + 1;
    seen.set(base, count);
    return count === 1 ? base : `${base}_${count}`;
  });
}

/**
 * Parse CSV text into a header row plus data rows.
 *
 * Short rows are padded with empty strings; longer rows are kept as they are,
 * and `toObjects` only maps the columns the header names. With `strict: true`,
 * any row whose width differs from the header throws.
 *
 * @param {string} text
 * @param {object} [options] Same options as {@link parseRows}, plus:
 * @param {boolean} [options.header=true] Treat the first record as the header.
 * @returns {{delimiter: string, headers: string[]|null, rows: string[][]}}
 */
export function parseCsv(text, options = {}) {
  const { header = true, ...rest } = resolveOptions(options);
  assertBoolean(header, "header");

  const { delimiter, rows } = parseRows(text, rest);
  if (!header) return { delimiter, headers: null, rows };

  const [headerRow, ...body] = rows;
  const headers = headerRow ? dedupeHeaders(headerRow) : [];
  const width = headers.length;

  const normalised = body.map((row, index) => {
    if (row.length === width) return row;
    if (row.length < width) return [...row, ...Array(width - row.length).fill("")];
    if (rest.strict) {
      throw new CsvParseError(
        `Row has ${row.length} fields but the header has ${width}`,
        { line: index + 2, column: 1 },
      );
    }
    return row;
  });

  return { delimiter, headers, rows: normalised };
}

/**
 * Map parsed rows onto plain objects keyed by header name.
 *
 * @param {string[]} headers
 * @param {string[][]} rows
 * @returns {Record<string, string>[]}
 */
export function toObjects(headers, rows) {
  if (!Array.isArray(headers)) {
    throw new TypeError(`headers must be an array, received ${describeValue(headers)}`);
  }
  if (!Array.isArray(rows)) {
    throw new TypeError(`rows must be an array, received ${describeValue(rows)}`);
  }
  return rows.map((row) => {
    /** @type {Record<string, string>} */
    const record = {};
    headers.forEach((header, index) => {
      record[header] = row[index] ?? "";
    });
    return record;
  });
}

/**
 * Turn plain objects into a header row and matching data rows.
 *
 * @param {Record<string, unknown>[]} objects
 * @param {{headers?: string[]}} [options]
 * @returns {{headers: string[], rows: string[][]}}
 */
export function fromObjects(objects, options = {}) {
  if (!Array.isArray(objects)) {
    throw new TypeError(`objects must be an array, received ${describeValue(objects)}`);
  }
  const { headers: given } = resolveOptions(options);
  if (given !== undefined && !Array.isArray(given)) {
    throw new TypeError(`headers must be an array, received ${describeValue(given)}`);
  }

  let headers = given;
  if (!headers) {
    const seen = new Set();
    for (const object of objects) {
      if (object === null || typeof object !== "object" || Array.isArray(object)) {
        throw new TypeError(`objects must contain plain objects, received ${describeValue(object)}`);
      }
      for (const key of Object.keys(object)) seen.add(key);
    }
    headers = [...seen];
  }

  const rows = objects.map((object) =>
    headers.map((header) => {
      const value = object?.[header];
      return value === null || value === undefined ? "" : String(value);
    }),
  );

  return { headers, rows };
}

function formatField(value, delimiter, quote, quoteAll) {
  const text = value === null || value === undefined ? "" : String(value);
  const needsQuoting =
    quoteAll ||
    text.includes(quote) ||
    text.includes(delimiter) ||
    text.includes("\r") ||
    text.includes("\n") ||
    /^\s|\s$/.test(text);

  if (!needsQuoting) return text;
  return `${quote}${text.split(quote).join(quote + quote)}${quote}`;
}

/**
 * Serialise rows back to CSV text.
 *
 * Fields are quoted only when they have to be (a delimiter, a quote, a line
 * break, or surrounding whitespace), unless `quoteAll` is set. The result ends
 * with a single trailing newline, which RFC 4180 permits and most tools expect.
 *
 * @param {(string[]|Record<string, unknown>)[]} rows Array rows, or objects when `headers` is given.
 * @param {object} [options]
 * @param {string} [options.delimiter=","]
 * @param {string} [options.quote='"']
 * @param {string} [options.newline="\n"]
 * @param {boolean} [options.quoteAll=false]
 * @param {string[]|null} [options.headers=null] Written as the first line; also picks object keys.
 * @returns {string}
 */
export function stringifyCsv(rows, options = {}) {
  const {
    delimiter = ",",
    quote = '"',
    newline = "\n",
    quoteAll = false,
    headers = null,
  } = resolveOptions(options);

  if (!Array.isArray(rows)) {
    throw new TypeError(`rows must be an array, received ${describeValue(rows)}`);
  }
  assertBoolean(quoteAll, "quoteAll");
  const delimiterChar = resolveDelimiter(delimiter);
  const quoteChar = resolveQuote(quote);
  if (typeof newline !== "string" || !/^(\r\n|\r|\n)$/.test(newline)) {
    throw new TypeError(`newline must be "\\n", "\\r\\n" or "\\r", received ${describeValue(newline)}`);
  }
  if (headers !== null && !Array.isArray(headers)) {
    throw new TypeError(`headers must be an array or null, received ${describeValue(headers)}`);
  }

  const lines = [];
  const format = (value) => formatField(value, delimiterChar, quoteChar, quoteAll);

  if (headers) lines.push(headers.map(format).join(delimiterChar));

  for (const row of rows) {
    const values = Array.isArray(row)
      ? row
      : headers
        ? headers.map((header) => row?.[header])
        : Object.values(row ?? {});
    lines.push(values.map(format).join(delimiterChar));
  }

  return lines.length === 0 ? "" : lines.join(newline) + newline;
}
