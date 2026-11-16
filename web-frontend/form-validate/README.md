# form-validate

Declarative, dependency-free form validation. Describe each field's rules once
and get back **structured errors** — a field name, a stable code, a ready
message and the parameters behind it — instead of a bare `true`/`false` or a
first-error-only string.

It is deliberately DOM-free: no inputs, no event wiring, no framework. It runs
unchanged in the browser and in Node, so the same schema that drives the form
also drives the tests.

The awkward parts of real forms are handled here, once:

- **Blank values skip the rules that cannot judge them.** An optional
  `nickname` with `minLength: 5` must not fail because it is empty; only
  `required` (and rules marked `always`) run on a blank.
- **Whitespace-only counts as blank**, and trimming is on by default, so
  `"   "` fails `required` and `"  ab  "` measures as two characters.
- **`false` counts as blank**, which is what makes `required` work on a
  "I accept the terms" checkbox without a special-case rule.
- **All failures on a field are reported**, in rule order, unless you ask for
  the first only — so a password field can show all three of its problems.
- **Async rules are opt-in and loud.** `validate` throws if a rule returns a
  promise; `validateAsync` awaits it. A "this handle is taken" check cannot
  silently pass.
- **Bad schemas fail at build time**, naming the field and the rule position,
  rather than at 9 a.m. on launch day.

## What it does

| Member | Purpose |
| --- | --- |
| `createValidator(schema, options?)` | Compile a schema once; returns the validator. |
| `validate(values)` | Synchronous run over every field. |
| `validateAsync(values)` | Same, awaiting promise-returning rules. |
| `validateField(name, values)` | One field only — for blur/change handlers. |
| `validateFieldAsync(name, values)` | One field, async rules awaited. |
| `validator.fields` | Field names in schema order (frozen). |
| `validator.hasField(name)` | Whether the schema knows the field. |
| `validator.labelFor(name)` | The display label used in messages. |
| `rules` | The built-in rule implementations, for extension. |
| `defaultMessages` | The built-in message templates, for reference. |
| `isBlank(value)` | The blank test the validator uses. |
| `readField(source, path)` | Read `"user.email"` out of a nested object. |
| `humanizeField(field)` | `"shipping.postCode"` → `"Post code"`. |

### Result shape

`validate` and `validateAsync` both return:

```js
{
  valid: false,
  errors: {                      // only failing fields, in schema order
    password: [ { field, code, message, params }, ... ],
  },
  list: [ ...errors in field order... ],   // flat, for a summary banner
  values,                        // the object you passed in, untouched
}
```

`validateField` returns `{ valid, errors: [ ...for that field... ], values }`.

Each error is frozen and carries:

| Key | Meaning |
| --- | --- |
| `field` | The field name from the schema. |
| `code` | Stable identifier for styling/i18n: `"required"`, `"minLength"`, your `code`, … |
| `message` | The rendered message, from the template and its parameters. |
| `params` | `{ label, field, value, ...ruleSpec }` — everything the template used. |

`params` is what makes the output usable beyond the default English: map
`code` to your own translation and interpolate `params.value`.

## Schema

A schema is a plain object: field name → rule, or an array of rules. A rule is
one of three things:

| Form | Example | Notes |
| --- | --- | --- |
| Name | `"required"` | Any built-in name, or one you added via `options.rules`. |
| Function | `(value) => value.length < 40` | Shorthand for a custom test. |
| Object | `{ rule: "minLength", value: 2 }` | The full form; accepts `message`, `code`, `when`, `always`, and rule parameters. |

```js
const form = createValidator({
  fullName: ["required", { rule: "minLength", value: 2 }],
  email: ["required", "email"],
  password: [{ rule: "minLength", value: 8 }],
  confirmPassword: [{ rule: "matchesField", field: "password" }],
  age: ["integer", { rule: "min", value: 18 }],
  plan: ["required", { rule: "oneOf", value: ["free", "pro", "team"] }],
  terms: [{ rule: "required", message: "Please accept the terms." }],
});
```

### Built-in rules

| Rule | Parameter | Passes when |
| --- | --- | --- |
| `required` | — | The value is not blank (`false` counts as blank). |
| `email` | — | One `@`, no whitespace, a dot in the domain. Pragmatic, not RFC 5322. |
| `minLength` | `value` (integer) | `String(value).length >= value`. |
| `maxLength` | `value` (integer) | `String(value).length <= value`. |
| `min` | `value` (number) | The value coerces to a finite number `>= value`. |
| `max` | `value` (number) | The value coerces to a finite number `<= value`. |
| `integer` | — | The value coerces to a whole number. |
| `pattern` | `value` (RegExp or string) | The pattern matches `String(value)`. Global patterns are reset per run. |
| `oneOf` | `value` (non-empty array) | The value matches one option as a string. |
| `matchesField` | `field` | `String(other) === String(value)`; the message names the other field's label. |

Custom rules are just functions `(value, spec, context) => boolean`, where
`context` is `{ values, field }`:

```js
const form = createValidator(
  { amount: [{ rule: "multipleOf", value: 100 }] },
  { rules: { multipleOf: (value, spec) => Number(value) % spec.value === 0 } },
);
```

Passing `options.rules` also **overrides** a built-in of the same name, which is
how you swap in, say, a stricter email check without touching the schema.

### Async rules

Any rule may return a promise. Run it through `validateAsync` /
`validateFieldAsync`; a plain `validate` throws a `TypeError` telling you so.

```js
const form = createValidator({
  handle: [
    { rule: "required" },
    {
      code: "taken",
      test: async (value) => !(await isTaken(value)),
      message: "That {label} is already taken.",
    },
  ],
});

await form.validateFieldAsync("handle", { handle: "admin" });
```

### Per-rule options

| Key | Meaning |
| --- | --- |
| `message` | Template for this rule; overrides the global one. |
| `code` | Override the error `code` (defaults to the rule name). Two `pattern` rules can be told apart. |
| `when` | `(values, field) => boolean` — skip the rule when it returns false. |
| `always` | Custom rules only: also run on blank values (e.g. "pick at least one tag"). |

### Options

| Option | Default | Notes |
| --- | --- | --- |
| `trim` | `true` | Trim strings before validating. The input object is never rewritten. |
| `firstOnly` | `false` | Stop at the first error per field. |
| `labels` | `{}` | `field → label`, for messages. Defaults to `humanizeField`. |
| `messages` | `{}` | `code → template`, overriding `defaultMessages`. |
| `rules` | `{}` | Extra or overriding rule implementations. |
| `defaultMessage` | `"{label} is invalid."` | Fallback for codes with no template. |

### Message templates

Placeholders are filled from the rule spec plus `{field}` and `{label}`, so
`"{label} must be at least {value} characters."` renders as *"Password must be
at least 8 characters."* An unknown placeholder is left as-is rather than
printing `undefined`.

## Layout

```
web-frontend/form-validate/
├── README.md
├── form-validate.js            # implementation (ES module, no dependencies)
├── form-validate.css           # styles for the demo
├── index.html                  # runnable signup-form demo
├── package.json                # `npm test` runs the suite
└── form-validate.test.mjs      # node:test suite
```

## Try the demo

The demo is an ES module, so serve the folder over HTTP rather than opening the
file directly (module scripts are blocked on `file://`):

```bash
cd web-frontend/form-validate
python3 -m http.server 8000
# then open http://localhost:8000/
```

Fields validate on blur and on change; **Create account** runs the whole schema.
The *Handle* field is an async rule against a fake registry — `admin`, `root`
and `support` are taken, and you can watch the ~120 ms wait before the error
appears. The panel on the right shows exactly what the validator returned:
`valid`, and every `{ field, code, message }`. Press *Reset* to clear the marks.

## Run the tests

```bash
cd web-frontend/form-validate
node --test
```

39 tests, no third-party dependencies; Node 18+ is enough.

## Example

```js
import { createValidator } from "./form-validate.js";

const form = createValidator(
  {
    email: ["required", "email"],
    password: [
      { rule: "minLength", value: 8 },
      { rule: "pattern", value: /[A-Z]/, code: "uppercase",
        message: "{label} needs a capital letter." },
    ],
    confirmPassword: [{ rule: "matchesField", field: "password" }],
  },
  { labels: { confirmPassword: "Password confirmation" } },
);

const result = form.validate({
  email: "asha@example",
  password: "Abc",
  confirmPassword: "Abd",
});

result.valid;                       // false
result.list.map((e) => e.code);     // ["email", "minLength", "matchesField"]
result.errors.confirmPassword[0].message;
// "Password confirmation must match Password."

// One field, on blur — cheap and synchronous.
form.validateField("email", { email: "asha@example.com" }).valid; // true

// For a UI: paint each field's message, or the whole summary.
for (const field of form.fields) {
  const messages = (result.errors[field] ?? []).map((e) => e.message);
  render(field, messages);
}
```

## Behaviour notes

- **The values object is never mutated.** Trimming happens on the way into a
  rule, not on your data, so `validate` is safe to call on state you also
  render. Trim at the boundary if you store it.
- **Blank is a specific list:** `null`, `undefined`, `false`, `NaN`, empty and
  whitespace-only strings, and empty arrays. `0` and `"0"` are values, so
  `min: 0` passes on `0` and `required` fails only on real absence.
- **`false` is blank on purpose.** It makes a single `"required"` rule correct
  for a consent checkbox; if you need "a boolean must be present", use a custom
  rule with `always: true`.
- **Skipped, not failed.** A blank optional field skips `minLength`, `pattern`,
  `email` and friends entirely — they have nothing to judge. Only `required`,
  and custom rules with `always: true`, run on a blank.
- **Rules run in the order you wrote them**, and errors are reported in field
  order, then rule order. `firstOnly` truncates per field, not globally.
- **`oneOf` compares as strings**, so `<select>` values (`"3"`) match numeric
  options (`3`). It is a membership test, not a type check.
- **`min`/`max`/`integer` coerce, and reject what will not coerce.**
  `"18"` passes `min: 18`; `"eighteen"` fails it rather than comparing `NaN`.
- **`pattern` accepts a string and compiles it at build time**, so a broken
  regex throws once, with the field name, instead of on every keystroke.
  `lastIndex` is reset before each test, so `/g` and `/y` patterns are safe.
- **`email` is deliberately loose** — one `@`, no whitespace, a dot in the
  domain. Stricter or IDN-aware checks belong in `options.rules.email`.
- **Dotted field names read nested values** (`"shipping.postCode"`), and the
  label falls back to the last segment (`"Post code"`).
- **Everything returned is frozen**: `fields`, each field's error array, the
  flat `list`, and every error object. A UI cannot corrupt the validator's
  output by accident.
- **Errors, not exceptions, for invalid input.** Bad *schemas*, bad *options*
  and unknown *field names* throw a `TypeError` that names what is wrong;
  user data never throws.
