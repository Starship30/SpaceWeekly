"use strict";

const ppro = require("premierepro");
const { host, versions } = require("uxp");

function hostInfo() {
  const version = String(host.version);
  const major = Number(version.split(".")[0]);
  if (host.name !== "premierepro" || ![26, 27].includes(major)) throw Error(`未支持的宿主：${host.name} ${version}`);
  let mogrtText = false;
  if (typeof ppro.MogrtText === "function") {
    const value = new ppro.MogrtText();
    mogrtText = typeof value.setText === "function" && typeof value.getText === "function";
  }
  return { version, uxp: versions.uxp, mogrtText };
}

async function readText(param, expectedText) {
  const info = hostInfo();
  if (param.isTimeVarying()) throw Error(`模板控件不得有关键帧：${param.displayName}`);
  let value, method = "getStartValue";
  try { value = await param.getStartValue(); }
  catch (error) {
    if (info.mogrtText) throw Error(`MogrtText 读取失败：${param.displayName}：${error.message}`);
    // 26.x exposes this public scalar accessor too; the actual type is still checked below.
    method = "getValueAtTime";
    try { value = await param.getValueAtTime(ppro.TickTime.createWithSeconds(0)); }
    catch (readError) {
      throw Error(`PR ${info.version} 控件 ${param.displayName} 无公开可读文字：getStartValue: ${error.message}；getValueAtTime: ${readError.message}`);
    }
  }
  if (info.mogrtText) {
    if (!(value instanceof ppro.MogrtText) || !value.isUniformStyling()) {
      throw Error(`控件 ${param.displayName} 必须是单一文字样式的 MogrtText`);
    }
    return { kind: "mogrt", value, text: value.getText() };
  }
  // Adobe's corrected runtime typing also documents a { value: scalar } wrapper.
  let text = method === "getValueAtTime" ?
    (value && typeof value.value === "string" ? value.value : value) :
    (typeof ppro.Keyframe === "function" && value instanceof ppro.Keyframe ? value.value.value : undefined);
  if (typeof text !== "string" && method === "getStartValue" && typeof param.getValueAtTime === "function") {
    method = "getValueAtTime";
    try { value = await param.getValueAtTime(ppro.TickTime.createWithSeconds(0)); }
    catch (error) { throw Error(`控件 ${param.displayName} 无公开标量文字：${error.message}`); }
    text = value && typeof value.value === "string" ? value.value : value;
  }
  // A generic setter's existence does NOT establish MOGRT support. Only an actual
  // public scalar string is eligible for the first-card write/readback probe.
  if (typeof text !== "string" || (/^\s*[\[{]/.test(text) && text !== expectedText)) {
    throw Error(`PR ${info.version} 控件 ${param.displayName} 返回不透明/结构化文字；读取=${method}，类型=${typeof value}，构造=${value && value.constructor ? value.constructor.name : "无"}，value类型=${typeof (value && value.value)}，嵌套value类型=${typeof (value && value.value && value.value.value)}；公开 UXP 无已核实的样式载荷写入方式，拒绝猜测`);
  }
  return { kind: "scalar", text };
}

function textAction(param, state, text) {
  let value = text;
  if (state.kind === "mogrt") {
    if (!state.value.setText(text)) throw Error(`设置文字失败：${param.displayName}`);
    value = state.value;
  }
  // createKeyframe checks compatibility against the ACTUAL parameter type.
  // On 26.x this is a conditional probe, not a promise that MOGRT text works.
  try { return param.createSetValueAction(param.createKeyframe(value), true); }
  catch (error) { throw Error(`控件 ${param.displayName} 不接受公开文本写入：${error.message}`); }
}

module.exports = { hostInfo, readText, textAction };
