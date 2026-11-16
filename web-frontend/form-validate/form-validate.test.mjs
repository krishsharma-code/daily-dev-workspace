import test from "node:test";
import assert from "node:assert/strict";

import createValidator, {
  createValidator as named,
  defaultMessages,
  humanizeField,
  isBlank,
  readField,
  rules,
} from "./form-validate.js";

test("exports the factory as both default and named", () => {
  assert.equal(createValidator, named);
  assert.equal(typeof defaultMessages.required, "string");
  assert.equal(typeof rules.required, "function");
});

test("isBlank treats null, undefined, false, NaN, empty and whitespace as blank", () => {
  for (const value of [null, undefined, false, NaN, "", "   ", [], "\n\t"]) {
    assert.equal(isBlank(value), true, `${String(value)} should be blank`);
  }
  for (const value of [0, -1, "0", "x", true, [0], {}]) {
    assert.equal(isBlank(value), false, `${String(value)} should not be blank`);
  }
});

test("readField walks dotted paths and tolerates missing branches", () => {
  const source = { user: { name: "Asha", address: null } };
  assert.equal(readField(source, "user.name"), "Asha");
  assert.equal(readField(source, "user.address.city"), undefined);
  assert.equal(readField(source, "nope.deep"), undefined);
  assert.equal(readField(null, "user.name"), undefined);
});

test("humanizeField turns camelCase and snake_case into a label", () => {
  assert.equal(humanizeField("firstName"), "First name");
  assert.equal(humanizeField("shipping.postCode"), "Post code");
  assert.equal(humanizeField("confirm_password"), "Confirm password");
  assert.equal(humanizeField("email"), "Email");
});

test("a valid object passes with no errors", () => {
  const form = createValidator({
    name: ["required", { rule: "minLength", value: 2 }],
    email: ["required", "email"],
  });
  const result = form.validate({ name: "Asha", email: "asha@example.com" });
  assert.equal(result.valid, true);
  assert.deepEqual(result.list, []);
  assert.deepEqual(result.errors, {});
  assert.equal(result.values.name, "Asha");
});

test("required fails on blank values and reports a humanised label", () => {
  const form = createValidator({ firstName: ["required"] });
  const result = form.validate({ firstName: "   " });
  assert.equal(result.valid, false);
  assert.equal(result.list.length, 1);
  assert.equal(result.errors.firstName[0].code, "required");
  assert.equal(result.errors.firstName[0].message, "First name is required.");
});

test("non-required rules are skipped for blank values", () => {
  const form = createValidator({
    nickname: [{ rule: "minLength", value: 5 }, "email"],
  });
  assert.equal(form.validate({ nickname: "" }).valid, true);
  assert.equal(form.validate({}).valid, true);
  assert.equal(form.validate({ nickname: "abc" }).valid, false);
});

test("required fails a false checkbox value", () => {
  const form = createValidator({ terms: ["required"] });
  assert.equal(form.validate({ terms: false }).valid, false);
  assert.equal(form.validate({ terms: true }).valid, true);
});

test("zero is a value, so min(0) passes on 0", () => {
  const form = createValidator({ qty: [{ rule: "min", value: 0 }] });
  assert.equal(form.validate({ qty: 0 }).valid, true);
  assert.equal(form.validate({ qty: -1 }).valid, false);
});

test("multiple failures on one field are all collected, in rule order", () => {
  const form = createValidator({
    password: [
      { rule: "minLength", value: 8 },
      { rule: "pattern", value: /\d/ },
      { rule: "pattern", value: /[A-Z]/, code: "uppercase", message: "{label} needs a capital letter." },
    ],
  });
  const result = form.validate({ password: "abc" });
  assert.deepEqual(
    result.list.map((error) => error.code),
    ["minLength", "pattern", "uppercase"],
  );
  assert.equal(result.list[2].message, "Password needs a capital letter.");
});

test("firstOnly stops at the first error per field", () => {
  const form = createValidator(
    { password: [{ rule: "minLength", value: 8 }, { rule: "pattern", value: /\d/ }] },
    { firstOnly: true },
  );
  const result = form.validate({ password: "abc" });
  assert.equal(result.list.length, 1);
  assert.equal(result.list[0].code, "minLength");
});

test("errors are keyed by field and listed in schema order", () => {
  const form = createValidator({ a: ["required"], b: ["required"], c: ["required"] });
  const result = form.validate({ b: "set" });
  assert.deepEqual(Object.keys(result.errors), ["a", "c"]);
  assert.deepEqual(result.list.map((error) => error.field), ["a", "c"]);
});

test("trim is on by default and can be turned off", () => {
  const strict = createValidator({ code: [{ rule: "minLength", value: 3 }] });
  assert.equal(strict.validate({ code: "  ab  " }).valid, false);

  const raw = createValidator({ code: [{ rule: "minLength", value: 3 }] }, { trim: false });
  assert.equal(raw.validate({ code: "  ab  " }).valid, true, "6 raw characters");

  // The same input measures differently once padding is trimmed away.
  const padded = { code: "  ab  " };
  const maxed = createValidator({ code: [{ rule: "maxLength", value: 3 }] });
  assert.equal(maxed.validate(padded).valid, true, "2 trimmed characters");
  const maxedRaw = createValidator({ code: [{ rule: "maxLength", value: 3 }] }, { trim: false });
  assert.equal(maxedRaw.validate(padded).valid, false, "6 raw characters");
});

test("the input values object is never mutated", () => {
  const values = { email: "  asha@example.com  " };
  const form = createValidator({ email: ["email"] });
  form.validate(values);
  assert.equal(values.email, "  asha@example.com  ");
});

test("email accepts ordinary addresses and rejects malformed ones", () => {
  const form = createValidator({ email: ["email"] });
  for (const good of ["a@b.co", "first.last+tag@sub.example.org"]) {
    assert.equal(form.validate({ email: good }).valid, true, good);
  }
  for (const bad of ["plain", "a@b", "@b.co", "a b@c.co", "a@b.c d"]) {
    assert.equal(form.validate({ email: bad }).valid, false, bad);
  }
});

test("length rules measure the normalised string", () => {
  const form = createValidator({
    code: [
      { rule: "minLength", value: 3 },
      { rule: "maxLength", value: 5 },
    ],
  });
  assert.equal(form.validate({ code: "  abc  " }).valid, true);
  assert.equal(form.validate({ code: "ab" }).valid, false);
  assert.equal(form.validate({ code: "abcdef" }).valid, false);
});

test("numeric rules coerce numeric strings and reject non-numbers", () => {
  const form = createValidator({ age: [{ rule: "min", value: 18 }, { rule: "max", value: 120 }] });
  assert.equal(form.validate({ age: "18" }).valid, true);
  assert.equal(form.validate({ age: 17 }).valid, false);
  assert.equal(form.validate({ age: 121 }).valid, false);
  assert.equal(form.validate({ age: "eighteen" }).valid, false);
});

test("integer rejects fractions and non-numeric text", () => {
  const form = createValidator({ seats: ["integer"] });
  assert.equal(form.validate({ seats: "4" }).valid, true);
  assert.equal(form.validate({ seats: 4.5 }).valid, false);
  assert.equal(form.validate({ seats: "4a" }).valid, false);
});

test("a global pattern does not carry lastIndex between runs", () => {
  const form = createValidator({ code: [{ rule: "pattern", value: /ab/g }] });
  for (let i = 0; i < 4; i += 1) {
    assert.equal(form.validate({ code: "ab" }).valid, true, `run ${i}`);
  }
});

test("a pattern string is compiled, and an invalid one throws at build time", () => {
  const form = createValidator({ code: [{ rule: "pattern", value: "^[A-Z]{2}\\d{3}$" }] });
  assert.equal(form.validate({ code: "MH123" }).valid, true);
  assert.equal(form.validate({ code: "mh123" }).valid, false);
  assert.throws(() => createValidator({ code: [{ rule: "pattern", value: "(" }] }), TypeError);
});

test("oneOf compares loosely so select values can be strings or numbers", () => {
  const form = createValidator({ plan: [{ rule: "oneOf", value: ["free", "pro", 3] }] });
  assert.equal(form.validate({ plan: "pro" }).valid, true);
  assert.equal(form.validate({ plan: 3 }).valid, true);
  assert.equal(form.validate({ plan: "3" }).valid, true);
  assert.equal(form.validate({ plan: "enterprise" }).valid, false);
});

test("matchesField compares against another field and names it in the message", () => {
  const form = createValidator({
    password: [{ rule: "minLength", value: 4 }],
    confirmPassword: [{ rule: "matchesField", field: "password" }],
  });
  assert.equal(form.validate({ password: "abcd", confirmPassword: "abcd" }).valid, true);
  const result = form.validate({ password: "abcd", confirmPassword: "abce" });
  assert.equal(result.valid, false);
  assert.equal(result.list[0].message, "Confirm password must match Password.");
});

test("matchesField compares numbers and blanks without throwing", () => {
  const validator = createValidator({ a: ["required"], b: [{ rule: "matchesField", field: "a" }] });
  assert.equal(validator.validate({ a: 5, b: "5" }).valid, true);
  assert.equal(validator.validate({ a: 5, b: "" }).valid, true, "blank b is skipped");
});

test("when gates a rule on other values", () => {
  const form = createValidator({
    country: ["required"],
    state: [{ rule: "required", when: (values) => values.country === "IN" }],
  });
  assert.equal(form.validate({ country: "IN", state: "" }).valid, false);
  assert.equal(form.validate({ country: "SG", state: "" }).valid, true);
});

test("always:true makes a custom rule run on blank values", () => {
  const form = createValidator({
    tags: [
      { code: "atLeastOne", always: true, test: (value) => Array.isArray(value) && value.length > 0, message: "Pick at least one tag." },
    ],
  });
  const result = form.validate({ tags: [] });
  assert.equal(result.valid, false);
  assert.equal(result.list[0].message, "Pick at least one tag.");
});

test("function shorthand registers a custom test", () => {
  const form = createValidator({ handle: [(value) => String(value).startsWith("@")] });
  assert.equal(form.validate({ handle: "@asha" }).valid, true);
  assert.equal(form.validate({ handle: "asha" }).valid, false);
});

test("custom rules can be added through options.rules", () => {
  const form = createValidator(
    { amount: [{ rule: "multipleOf", value: 100 }] },
    { rules: { multipleOf: (value, spec) => Number(value) % spec.value === 0 } },
  );
  assert.equal(form.validate({ amount: 500 }).valid, true);
  assert.equal(form.validate({ amount: 550 }).valid, false);
});

test("options.rules can override a built-in rule", () => {
  const form = createValidator(
    { email: ["email"] },
    { rules: { email: (value) => value === "admin@example.com" } },
  );
  assert.equal(form.validate({ email: "admin@example.com" }).valid, true);
  assert.equal(form.validate({ email: "asha@example.com" }).valid, false);
});

test("messages can be overridden per rule and per field", () => {
  const form = createValidator(
    { email: ["email"], name: [{ rule: "required", message: "We need a name." }] },
    { messages: { email: "{label} looks off." }, labels: { email: "Work email" } },
  );
  const result = form.validate({ email: "nope", name: "" });
  assert.equal(result.errors.email[0].message, "Work email looks off.");
  assert.equal(result.errors.name[0].message, "We need a name.");
});

test("message templates receive the rule parameters", () => {
  const form = createValidator({ age: [{ rule: "min", value: 18 }] });
  const error = form.validate({ age: 12 }).list[0];
  assert.equal(error.message, "Age must be at least 18.");
  assert.equal(error.params.value, 18);
  assert.equal(error.params.field, "age");
  assert.equal(error.params.label, "Age");
});

test("an unknown placeholder is left alone rather than printing undefined", () => {
  const form = createValidator({ x: [{ rule: "required", message: "{label} {missing} here" }] });
  assert.equal(form.validate({ x: "" }).list[0].message, "X {missing} here");
});

test("dotted field names read nested values", () => {
  const form = createValidator({ "user.email": ["required", "email"] });
  const result = form.validate({ user: { email: "asha@example.com" } });
  assert.equal(result.valid, true);
  assert.equal(form.validate({ user: {} }).errors["user.email"][0].code, "required");
  assert.equal(form.labelFor("user.email"), "Email");
});

test("validateField checks one field and ignores the rest", () => {
  const form = createValidator({ a: ["required"], b: ["required"] });
  const result = form.validateField("b", { a: "", b: "set" });
  assert.equal(result.valid, true);
  assert.deepEqual(result.errors, []);
  assert.equal(form.validateField("a", {}).errors[0].code, "required");
});

test("validateAsync awaits promise-returning rules", async () => {
  const form = createValidator({
    handle: [
      {
        code: "taken",
        test: async (value) => {
          await new Promise((resolve) => setTimeout(resolve, 5));
          return value !== "admin";
        },
        message: "{label} is taken.",
      },
    ],
  });
  assert.equal((await form.validateAsync({ handle: "asha" })).valid, true);
  const result = await form.validateAsync({ handle: "admin" });
  assert.equal(result.valid, false);
  assert.equal(result.list[0].message, "Handle is taken.");
});

test("validate throws a clear error when a rule returns a promise", () => {
  const form = createValidator({ handle: [{ test: async () => true }] });
  assert.throws(() => form.validate({ handle: "x" }), /use validateAsync/);
});

test("validateFieldAsync resolves async rules for one field", async () => {
  const form = createValidator({
    a: [{ test: async (value) => value === "ok" }],
    b: ["required"],
  });
  assert.equal((await form.validateFieldAsync("a", { a: "ok" })).valid, true);
  assert.equal((await form.validateFieldAsync("a", { a: "no" })).valid, false);
});

test("malformed schemas and options fail at build time", () => {
  assert.throws(() => createValidator(null), /schema must be an object/);
  assert.throws(() => createValidator({ a: [] }), /empty rule list/);
  assert.throws(() => createValidator({ a: [42] }), /field "a" rule #1/);
  assert.throws(() => createValidator({ a: ["nope"] }), /unknown rule "nope"/);
  assert.throws(() => createValidator({ a: [{ rule: "minLength" }] }), /non-negative integer/);
  assert.throws(() => createValidator({ a: [{ rule: "min", value: "18" }] }), /numeric "value"/);
  assert.throws(() => createValidator({ a: [{ rule: "oneOf", value: [] }] }), /non-empty array/);
  assert.throws(() => createValidator({ a: [{ rule: "matchesField" }] }), /needs the "field"/);
  assert.throws(() => createValidator({ a: [{ rule: "required", when: "yes" }] }), /non-function "when"/);
  assert.throws(() => createValidator({ a: ["required"] }, "no"), /options must be an object/);
});

test("bad runtime arguments name the problem", () => {
  const form = createValidator({ a: ["required"] });
  assert.throws(() => form.validate(null), /values must be an object/);
  assert.throws(() => form.validateField("zzz", {}), /unknown field "zzz"/);
  assert.equal(form.hasField("a"), true);
  assert.equal(form.hasField("zzz"), false);
  assert.deepEqual(form.fields, ["a"]);
});

test("errors and field lists are frozen so a UI cannot corrupt them", () => {
  const form = createValidator({ a: ["required"] });
  const result = form.validate({});
  assert.equal(Object.isFrozen(result.list), true);
  assert.equal(Object.isFrozen(result.errors.a), true);
  assert.equal(Object.isFrozen(result.list[0]), true);
  assert.equal(Object.isFrozen(form.fields), true);
});
