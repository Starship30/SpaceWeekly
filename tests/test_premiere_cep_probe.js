"use strict";
// Fake ExtendScript checks only, not a Premiere/MOGRT acceptance result.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const mapping = require("../plugins/premiere-uxp/mapping.json");
const { validatePackage, durationFrames } = require("../plugins/premiere-uxp/protocol.js");
const source = fs.readFileSync(require.resolve("../plugins/premiere-cep-probe/probe.jsx"), "utf8");
function fake({ duplicate = false, opaque = false, nonString = false, structuredReadback = false, mismatch = false, durationMismatch = false, setterResult = 0 } = {}) {
  const video = { clips: { numItems: 0 }, transitions: { numItems: 0 } };
  const audio = { clips: { numItems: 0 }, transitions: { numItems: 0 } };
  const fields = ["title", "summary"];
  const params = fields.map(field => {
    let value = nonString ? { value: "模板文字" } : opaque ? "{\"style\":1}" : "模板文字";
    return { displayName: mapping[field], isTimeVarying: () => false,
      getValue: () => value, setValue: text => {
        if (!mismatch) value = structuredReadback ? JSON.stringify({ textEditValue: text }) : text;
        return setterResult;
      } };
  });
  if (duplicate) params.push(params[0]); params.numItems = params.length;
  let inserted = 0;
  const clip = { nodeId: "clip", start: { ticks: "0" }, getMGTComponent: () => ({ properties: params }) };
  let end = { ticks: "999" };
  Object.defineProperty(clip, "end", { get: () => end, set: v => { if (!durationMismatch) end = v; } });
  const sequence = { sequenceID: "seq", name: "空序列", timebase: "8475667200",
    videoTracks: { 0: video, numTracks: 1 }, audioTracks: { 0: audio, numTracks: 1 },
    importMGT: (_path, ticks, v, a) => { assert.equal(ticks, "0"); assert.equal(v, 0); assert.equal(a, 0);
      inserted++; video.clips.numItems = 1; video.clips[0] = clip; return clip; } };
  const app = { version: "26.3.0", build: "93", project: { path: "test.prproj", activeSequence: sequence } };
  const context = { app, $: {}, File: function () { this.exists = true; }, Time: function () {} };
  vm.runInNewContext(source, context);
  return { probe: context.$._swSingleCard, app, sequence, inserted: () => inserted, params, clip };
}
const sample = validatePackage(JSON.parse(fs.readFileSync(require.resolve("../plugins/premiere-uxp/example.premiere.json"), "utf8")));
const frames = durationFrames(8, 30000 / 1001, 1);
const request = { path: "test.prproj", sequenceID: "seq", timebase: "8475667200", mogrt: "real-template.mogrt", mapping,
  frames, endTicks: String(frames * 8475667200), item: { ...sample.items[0], title: "中文标题\"测试", summary: "摘要\n第二行" } };
const h = fake();
assert.match(h.probe.bind(), /^OK/);
assert.match(h.probe.run(request), /PASS\tcomplete/);
assert.equal(h.inserted(), 1);
assert.equal(h.params[0].getValue(), request.item.title);
assert.equal(h.params[1].getValue(), request.item.summary);
assert.equal(h.clip.end.ticks, request.endTicks);
assert.match(h.probe.inspect(mapping), /PASS\tinspect/);
assert.equal(h.inserted(), 1);
assert.equal(h.params[0].getValue(), request.item.title);
assert.match(fake().probe.inspect(mapping), /FAIL\tinspect/);
const noReturn = fake({ setterResult: null });
assert.match(noReturn.probe.run(request), /PASS\tcomplete/);
const structuredInitial = fake({ opaque: true });
assert.match(structuredInitial.probe.run(request), /PASS\tcomplete/);
assert.equal(structuredInitial.params[0].getValue(), request.item.title);
const structuredResult = fake({ structuredReadback: true });
const stopped = structuredResult.probe.run(request);
assert.match(stopped, /FAIL\treadback-title/);
assert.ok(stopped.includes(encodeURIComponent(JSON.stringify({ textEditValue: request.item.title }))));
assert.equal(structuredResult.params[1].getValue(), "模板文字");
assert.equal(structuredResult.clip.end.ticks, "999");
assert.match(h.probe.run(request), /FAIL\ttarget/); assert.equal(h.inserted(), 1);
for (const [option, stage] of [[{ duplicate: true }, "controls"], [{ nonString: true }, "controls"],
  [{ opaque: true, mismatch: true }, "readback-title"],
  [{ mismatch: true }, "readback-title"], [{ setterResult: false }, "write-title"],
  [{ durationMismatch: true }, "duration"]]) {
  const failed = fake(option); assert.match(failed.probe.run(request), new RegExp(`FAIL\\t${stage}`));
  assert.equal(failed.inserted(), 1);
}
const changed = fake(); changed.sequence.sequenceID = "other";
assert.match(changed.probe.run(request), /FAIL\ttarget/); assert.equal(changed.inserted(), 0);
changed.app.version = "27.0.0"; assert.match(changed.probe.bind(), /^ERROR/);
console.log("PASS shared JSON/mapping, single card, Chinese text readback, duration, duplicate/opaque/target/failure guards. Fake host only.");

async function checkPanelStartup(installedRoot) {
  const path = require("node:path");
  const panelPath = require.resolve("../plugins/premiere-cep-probe/panel.js");
  const manifest = fs.readFileSync(path.join(path.dirname(panelPath), "CSXS/manifest.xml"), "utf8");
  assert.doesNotMatch(manifest, /<ScriptPath>/);
  assert.match(manifest, /<AutoVisible>false<\/AutoVisible>/);
  assert.doesNotMatch(manifest, /spaceweekly\.cep\.singlecard\.panel/);
  assert.equal((manifest.match(/Id="spaceweekly\.cep\.singlecard\.manual"/g) || []).length, 2);
  const elements = Object.fromEntries(["bind", "run", "inspect", "log", "target", "dedicated"].map(id => [id, { textContent: "", checked: true }]));
  const calls = [];
  vm.runInNewContext(fs.readFileSync(panelPath, "utf8"), {
    require, document: { getElementById: id => elements[id] },
    window: { __adobe_cep__: {
      getSystemPath: () => installedRoot || path.dirname(panelPath),
      evalScript: (script, callback) => {
        calls.push(script);
        callback("OK\t26.3.0\t93\ttest.prproj\tseq\tempty\t8475667200");
      }
    } }
  });
  assert.equal(calls.length, 0, "Opening the panel must not call the Premiere host");
  await elements.bind.onclick();
  assert.equal(calls.length, 1);
  assert.match(calls[0], /^\$\.evalFile\(new File\(/);
  assert.ok(calls[0].includes(JSON.stringify(path.join(path.dirname(panelPath), "probe.jsx"))));
  assert.match(calls[0], /\$\._swSingleCard\.bind\(\)/);
  assert.equal(JSON.parse(elements.target.textContent).sequenceID, "seq");
  console.log("PASS no host calls at panel startup; explicit button loads JSX. Fake panel only.");
}
async function checkPhysicalInstall() {
  await checkPanelStartup();
  const path = require("node:path"), os = require("node:os");
  const installedRoot = fs.mkdtempSync(path.join(os.tmpdir(), "spaceweekly-cep-probe-"));
  const sourceFile = path.join(installedRoot, "probe-source.json");
  try {
    fs.writeFileSync(sourceFile, "\uFEFF" + JSON.stringify({ source_dir: path.dirname(require.resolve("../plugins/premiere-cep-probe/panel.js")) }));
    await checkPanelStartup(installedRoot);
  } finally { fs.unlinkSync(sourceFile); fs.rmdirSync(installedRoot); }
}
checkPhysicalInstall().catch(error => { console.error(error); process.exitCode = 1; });
