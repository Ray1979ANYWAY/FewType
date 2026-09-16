// ---- 本地桥接服务端口自动探测 ----
// FewType 主线（Tauri）后端默认 5010；旧版（TK）后端默认 5005。
// 本模块被 popup / background / offscreen 共用：首次探测结果缓存到
// chrome.storage.local，其余脚本直接复用缓存；缓存端口不可达时自动重新探测。
export const BRIDGE_PORTS = [5010, 5005];
export const BRIDGE_PORT_KEY = "fewtypeBridgePort";

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
  const tryPort = async (p) => {
    const base = "http://127.0.0.1:" + p;
    if (await voxProbePort(base)) {
      await chrome.storage.local.set({ [BRIDGE_PORT_KEY]: p });
      return base;
    }
    return null;
  };
  try {
    const cached = await chrome.storage.local.get(BRIDGE_PORT_KEY);
    const cachedPort = Number(cached[BRIDGE_PORT_KEY]);
    // 主线（5010）优先：缓存端口只有在命中主线时才做快速路径。
    // 若缓存是旧版端口（5005），直接按 [5010, 5005] 顺序重探——
    // 避免两条线同时运行时扩展粘在旧版 bridge 上，导致主线收不到心跳。
    if (cachedPort === BRIDGE_PORTS[0]) {
      const hit = await tryPort(cachedPort);
      if (hit) return hit;
    }
    for (const port of BRIDGE_PORTS) {
      if (port === cachedPort) continue; // 主线缓存已试过，避免重复探测
      const hit = await tryPort(port);
      if (hit) return hit;
    }
  } catch (e) {
    // storage 不可用等极端情况：直接按默认顺序探测
    for (const port of BRIDGE_PORTS) {
      const hit = await tryPort(port);
      if (hit) return hit;
    }
  }
  return null;
}
