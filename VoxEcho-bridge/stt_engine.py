# -*- coding: utf-8 -*-
"""场景 3：语音输入引擎（录音 → VAD → 转写 → 风格/翻译后处理）。

设计要点：
- 按住热键说话、松开结束；VAD 只做质量门（去头尾静音 + 全静音拦截），不做实时截断
- 忠实记录（verbatim）= 纯 Whisper 通道（自带标点），不调 LLM，最省额度
- 风格化 / 翻译 = Whisper + LLM 一次请求（风格 prompt 与翻译指令拼成单个 System Prompt）
- 音频：流式上传前压缩为 OGG Vorbis（soundfile，约 1/9 体积）；快速按放单次路径仍直传 WAV
"""
from __future__ import annotations

import io
import threading
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_MS = 30


class VAD:
    """能量门控：按 30ms 块计算 RMS，保留首个/末个语音块并加前后 padding。"""

    def __init__(
        self,
        silence_thresh: int = 150,
        min_speech_blocks: int = 4,
        tail_pad_s: float = 0.2,
    ):
        # 阈值 150（原 500）：安静环境远距离说话 RMS 常在 200~600，原阈值会误裁；
        # 短噪声块（<4×30ms）被 min_speech_blocks 拦下，不误判
        self.silence_thresh = silence_thresh
        self.min_speech_blocks = min_speech_blocks
        self.tail_pad_s = tail_pad_s

    def trim(self, audio: np.ndarray) -> np.ndarray | None:
        """返回去头尾静音后的音频；全静音或语音过短返回 None。"""
        block = int(SAMPLE_RATE * BLOCK_MS / 1000)
        n = len(audio) // block
        if n == 0:
            return None
        x = audio[: n * block].reshape(n, block).astype(np.float32)
        rms = np.sqrt((x * x).mean(axis=1))
        speech = rms >= self.silence_thresh
        idx = np.where(speech)[0]
        if idx.size < self.min_speech_blocks:
            return None
        first, last = int(idx[0]), int(idx[-1])
        pad = int(self.tail_pad_s * 1000 / BLOCK_MS)
        first = max(0, first - pad)
        last = min(n - 1, last + pad)
        return audio[first * block : (last + 1) * block]


_vad = VAD()


# --- 降噪（NS） ---
# 与 AGC 组成输入端信号链：降噪 → VAD → AGC。
# 做法：短时傅里叶变换 → 每频点噪声底估计（全段最低 10% 分位，min-tracking，
# 对"开口即说话"也鲁棒）→ Wiener 式增益 g = mag2/(mag2 + α·noise2)（下限 β，抑制音乐噪声）
# → 逆变换。纯静音段（整段能量过低）直接返回，避免把底噪抬升。
_DENOISE_ALPHA = 3.0    # Wiener 分母系数（谱减过减值）：噪声区压 60%+，语音共振峰 g≈1 不受影响
_DENOISE_BETA = 0.02    # 增益下限（剩余噪声地板，防"水下"感）
_DENOISE_FRAME = 400    # 25ms @16k
_DENOISE_HOP = 160      # 10ms
_DENOISE_PCT = 0.10     # 噪声底取每频点最低 10% 分位


def _stft(x: np.ndarray, frame: int, hop: int):
    pad = frame // 2
    xp = np.pad(x, (pad, frame - pad), mode="reflect")
    n = (len(xp) - frame) // hop + 1
    win = np.hanning(frame).astype(np.float32)
    cols = np.stack([xp[i * hop : i * hop + frame] for i in range(n)])
    return np.fft.rfft(cols * win, axis=1), win


def _istft(S: np.ndarray, win: np.ndarray, hop: int) -> np.ndarray:
    frame = len(win)
    n = S.shape[0]
    out = np.zeros((n - 1) * hop + frame, np.float32)
    wsum = np.zeros_like(out)
    x = np.fft.irfft(S, axis=1)
    for i in range(n):
        out[i * hop : i * hop + frame] += x[i] * win
        wsum[i * hop : i * hop + frame] += win * win
    return out / np.maximum(wsum, 1e-8)


def denoise(raw: bytes) -> bytes:
    """对裸 int16 bytes 做噪声抑制（Wiener 式谱门控）。整段能量过低（纯静音/底噪）原样返回。"""
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if a.size < _DENOISE_FRAME * 2:
        return raw
    rms = float(np.sqrt((a * a).mean()))
    if rms < _AGC_NOISE_GATE / 32768.0:
        return raw  # 疑似纯静音/底噪，不做处理（与 AGC 门限一致）
    S, win = _stft(a, _DENOISE_FRAME, _DENOISE_HOP)
    mag = np.abs(S)
    phase = np.angle(S)
    # 噪声底：能量最低的 10% 帧的平均谱（帧级选择 + 谱平均）。
    # 不用 per-bin 分位：白噪声 FFT 幅度分布宽（Rayleigh），最低分位会严重低估噪声水平，
    # 导致增益 g 偏高、噪声压不动；帧级选择对"开口即说话"也鲁棒（停顿帧落在最低能量区）。
    e = (mag * mag).sum(axis=1)
    k = max(1, int(mag.shape[0] * _DENOISE_PCT))
    quiet_idx = np.argpartition(e, k)[:k]
    noise = mag[quiet_idx].mean(axis=0)
    noise2 = np.maximum(noise * noise, 1e-12)
    mag2 = mag * mag
    g = mag2 / (mag2 + _DENOISE_ALPHA * noise2)
    g = np.maximum(g, _DENOISE_BETA)
    # 低频压制：<60Hz 频点额外衰减（去 50/60Hz 交流声与 DC 漂移，不影响语音基频）
    g[:, np.fft.rfftfreq(_DENOISE_FRAME, 1.0 / SAMPLE_RATE) < 60.0] *= 0.05
    out = _istft(mag * g * np.exp(1j * phase), win, _DENOISE_HOP)
    # 裁掉反射 padding 的边缘帧（那里 OLA 权重极小、会被异常放大）
    out = out[_DENOISE_FRAME // 2 : _DENOISE_FRAME // 2 + a.size]
    return np.clip(out * 32768.0, -32768, 32767).astype(np.int16).tobytes()


# --- 自动增益控制（AGC） ---
# 复刻微信语音输入"离麦克风远也能收到"的关键一环：采集是裸 int16，
# 距离远时语音 RMS 很低（~200-600），Whisper 对低音量敏感且易被 VAD 裁掉。
# 做法：VAD 用原始信号判断（阈值已降低），送入识别的音频统一提升到目标 RMS。
# 噪声门限：疑似静音/底噪段（RMS 过低）不放大，避免把环境噪音抬成"语音"。
_AGC_TARGET_RMS = 4000.0   # 目标 RMS（int16 满刻度 32768，≈ -18 dBFS，正常说话水平）
_AGC_MAX_GAIN_DB = 24.0    # 最大增益 24 dB（RMS 250 的小声即可拉到目标；底噪 <100 由门限挡住不放大）
_AGC_NOISE_GATE = 100      # RMS 低于此值视为静音/底噪，不做增益


def apply_agc_int16(a: np.ndarray) -> np.ndarray:
    """对 int16 ndarray 做自动增益（去直流 → 限幅提升 → clip）。静音段原样返回。"""
    if a.size < 256:
        return a
    x = a.astype(np.float32)
    x -= x.mean()  # 去直流偏置
    rms = float(np.sqrt((x * x).mean()))
    if rms < _AGC_NOISE_GATE:
        return a
    gain = min(_AGC_TARGET_RMS / rms, 10 ** (_AGC_MAX_GAIN_DB / 20.0))
    x *= gain
    np.clip(x, -32768, 32767, out=x)
    return x.astype(np.int16)


def apply_agc(raw: bytes) -> bytes:
    """bytes 版 AGC（int16 裸采样）。"""
    return apply_agc_int16(np.frombuffer(raw, dtype=np.int16)).tobytes()


def wav_bytes_from_int16(raw: bytes, sr: int = SAMPLE_RATE) -> bytes:
    """把 16k mono int16 裸采样封成 WAV bytes。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(raw)
    return buf.getvalue()


def compress_audio(raw: bytes, sr: int = SAMPLE_RATE) -> bytes | None:
    """16k mono int16 裸采样 → MP3 bytes（lameenc 编码，64kbps）。
    为什么用 MP3 而不是 OGG Vorbis：
      - libsndfile 的 Vorbis 编码在 Windows 上对长音频会原生栈溢出（0xC00000FD，
        实测约 50s 必崩、40s+ 有概率），原生崩溃无 Python traceback；
      - lameenc 是纯 Python 绑定的 LAME MP3 编码器，不依赖 libsndfile，永不崩；
      - MP3 压缩比约 8-10x（5s 音频从 160KB → 16-20KB），Whisper 对 MP3 识别率与 WAV 几乎一致；
      - 64kbps 对 16k 语音足够，再高体积增加但识别率无提升。
    压缩失败返回 None（调用方回退 WAV）。
    """
    try:
        import lameenc
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(64)
        encoder.set_in_sample_rate(sr)
        encoder.set_channels(1)
        encoder.set_quality(2)  # 2=high quality (0-9, 0 best)
        mp3 = encoder.encode(raw)
        mp3 += encoder.flush()
        return mp3 if mp3 else None
    except Exception:
        return None


class Recorder:
    """按住说话：start() 开始采集整段，stop() 停止并返回裸 int16 bytes（降噪 → VAD 裁剪 → AGC）。"""

    def __init__(self, sample_rate: int = SAMPLE_RATE, on_block=None):
        self.sr = sample_rate
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._active = False
        self._lock = threading.Lock()
        self._on_block = on_block  # 可选实时帧回调（每块约200ms int16，回调线程调用，须轻量如 queue.put）
        self.rms = 0.0  # 最近一块音频的 RMS（声浪波形驱动，波形动画每秒约 14 帧读取）

    def set_on_block(self, cb):
        """运行时切换实时帧回调（火山流式：录音期间把块推给发送线程）。"""
        with self._lock:
            self._on_block = cb

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def start(self) -> bool:
        with self._lock:
            if self._active:
                return False
            self._frames = []
            self._active = True
        self._stream = sd.InputStream(
            samplerate=self.sr,
            channels=CHANNELS,
            dtype="int16",
            blocksize=int(self.sr * BLOCK_MS / 1000),
            callback=self._cb,
        )
        self._stream.start()
        return True

    def _cb(self, indata, frames, time_info, status):
        data = indata.copy()
        try:
            self.rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
        except Exception:
            pass
        cb = None
        with self._lock:
            if self._active:
                self._frames.append(data)
                cb = self._on_block
        if cb is not None:
            try:
                cb(data)
            except Exception:
                pass

    def stop_stream(self) -> None:
        """停止采集（保留已采帧）。"""
        with self._lock:
            self._active = False
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def stop(self) -> bytes | None:
        """停止录音。返回裸 int16 bytes（降噪 → VAD 裁剪 → AGC）；静音/过短返回 None。"""
        self.stop_stream()
        with self._lock:
            if not self._frames:
                return None
            audio = np.concatenate(self._frames)
        audio_int16 = np.frombuffer(denoise(audio.tobytes()), dtype=np.int16)
        audio = _vad.trim(audio_int16)
        if audio is None or len(audio) < int(0.1 * self.sr):
            return None
        audio = apply_agc_int16(audio)
        return audio.tobytes()


# ---------------------------------------------------------------- Prompt 资产
# 分层解耦：
# - Whisper 层：ASR 靠"示范"而非"指令"工作 → 全局固定标点引导，单通道（忠实记录）也使用
# - LLM 层：模块化 System Prompt 工厂，风格积木 × 翻译积木自由叠加，单次请求完成

# ASR 层引导 Prompt（解决 Whisper 漏标点；忠实记录单通道同样生效）。
# Whisper 靠"示范"工作：标点示范应使用目标语言的标点体系（CJK → 全角，拉丁 → 半角）。
# 设计原则：
#   1. 示范要短而密集——Groq 的 Whisper 对长 Prompt 利用不充分，短句示范更有效
#   2. 每句都带标点，覆盖逗号、句号、问号、感叹号
#   3. 粤语示范单独放，只在检测到粤语时才拼入（避免干扰中文标点引导）
WHISPER_PUNCTUATION_PROMPT = (
    "以下是标准听写文本，每句末尾必须加标点（逗号、句号、问号、感叹号），"
    "停顿处用逗号，句末用句号，不要输出任何孤立的单个字母或无意义字符："
    "你好，请问今天的会议几点开始？"
    "我们下午三点开会，请准时参加。"
    "这个方案很好，我同意，就这么定了。"
)
# 粤语专用示范（检测到粤语时拼入，保留粤语白话字）
WHISPER_CANTONESE_PROMPT = (
    "以下是粵語聽寫，請保留粵語白話字，不要轉成普通話："
    "我今晚唔翻去瞓，喺屋企食飯。"
    "佢話唔得閒，唔嚟喇，雞髀留畀我。"
    "你喺邊度？幾時嚟？冇問題，咁樣得唔得？"
)


HOTWORDS_FILE = Path(__file__).resolve().parent / "hotwords.txt"
_MAX_HOTWORDS = 500
_MAX_HOTWORDS_CHARS = 2500


def load_hotwords(path=None) -> list[str]:
    """读取热词文件：每行一个词，# 开头为注释，去重并按条数/总长截断。文件缺失/异常返回空。"""
    p = Path(path) if path else HOTWORDS_FILE
    try:
        raw = p.read_text(encoding="utf-8-sig")
    except Exception:
        return []
    words: list[str] = []
    seen: set[str] = set()
    total = 0
    for line in raw.splitlines():
        w = line.strip()
        if not w or w.startswith("#") or w in seen:
            continue
        if total + len(w) > _MAX_HOTWORDS_CHARS:
            break
        seen.add(w)
        words.append(w)
        total += len(w)
        if len(words) >= _MAX_HOTWORDS:
            break
    return words


def build_whisper_prompt(lang_hint: str = "zh", cantonese: bool = False) -> str:
    """按语言选择全角/半角标点示范，并拼入热词纠偏（如有）。
    lang_hint: 主语言代码（zh/ja/ko/en 等）
    cantonese: 是否检测到粤语，检测到则拼入粤语专用示范（保留粤语白话字）
    """
    if lang_hint in ("zh", "ja", "ko"):
        base = WHISPER_PUNCTUATION_PROMPT
        if cantonese:
            # 粤语示范拼在中文标点示范后面，先引导标点，再引导粤语保留
            base = base + WHISPER_CANTONESE_PROMPT
    else:
        base = (
            "This is a standard dictation text with proper punctuation: "
            "Hello, what time does the meeting start today? "
            "We need to discuss the project status. This approach works well!"
        )
    hot = load_hotwords()
    if hot:
        base += "\n以下专有名词可能出现在语音中，请优先准确识别：" + " ".join(hot)
    return base

# --- 基础风格积木 (Style Blocks) ---
STYLE_RULES = {
    "verbatim": (
        "Role: Verbatim Transcription Post-Processor.\n"
        "Task: Add proper punctuation and capitalization.\n"
        "Constraint: Retain EVERY word exactly as spoken, including all filler words "
        "(e.g., 'uh', 'um', 'I mean'), false starts, and interjections. Do NOT remove or alter anything."
    ),
    "fluent": (
        "Role: Speech-to-Text Refiner.\n"
        "Task: Clean up spoken text by removing all verbal fillers (e.g., 'uh', 'um', 'let me see'), "
        "false starts, and hesitations.\n"
        "Constraint: Enhance clarity and flow while strictly preserving the core original message and key terminology.\n"
        "Punctuation: Add punctuation strictly by meaning; every sentence MUST end with a terminal mark "
        "(period / question mark / exclamation). For Chinese input use ONLY full-width punctuation "
        "（，。！？；：）, never half-width comma or period ( , . ); for English input use standard punctuation."
    ),
    "email": (
        "Role: Executive Assistant for Business Email Creation.\n"
        "Task: Convert spoken raw text into a professional, structured email.\n"
        "Constraint: Include [Subject Line], proper [Salutation], structured [Body Paragraphs], and [Sign-off]. "
        "Replace missing info with placeholders like [Name]."
    ),
    "formal": (
        "Role: Technical Editor for Formal Documentation.\n"
        "Task: Rewrite spoken content into an authoritative, highly formal document style.\n"
        "Constraint: Eliminate slang and conversational phrasing. Use bullet points or numbered lists "
        "where applicable for logical structure."
    ),
}


# 同音纠错积木：先于风格化执行。专治 ASR 同音/谐音错字（三十二例→三十而立），
# 铁律：只修错字，不改写、不润色、不删口头语、不改事实（LLM 自作主张改话是最大风险）。
HOMOPHONE_RULE = (
    "Step 1 - Homophone Correction (run this FIRST, before any style processing):\n"
    "The input is a speech-to-text result that may contain homophone errors: characters or words "
    "that sound identical or very similar but are wrong (e.g. '三十二例' when the context means "
    "'三十而立'; '拳脚' when '全角' is meant).\n"
    "Correct ONLY obvious homophone/phonetic errors, inferred from context and common usage "
    "of the input language.\n"
    "Hard constraints: Do NOT rewrite, polish, summarize, or rephrase. Do NOT remove filler words "
    "(e.g. '嗯', '啊', 'um'). Do NOT change facts, numbers, names, or meaning. "
    "If a segment is plausibly correct as-is, leave it unchanged.\n"
    "Then proceed to the style task below."
)


def build_system_prompt(
    style_mode: str = "fluent",
    enable_translation: bool = False,
    target_lang: str = "English",
    custom_prompt: str | None = None,
) -> str:
    """根据 UI 勾选，动态拼接单次 LLM 请求的顶级 System Prompt（同音纠错 × 风格 × 翻译自由叠加）。
    style_mode='custom' 时用 custom_prompt 作为风格积木。
    verbatim（忠实记录）不走本函数（单通道直出）；本函数仅用于调 LLM 的模式。"""
    if style_mode == "custom" and custom_prompt and custom_prompt.strip():
        base_style_rule = custom_prompt.strip()
    else:
        base_style_rule = STYLE_RULES.get(style_mode, STYLE_RULES["fluent"])

    if enable_translation:
        translation_rule = (
            f"Language Instruction: Translate the final refined result into idiomatic, native-sounding {target_lang}. "
            "Ensure natural expressions rather than direct word-for-word translation.\n"
            f"Output Constraint: Output ONLY the final processed content in {target_lang}. "
            "Do NOT include Chinese/Cantonese, markdown wrappers, or translator commentary."
        )
    else:
        translation_rule = (
            "Language Instruction: Preserve the original language of the input spoken text "
            "(including Cantonese or CJK/English code-switching).\n"
            "Output Constraint: Output ONLY the final refined content in its original language. "
            "Do NOT add meta explanations."
        )

    return f"{HOMOPHONE_RULE}\n\n{base_style_rule}\n\n{translation_rule}".strip()


def process_result(
    provider,
    text: str,
    style: str,
    translate: bool,
    target_lang: str,
    custom_prompt: str | None = None,
    timeout: int = 90,
    max_tokens: int | None = 512,
) -> str:
    """忠实记录且不翻译 → 纯 Whisper 直出（不调 LLM，零多余交互）；否则一次 chat 完成风格/翻译。
    style='custom' 时需传 custom_prompt（用户自定义积木）。
    max_tokens 默认 512：Groq 免费档 qwen 系 OTPM=1000，不设上限按模型最大输出预留会直接 429
    "Request too large for model"。STT 输出中文几百字足够，512 < 1000 不触发限流。"""
    from normalize import normalize_text

    if style == "verbatim" and not translate:
        # 单通道直出：只做轻量格式化（数字/缩写/空格/标点），不调 LLM、不改语义
        return normalize_text(text.strip())
    system = build_system_prompt(style, translate, target_lang, custom_prompt)
    from provider import build_messages

    final = provider.chat(
        build_messages(system, text),
        temperature=0.2,
        timeout=timeout,
        max_tokens=max_tokens,
    ).strip()
    # LLM 输出同样过一遍格式层（补齐 LLM 偶尔漏的全角/数字/缩写规范）
    return normalize_text(final)
