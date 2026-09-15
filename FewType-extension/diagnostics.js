// ---- 诊断日志（平台无关，Play Books 和 Koodo 共用）----
// content 脚本和 offscreen.js 运行在不同上下文，各自的 console 分开看很麻烦，
// 统一发到这里汇总成一条时间线，方便定位"到底哪个环节把内容漏掉/切错了"。
// 只留最近 N 条，不然长时间朗读会把内存占满。
const MAX_LOG_ENTRIES = 4000;
let diagnosticLog = [];

export function logEvent(source, message, data) {
  diagnosticLog.push({ t: Date.now(), source, message, data });
  if (diagnosticLog.length > MAX_LOG_ENTRIES) {
    diagnosticLog.splice(0, diagnosticLog.length - MAX_LOG_ENTRIES);
  }
}

function formatLogEntry(entry) {
  const d = new Date(entry.t);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  const ms = String(d.getMilliseconds()).padStart(3, "0");
  const dataStr = entry.data !== undefined ? " | " + JSON.stringify(entry.data) : "";
  return `[${hh}:${mm}:${ss}.${ms}] [${entry.source}] ${entry.message}${dataStr}`;
}

export function exportDiagnosticLogText() {
  return diagnosticLog.map(formatLogEntry).join("\n");
}

export function diagnosticLogCount() {
  return diagnosticLog.length;
}

export function clearDiagnosticLog() {
  diagnosticLog = [];
}

// chunk/segment 文本很长，日志里只截前 N 字做摘要，不然一次导出几百 KB 没法看。
// 两个平台的分块逻辑都会用到这个，所以放在共享模块里。
export function textPreview(text, len = 20) {
  if (typeof text !== "string") return text;
  return text.length > len ? text.slice(0, len) + "…" : text;
}
