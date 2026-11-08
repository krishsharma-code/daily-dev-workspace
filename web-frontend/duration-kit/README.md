# duration-kit

ISO 8601 durations, done properly: a strict parser, a canonical formatter,
humanised labels, and calendar-aware date arithmetic. Dependency-free, an ES
module, and it runs unchanged in the browser and in Node.

A `Duration` keeps calendar units (years, months, weeks, days) and clock units
(hours, minutes, seconds) apart instead of collapsing everything into
milliseconds. That is the whole point: a month is not 30 days, so `P1M` and
`PT720H` are different values, and only `addDuration` decides what a month means
— by walking the calendar.

```js
import {
  parseDuration,
  formatDuration,
  humanizeDuration,
  addDuration,
  toMilliseconds,
} from "./duration-kit.js";

const sprint = parseDuration("P2W");
sprint.days;                                  // 0 — it stays two weeks
formatDuration(sprint);                       // "P2W"
humanizeDuration(sprint);                     // "2 weeks"
humanizeDuration(sprint, { style: "narrow" }); // "2w"

addDuration(new Date("2024-01-31T00:00:00Z"), "P1M").toISOString();
// "2024-02-29T00:00:00.000Z" — clamped, not overflowed into March

toMilliseconds("P1Y");                        // 31556952000 (mean Gregorian year)
```

## API

| Function | Purpose |
| --- | --- |
| `parseDuration(text)` | Parse `P1Y2M3DT4H5M6S`, `P2W`, `-PT30M`, `PT0.5S`. Returns a `Duration`; throws `SyntaxError` when invalid. |
| `formatDuration(input)` | Canonical ISO 8601 string. Zero units are dropped; an empty duration is `PT0S`. |
| `humanizeDuration(input, options)` | `"1 year, 2 months, 3 days"`. Options: `style` (`long`/`short`/`narrow`), `maxUnits`, `separator`, `zero`. |
| `addDuration(date, input)` | New `Date` with the duration applied; calendar-aware for years and months. |
| `toMilliseconds(input)` | Approximate span in ms — exact for weeks and smaller, mean-length for years and months. |
| `fromMilliseconds(ms)` | Inverse for clock spans, decomposed into days/hours/minutes/seconds. |
| `compareDurations(a, b)` | `-1` / `0` / `1` by approximate length. |
| `Duration` | The class. `Duration.parse`, `Duration.from`, `Duration.fromMilliseconds`, `.toObject()`, `.toMilliseconds()`, `.negate()`, `.equals()`, `.isEmpty`, `.toString()`, `.toJSON()`. |

Every function accepts a `Duration`, an ISO 8601 string, a plain units object
(`{ hours: 1.5 }`) or a number of milliseconds, so call sites stay short.

## What the parser enforces

These are ISO 8601 rules, not house style — each one rejects a string that a
naive `/(\d+)Y(\d+)M/` regex would happily accept:

- at least one component is required (`P` and `PT` are errors), and a `T` must
  be followed by a time component (`P1DT` is an error);
- a decimal fraction may appear only on the smallest component present, so
  `PT1.5H` and `PT0,25S` are valid while `P1.5Y2M` is not;
- `W` cannot be combined with any other component (`P1W2D` is an error);
- the sign is `-`, `+`, or absent; whitespace around the string is trimmed.

A zero duration always normalises to a positive sign, so `-PT0S` cannot exist.

## Semantics worth knowing

- **Units are never normalised.** `PT90M` stays 90 minutes and formats back as
  `PT90M`; `PT1H30M` stays as written. Nothing is rebalanced into larger units
  behind your back.
- **Immutable values.** A `Duration` is frozen; `negate()` returns a new one.
  Every unit is a non-negative finite number and the direction lives in `sign`.
- **Calendar arithmetic is UTC.** `addDuration` applies years and months with
  `setUTCMonth`, so the result never depends on the machine's time zone or on a
  daylight-saving shift. Day-of-month overflow is clamped: 31 January + `P1M` is
  29 February in a leap year and 28 February otherwise, and 29 February + `P1Y`
  is 28 February.
- **Fractional years and months** cannot be walked on a calendar, so
  `addDuration` applies the whole part on the calendar and the fraction at the
  mean length used by `toMilliseconds`.
- **Approximations are labelled as such.** `toMilliseconds` uses 365.2425 days
  per year and a twelfth of that per month. Days, weeks, hours, minutes and
  seconds are exact.
- **`toJSON` emits the ISO string**, so a duration survives `JSON.stringify`
  and a round trip through storage.

## Layout

```
web-frontend/duration-kit/
├── README.md
├── duration-kit.js         # implementation (ES module)
├── duration-kit.css        # styles for the demo
├── index.html              # runnable interactive demo
├── package.json            # `npm test` runs the suite
└── duration-kit.test.mjs   # node:test suite
```

## Try the demo

The demo is an ES module, so serve the folder over HTTP rather than opening the
file directly (module scripts are blocked on `file://`):

```bash
cd web-frontend/duration-kit
python3 -m http.server 8000
# then open http://localhost:8000/
```

Type a duration or pick a preset chip, switch the human style and the number of
units shown, and change the base date to watch the calendar arithmetic — set the
base to `2024-01-31T23:30:00` and try `P1M` to see the day clamp in action.

## Run the tests

```bash
cd web-frontend/duration-kit
node --test
```

No third-party dependencies; Node 18+ is enough. The suite covers the grammar
(valid forms and every rejection above), format/parse round trips, all three
human styles with pluralisation and `maxUnits`, millisecond exactness for clock
units, and calendar edge cases — leap days, month-end clamping, negative
durations, and fractional units.

## Use it in a page

```html
<script type="module">
  import { parseDuration, humanizeDuration, addDuration } from "./duration-kit.js";

  // "Your trial ends in 14 days"
  const trial = parseDuration("P14D");
  const endsAt = addDuration(new Date(), trial);
  document.querySelector("#trial").textContent =
    `Your trial ends on ${endsAt.toLocaleDateString("en-IN")} ` +
    `(${humanizeDuration(trial)} from now)`;

  // Turn a duration from an API into a timer label
  function label(iso) {
    try {
      return humanizeDuration(iso, { style: "short", maxUnits: 2 });
    } catch {
      return "unknown duration";   // SyntaxError: the API sent something odd
    }
  }

  label("PT1H45M");  // "1 hr 45 mins"
</script>
```
