"use strict";

function validatePackage(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) throw Error("内容包必须是 JSON 对象");
  if (data.protocol_version !== "1.0") throw Error("仅支持 protocol_version 1.0");
  for (const key of ["job_id", "report_title", "language"]) {
    if (typeof data[key] !== "string" || !data[key].trim()) throw Error(`缺少 ${key}`);
  }
  if (!Array.isArray(data.items) || !data.items.length) throw Error("items 必须是非空新闻数组");
  const ids = new Set();
  data.items.forEach((item, index) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) throw Error(`第 ${index + 1} 条无效`);
    for (const key of ["id", "title", "summary", "source", "published", "url"]) {
      if (typeof item[key] !== "string") throw Error(`第 ${index + 1} 条缺少字符串 ${key}`);
    }
    for (const key of ["id", "title", "source", "url"]) {
      if (!item[key].trim()) throw Error(`第 ${index + 1} 条 ${key} 为空`);
    }
    if (!/^https?:\/\/[^\s/]+(?:\/[^\s]*)?$/i.test(item.url)) throw Error(`第 ${index + 1} 条 URL 无效`);
    if (ids.has(item.id)) throw Error(`重复新闻 ID：${item.id}`);
    ids.add(item.id);
    if (!Array.isArray(item.categories) || item.categories.some(x => typeof x !== "string" || !x.trim())) {
      throw Error(`第 ${index + 1} 条 categories 必须是字符串列表`);
    }
  });
  return data;
}

function durationFrames(seconds, fps, count) {
  if (!Number.isFinite(seconds) || seconds <= 0 || !Number.isFinite(fps) || fps <= 0) {
    throw Error("持续秒数和序列帧率必须是有限正数");
  }
  const frames = Math.round(seconds * fps);
  if (!Number.isSafeInteger(frames) || frames < 1 || !Number.isSafeInteger(frames * count)) {
    throw Error("持续时间不足一帧或超出整数帧范围");
  }
  return frames;
}

module.exports = { validatePackage, durationFrames };
