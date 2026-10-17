import { describe, expect, it } from "vitest";
import { fitView, project, tilesFor, unproject, type MapProviderInfo } from "./tiles";

const prov = (projection: "3857" | "3395"): MapProviderInfo => ({
  id: "p", name: "t", preset: "osm", projection, min_zoom: 1, max_zoom: 19, attribution: "", tile_url: "/t/{z}/{x}/{y}",
});

describe("проекции", () => {
  it.each(["3857", "3395"] as const)("project/unproject взаимно обратны (%s)", (proj) => {
    const p = { lat: 55.7558, lon: 37.6173 };
    const w = project(p, 12, proj);
    const back = unproject(w.x, w.y, 12, proj);
    expect(back.lat).toBeCloseTo(p.lat, 9);
    expect(back.lon).toBeCloseTo(p.lon, 9);
  });
  it("плитка Москвы на z=10 в Web Mercator — 619/320", () => {
    const w = project({ lat: 55.7558, lon: 37.6173 }, 10, "3857");
    expect([Math.floor(w.x / 256), Math.floor(w.y / 256)]).toEqual([619, 320]);
  });
  it("в проекции Яндекса (EPSG:3395) та же точка ниже по сетке", () => {
    const a = project({ lat: 55.7558, lon: 37.6173 }, 15, "3857");
    const b = project({ lat: 55.7558, lon: 37.6173 }, 15, "3395");
    expect(b.x).toBeCloseTo(a.x, 6);
    expect(b.y - a.y).toBeGreaterThan(100);
  });
});

describe("вид карты", () => {
  it("все точки помещаются в окно", () => {
    const pts = [{ lat: 55.745, lon: 37.59 }, { lat: 55.76, lon: 37.63 }];
    const v = fitView(pts, 600, 360, 40, "3857", 1, 19);
    for (const p of pts) {
      const w = project(p, v.z, "3857");
      expect(Math.abs(w.x - v.cx)).toBeLessThanOrEqual(300 - 39);
      expect(Math.abs(w.y - v.cy)).toBeLessThanOrEqual(180 - 39);
    }
  });
  it("плитки покрывают окно без пропусков", () => {
    const v = fitView([{ lat: 55.75, lon: 37.6 }], 600, 360, 40, "3857", 1, 19);
    const tiles = tilesFor({ ...v, z: 14.5 }, 600, 360, prov("3857"));
    expect(Math.min(...tiles.map((t) => t.x))).toBeLessThanOrEqual(0);
    expect(Math.max(...tiles.map((t) => t.x + t.size))).toBeGreaterThanOrEqual(600);
    expect(tiles.every((t) => t.href.startsWith("/t/14/"))).toBe(true);
  });
});
