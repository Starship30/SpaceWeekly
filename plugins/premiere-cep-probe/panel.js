"use strict";
// Directly use the same native calls as Adobe CSInterface; no service or bridge.
try {
const fs = require("fs"), path = require("path"), crypto = require("crypto");
const extensionRoot = fs.realpathSync(decodeURI(window.__adobe_cep__.getSystemPath("extension")).replace(/^file:\/\/\//, ""));
const sourceFile = path.join(extensionRoot, "probe-source.json");
const sourceDir = fs.existsSync(sourceFile) ? JSON.parse(fs.readFileSync(sourceFile, "utf8").replace(/^\uFEFF/, "")).source_dir : extensionRoot;
if (typeof sourceDir !== "string" || !path.isAbsolute(sourceDir)) throw Error("实验源码目录无效，请重新安装面板");
const root = fs.realpathSync(sourceDir);
const { validatePackage, durationFrames } = require(path.join(root, "../premiere-uxp/protocol.js"));
const mapping = require(path.join(root, "../premiere-uxp/mapping.json"));
const el = id => document.getElementById(id);
let bound, busy = false;
const log = message => { el("log").textContent += `${message}\n`; };
// Load host code only after an explicit button click, not while Premiere opens a project.
const host = script => new Promise((resolve, reject) => window.__adobe_cep__.evalScript(
  `$.evalFile(new File(${JSON.stringify(path.join(root, "probe.jsx"))}));${script}`, text => {
  if (!text || text === "EvalScript error.") reject(Error("ExtendScript 未执行，停止并检查宿主"));
  else resolve(text);
}));
el("inspect").onclick = async () => {
  if (busy) return;
  busy = true;
  try {
    const result = await host(`$._swSingleCard.inspect(${JSON.stringify(mapping)})`);
    const lines = result.trim().split("\n").map(line => {
      const [status, stage, message] = line.split("\t");
      return { status, stage, message: decodeURIComponent(message || "") };
    });
    const output = path.resolve(root, "../../output"); fs.mkdirSync(output, { recursive: true });
    const file = path.join(output, `cep-text-inspection-${Date.now()}.json`);
    fs.writeFileSync(file, JSON.stringify({ read_only: true, steps: lines }, null, 2), { encoding: "utf8", flag: "wx" });
    log(`只读诊断记录：${file}`);
    lines.forEach(step => log(`${step.status} ${step.stage}: ${step.message}`));
  } catch (error) { log(`只读诊断停止：${error.message}`); } finally { busy = false; }
};
el("bind").onclick = async () => {
  if (busy) return;
  busy = true; bound = null;
  try {
    if (!el("dedicated").checked) throw Error("请勾选专用空序列声明");
    const result = (await host("$._swSingleCard.bind()" )).split("\t").map(decodeURIComponent);
    if (result[0] !== "OK") throw Error(result.slice(1).join(" "));
    bound = { version: result[1], build: result[2], path: result[3], sequenceID: result[4], name: result[5], timebase: result[6] };
    el("target").textContent = JSON.stringify(bound, null, 2);
  } catch (error) { log(error.message); } finally { busy = false; }
};
el("run").onclick = async () => {
  if (busy) return;
  busy = true; el("run").disabled = true;
  let record, recordPath;
  try {
    if (!bound || !el("dedicated").checked) throw Error("请先声明并绑定专用空序列");
    const json = el("json").files[0], mogrt = el("mogrt").files[0];
    if (!json || !mogrt || !/\.mogrt$/i.test(mogrt.path)) throw Error("请选择 JSON 和 MOGRT");
    const data = validatePackage(JSON.parse(fs.readFileSync(json.path, "utf8").replace(/^\uFEFF/, "")));
    const ticksPerFrame = Number(bound.timebase);
    if (!Number.isSafeInteger(ticksPerFrame) || ticksPerFrame <= 0) throw Error("序列 timebase 无效");
    const frames = durationFrames(Number(el("seconds").value), 254016000000 / ticksPerFrame, 1);
    const endTicks = frames * ticksPerFrame;
    if (!Number.isSafeInteger(endTicks)) throw Error("单卡时间超出安全整数范围");
    const output = path.resolve(root, "../../output"); fs.mkdirSync(output, { recursive: true });
    recordPath = path.join(output, `cep-single-card-${crypto.createHash("sha256").update(data.job_id).digest("hex")}.json`);
    record = { status: "started", job_id: data.job_id, target: bound, mogrt: mogrt.path,
      item_id: data.items[0].id, expected: { title: data.items[0].title, summary: data.items[0].summary, frames, endTicks: String(endTicks) }, steps: [] };
    fs.writeFileSync(recordPath, JSON.stringify(record, null, 2), { encoding: "utf8", flag: "wx" });
    log(`只验证第 1 条，共 ${data.items.length} 条；记录：${recordPath}`);
    const request = { ...bound, item: data.items[0], mapping, mogrt: mogrt.path, frames, endTicks: String(endTicks) };
    const literal = JSON.stringify(request).replace(/\u2028/g, "\\u2028").replace(/\u2029/g, "\\u2029");
    const result = await host(`$._swSingleCard.run(${literal})`);
    record.steps = result.trim().split("\n").map(line => {
      const [status, stage, message] = line.split("\t");
      return { status, stage, message: decodeURIComponent(message || "") };
    });
    record.status = record.steps.some(step => step.status === "FAIL") ? "failed" :
      record.steps.some(step => step.stage === "complete" && step.status === "PASS") ? "passed-api-checks" : "unknown";
    fs.writeFileSync(recordPath, JSON.stringify(record, null, 2), "utf8");
    record.steps.forEach(step => log(`${step.status} ${step.stage}: ${step.message}`));
  } catch (error) {
    log(`停止：${error.message}；不自动重试，请保留现场。`);
    // Leave the exclusive started record on callback/storage failure; never replay.
  } finally { busy = false; el("run").disabled = false; }
};
} catch (error) {
  document.getElementById("log").textContent = `面板初始化失败：${error.message}`;
}
