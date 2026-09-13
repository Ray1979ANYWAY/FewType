// ---- 本地桥接服务端口自动探测 ----
// VoxEcho 主线（Tauri）后端默认 5010；旧版（TK）后端默认 5005。
// 本模块被 popup / background / offscreen 共用：首次探测结果缓存到
// chrome.storage.local，其余脚本直接复用缓存；缓存端口不可达时自动重新探测。
export const BRIDGE_PORTS = [5010, 5005];
export const BRIDGE_PORT_KEY = "voxechoBridgePort";

async function voxProbePort(base) {
  try {
    const res = await fetch(base + "/voices", {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    // 端口上有 HTTP 服务即视为桥存在（具体 200/4xx 由后续业务请求判断）；
    // 只有连接被拒 / 超时才算不可达。
    return true;
  } catch (e) {
    return false;
  }
}

export async function voxResolveBridgeUrl() {
  try {
    const cached = await chrome.storage.local.get(BRIDGE_PORT_KEY);
    const cachedPort = cached[BRIDGE_PORT_KEY];
    if (cachedPort) {
      const base = "http://127.0.0.1:" + cachedPort;
      if (await voxProbePort(base)) return base;
    }
    for (const port of BRIDGE_PORTS) {
      const base = "http://127.0.0.1:" + port;
      if (await voxProbePort(base)) {
        await chrome.storage.local.set({ [BRIDGE_PORT_KEY]: port });
        return base;
      }
    }
  } catch (e) {
    // storage 不可用等极端情况：直接按默认顺序探测
    for (const port of BRIDGE_PORTS) {
      const base = "http://127.0.0.1:" + port;
      if (await voxProbePort(base)) return base;
    }
  }
  return null;
}
