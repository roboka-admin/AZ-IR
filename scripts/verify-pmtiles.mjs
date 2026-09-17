#!/usr/bin/env node
/**
 * Verify a PMTiles archive with the *reference* reader.
 *
 * Why this exists: the archive is written by our own Python encoder (`backend/src/azir/tiles/`). A
 * writer that only its own reader can understand is a writer that will produce a blank map, and the
 * failure is silent -- no exception, no log, just nothing on screen. So the check is done by
 * `pmtiles`, the JavaScript library MapLibre actually uses in the browser: if it can open the
 * archive, read the metadata and pull a tile out by (z, x, y), the format is right.
 *
 * It also validates an archive *over HTTP*, which is how it is served: the reader issues range
 * requests, so a proxy that swallows `Range` (or answers 200 where 206 is required) fails here
 * rather than in somebody's browser.
 *
 * Usage:
 *   node scripts/verify-pmtiles.mjs <archive.pmtiles | https://host/path/archive.pmtiles>
 *   node scripts/verify-pmtiles.mjs --tiles 6 backend/tiles/atlas-1.0.0-….pmtiles
 *
 * Exit code 0 means "a browser can render this"; 1 means it cannot, and stdout says why.
 */

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

/* ------------------------------------------------------------------ loading the reference reader */

async function loadPmtiles() {
  // `pmtiles` is a frontend dependency, and ESM resolves from the importing file's directory -- not
  // the working directory -- so the candidates are tried explicitly.
  const bases = ["../frontend/", "../", "./"];
  for (const base of bases) {
    try {
      const require = createRequire(new URL(base, import.meta.url));
      const entry = require.resolve("pmtiles");
      const mod = await import(pathToFileURL(entry).href);
      if (mod.PMTiles) return mod;
      if (mod.default?.PMTiles) return mod.default;
    } catch {
      /* try the next candidate */
    }
  }
  try {
    const mod = await import("pmtiles");
    return mod.PMTiles ? mod : mod.default;
  } catch (cause) {
    fail(
      `cannot load the reference reader ("pmtiles"). Run \`npm ci\` in frontend/, or set ` +
        `AZIR_PMTILES_MODULE to its entry file. (${cause.message})`,
    );
  }
  return fail("cannot load the reference reader");
}

/* ------------------------------------------------------------------ a minimal MVT sanity reader */

function readVarint(bytes, offset) {
  let result = 0;
  let shift = 0;
  let at = offset;
  for (;;) {
    const byte = bytes[at++];
    result |= (byte & 0x7f) << shift;
    if (!(byte & 0x80)) return [result >>> 0, at];
    shift += 7;
  }
}

/**
 * Read the top level of an MVT tile: enough to prove it is a tile, not to render it.
 * Returns the layers as `{ name, version, extent, features }`.
 */
function inspectMvt(bytes) {
  const layers = [];
  let offset = 0;
  while (offset < bytes.length) {
    let key;
    [key, offset] = readVarint(bytes, offset);
    const field = key >> 3;
    const wire = key & 0x7;
    if (wire !== 2) return fail(`unexpected wire type ${wire} in tile protobuf`);
    let length;
    [length, offset] = readVarint(bytes, offset);
    const payload = bytes.subarray(offset, offset + length);
    offset += length;
    if (field === 3) layers.push(inspectLayer(payload));
  }
  return layers;
}

function inspectLayer(bytes) {
  const layer = { name: null, version: null, extent: null, features: 0 };
  let offset = 0;
  while (offset < bytes.length) {
    let key;
    [key, offset] = readVarint(bytes, offset);
    const field = key >> 3;
    const wire = key & 0x7;
    if (wire === 2) {
      let length;
      [length, offset] = readVarint(bytes, offset);
      const payload = bytes.subarray(offset, offset + length);
      offset += length;
      if (field === 1) layer.name = new TextDecoder().decode(payload);
      else if (field === 2) layer.features += 1;
    } else if (wire === 0) {
      let value;
      [value, offset] = readVarint(bytes, offset);
      if (field === 5) layer.extent = value;
      else if (field === 15) layer.version = value;
    } else {
      return fail(`unexpected wire type ${wire} in layer protobuf`);
    }
  }
  return layer;
}

/* ------------------------------------------------------------------ the checks */

const failures = [];
const notes = [];

function check(condition, message) {
  if (condition) notes.push(`ok   ${message}`);
  else failures.push(`FAIL ${message}`);
  return Boolean(condition);
}

function fail(message) {
  failures.push(`FAIL ${message}`);
  throw new Error(message);
}

async function main() {
  const argv = process.argv.slice(2);
  let tilesToRead = 4;
  const targets = [];
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--tiles") {
      tilesToRead = Number.parseInt(argv[++i], 10);
    } else {
      targets.push(argv[i]);
    }
  }
  if (targets.length === 0) {
    console.error("usage: verify-pmtiles.mjs [--tiles N] <archive.pmtiles | https://…pmtiles>");
    process.exit(2);
  }

  const { PMTiles } = await loadPmtiles();

  for (const target of targets) {
    const remote = /^https?:\/\//.test(target);
    console.log(`\n== ${target} (${remote ? "over HTTP, range requests" : "from disk"})`);
    const source = remote
      ? target // PMTiles builds a FetchSource: exactly what the browser does
      : new BufferSource(readFileSync(target));
    const archive = new PMTiles(source);

    let header;
    try {
      header = await archive.getHeader();
    } catch (cause) {
      fail(`the reference reader could not open the archive: ${cause.message}`);
      continue;
    }
    check(header.specVersion === 3, `spec version is 3 (got ${header.specVersion})`);
    check(header.tileType === 1, `tile type is MVT (got ${header.tileType})`);
    check(
      header.tileCompression === 2 || header.tileCompression === 1,
      `tile compression is gzip or none (got ${header.tileCompression})`,
    );
    check(header.clustered === true, "tile data is clustered by tile id");
    check(header.minZoom <= header.maxZoom, `zoom range ${header.minZoom}..${header.maxZoom}`);
    check(header.numTileEntries > 0, `${header.numTileEntries} directory entries`);
    check(
      header.minLon < header.maxLon && header.minLat < header.maxLat,
      `bounds ${header.minLon},${header.minLat} .. ${header.maxLon},${header.maxLat}`,
    );
    check(
      header.minLon >= -180 && header.maxLon <= 180 && header.minLat >= -90 && header.maxLat <= 90,
      "bounds are inside the world",
    );

    let metadata;
    try {
      metadata = await archive.getMetadata();
    } catch (cause) {
      fail(`metadata could not be read: ${cause.message}`);
      continue;
    }
    const vectorLayers = metadata?.vector_layers;
    check(Array.isArray(vectorLayers) && vectorLayers.length > 0, "metadata has vector_layers");
    check(
      (vectorLayers ?? []).every((layer) => layer.id && layer.fields),
      "every vector layer declares an id and its fields",
    );
    check(typeof metadata?.version === "string", `tileset version is "${metadata?.version}"`);
    if (metadata?.azir) {
      check(
        typeof metadata.azir.data_revision === "string",
        `data revision ${metadata.azir.data_revision} (driver ${metadata.azir.driver})`,
      );
    }

    // Tiles spread over the zoom range, at the centre of the archive's own bounds: the archive
    // declares where its data is, so the check does not have to guess.
    const centreLon = (header.minLon + header.maxLon) / 2;
    const centreLat = (header.minLat + header.maxLat) / 2;
    const zooms = pickZooms(header.minZoom, header.maxZoom, tilesToRead);
    let read = 0;
    let features = 0;
    for (const zoom of zooms) {
      const [x, y] = tileOf(centreLon, centreLat, zoom);
      const response = await archive.getZxy(zoom, x, y);
      if (!response) {
        notes.push(`--   ${zoom}/${x}/${y} absent (allowed: an empty tile is simply not stored)`);
        continue;
      }
      const bytes = new Uint8Array(response.data);
      const layers = inspectMvt(bytes);
      const counted = layers.reduce((total, layer) => total + layer.features, 0);
      read += 1;
      features += counted;
      check(
        layers.every((layer) => layer.version === 2 && layer.extent === 4096 && layer.name),
        `${zoom}/${x}/${y}: ${bytes.length} bytes, ${layers.length} layer(s), ${counted} feature(s)`,
      );
    }
    check(read > 0, `read ${read} tile(s) containing ${features} feature(s)`);

    // A tile far outside the bounds must be reported absent, not served as an empty blob: that is
    // how the format distinguishes "nothing here" from "here is nothing".
    const [farX, farY] = tileOf(header.minLon - 40 > -180 ? header.minLon - 40 : header.maxLon + 40, centreLat, header.maxZoom);
    const absent = await archive.getZxy(header.maxZoom, farX, farY);
    check(absent === undefined || absent === null, "a tile outside the archive is reported absent");
  }

  console.log("");
  for (const note of notes) console.log(note);
  if (failures.length > 0) {
    console.error("");
    for (const failure of failures) console.error(failure);
    console.error(`\n${failures.length} check(s) failed: a browser would not render this archive.`);
    process.exit(1);
  }
  console.log(`\n${notes.length} checks passed: the reference reader can serve this archive.`);
}

/** A Buffer-backed Source, so a local file goes through the same reader code as a remote one. */
class BufferSource {
  constructor(buffer) {
    this.buffer = buffer;
  }

  getKey() {
    return `buffer:${this.buffer.length}`;
  }

  async getBytes(offset, length) {
    if (offset + length > this.buffer.length) {
      throw new Error(`range ${offset}+${length} is past the end of the archive`);
    }
    const slice = this.buffer.subarray(offset, offset + length);
    return { data: slice.buffer.slice(slice.byteOffset, slice.byteOffset + slice.byteLength) };
  }
}

function pickZooms(minZoom, maxZoom, count) {
  const zooms = new Set([minZoom, maxZoom]);
  const step = Math.max(1, Math.floor((maxZoom - minZoom) / Math.max(1, count - 1)));
  for (let zoom = minZoom + step; zoom < maxZoom && zooms.size < count; zoom += step) zooms.add(zoom);
  return [...zooms].sort((a, b) => a - b);
}

function tileOf(lon, lat, zoom) {
  const size = 2 ** zoom;
  const x = Math.floor(((lon + 180) / 360) * size);
  const sinLat = Math.sin((Math.max(Math.min(lat, 89.9), -89.9) * Math.PI) / 180);
  const y = Math.floor((0.5 - Math.log((1 + sinLat) / (1 - sinLat)) / (4 * Math.PI)) * size);
  return [Math.min(size - 1, Math.max(0, x)), Math.min(size - 1, Math.max(0, y))];
}

main().catch((cause) => {
  console.error(`FAIL ${cause.message}`);
  process.exit(1);
});
