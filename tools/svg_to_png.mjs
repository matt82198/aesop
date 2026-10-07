#!/usr/bin/env node
// INDEX: Rasterize SVG to PNG via @resvg/resvg-js (lazy import error handling)
/**
 * svg_to_png.mjs — Rasterize an SVG file to PNG using @resvg/resvg-js.
 *
 * Why this exists: on systems without rsvg-convert, ImageMagick, or Inkscape,
 * @resvg/resvg-js ships a prebuilt native binary via npm with zero extra system
 * deps, making it the reliable local converter for cross-platform automation.
 *
 * Usage:
 *   node svg_to_png.mjs <input.svg> <output.png> [width]
 *
 * If [width] is given, the SVG is rasterized at that pixel width (height keeps
 * aspect ratio unless the SVG sets an explicit height). Requires
 * @resvg/resvg-js to be installed (locally in the calling project, or
 * globally) — install with: npm install @resvg/resvg-js
 */
import { readFileSync, writeFileSync } from "node:fs";

let Resvg;
try {
  const resvgModule = await import("@resvg/resvg-js");
  Resvg = resvgModule.Resvg;
} catch (err) {
  console.error(
    "Error: @resvg/resvg-js is not installed.\n" +
    "Install it with: npm install @resvg/resvg-js\n" +
    "Or globally with: npm install -g @resvg/resvg-js"
  );
  process.exit(1);
}

const [, , inputPath, outputPath, widthArg] = process.argv;

if (!inputPath || !outputPath) {
  console.error("Usage: node svg_to_png.mjs <input.svg> <output.png> [width]");
  process.exit(1);
}

const svg = readFileSync(inputPath, "utf8");
const opts = {};
if (widthArg) {
  opts.fitTo = { mode: "width", value: parseInt(widthArg, 10) };
}

const resvg = new Resvg(svg, opts);
const pngData = resvg.render();
const buffer = pngData.asPng();
writeFileSync(outputPath, buffer);

console.log(`${outputPath}: ${pngData.width}x${pngData.height}`);
