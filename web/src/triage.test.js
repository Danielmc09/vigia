// `bun test` (make web-test): the pure helpers of the operator's triage console.
import { expect, test } from "bun:test";
import { ago, blank, clip, costPerCase, isToday, metric, shownColumns } from "./triage.js";

const t = (key, values = {}) => `${key}${JSON.stringify(values)}`;
// 2026-11-01 12:00 in Bogota (UTC-5, no daylight saving).
const NOW = new Date("2026-11-01T17:00:00Z");

test("a recent verdict reads in minutes", () => {
  expect(ago("2026-11-01T16:59:40Z", NOW, t)).toBe("panel.ago.now{}");
  expect(ago("2026-11-01T16:57:00Z", NOW, t)).toBe('panel.ago.min{"n":3}');
});

test("earlier today reads in hours, yesterday at its Bogota time, older by date", () => {
  expect(ago("2026-11-01T13:30:00Z", NOW, t)).toBe('panel.ago.hour{"n":3}');
  expect(ago("2026-10-31T22:43:00Z", NOW, t)).toBe('panel.ago.yesterday{"time":"17:43"}');
  expect(ago("2026-10-30T10:49:00Z", NOW, t)).toBe("10-30 05:49");
});

test("the cost per case is total spend over cases", () => {
  expect(costPerCase({ usd_total: 0.87948315, casos: 231 })).toBe("0.0038");
  expect(costPerCase({ usd_total: 0.5 })).toBe("");
});

test("rates read as percents, counts as integers", () => {
  expect(metric("recall_estafa", 0.9848484848484849)).toBe("98.5%");
  expect(metric("mediana_ms", 3217.6)).toBe("3218");
  expect(metric("tokens_total", 540087)).toBe("540087");
});

test("a column whose visible rows are all empty is not drawn", () => {
  const columns = [
    { key: "label", cell: (r) => r.label },
    { key: "brand", cell: (r) => r.brand || "" },
  ];
  const rows = [{ label: "scam" }, { label: "doubtful", brand: null }];

  expect(shownColumns(columns, rows).map((c) => c.key)).toEqual(["label"]);
  expect(shownColumns(columns, []).map((c) => c.key)).toEqual(["label", "brand"]);
});

test("blank covers the empty string, null and the empty list", () => {
  expect([blank(""), blank(null), blank([]), blank("x"), blank(0)]).toEqual([
    true, true, true, false, false,
  ]);
});

test("signals beyond the first two collapse into +N", () => {
  expect(clip(["a", "b", "c", "d"])).toEqual({ shown: ["a", "b"], more: 2 });
  expect(clip(["a"])).toEqual({ shown: ["a"], more: 0 });
});

test("today is the Colombia day the counters count, not the last 24 h", () => {
  expect(isToday("2026-11-01T05:01:00Z", NOW)).toBe(true); // 00:01 in Bogota
  expect(isToday("2026-11-01T04:59:00Z", NOW)).toBe(false); // 23:59 yesterday in Bogota
});
