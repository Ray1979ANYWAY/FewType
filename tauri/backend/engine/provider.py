# -*- coding: utf-8 -*-
"""
VoxEcho provider abstraction layer (provider.py)

统一封装 LLM / ASR 平台调用（OpenAI 兼容接口）：
- chat():              翻译/风格化后处理（LLM 通道，llm_model）
- transcribe():        语音转写（ASR 通道，asr_model）
- stream_transcribe(): 实时流式转写（预留，Groq 实时 API 形态核实后实现）
- list_models():       实时抓取 GET /models，失败抛 ProviderError（调用方决定回退）

平台：Groq（推荐）/ 硅基流动 / 自定义（OpenAI 兼容）
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

import re
import requests

import logging

logger = logging.getLogger("voxecho.provider")


@dataclass(frozen=True)
class PlatformDef:
    name: str
    base_url: str
    signup_url: str
    default_asr: str
    default_llm: str
    llm_pinned: tuple = ()    # LLM 下拉置顶（默认之外紧跟的推荐模型）
    recommended: bool = False  # 界面标"推荐！"
    proxy_hint: bool = False   # 中文界面提示"需要使用代理"
    custom: bool = False       # 用户自填 base_url / 模型
    needs_proxy: bool = True   # 国内直连平台（硅基流动）不需要 Clash 代理，避免代理分流不稳定
    asr_signup_url: str = ""   # ASR 与 LLM 分属不同控制台时，各自的注册/开通链接（非空则界面显示两行链接）
    llm_signup_url: str = ""


PROVIDERS: dict[str, PlatformDef] = {
    "groq": PlatformDef(
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        signup_url="https://console.groq.com/keys",
        default_asr="whisper-large-v3-turbo",
        default_llm="openai/gpt-oss-120b",
        llm_pinned=("openai/gpt-oss-120b", "groq/compound", "qwen/qwen3.8-27b"),
        recommended=True,
        proxy_hint=True,
    ),
    "siliconflow": PlatformDef(
        name="硅基流动 SiliconFlow",
        base_url="https://api.siliconflow.cn/v1",
        signup_url="https://cloud.siliconflow.cn",
        default_asr="whisper-large-v3-turbo",  # SenseVoiceSmall 实测慢(17s)/超时(30s×3)/识别差，弃用
        default_llm="deepseek-ai/DeepSeek-V4-Flash",
        llm_pinned=("deepseek-ai/DeepSeek-V4-Flash", "deepseek-ai/DeepSeek-V3.2"),
        needs_proxy=False,  # 国内直连，不走 Clash（走代理可能分流失败/变慢）
    ),
    "volcengine": PlatformDef(
        name="火山引擎 Volcengine",
        base_url="https://ark.cn-beijing.volces.com/api/v3",  # 火山方舟 OpenAI 兼容端点（LLM 润色用）
        signup_url="https://console.volcengine.com/speech/new/",
        asr_signup_url="https://console.volcengine.com/speech/new/",  # 语音识别（豆包流式 2.0）独立控制台
        llm_signup_url="https://console.volcengine.com/ark",          # 方舟大模型（Doubao-Seed-Evolving）独立控制台
        default_asr="volc.seedasr.sauc.duration",  # 豆包流式语音识别模型 2.0（小时版资源 ID）
        default_llm="deepseek-v4-flash-260425",  # 实测最快，STT 润色响应速度最优
        llm_pinned=("deepseek-v4-flash-260425", "doubao-seed-2-0-mini-260428", "doubao-seed-2-0-lite-260428", "doubao-seed-evolving"),
        needs_proxy=False,  # 国内直连，不走 Clash
    ),
    "custom": PlatformDef(
        name="自定义托管平台",
        base_url="",
        signup_url="",
        default_asr="",
        default_llm="",
        custom=True,
    ),
}

# 实时抓取失败时的后备模型清单
FALLBACK_MODELS = {
    "asr": ["whisper-large-v3-turbo", "whisper-large-v3", "FunAudioLLM/SenseVoiceSmall"],
    "llm": [
        "llama-3.3-70b-versatile",
        "DeepSeek-V3",
        "Qwen/Qwen2.5-72B-Instruct",
        "Doubao-1.5-pro",
    ],
}

# ASR 模型名关键词（用于从全量 /models 列表里筛出语音类模型）
ASR_KEYWORDS = ("whisper", "sensevoice", "audio", "stt", "asr", "speech", "voice")

VOLC_ASR_IDS = ("volc.seedasr.sauc.duration", "volc.seedasr.sauc.concurrent",
                "volc.bigasr.sauc.duration", "volc.bigasr.sauc.concurrent")


class ProviderError(Exception):
    """平台调用失败（网络 / 鉴权 / 接口 / 解析）"""


def sanitize_api_key(key: str) -> str:
    """清洗 API Key：剔除非 ASCII（零宽空格/全角/中文）、换行与隐藏字符。

    requests 编码 header 用 latin-1，key 里混入脏字符会直接抛
    UnicodeEncodeError。保留字母数字、-_ 及 =（部分平台 base64 key）。"""
    if not key or not isinstance(key, str):
        return ""
    ascii_only = key.encode("ascii", errors="ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9\-_=]", "", ascii_only)


def _is_network_error(e: Exception) -> bool:
    """网络层异常（连接被重置/超时/SSL 握手失败）——可重试；HTTP 业务错误不可重试。"""
    return isinstance(e, requests.RequestException)


def _request_with_retry(method: str, url: str, retries: int = 2, use_proxy: bool = True,
                         session: requests.Session | None = None, force_close: bool = False, **kw) -> requests.Response:
    """统一请求入口：网络层异常自动重试（Groq 走代理时 TLS 握手会被间歇性 RST）。

    每次重试前重新读系统代理（Clash 节点/开关可能变化）。退避 0.8s/1.6s。
    session: 传入 requests.Session 复用 TLS 连接（省 100-300ms 握手）；None 则裸 requests.request。
    force_close: 强制 Connection: close（国内不稳定平台如方舟/硅基，避免复用半关闭连接超时）。
    重试耗尽后抛出最后一次异常（带重试次数说明）。"""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if use_proxy:
                kw["proxies"] = system_proxy()
            if force_close:
                # 国内不稳定平台：强制每次新建连接，避免复用半关闭的连接导致后续请求超时
                headers = dict(kw.get("headers") or {})
                headers["Connection"] = "close"
                kw["headers"] = headers
            if session is not None:
                r = session.request(method, url, **kw)
            else:
                r = requests.request(method, url, **kw)
            return r
        except requests.RequestException as e:
            last = e
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
    raise ProviderError(
        f"网络请求失败（已重试 {retries} 次）：{last}。请检查网络连接或代理设置"
    ) from last


# 国内平台直连规则：识别国内服务商域名 → 不走代理；其余（含自定义国外平台）走系统代理。
# 覆盖：硅基/阿里/火山/智谱/腾讯/百度/讯飞/MiniMax/商汤/月之暗面/DeepSeek/百川/阶跃 及 .cn 域名。
_DOMESTIC_HINTS = (
    "siliconflow", "aliyun", "alibaba", "volcengine", "volces",
    "bigmodel", "zhipuai", "z.ai", "tencent", "qcloud", "baidu",
    "bcebos", "iflytek", "minimaxi", "minimax", "sensetime",
    "moonshot", "01.ai", "deepseek", "baichuan", "stepfun",
)


def is_domestic_url(url: str) -> bool:
    """URL 是否属于国内直连服务（.cn 域名或已知国内服务商）。"""
    h = (url or "").lower().strip()
    if not h:
        return False
    return h.rstrip("/").endswith(".cn") or any(d in h for d in _DOMESTIC_HINTS)


def should_use_proxy(url: str) -> bool:
    """规则：国内平台直连，其余走系统代理。"""
    return not is_domestic_url(url)


def system_proxy() -> dict | None:
    """读取 Windows 系统代理（Clash 等设置后注册表可查），供 requests 使用。

    国内直连 api.groq.com 会被墙；Python requests 默认不走 WININET 系统代理，
    必须显式传 proxies。无系统代理时返回 None（直连）。
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as k:
            if not winreg.QueryValueEx(k, "ProxyEnable")[0]:
                return None
            server = winreg.QueryValueEx(k, "ProxyServer")[0]
        if not server:
            return None
        if not server.startswith(("http://", "https://")):
            server = "http://" + server
        return {"http": server, "https": server}
    except Exception:
        return None


class Provider:
    def __init__(
        self,
        platform: str = "groq",
        api_key: str = "",
        asr_model: str = "",
        llm_model: str = "",
        base_url: str = "",
        llm_key: str = "",
        llm_base_url: str = "",
    ):
        self.platform = platform
        self.api_key = sanitize_api_key(api_key or "")
        self.llm_key = sanitize_api_key(llm_key or "") or self.api_key  # LLM 独立 Key（火山：方舟 Key），缺省回退主 Key
        self.asr_model = asr_model or PROVIDERS[platform].default_asr
        self.llm_model = llm_model or PROVIDERS[platform].default_llm
        # 火山引擎 ASR 是 WebSocket 流式，界面上 base_url 可能显示说明文字而非真实 URL；
        # 检测到说明文字时清空，让 LLM 端点回退到 provider 定义的默认方舟地址。
        _bu = base_url.strip()
        if platform == "volcengine" and ("WebSocket" in _bu or "无需配置" in _bu or "no config" in _bu):
            _bu = ""
        self._base_url = _bu or PROVIDERS[platform].base_url
        self._llm_base_url = llm_base_url.strip()  # 自定义平台可独立配置 LLM 端点；留空则与 ASR 共用
        # 连接复用：用 requests.Session 保持 TLS 连接，第二次请求省 100-300ms 握手
        self._session = requests.Session()
        # 浏览器 UA：Groq 等平台用 Cloudflare 防护（error code 1010 / SSL EOF），
        # 默认 python-requests UA 会被直接拦截（表现为「key 无效」/连接被重置），
        # 带浏览器 UA 后正常放行。对所有平台无害（火山/硅基不校验 UA）。
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        })
        # 【连接复用优化】记录上次代理指纹，用于请求前检测机场是否静默换节点
        # 换节点后旧 TLS 连接会失效，不检测会导致请求超时 10-30s；检测开销 <5ms 可忽略
        self._last_proxy = None
        # 代理判定拆分为 ASR / LLM 两路：自定义平台 ASR 与 LLM 可分属国内外服务商
        # （如 ASR=Groq 海外需代理 + LLM=硅基国内直连），各自按端点域名独立判定

    @property
    def base_url(self) -> str:
        return self._base_url.rstrip("/")

    @property
    def asr_needs_proxy(self) -> bool:
        """ASR（转写）请求代理判定：按 ASR 端点域名。"""
        return should_use_proxy(self._base_url)

    @property
    def llm_needs_proxy(self) -> bool:
        """LLM（chat/模型列表）请求代理判定：按 LLM 端点域名。"""
        return should_use_proxy(self.llm_base_url)

    @property
    def llm_base_url(self) -> str:
        """LLM 端点：独立配置优先，否则回退 ASR/主端点。"""
        return (self._llm_base_url or self._base_url).rstrip("/")

    # ================================================================
    # 【连接复用优化】以下三个方法实现完整的连接复用策略
    # ----------------------------------------------------------------
    # 背景：
    #   - 海外直连用户：连接稳定，Session 复用省 TLS 握手 100-300ms/次
    #   - 机场用户：Clash 静默换节点后旧 TLS 连接失效，用旧连接请求会超时
    #   - 国内平台（火山/硅基）：连接复用可能拿到半关闭连接，需强制 close
    #
    # 策略（按执行顺序）：
    #   1. _proxy_fingerprint()     → 读系统代理，生成指纹（<5ms）
    #   2. _refresh_session_if_proxy_changed() → 代理变了就重建 Session（换节点场景）
    #   3. _request()               → 统一请求入口：复用 Session + 连接失败自动重建重试
    #   4. 超时分离：连接超时 5s（快速检测失效连接），读取超时用调用方设置
    # ================================================================

    def _proxy_fingerprint(self) -> str:
        """当前系统代理的指纹字符串，用于对比是否变化（机场换节点检测）。
        直连/无代理返回 "direct"；有代理返回代理地址。"""
        px = system_proxy()
        if not px:
            return "direct"
        return px.get("https") or px.get("http") or "direct"

    def _refresh_session_if_proxy_changed(self):
        """请求前检测：系统代理变了（机场静默换节点/开关代理）→ 重建 Session。

        【为什么要做这个】
        - 机场用户换节点是静默的，Clash 不会通知其他应用
        - 旧 TLS 连接通过旧节点建立，换节点后连接随即失效
        - 如果不检测，用失效连接发请求会超时（10-30s），用户明显感知卡顿
        - 检测开销极小（读注册表 <5ms），换节点后直接用新连接，零额外延迟
        - 对直连用户零影响：代理永远是 "direct"，永不触发重建
        """
        cur = self._proxy_fingerprint()
        if self._last_proxy is not None and cur != self._last_proxy:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = requests.Session()
        self._last_proxy = cur

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        """统一请求入口：代理检测 → Session 复用 → 连接失败自动重建重试。

        【连接复用完整策略】
        1. 正常路径：复用 self._session 的 TLS 连接，省 100-300ms/次握手
        2. 代理变化：请求前检测，变化则重建 Session（机场换节点场景）
        3. 连接失败：ConnectionError/ProxyError/ReadTimeout/ConnectionResetError
           → 第一次失败时自动重建 Session 重试（可能是临时网络波动或节点切换）
        4. 超时分离：连接超时 5s（快速检测失效连接，避免等 30s），读取超时用调用方设置
        5. 国内平台：force_close=True，强制 Connection: close（避免复用半关闭连接超时）

        【对各类用户的影响】
        - 海外直连用户：零额外开销，享受连接复用收益（省 TLS 握手）
        - 机场用户：换节点后第一次请求直接用新连接，不会超时；正常时复用连接
        - 国内平台用户：强制 close 避免半关闭连接问题，每次新建连接但稳定
        """
        self._refresh_session_if_proxy_changed()
        force_close = kw.pop("force_close", False)
        retries = kw.pop("retries", 2)
        use_proxy = kw.pop("use_proxy", True)

        # 超时分离：连接超时 5s（快速检测失效连接），读取超时用调用方设置
        # 为什么连接超时要短：换节点后的失效连接通常在连接阶段就失败，5s 足够检测
        # 读取超时保持正常（API 处理可能需要较长时间）
        if "timeout" in kw:
            t = kw["timeout"]
            if isinstance(t, (int, float)):
                kw["timeout"] = (5, t)  # (connect_timeout, read_timeout)

        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                if use_proxy:
                    kw["proxies"] = system_proxy()
                if force_close:
                    # 国内不稳定平台（火山/硅基）：强制每次新建连接
                    # 原因：这些平台的连接复用容易拿到半关闭状态，导致后续请求超时
                    headers = dict(kw.get("headers") or {})
                    headers["Connection"] = "close"
                    kw["headers"] = headers
                return self._session.request(method, url, **kw)
            except (requests.ConnectionError, requests.exceptions.ProxyError,
                    requests.ReadTimeout, ConnectionResetError) as e:
                # 连接类错误：可能是代理换节点/网络波动，第一次失败时重建 Session 重试
                last = e
                if attempt == 0:
                    try:
                        self._session.close()
                    except Exception:
                        pass
                    self._session = requests.Session()
                    self._last_proxy = self._proxy_fingerprint()
                    continue
                if attempt < retries:
                    time.sleep(0.8 * (attempt + 1))
            except requests.RequestException as e:
                last = e
                if attempt < retries:
                    time.sleep(0.8 * (attempt + 1))
        raise ProviderError(
            f"网络请求失败（已重试 {retries} 次）：{last}。请检查网络连接或代理设置"
        ) from last

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _llm_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.llm_key}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------ 模型列表
    def list_models(self, timeout: int = 8) -> list[str]:
        """实时抓取平台模型列表（OpenAI 兼容 GET /models）。
        用 LLM Key（方舟 /models 需 ark Key 鉴权，语音 Key 会 401）。"""
        if not self.llm_key:
            raise ProviderError("未填写 API Key")
        try:
            r = self._request(
                "GET", f"{self.llm_base_url}/models",
                headers=self._llm_headers(),
                timeout=timeout,
                use_proxy=self.llm_needs_proxy,
                force_close=is_domestic_url(self.llm_base_url),
            )
        except ProviderError as e:
            raise e
        if r.status_code == 401:
            raise ProviderError("API Key 无效（HTTP 401）")
        if r.status_code != 200:
            raise ProviderError(f"获取模型列表失败（HTTP {r.status_code}）")
        try:
            data = r.json()
            return [m["id"] for m in data.get("data", [])]
        except Exception as e:
            raise ProviderError(f"模型列表解析失败: {e}") from e

    def fetch_models_split(self) -> tuple[list[str], list[str]]:
        """抓取并按 ASR / LLM 分类。失败抛 ProviderError，由调用方决定回退。"""
        ids = self.list_models()
        asr = [m for m in ids if any(k in m.lower() for k in ASR_KEYWORDS)]
        llm = [m for m in ids if not any(k in m.lower() for k in ASR_KEYWORDS)]
        return (asr or ids), (llm or ids)

    # ------------------------------------------------------------ LLM 通道
    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        timeout: int = 60,
        max_tokens: int | None = None,
    ) -> str:
        """max_tokens：默认不传（让平台用模型默认输出上限）。Groq 免费档按
        OTPM（输出 token/分钟）预留容量，不传时按模型最大输出上限计算，一次请求
        就超限（429 "Request too large for model"）——STT 等短输出场景应显式传小值。"""
        if not self.llm_key:
            raise ProviderError("未填写 LLM API Key（火山平台请在配置中填入方舟 Key）")
        payload = {
            "model": self.llm_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        try:
            r = self._request(
                "POST", f"{self.llm_base_url}/chat/completions",
                headers=self._llm_headers(),
                json=payload,
                timeout=timeout,
                use_proxy=self.llm_needs_proxy,
                force_close=is_domestic_url(self.llm_base_url),
            )
        except ProviderError as e:
            raise e
        if r.status_code != 200:
            body = r.text[:300]
            if "Request too large" in body:
                body += "（当前模型输出速率受限 OTPM；短文本场景请降低 max_tokens，长文本翻译建议更换 LLM 模型或平台）"
            raise ProviderError(f"LLM 调用失败（HTTP {r.status_code}）: {body}")
        try:
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            logger.info(
                f"[debug] LLM 响应: status={r.status_code} "
                f"content_len={len(content or '')} content={str(content)[:120]!r}"
            )
            if not content:
                import json as _json
                logger.info(
                    f"[debug] LLM 空响应详情: model={self.llm_model} "
                    f"max_tokens={payload.get('max_tokens')} "
                    f"finish={data['choices'][0].get('finish_reason')} "
                    f"full={_json.dumps(data, ensure_ascii=False)[:600]}"
                )
            return content
        except Exception as e:
            raise ProviderError(f"LLM 响应解析失败: {e}") from e

    # ------------------------------------------------------------ ASR 通道
    def transcribe(
        self, audio_path: str | Path, prompt: str = "", timeout: int = 60
    ) -> str:
        if not self.api_key:
            raise ProviderError("未填写 API Key")
        path = Path(audio_path)
        if not path.exists():
            raise ProviderError(f"音频文件不存在: {path}")
        data: dict = {"model": self.asr_model}
        if prompt:
            data["prompt"] = prompt
        last: Exception | None = None
        last_body = ""
        for attempt in range(3):
            try:
                with path.open("rb") as f:
                    files = {"file": (path.name, f, "application/octet-stream")}
                    # ASR 转写也走统一请求入口：代理检测 + Session 复用 + 连接失败自动重建
                    # 注意：files 参数不能放在 _request 的 **kw 里（会被当 headers 处理），
                    # 所以这里直接调用 _request 的内部逻辑，传 files 参数
                    self._refresh_session_if_proxy_changed()
                    headers = {"Authorization": f"Bearer {self.api_key}"}
                    use_proxy = self.asr_needs_proxy
                    force_close = is_domestic_url(self.base_url)
                    if force_close:
                        headers["Connection"] = "close"
                    # 超时分离：连接 5s，读取用调用方设置
                    req_timeout = (5, timeout) if isinstance(timeout, (int, float)) else timeout
                    last_exc: Exception | None = None
                    for _attempt in range(3):
                        try:
                            r = self._session.post(
                                f"{self.base_url}/audio/transcriptions",
                                headers=headers,
                                proxies=system_proxy() if use_proxy else None,
                                data=data,
                                files=files,
                                timeout=req_timeout,
                            )
                            break
                        except (requests.ConnectionError, requests.exceptions.ProxyError,
                                requests.ReadTimeout, ConnectionResetError) as e:
                            # 连接类错误：重建 Session 重试
                            last_exc = e
                            if _attempt == 0:
                                try:
                                    self._session.close()
                                except Exception:
                                    pass
                                self._session = requests.Session()
                                self._last_proxy = self._proxy_fingerprint()
                                continue
                            time.sleep(0.8 * (_attempt + 1))
                        except requests.RequestException as e:
                            last_exc = e
                            if _attempt < 2:
                                time.sleep(0.8 * (_attempt + 1))
                    else:
                        raise ProviderError(
                            f"网络请求失败（已重试 2 次）：{last_exc}。请检查网络连接或代理设置"
                        ) from last_exc
                # 429 限流 / 5xx 节点抖动属瞬时业务错误，退避重试（免费档常见：第一次成功第二次撞限流）
                if r.status_code in (429,) or r.status_code >= 500:
                    last_body = r.text[:200]
                    last = ProviderError(f"HTTP {r.status_code}: {last_body}")
                    if attempt < 2:
                        time.sleep(1.0 + 1.5 * attempt)
                    continue
                break
            except requests.RequestException as e:
                last = e
                if attempt < 2:
                    time.sleep(0.8 * (attempt + 1))
        else:
            if isinstance(last, ProviderError):
                # 业务层耗尽（429/5xx 重试仍失败）：限流/节点抖动，提示与网络无关
                raise ProviderError(
                    f"服务暂时不可用（{last}），已重试仍失败。"
                    "可能是该平台免费额度/限流，请稍候再试"
                ) from last
            raise ProviderError(
                f"网络请求失败（已重试 2 次）：{last}。请检查网络连接或代理设置"
            ) from last
        if r.status_code != 200:
            _px = system_proxy()
            _net = f"走代理 {_px['https']}" if _px else "直连（未走代理）"
            raise ProviderError(
                f"转写失败（HTTP {r.status_code}，{_net}"
                + ("，已按瞬时错误重试仍失败" if last else "")
                + f"）: {last_body or r.text[:200]}"
            )
        try:
            return r.json().get("text", "")
        except Exception as e:
            raise ProviderError(f"转写响应解析失败: {e}") from e

    # ------------------------------------------------------------ 实时流（预留）
    def stream_transcribe(self, audio_stream):
        """实时流式转写。Groq 实时 API 形态核实后实现。"""
        raise NotImplementedError("实时流式转写待实现（Groq 实时 API 核实中）")

    # ------------------------------------------------------------ 测试
    def test_connection(self, timeout: int = 8) -> str:
        """返回成功摘要，失败抛 ProviderError。"""
        t0 = time.time()
        ids = self.list_models(timeout=timeout)
        ms = int((time.time() - t0) * 1000)
        return f"连接成功（{len(ids)} 个模型，耗时 {ms}ms）"


def build_messages(system: str, user: str) -> list[dict]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
