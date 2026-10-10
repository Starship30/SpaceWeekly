"use strict";

const ppro = require("premierepro");
const { storage } = require("uxp");
const fs = storage.localFileSystem;
const { validatePackage, durationFrames } = require("./protocol.js");
const mapping = require("./mapping.json");
const { hostInfo, readText, textAction } = require("./text-compat.js");
let importing = false;

function capabilities() {
  const info = hostInfo();
  if (!ppro.SequenceEditor || typeof ppro.SequenceEditor.getEditor !== "function" ||
      !ppro.TickTime || typeof ppro.TickTime.createWithFrameAndFrameRate !== "function" ||
      !ppro.Project || typeof ppro.Project.getProject !== "function") {
    throw Error("宿主缺少所需公开时间线 API");
  }
  return info;
}

async function sequenceFrameRate(sequence) {
  const settings = await sequence.getSettings();
  if (typeof settings.getVideoFrameRate === "function") return await settings.getVideoFrameRate();
  // Public 25.6+ fallback: preserve exact ticks per frame on hosts before 26.2.
  if (typeof sequence.getTimebase !== "function" || typeof ppro.FrameRate !== "function") {
    throw Error("宿主缺少公开序列帧率读取接口");
  }
  const ticks = Number(await sequence.getTimebase());
  if (!Number.isSafeInteger(ticks) || ticks <= 0) throw Error("序列 timebase 无效");
  const rate = new ppro.FrameRate();
  rate.ticksPerFrame = ticks;
  if (!Number.isFinite(rate.value) || rate.value <= 0) throw Error("无法从 timebase 获得帧率");
  return rate;
}

async function bindTarget() {
  capabilities();
  const project = await ppro.Project.getActiveProject();
  if (!project) throw Error("请先打开项目");
  const sequence = await project.getActiveSequence();
  if (!sequence) throw Error("请先激活专用空序列");
  const target = {
    projectGuid: project.guid.toString(), sequenceGuid: sequence.guid.toString(),
    path: project.path, projectName: project.name, sequenceName: sequence.name,
  };
  await inspectTimeline(sequence, 0);
  return Object.freeze(target);
}

async function resolveTarget(target) {
  if (!target) throw Error("请明确绑定目标项目 / 序列");
  const project = await ppro.Project.getProject(ppro.Guid.fromString(target.projectGuid));
  if (!project || project.path !== target.path) throw Error("目标项目已关闭或路径已变更，请停止并人工检查");
  const sequence = await project.getSequence(ppro.Guid.fromString(target.sequenceGuid));
  if (!sequence || sequence.guid.toString() !== target.sequenceGuid) throw Error("绑定的序列已失效");
  const activeProject = await ppro.Project.getActiveProject();
  const activeSequence = activeProject && await activeProject.getActiveSequence();
  if (!activeProject || activeProject.guid.toString() !== target.projectGuid ||
      !activeSequence || activeSequence.guid.toString() !== target.sequenceGuid) {
    throw Error("活动项目 / 序列已切换；停止导入，不改写其他序列");
  }
  return { project, sequence };
}

async function inspectTimeline(sequence, expectedCount, frames, frameRate) {
  // ponytail: O(n²) across a whole import; use track-change based checks if large batches matter.
  if (await sequence.getCaptionTrackCount()) throw Error("首版要求序列不含字幕轨道");
  for (const [countMethod, trackMethod, video] of [
    ["getVideoTrackCount", "getVideoTrack", true],
    ["getAudioTrackCount", "getAudioTrack", false],
  ]) {
    const count = await sequence[countMethod]();
    if (count < 1) throw Error("序列需要至少 V1 和 A1；模板必须无音频");
    for (let index = 0; index < count; index++) {
      const track = await sequence[trackMethod](index);
      const items = track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false);
      const transitions = track.getTrackItems(ppro.Constants.TrackItemType.TRANSITION, false);
      const expected = video && index === 0 ? expectedCount : 0;
      if (items.length !== expected || transitions.length) throw Error("序列不为空，或导入期间轨道内容发生变化");
      for (let i = 0; i < items.length; i++) {
        const start = ppro.TickTime.createWithFrameAndFrameRate(i * frames, frameRate);
        const end = ppro.TickTime.createWithFrameAndFrameRate((i + 1) * frames, frameRate);
        if ((await items[i].getStartTime()).ticks !== start.ticks || (await items[i].getEndTime()).ticks !== end.ticks) {
          throw Error("已导入卡片位置或时长发生变化");
        }
      }
    }
  }
}

async function readRuns() {
  const folder = await fs.getDataFolder();
  const entries = await folder.getEntries();
  const file = entries.find(entry => entry.name === "runs.json");
  if (!file) return [];
  // Fail closed on truncated/corrupt records; never silently replay a job.
  let runs;
  try { runs = JSON.parse(await file.read({ format: storage.formats.utf8 })); }
  catch (error) { throw Error(`执行记录无法读取或已损坏；拒绝导入：${error.message}`); }
  if (!Array.isArray(runs) || runs.some(run => !run || typeof run.job_id !== "string" ||
      !["running", "completed", "failed"].includes(run.status) ||
      !Array.isArray(run.completed_ids) || !Array.isArray(run.inserted_ids))) {
    throw Error("执行记录损坏，需人工检查；拒绝导入");
  }
  return runs;
}

async function saveRuns(runs) {
  // ponytail: rewrite the small local ledger; per-job files if history becomes large.
  const folder = await fs.getDataFolder();
  const file = await folder.createFile("runs.json", { overwrite: true });
  await file.write(JSON.stringify(runs, null, 2), { format: storage.formats.utf8 });
}

function rejectDuplicate(runs, jobId) {
  const previous = runs.find(run => run.job_id === jobId);
  if (previous) throw Error(`job_id 已执行或曾启动：${previous.status}；已完成 ${previous.completed_ids.length} 条。禁止重放`);
}

async function preflight(data, mogrt, target, seconds) {
  capabilities();
  validatePackage(data);
  if (!mogrt || !mogrt.isFile || !/\.mogrt$/i.test(mogrt.name) || !mogrt.nativePath) {
    throw Error("请通过文件选择器选择本地 .mogrt");
  }
  // Reading the selected Entry verifies access without requiring fullAccess.
  await mogrt.read({ format: storage.formats.binary });
  rejectDuplicate(await readRuns(), data.job_id);
  const { sequence } = await resolveTarget(target);
  await inspectTimeline(sequence, 0);
  const frameRate = await sequenceFrameRate(sequence);
  const editor = ppro.SequenceEditor.getEditor(sequence);
  if (!editor || typeof editor.insertMogrtFromPath !== "function") throw Error("宿主缺少 insertMogrtFromPath");
  const frames = durationFrames(seconds, frameRate.value, data.items.length);
  return { frames, frameRate };
}

async function readControls(clip, log = () => {}) {
  const chain = await clip.getComponentChain();
  const found = {};
  const names = Object.values(mapping);
  if (new Set(names).size !== names.length) throw Error("映射配置存在重复控件名");
  // Search by unique display name across all components, never fixed indices.
  const componentCount = await chain.getComponentCount();
  log(`公开组件数：${componentCount}`);
  for (let c = 0; c < componentCount; c++) {
    const component = await chain.getComponentAtIndex(c);
    const count = await component.getParamCount();
    log(`组件 ${c}：${typeof component.getMatchName === "function" ? await component.getMatchName() : "未知"}；参数数：${count}`);
    for (let p = 0; p < count; p++) {
      const param = await component.getParam(p);
      log(`参数 ${c}/${p}：${JSON.stringify(param.displayName)}`);
      if (!names.includes(param.displayName)) continue;
      if (found[param.displayName]) throw Error(`模板控件不唯一：${param.displayName}`);
      if (typeof param.createKeyframe !== "function" || typeof param.createSetValueAction !== "function") {
        throw Error(`控件缺少公开写入接口：${param.displayName}`);
      }
      found[param.displayName] = { param, state: await readText(param) };
    }
  }
  for (const field of ["title", "summary"]) {
    const name = mapping[field];
    if (!found[name]) throw Error(`缺少模板控件：${name}`);
  }
  return found;
}

function transact(project, actions, label) {
  let success = false;
  project.lockedAccess(() => {
    success = project.executeTransaction(compound => {
      for (const action of actions) {
        if (!compound.addAction(action)) throw Error(`无法加入事务操作：${label}`);
      }
    }, label);
  });
  if (!success) throw Error(`Premiere 事务失败：${label}`);
}

async function fillCard(project, clip, item, start, end, target) {
  const controls = await readControls(clip);
  const actions = [];
  for (const [field, name] of Object.entries(mapping)) {
    if (!controls[name]) continue; // Source is optional in the template; JSON keeps it.
    const { param, state } = controls[name];
    actions.push(textAction(param, state, item[field]));
  }
  actions.push(clip.createSetEndAction(end));
  await resolveTarget(target);
  transact(project, actions, `SpaceWeekly ${item.id}`);
  // Successful transactions alone do not prove graphics accepted the text.
  for (const [field, name] of Object.entries(mapping)) {
    if (!controls[name]) continue;
    const actual = await readText(controls[name].param, item[field]);
    if (actual.text !== item[field]) throw Error(`文字回读不一致：${name}`);
  }
  if ((await clip.getStartTime()).ticks !== start.ticks || (await clip.getEndTime()).ticks !== end.ticks) {
    throw Error("MOGRT 时长/位置回读不一致，请检查模板可裁剪范围");
  }
}

async function importJob(data, mogrt, target, seconds, log = () => {}) {
  if (importing) throw Error("已有导入正在执行");
  importing = true;
  let record, runs, current;
  try {
    const { frames, frameRate } = await preflight(data, mogrt, target, seconds);
    runs = await readRuns();
    rejectDuplicate(runs, data.job_id);
    record = {
      job_id: data.job_id, status: "running", target,
      host: capabilities(),
      frames_per_card: frames, fps: frameRate.value,
      completed_ids: [], inserted_ids: [], attempted_id: null, error: null,
    };
    runs.push(record);
    await saveRuns(runs); // Must persist intent BEFORE the first timeline mutation.
    for (let index = 0; index < data.items.length; index++) {
      current = data.items[index];
      log(`${index + 1}/${data.items.length} 正在导入：${current.id} ${current.title}`);
      const { project, sequence } = await resolveTarget(target);
      const liveRate = await sequenceFrameRate(sequence);
      if (!liveRate.equals(frameRate)) throw Error("序列帧率已变更");
      await inspectTimeline(sequence, index, frames, frameRate);
      record.attempted_id = current.id;
      await saveRuns(runs);
      // Re-resolve immediately before mutation. Editor is always bound to the selected sequence.
      await resolveTarget(target);
      const start = ppro.TickTime.createWithFrameAndFrameRate(index * frames, frameRate);
      const end = ppro.TickTime.createWithFrameAndFrameRate((index + 1) * frames, frameRate);
      const editor = ppro.SequenceEditor.getEditor(sequence);
      let inserted;
      project.lockedAccess(() => { inserted = editor.insertMogrtFromPath(mogrt.nativePath, start, 0, 0); });
      if (!inserted || !inserted.length) throw Error("MOGRT 插入未返回片段；请人工检查序列");
      record.inserted_ids.push(current.id);
      await saveRuns(runs);
      if (inserted.length !== 1 || !(inserted[0] instanceof ppro.VideoClipTrackItem)) {
        throw Error("首版只支持一个无音频的视频 MOGRT 片段");
      }
      await resolveTarget(target);
      await fillCard(project, inserted[0], current, start, end, target);
      await resolveTarget(target);
      await inspectTimeline(sequence, index + 1, frames, frameRate);
      record.completed_ids.push(current.id);
      record.attempted_id = null;
      await saveRuns(runs);
      log(`${index + 1}/${data.items.length} 完成：${current.id} ${current.title}`);
    }
    record.status = "completed";
    await saveRuns(runs);
    log(`成功：${record.completed_ids.length} 条；未自动保存工程`);
    return record;
  } catch (error) {
    if (!record) throw error;
    record.status = "failed";
    record.error = String(error.message || error);
    try { await saveRuns(runs); } catch (writeError) { log(`执行记录写入失败：${writeError.message}；禁止重放，人工检查`); }
    log(`${record.completed_ids.length ? "部分完成" : "失败"}：已完成 ${record.completed_ids.length} 条，已插入 ${record.inserted_ids.length} 条；当前 ${current ? current.id : "未插入"}：${record.error}`);
    log(`完成 ID：${record.completed_ids.join(", ") || "无"}；未完成项可能已留在序列中。不自动重试或回滚。`);
    return record;
  } finally {
    importing = false;
  }
}

// CommonJS exports let Node checks exercise guards; they are not host verification.
if (typeof module !== "undefined") {
  module.exports = { bindTarget, preflight, importJob, readControls };
}

if (typeof document !== "undefined") {
  let data, mogrt, target, ready = false, busy = false;
  const el = id => document.getElementById(id);
  const log = message => { el("log").textContent += `${message}\n`; el("progress").textContent = message; };
  try {
    const info = capabilities();
    log(`Premiere ${info.version} / UXP ${info.uxp}；${info.mogrtText ? "MogrtText 路径" : "条件性标量文字探测路径"}；未经过本机模板验收`);
  } catch (error) { log(`宿主能力检测失败：${error.message}`); }
  const invalidate = () => { ready = false; el("start").disabled = true; };
  // Inspect a leftover single card without changing it or replaying a failed job.
  (async () => {
    const project = await ppro.Project.getActiveProject();
    const sequence = project && await project.getActiveSequence();
    if (!sequence || !await sequence.getVideoTrackCount()) return;
    const track = await sequence.getVideoTrack(0);
    const clips = await track.getTrackItems(ppro.Constants.TrackItemType.CLIP, false);
    if (clips.length !== 1) return;
    log("现有 V1 单卡只读诊断（不写入文字或时间线）：");
    await readControls(clips[0], log);
    log("必需控件可读取；尚未验证写入。");
  })().catch(error => log(`单卡诊断：${error.message}`));
  const buttons = ["json", "mogrt", "bind", "preflight", "start", "seconds", "dedicated"];
  function handle(id, action) {
    el(id).addEventListener("click", async () => {
      if (busy) return;
      busy = true;
      for (const name of buttons) el(name).disabled = true;
      try { await action(); } catch (error) { invalidate(); log(`错误：${error.message}`); }
      finally {
        busy = false;
        for (const name of buttons) el(name).disabled = false;
        el("start").disabled = !ready;
      }
    });
  }
  handle("json", async () => {
    invalidate(); data = null;
    const file = await fs.getFileForOpening({ types: ["json"], allowMultiple: false });
    if (!file) return;
    data = validatePackage(JSON.parse((await file.read({ format: storage.formats.utf8 })).replace(/^\uFEFF/, "")));
    el("json-name").textContent = `${file.name} · ${data.items.length} 条 · ${data.job_id}`;
  });
  handle("mogrt", async () => {
    invalidate(); mogrt = null;
    mogrt = await fs.getFileForOpening({ types: ["mogrt"], allowMultiple: false });
    el("mogrt-name").textContent = mogrt ? mogrt.name : "未选择模板";
  });
  handle("bind", async () => {
    invalidate(); target = null;
    if (!el("dedicated").checked) throw Error("请勾选专用空序列声明");
    target = await bindTarget();
    el("target").textContent = `已绑定项目：${target.projectName}\n序列：${target.sequenceName}\n${target.sequenceGuid}`;
  });
  for (const id of ["seconds", "dedicated"]) el(id).addEventListener("change", invalidate);
  handle("preflight", async () => {
    invalidate();
    if (!el("dedicated").checked) throw Error("请勾选专用空序列声明");
    const result = await preflight(data, mogrt, target, Number(el("seconds").value));
    ready = true;
    log(`基础预检通过：${result.frames} 帧/卡，${result.frameRate.value} fps。模板控件将在开始导入时由首卡实测。`);
  });
  handle("start", async () => {
    if (!ready || !el("dedicated").checked) throw Error("请先预检");
    invalidate();
    await importJob(data, mogrt, target, Number(el("seconds").value), log);
  });
}
