"use strict";
// Node-only behavior checks with an explicit fake host. NO Premiere verification.
const assert = require("node:assert/strict");
const path = require("node:path");
const Module = require("node:module");
const { validatePackage, durationFrames } = require("../plugins/premiere-uxp/protocol.js");
const mapping = require("../plugins/premiere-uxp/mapping.json");
const pluginRoot = path.resolve(__dirname, "../plugins/premiere-uxp");

function news(count = 3) {
  return { protocol_version: "1.0", job_id: "test-job", report_title: "虚构周报", language: "zh_CN",
    items: Array.from({ length: count }, (_, i) => ({ id: `id-${i}`, title: `中文标题 ${i}`,
      summary: `摘要 ${i}`, source: "虚构来源", published: "原始日期", url: `https://example.com/${i}`, categories: ["航天", "科学"] })) };
}

function fakeHost({ typed = true, badAt = -1, switchOnRead = false, writeFails = false, fallback = false, wrapped = false, missingField } = {}) {
  const state = { active: "sequence", inserted: 0, files: new Map(), switchOnRead };
  const guid = name => ({ toString: () => name });
  const frameRate = { value: 30000 / 1001, equals: other => other.value === frameRate.value };
  const time = frames => ({ ticks: String(frames * 8475667200), seconds: frames / frameRate.value });
  class MogrtText {
    constructor(text = "示例") { this.text = text; }
    isUniformStyling() { return true; }
    getText() { return this.text; }
    setText(text) { this.text = text; return true; }
  }
  class Keyframe { constructor(value) { this.raw = value; this.value = { value }; } }
  class VideoClipTrackItem {
    constructor(start, number) {
      this.start = start; this.end = time(300);
      this.params = Object.values(mapping).filter(name => name !== mapping[missingField]).map(displayName => {
        let saved = number === badAt ? 42 : "模板文字";
        return {
          displayName, isTimeVarying: () => false,
          getStartValue: async () => {
            if (state.switchOnRead) { state.active = "other"; state.switchOnRead = false; }
            if (fallback) throw Error("start unsupported");
            return typed ? (typeof saved === "string" ? new MogrtText(saved) : new Keyframe(saved)) : new Keyframe(saved);
          },
          getValueAtTime: async () => wrapped ? { value: saved } : saved,
          createKeyframe: value => new Keyframe(value),
          createSetValueAction: key => () => { saved = typed ? key.raw.getText() : key.raw; },
        };
      });
    }
    getComponentChain() { return Promise.resolve({ getComponentCount: () => 1,
      getComponentAtIndex: () => ({ getParamCount: () => this.params.length, getParam: i => this.params[i] }) }); }
    createSetEndAction(end) { return () => { this.end = end; }; }
    getStartTime() { return Promise.resolve(this.start); }
    getEndTime() { return Promise.resolve(this.end); }
  }
  const clips = [];
  const track = items => ({ getTrackItems: type => type === 1 ? items : [] });
  const sequence = {
    guid: guid("sequence"), name: "专用空序列", getCaptionTrackCount: async () => 0,
    getVideoTrackCount: async () => 1, getAudioTrackCount: async () => 1,
    getVideoTrack: async () => track(clips), getAudioTrack: async () => track([]),
    getSettings: async () => ({ getVideoFrameRate: () => frameRate }),
  };
  const project = {
    guid: guid("project"), path: "project.prproj", name: "测试项目",
    getActiveSequence: async () => state.active === "sequence" ? sequence : { guid: guid("other") },
    getSequence: async id => id.toString() === "sequence" ? sequence : null,
    lockedAccess: callback => callback(),
    executeTransaction: callback => { const actions = []; callback({ addAction: a => { actions.push(a); return true; } }); actions.forEach(a => a()); return true; },
  };
  const ppro = {
    Keyframe, VideoClipTrackItem, Guid: { fromString: guid },
    Project: { getActiveProject: async () => project, getProject: id => id.toString() === "project" ? project : null },
    SequenceEditor: { getEditor: target => ({ insertMogrtFromPath: (_file, start, video, audio) => {
      assert.equal(target, sequence); assert.equal(video, 0); assert.equal(audio, 0);
      const clip = new VideoClipTrackItem(start, state.inserted++); clips.push(clip); return [clip];
    } }) },
    Constants: { TrackItemType: { CLIP: 1, TRANSITION: 2 } },
    TickTime: { createWithFrameAndFrameRate: time, createWithSeconds: s => time(s * frameRate.value) },
  };
  if (typed) ppro.MogrtText = MogrtText;
  const entry = name => ({ name, read: async () => state.files.get(name), write: async text => {
    if (writeFails) throw Error("disk full"); state.files.set(name, text);
  } });
  const storage = { formats: { utf8: "utf8", binary: "binary" }, localFileSystem: {
    getDataFolder: async () => ({ getEntries: async () => [...state.files.keys()].map(entry), createFile: async name => entry(name) }),
  } };
  const original = Module._load;
  Module._load = function(request, parent, isMain) {
    if (request === "premierepro") return ppro;
    if (request === "uxp") return { storage, host: { name: "premierepro", version: typed ? "27.0.0-beta" : "26.5.0" }, versions: { uxp: "fake" } };
    return original.call(this, request, parent, isMain);
  };
  let plugin, compat;
  try {
    for (const name of ["main.js", "text-compat.js"]) delete require.cache[path.join(pluginRoot, name)];
    plugin = require(path.join(pluginRoot, "main.js"));
    compat = require(path.join(pluginRoot, "text-compat.js"));
  } finally { Module._load = original; }
  const mogrt = { isFile: true, name: "test.mogrt", nativePath: "test.mogrt", read: async () => new ArrayBuffer(1) };
  return { plugin, compat, state, mogrt, clips, ppro, sequence };
}

let passed = 0;
async function check(name, run) { await run(); passed++; console.log(`PASS ${name}`); }

(async () => {
  await check("异步组件遍历与非标量 startValue 的公开标量回退", async () => {
    const h = fakeHost({ typed: false });
    const target = await h.plugin.bindTarget();
    await h.plugin.importJob(news(1), h.mogrt, target, 8);
    const clip = h.clips[0];
    clip.getComponentChain = async () => ({ getComponentCount: async () => 1,
      getComponentAtIndex: async () => ({ getParamCount: async () => clip.params.length,
        getParam: async i => clip.params[i] }) });
    const lines = [];
    assert.ok((await h.plugin.readControls(clip, line => lines.push(line)))[mapping.title]);
    assert.ok(lines.some(line => line.includes(mapping.title)));
    const param = { displayName: mapping.title, isTimeVarying: () => false,
      getStartValue: async () => ({ value: { value: {} } }),
      getValueAtTime: async () => ({ value: "公开文字" }) };
    assert.equal((await h.compat.readText(param)).text, "公开文字");
    param.getValueAtTime = async () => ({ value: {} });
    await assert.rejects(h.compat.readText(param), /拒绝猜测/);
  });
  await check("26.0 缺少 getVideoFrameRate 时用公开 timebase，非法值零写入", async () => {
    const h = fakeHost({ typed: false });
    h.sequence.getSettings = async () => ({});
    h.sequence.getTimebase = async () => "8475667200";
    h.ppro.FrameRate = class {
      get value() { return 254016000000 / this.ticksPerFrame; }
      equals(other) { return this.value === other.value; }
    };
    const target = await h.plugin.bindTarget();
    const result = await h.plugin.preflight(news(), h.mogrt, target, 8);
    assert.equal(result.frames, 240);
    assert.equal(result.frameRate.ticksPerFrame, 8475667200);
    h.sequence.getTimebase = async () => "invalid";
    await assert.rejects(h.plugin.importJob(news(), h.mogrt, target, 8), /timebase 无效/);
    assert.equal(h.state.inserted, 0);
  });
  await check("HTML 脚本无 CommonJS module 时仍初始化按钮", () => {
    const elements = new Map();
    const document = { getElementById(id) {
      if (!elements.has(id)) elements.set(id, { textContent: "", addEventListener(event, callback) {
        this[event] = callback;
      } });
      return elements.get(id);
    } };
    const requireLocal = Module.createRequire(path.join(pluginRoot, "main.js"));
    require("node:vm").runInNewContext(require("node:fs").readFileSync(path.join(pluginRoot, "main.js"), "utf8"), {
      document, require(name) {
        if (name === "premierepro") return {};
        if (name === "uxp") return { storage: { localFileSystem: {} } };
        if (name === "./text-compat.js") return { hostInfo: () => ({ version: "26.0.0" }) };
        return requireLocal(name);
      },
    });
    for (const id of ["json", "mogrt", "bind", "preflight", "start"]) {
      assert.equal(typeof elements.get(id).click, "function");
    }
    assert.match(elements.get("log").textContent, /宿主能力检测失败/);
  });
  await check("中文协议、未知版本、缺字段、重复 ID、分类数组", () => {
    const example = JSON.parse(require("node:fs").readFileSync(path.join(pluginRoot, "example.premiere.json"), "utf8"));
    assert.equal(validatePackage(example).items.length, 3);
    assert.equal(validatePackage(news()).items[0].title, "中文标题 0");
    assert.throws(() => validatePackage({ ...news(), protocol_version: "2.0" }));
    const missing = news(); delete missing.items[0].summary; assert.throws(() => validatePackage(missing));
    const duplicate = news(); duplicate.items[1].id = duplicate.items[0].id; assert.throws(() => validatePackage(duplicate));
    const category = news(); category.items[0].categories = "航天"; assert.throws(() => validatePackage(category));
  });
  await check("29.97fps 量化、过短及非法时长", () => {
    assert.equal(durationFrames(8, 30000 / 1001, 3), 240);
    for (const value of [0, -1, NaN, Infinity, 0.001]) assert.throws(() => durationFrames(value, 30, 3));
  });
  await check("27.x 模拟：按序写入三卡、文字回读、整数帧时长、拒绝重复任务", async () => {
    const h = fakeHost(); const target = await h.plugin.bindTarget();
    await h.plugin.preflight(news(), h.mogrt, target, 8); assert.equal(h.state.inserted, 0);
    const result = await h.plugin.importJob(news(), h.mogrt, target, 8);
    assert.equal(result.status, "completed"); assert.deepEqual(result.completed_ids, ["id-0", "id-1", "id-2"]);
    assert.equal((await h.clips[2].params[0].getStartValue()).getText(), "中文标题 2");
    assert.equal((await h.clips[0].params.find(p => p.displayName === mapping.source).getStartValue()).getText(), "虚构来源");
    assert.equal((await h.clips[2].getEndTime()).ticks, String(720 * 8475667200));
    await assert.rejects(h.plugin.importJob(news(), h.mogrt, target, 8), /禁止重放/);
  });
  await check("26.x 模拟：仅真实标量参数完成首卡写入回读", async () => {
    const h = fakeHost({ typed: false }); const target = await h.plugin.bindTarget();
    const result = await h.plugin.importJob(news(), h.mogrt, target, 8);
    assert.equal(result.status, "completed"); assert.equal((await h.clips[0].params[0].getStartValue()).value.value, "中文标题 0");
  });
  await check("26.x 公开 getValueAtTime 回退路径", async () => {
    for (const wrapped of [false, true]) {
      const h = fakeHost({ typed: false, fallback: true, wrapped }); const target = await h.plugin.bindTarget();
      assert.equal((await h.plugin.importJob(news(1), h.mogrt, target, 8)).status, "completed");
    }
  });
  await check("26/27 模板缺少来源控件仍完整导入标题、摘要和时长", async () => {
    for (const typed of [false, true]) {
      const h = fakeHost({ typed, missingField: "source" }); const target = await h.plugin.bindTarget();
      const data = news();
      const result = await h.plugin.importJob(data, h.mogrt, target, 8);
      assert.equal(result.status, "completed"); assert.equal(result.completed_ids.length, 3);
      for (let index = 0; index < h.clips.length; index++) {
        const controls = await h.plugin.readControls(h.clips[index]);
        assert.equal(controls[mapping.source], undefined);
        assert.equal(controls[mapping.title].state.text, data.items[index].title);
        assert.equal(controls[mapping.summary].state.text, data.items[index].summary);
      }
      assert.equal((await h.clips[2].getEndTime()).ticks, String(720 * 8475667200));
      assert.equal(data.items[0].source, "虚构来源");
    }
  });
  await check("标题和摘要控件仍必需，缺少任一控件立即停止", async () => {
    for (const missingField of ["title", "summary"]) {
      const h = fakeHost({ missingField }); const target = await h.plugin.bindTarget();
      const result = await h.plugin.importJob(news(), h.mogrt, target, 8);
      assert.equal(result.status, "failed"); assert.equal(result.completed_ids.length, 0);
      assert.equal(h.state.inserted, 1); assert.match(result.error, /缺少模板控件/);
    }
  });
  await check("26.x 不透明/样式 JSON 参数必须阻止文字写入", async () => {
    const h = fakeHost({ typed: false });
    for (const text of [42, '{"textEditValue":"旧文字"}']) {
      await assert.rejects(h.compat.readText({ displayName: "SW_TITLE", isTimeVarying: () => false,
        getStartValue: async () => new h.ppro.Keyframe(text) }), /拒绝猜测/);
    }
  });
  await check("部分失败立即停止、记录完成与插入数、拒绝重放", async () => {
    const h = fakeHost({ badAt: 1 }); const target = await h.plugin.bindTarget();
    const result = await h.plugin.importJob(news(), h.mogrt, target, 8);
    assert.equal(result.status, "failed"); assert.deepEqual(result.completed_ids, ["id-0"]);
    assert.equal(h.state.inserted, 2); assert.equal(result.inserted_ids.length, 2);
    await assert.rejects(h.plugin.importJob(news(), h.mogrt, target, 8));
  });
  await check("切换目标后零写入；切换发生于读取控件时停止事务", async () => {
    const h = fakeHost(); const target = await h.plugin.bindTarget(); h.state.active = "other";
    await assert.rejects(h.plugin.importJob(news(), h.mogrt, target, 8), /已切换/); assert.equal(h.state.inserted, 0);
    const during = fakeHost({ switchOnRead: true }); const bound = await during.plugin.bindTarget();
    const result = await during.plugin.importJob(news(), during.mogrt, bound, 8);
    assert.equal(result.status, "failed"); assert.equal(result.completed_ids.length, 0); assert.equal(during.state.inserted, 1);
  });
  await check("并发导入必须拒绝第二次点击", async () => {
    const h = fakeHost(); const target = await h.plugin.bindTarget();
    const first = h.plugin.importJob(news(), h.mogrt, target, 8);
    await assert.rejects(h.plugin.importJob(news(), h.mogrt, target, 8), /正在执行/);
    assert.equal((await first).status, "completed");
  });
  await check("执行记录损坏或无法保存时不得插入", async () => {
    const h = fakeHost(); const target = await h.plugin.bindTarget(); h.state.files.set("runs.json", "{");
    await assert.rejects(h.plugin.importJob(news(), h.mogrt, target, 8)); assert.equal(h.state.inserted, 0);
    const disk = fakeHost({ writeFails: true }); const bound = await disk.plugin.bindTarget();
    assert.equal((await disk.plugin.importJob(news(), disk.mogrt, bound, 8)).status, "failed"); assert.equal(disk.state.inserted, 0);
  });
  await check("非空序列不得绑定；重复控件不得通过模板检查", async () => {
    const h = fakeHost(); const bound = await h.plugin.bindTarget();
    await h.plugin.importJob(news(1), h.mogrt, bound, 8);
    await assert.rejects(h.plugin.bindTarget(), /不为空/);
    h.clips[0].params.push(h.clips[0].params[0]); await assert.rejects(h.plugin.readControls(h.clips[0]), /不唯一/);
  });
  console.log(`${passed} Node fake-host checks passed. Not tested in Premiere.`);
})().catch(error => { console.error(error); process.exitCode = 1; });
