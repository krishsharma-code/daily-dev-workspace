/**
 * form-validate — declarative, dependency-free form validation.
 *
 * Describe each field's rules once, then ask the validator for structured
 * errors. Works unchanged in the browser and in Node, with no DOM access.
 *
 *   import { createValidator } from "./form-validate.js";
 *
 *   const form = createValidator({
 *     email: ["required", "email"],
 *     password: [{ rule: "minLength", value: 8 }],
 *     confirm: [{ rule: "matchesField", field: "password" }],
 *   });
 *
 *   const result = form.validate({ email: "", password: "abc", confirm: "abc" });
 *   // result.valid === false
 *   // result.list[0] === { field, code, message, params }
 */

/** Pragmatic email shape: one @, no whitespace, a dot in the domain. */
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Default message per rule code. `{placeholders}` are filled from the rule
 * spec, plus `{field}` and `{label}`.
 */
export const defaultMessages = Object.freeze({
  required: "{label} is required.",
  email: "{label} must be a valid email address.",
  minLength: "{label} must be at least {value} characters.",
  maxLength: "{label} must be at most {value} characters.",
  min: "{label} must be at least {value}.",
  max: "{label} must be at most {value}.",
  integer: "{label} must be a whole number.",
  pattern: "{label} has an invalid format.",
  oneOf: "{label} must be one of the allowed options.",
  matchesField: "{label} must match {otherLabel}.",
  custom: "{label} is invalid.",
});

/**
 * Built-in rule implementations: `(value, spec, context) => boolean`.
 * `context` is `{ values, field }`. A rule may return a promise; only
 * `validateAsync` accepts that.
 */
export const rules = Object.freeze({
  required: (value) => !isBlank(value),
  email: (value) => EMAIL_RE.test(String(value)),
  minLength: (value, spec) => String(value).length >= spec.value,
  maxLength: (value, spec) => String(value).length <= spec.value,
  min: (value, spec) => {
    const number = toNumber(value);
    return Number.isFinite(number) && number >= spec.value;
  },
  max: (value, spec) => {
    const number = toNumber(value);
    return Number.isFinite(number) && number <= spec.value;
  },
  integer: (value) => {
    const number = toNumber(value);
    return Number.isFinite(number) && Number.isInteger(number);
  },
  pattern: (value, spec) => {
    // A /g or /y pattern carries state between calls; reset it every time.
    spec.value.lastIndex = 0;
    return spec.value.test(String(value));
  },
  oneOf: (value, spec) => spec.value.some((option) => String(option) === String(value)),
  matchesField: (value, spec, context) =>
    String(readField(context.values, spec.field) ?? "") === String(value ?? ""),
});

/** Rules that must still run when the value is blank. */
const BLANK_SAFE_RULES = new Set(["required"]);

/**
 * True for `null`, `undefined`, `false`, `NaN`, an empty or whitespace-only
 * string, and an empty array. `0` is a value, not a blank.
 */
export function isBlank(value) {
  if (value === null || value === undefined || value === false) return true;
  if (typeof value === "number") return Number.isNaN(value);
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

/** Read `"user.name"` out of a nested object, tolerating missing branches. */
export function readField(source, path) {
  if (source === null || source === undefined) return undefined;
  const parts = String(path).split(".");
  let current = source;
  for (const part of parts) {
    if (current === null || current === undefined) return undefined;
    current = current[part];
  }
  return current;
}

/** `"shipping.postCode"` → `"Post code"`, for default messages. */
export function humanizeField(field) {
  const leaf = String(field).split(".").pop();
  const words = String(leaf)
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim()
    .toLowerCase();
  return words ? words[0].toUpperCase() + words.slice(1) : String(leaf);
}

function toNumber(value) {
  if (typeof value === "number") return value;
  if (typeof value === "boolean") return Number(value);
  if (typeof value === "string") return value.trim() === "" ? NaN : Number(value);
  return NaN;
}

function isThenable(value) {
  return Boolean(value) && typeof value.then === "function";
}

function isPlainObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function render(template, params) {
  return String(template).replace(/\{(\w+)\}/g, (match, key) =>
    params[key] === undefined || params[key] === null ? match : String(params[key]),
  );
}

/**
 * Turn one user-supplied rule (string, function or object) into a compiled
 * spec: `{ name, impl, spec }`. Throws a TypeError naming the field and the
 * rule position when the spec is malformed.
 */
function compileRule(field, rawRule, position, config) {
  const where = `field "${field}" rule #${position + 1}`;
  let spec;

  if (typeof rawRule === "string") {
    spec = { rule: rawRule };
  } else if (typeof rawRule === "function") {
    spec = { rule: "custom", test: rawRule };
  } else if (isPlainObject(rawRule)) {
    spec = { ...rawRule };
  } else {
    throw new TypeError(
      `form-validate: ${where} must be a rule name, a function or an object, got ${typeof rawRule}.`,
    );
  }

  const isCustom = typeof spec.test === "function";
  const name = isCustom ? String(spec.code ?? spec.rule ?? "custom") : spec.rule;

  if (!isCustom && (typeof name !== "string" || name === "")) {
    throw new TypeError(`form-validate: ${where} needs a string "rule" or a "test" function.`);
  }
  if (isCustom && spec.test === null) {
    throw new TypeError(`form-validate: ${where} has a null "test".`);
  }

  const impl = isCustom ? spec.test : (config.rules[name] ?? rules[name]);
  if (typeof impl !== "function") {
    throw new TypeError(
      `form-validate: ${where} uses unknown rule "${name}". Pass it in options.rules to add one.`,
    );
  }

  if (spec.when !== undefined && typeof spec.when !== "function") {
    throw new TypeError(`form-validate: ${where} has a non-function "when".`);
  }

  // Rule-specific argument checks, so a typo fails at build time, not on the
  // first submit with a confusing message.
  const ruleName = isCustom ? null : name;
  if (ruleName === "minLength" || ruleName === "maxLength") {
    if (!Number.isInteger(spec.value) || spec.value < 0) {
      throw new TypeError(`form-validate: ${where} ("${name}") needs a non-negative integer "value".`);
    }
  }
  if (ruleName === "min" || ruleName === "max") {
    if (typeof spec.value !== "number" || Number.isNaN(spec.value)) {
      throw new TypeError(`form-validate: ${where} ("${name}") needs a numeric "value".`);
    }
  }
  if (ruleName === "pattern") {
    if (spec.value instanceof RegExp) {
      // Already compiled; keep it.
    } else if (typeof spec.value === "string") {
      try {
        spec.value = new RegExp(spec.value);
      } catch (error) {
        throw new TypeError(`form-validate: ${where} has an invalid pattern: ${error.message}`);
      }
    } else {
      throw new TypeError(`form-validate: ${where} ("pattern") needs a RegExp or a pattern string.`);
    }
  }
  if (ruleName === "oneOf" && (!Array.isArray(spec.value) || spec.value.length === 0)) {
    throw new TypeError(`form-validate: ${where} ("oneOf") needs a non-empty array "value".`);
  }
  if (ruleName === "matchesField" && (typeof spec.field !== "string" || spec.field === "")) {
    throw new TypeError(`form-validate: ${where} ("matchesField") needs the "field" to compare with.`);
  }

  return Object.freeze({ name, impl, spec: Object.freeze(spec), isCustom });
}

/**
 * Build a validator from a schema.
 *
 * @param {object} schema  field name → rule | rule[] (a rule is a name, a test
 *   function, or `{ rule, value?, message?, code?, when?, always?, ... }`).
 * @param {object} [options]
 * @param {boolean} [options.trim=true]   Trim strings before validating.
 * @param {boolean} [options.firstOnly=false]  Stop at the first error per field.
 * @param {object} [options.labels]      field → display label.
 * @param {object} [options.messages]    rule code → message template.
 * @param {object} [options.rules]       Extra or overriding rule implementations.
 * @param {string} [options.defaultMessage]  Fallback template for unknown codes.
 */
export function createValidator(schema, options = {}) {
  if (!isPlainObject(schema)) {
    throw new TypeError("form-validate: schema must be an object of field → rules.");
  }
  if (!isPlainObject(options)) {
    throw new TypeError("form-validate: options must be an object.");
  }

  const config = Object.freeze({
    trim: options.trim !== false,
    firstOnly: options.firstOnly === true,
    labels: Object.freeze({ ...(options.labels ?? {}) }),
    messages: Object.freeze({ ...(options.messages ?? {}) }),
    rules: Object.freeze({ ...(options.rules ?? {}) }),
    defaultMessage: options.defaultMessage ?? "{label} is invalid.",
  });

  const compiled = new Map();
  for (const [field, rawRules] of Object.entries(schema)) {
    const list = Array.isArray(rawRules) ? rawRules : [rawRules];
    if (list.length === 0) {
      throw new TypeError(`form-validate: field "${field}" has an empty rule list.`);
    }
    compiled.set(
      field,
      Object.freeze(list.map((rawRule, index) => compileRule(field, rawRule, index, config))),
    );
  }

  const fields = Object.freeze([...compiled.keys()]);

  function labelFor(field) {
    return config.labels[field] ?? humanizeField(field);
  }

  function readValue(values, field) {
    const raw = readField(values, field);
    return config.trim && typeof raw === "string" ? raw.trim() : raw;
  }

  function shouldRun(entry, values, field) {
    const { spec } = entry;
    if (typeof spec.when !== "function") return true;
    return Boolean(spec.when(values, field));
  }

  function runsOnBlank(entry) {
    return entry.isCustom ? entry.spec.always === true : BLANK_SAFE_RULES.has(entry.name);
  }

  function makeError(field, entry, value, values) {
    const { spec, name } = entry;
    const code = entry.isCustom ? String(spec.code ?? spec.rule ?? "custom") : String(spec.code ?? name);
    const params = { ...spec, field, label: labelFor(field) };
    delete params.test;
    delete params.when;
    if (params.value === undefined) delete params.value;
    if (name === "matchesField") params.otherLabel = labelFor(spec.field);
    params.value = params.value === undefined ? value : params.value;

    const template =
      spec.message ?? config.messages[code] ?? defaultMessages[code] ?? config.defaultMessage;

    return Object.freeze({ field, code, message: render(template, params), params });
  }

  /**
   * Yield `{ entry, value, ok }` for every rule that should run on a field.
   * `ok` may be a promise; the caller decides whether that is allowed.
   */
  function* ruleResults(field, values) {
    for (const entry of compiled.get(field)) {
      if (!shouldRun(entry, values, field)) continue;
      const value = readValue(values, field);
      if (isBlank(value) && !runsOnBlank(entry)) continue;
      const ok = entry.impl(value, entry.spec, { values, field });
      yield { entry, value, ok };
    }
  }

  function runFieldSync(field, values) {
    const found = [];
    for (const { entry, value, ok } of ruleResults(field, values)) {
      if (isThenable(ok)) {
        throw new TypeError(
          `form-validate: rule "${entry.name}" on field "${field}" returned a promise; use validateAsync().`,
        );
      }
      if (ok === true) continue;
      found.push(makeError(field, entry, value, values));
      if (config.firstOnly) break;
    }
    return found;
  }

  async function runFieldAsync(field, values) {
    const found = [];
    for (const { entry, value, ok } of ruleResults(field, values)) {
      if ((await ok) === true) continue;
      found.push(makeError(field, entry, value, values));
      if (config.firstOnly) break;
    }
    return found;
  }

  function assertField(field) {
    if (!compiled.has(field)) {
      throw new TypeError(
        `form-validate: unknown field "${field}". Known fields: ${fields.join(", ") || "(none)"}.`,
      );
    }
  }

  function assertValues(values) {
    if (!isPlainObject(values)) {
      throw new TypeError("form-validate: values must be an object keyed by field name.");
    }
  }

  function collect(errorsByField) {
    const list = [];
    for (const field of fields) {
      const fieldErrors = errorsByField[field];
      if (fieldErrors) list.push(...fieldErrors);
    }
    return Object.freeze(list);
  }

  function validate(values) {
    assertValues(values);
    const errors = {};
    for (const field of fields) {
      const fieldErrors = runFieldSync(field, values);
      if (fieldErrors.length) errors[field] = Object.freeze(fieldErrors);
    }
    const list = collect(errors);
    return { valid: list.length === 0, errors, list, values };
  }

  async function validateAsync(values) {
    assertValues(values);
    const errors = {};
    for (const field of fields) {
      const fieldErrors = await runFieldAsync(field, values);
      if (fieldErrors.length) errors[field] = Object.freeze(fieldErrors);
    }
    const list = collect(errors);
    return { valid: list.length === 0, errors, list, values };
  }

  function validateField(field, values) {
    assertField(field);
    assertValues(values);
    const fieldErrors = Object.freeze(runFieldSync(field, values));
    return { valid: fieldErrors.length === 0, errors: fieldErrors, values };
  }

  async function validateFieldAsync(field, values) {
    assertField(field);
    assertValues(values);
    const fieldErrors = Object.freeze(await runFieldAsync(field, values));
    return { valid: fieldErrors.length === 0, errors: fieldErrors, values };
  }

  return Object.freeze({
    fields,
    validate,
    validateAsync,
    validateField,
    validateFieldAsync,
    hasField: (field) => compiled.has(field),
    labelFor,
  });
}

export default createValidator;
