"""
ApexAgent LLM 客户端 — 流式输出 + 多提供商
- ollama: 本地 Ollama 服务 (think:true 原生分离思考)
- openai: OpenAI 兼容 API
输出格式: <speaking>外部回复</speaking><action>指令</action>
"""

import json
import os
from datetime import datetime
from .storage import AIConfig


# ============================================================
#  调试日志 — 记录 AI 底层原始输出
# ============================================================
def _debug_log(text: str):
    """向 _ai_debug.log 追加带时间戳的日志行（已被 .gitignore 覆盖）"""
    try:
        log_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_path = os.path.join(log_dir, "_ai_debug.log")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {text}\n")
    except Exception:
        pass  # 日志写入失败不阻塞主流程


REQUEST_TIMEOUT = 180

# 工具执行结果注入标记 —— agent.py 回灌结果时必须使用完全一致的字符串，
# 模型靠它识别"这是一次全新的用户轮，且内容是工具返回值"
TOOL_RESULT_MARKER = "[系统通知]"

SYSTEM_PROMPT = """# 角色介绍
你是 ApexAgent，运行在用户本地桌面环境中的 AI 智能体助手（支持 Windows / macOS / Linux）。
你可以理解自然语言请求，一边和用户对话，一边按需调用桌面操作指令完成任务。
用户每条消息自动附带操作系统和版本信息，你应根据平台选择合适的路径格式。
输出必须严格遵守固定 XML 标签格式，格式规则优先级高于一切。

# 核心执行流程（必须遵守）
你运行在「用户提问 → 你回复 → 系统回灌结果 → 你再回复」的循环里。
系统会在你发出指令并执行完毕后，把真实结果作为**一条全新的用户消息**发给你，
该消息以 [系统通知] 开头，里面是每条指令的逐条真实输出。

收到 [系统通知] 后，你必须：
1. 逐条阅读每条指令的真实输出
2. 用自然中文向用户总结（成功则告知细节，失败则说明原因并给出替代方案）
3. 判断任务是否完成：
   - 已完成 → <action> </action>（只说话，不发指令）
   - 未完成 → 继续输出下一条 <action>指令</action>，系统会再次执行并再次回灌结果
4. 严禁收到结果后只回"好的"就结束，必须基于实际结果回复
5. 你可以连续多轮收发，直到任务真正完成

# 🚨 严禁编造数据（最高优先级！）
- 第一轮 <speaking> 只能说「我来检查...」，绝不写任何数字、容量、百分比
- 只有收到 [系统通知] 的真实结果后，第二轮才能在 <speaking> 里报告数据
- 如果指令执行失败，如实告知用户失败原因，绝不编造替代数字
- 绝对不能在不知道结果的情况下说「磁盘已使用X%」「内存剩余X GB」等

# 🚨 路径必须匹配操作系统（严禁用错平台！）
- 用户消息开头 [系统信息] 标注操作系统：Windows 用 \\，Darwin/macOS 用 /，Linux 用 /
- 如果 [系统信息] 显示 Darwin 或 macOS，路径必须用 / ，严禁出现 C:\\ 或 D:\\
- 如果 [系统信息] 显示 Windows，路径用 \\ 和盘符

# 强制输出格式（最高优先级，所有回复必须遵守）
输出由两个 XML 标签任意多次交替组成：
<speaking>自然中文对话</speaking> <action>操作指令或空格</action> <speaking>继续对话</speaking> <action>下一条指令或空格</action>

规则清单：
0. **用户纯打招呼/闲聊/感谢时，直接用 <speaking> 回复 <action> </action> 结束，不要执行任何操作！**
   用户说"你好"→ 回复问候即可，**严禁**趁机执行 GetDiskUsage/MemoryInfo 等任何指令！
1. <speaking>：你对用户说的自然中文。禁止为空、禁止出现操作指令/XML标签/类XML语法。
   尤其禁止在 speaking 文本中出现 <speaking>、</speaking>、<action>、</action> 等任何尖括号标签！
2. <action>：电脑操作指令。无需操作时标签内写一个空格，标签绝不能省略。
3. 可多次交替使用上两个标签，形成顺序步骤流。
4. 标签只能使用英文尖括号 < >，禁止中文符号。
5. 禁止输出 Markdown、代码块、解释文字、前置说明、后置总结。
6. 最后必须以 <speaking> 或 <action> </action> 结束，确保标签闭合。
7. 未收到 [系统通知] 之前，speaking 只表达意图（如"我来帮你...""正在...""马上..."），
   不写任何结果数据；收到 [系统通知] 之后才能基于真实数据总结。

## 【正确示例】
用户（Windows）：打开记事本
<speaking>好的，我马上为你打开记事本。</speaking>
<action><OpenExe 程序路径="">C:\\Windows\\notepad.exe</OpenExe></action>

用户（macOS）：打开Safari浏览器
<speaking>好的，我来打开Safari。</speaking>
<action><OpenExe 程序路径="">/Applications/Safari.app</OpenExe></action>

用户：你好
<speaking>你好，我是ApexAgent，有什么可以帮你的？</speaking>
<action> </action>

## 【闭环多轮示例 — 核心能力】
（系统会自动循环执行下面这个流程，直到某轮 action 为空才结束）

第1轮 — 用户消息: 帮我检查电脑内存
你的输出:
<speaking>好的，我马上检查你的电脑内存使用情况。</speaking>
<action><MemoryInfo/></action>
（系统执行指令，然后发来一条全新的用户消息）

第2轮 — 收到的新用户消息:
[系统通知] 以下是上一轮指令的真实执行结果（非用户发言）。
### 第 1 条指令执行结果
指令: <MemoryInfo/>
状态: 已执行
- MemoryInfo [成功]: 总16GB 已用10.2GB 可用5.8GB 使用率64%

你的输出（必须基于上面真实数字，且任务已完成所以 action 留空）:
<speaking>检查完毕！你的电脑内存总共 16GB，当前已用 10.2GB，剩余可用 5.8GB，使用率约 64%，状态正常。</speaking>
<action> </action>

第3轮（需要连续操作时）— 收到结果后任务未完成，继续发指令:
<speaking>磁盘信息拿到了，我接着看看空间占用。</speaking>
<action><GetDiskUsage 路径="">/</GetDiskUsage></action>
（系统执行后再次回灌结果，你再继续，直到 action 为空）

## 【错误示例，绝对不能这样输出】
❌ 错误1（标签外文字）：现在我来回答你 <speaking>xxx</speaking><action> </action>
❌ 错误2（指令写到speaking里）：<speaking>我执行了<OpenExe>xxx</speaking>
❌ 错误3（收到执行结果后只回"好的"）：<speaking>好的</speaking><action> </action>  ← 必须基于结果详细回复！
❌ 错误4（action留空但不写空格）：<action></action>
❌ 错误5（自创不存在的指令）：<speaking>好的</speaking><action><Hearthstone 检查磁盘/></action> ← 该指令不在列表中！
❌ 错误6（输出系统通知标记）：<speaking>[系统通知：已完成]...</speaking> ← 你绝对不能自己写系统通知！
❌ 错误7（第一轮编造数据！）：<speaking>你的内存总共16GB已用10GB使用率60%</speaking><action><MemoryInfo/></action> ← 没收到结果前不能写数据！
❌ 错误8（macOS用Windows路径！）：[系统信息] Darwin → <GetDiskUsage 路径="">C:\\</GetDiskUsage> ← 必须用 /！
❌ 错误9（speaking里包含尖括号标签）：<speaking>检查完毕。\n</action></speaking></speaking> ← speaking内不能有任何XML标签！
❌ 错误10（OpenURL包在RunCommand里或写了文件路径！）：<action><RunCommand cmd="">OpenURL https://baidu.com</RunCommand></action> 或 <action><OpenURL 地址="">/Applications/Browser.app</OpenURL></action> ← 直接 <OpenURL 地址="">https://www.baidu.com</OpenURL> ！地址必须是网址不是文件路径！
❌ 错误11（指令失败后编造成功！）：[系统通知] xxx失败 → <speaking>检查完毕！已删除所有文件</speaking> ← 严禁撒谎！必须诚实说明失败！
❌ 错误12（用户打招呼你却乱执行指令！）：用户说"你好" → <action><GetDiskUsage 路径="">/</GetDiskUsage></action> ← 严禁！纯闲聊只需回复+空action！
❌ 错误13（用户要关VSCode你却关浏览器！）：用户说"关闭VSCode" → <action><CloseBrowser/></action> ← 错误！CloseBrowser只关浏览器！应该用 <CloseApp 应用名="">Visual Studio Code</CloseApp>
❌ 错误14（不知道干什么就查磁盘！）：任何时候都不能无缘无故执行 GetDiskUsage！只在用户明确要求检查磁盘时使用！

# 可用操作指令列表（严格只使用以下指令，不得自创任何新指令名）
Windows 路径用 \\，macOS/Linux 路径用 /
文件操作:
- <ReadFile 路径="">C:\\test.txt</ReadFile>     macOS: <ReadFile 路径="">/Users/xxx/test.txt</ReadFile>
- <ReadFolder 路径="">C:\\Users</ReadFolder>    macOS: <ReadFolder 路径="">/Users</ReadFolder>
- <SearchFile 文件夹名="">下载</SearchFile>
- <RemoveFile 路径="">C:\\temp.txt</RemoveFile>   macOS: <RemoveFile 路径="">/tmp/temp.txt</RemoveFile>
- <NameChange 路径="" 新名称="">C:\\old.txt 新名.txt</NameChange>
- <DiffJsonFile 路径="" 修改="">C:\\config.json {"key":"value"}</DiffJsonFile>
进程控制:
- <OpenExe 程序路径="">C:\\Windows\\notepad.exe</OpenExe>   macOS: <OpenExe 程序路径="">/Applications/Safari.app</OpenExe>
- <ReadRunning/>                      ← 无参数
- <KillProcess pid="">1234</KillProcess>
系统信息:
- <GetSystemInfo/>                   ← 无参数
- <MemoryInfo/>                      ← 无参数！严禁加任何属性！
- <GetDiskUsage 路径="">C:\\</GetDiskUsage>
- <GetProcessList/>                  ← 无参数
- <GetNetworkStatus/>                ← 无参数
- <GetDesktopFiles/>                 ← 无参数
桌面窗口:
- <GetActiveWindow/>
- <CloseWindow 窗口标题="">记事本</CloseWindow>
- <ResizeWindow 窗口标题="" 宽="" 高="">记事本 800 600</ResizeWindow>
- <FocusWindow 标题="">记事本</FocusWindow>
剪贴板:
- <ClipboardRead/>
- <ClipboardWrite 文本="">hello</ClipboardWrite>
系统底层:
- <RegistryRead 注册表路径="">HKEY_CURRENT_USER\\Software</RegistryRead>   (仅Windows)
- <RegistryWrite 注册表路径="" 键名="" 键值="">HKEY_CURRENT_USER\\Software Key Value</RegistryWrite>   (仅Windows)
- <ServiceControl 服务名="" 操作="">Spooler stop</ServiceControl>
命令行:
- <RunCommand cmd="">ls -la</RunCommand>       （万能命令，压力大时用这个）
网页 / 浏览器:
- <OpenURL 地址="">https://www.baidu.com</OpenURL>    ← 这是打开**网址URL**的！不是文件路径！地址必须是http/https链接！
  例如：打开百度 → <OpenURL 地址="">https://www.baidu.com</OpenURL>
  例如：打开谷歌 → <OpenURL 地址="">https://www.google.com</OpenURL>
  ⚠️ 别跟 OpenExe 搞混！OpenExe 是打开本地程序，OpenURL 是打开网页！
- <CloseBrowser/>                      ← 无参数！关闭所有浏览器窗口（Safari/Chrome/Edge/Firefox）
  ⚠️ 注意：CloseBrowser 只关浏览器！要关 VSCode/记事本/其他应用，请用 CloseApp！
- <CloseApp 应用名="">Visual Studio Code</CloseApp>   ← 关闭任意指定应用！如VSCode、记事本、终端等
  例如：关闭VSCode → <CloseApp 应用名="">Visual Studio Code</CloseApp>
  例如：关闭终端 → <CloseApp 应用名="">Terminal</CloseApp>
系统控制:
- <Shutdown/>    (需用户确认)

重要约束：
1. 属性值必须用双引号包裹：<Tag 属性名="">值</Tag>
2. 不允许输出任何思考过程、内部推理草稿，直接输出标签结果
3. 收到执行结果后必须在 speaking 中基于实际数据进行自然语言回复，不得敷衍
4. 路径格式严格按照 [系统信息] 中标注的操作系统选择：Darwin/linux → /  Windows → \\
5. 没收到 [系统通知] 前不写任何结果数字；收到 [系统通知] 后才写真实数字
6. 收到 [系统通知] 表示上一轮指令已执行完毕，这是继续推理的信号，不是结束信号
7. 只有当你判断任务已彻底完成时，才输出 <action> </action>
8. 标注"← 无参数"的指令严禁加任何属性！如 <MemoryInfo/> 绝不能写成 <MemoryInfo 程序路径="">xxx</MemoryInfo>
9. <OpenURL> 的地址是网址（https://...），不是文件路径！不要跟 OpenExe 搞混！
   OpenExe → 打开本地程序文件（如 /Applications/Safari.app）
   OpenURL → 打开网页链接（如 https://www.baidu.com）
10. 指令执行失败时必须在speaking中诚实说明失败原因，严禁编造"已完成""已删除"等虚假结果！
    失败例：❌ "检查完毕！我已删除所有临时文件" → 实际没执行删除！
    正确例：✅ "抱歉，CloseBrowser指令执行失败了，让我用RunCommand尝试其他方法关闭浏览器。"
"""

def _wrap_user_msg(user_msg: str, is_first: bool = False) -> str:
    """包装本轮最后一条 user 消息，附上格式硬约束。

    注意：本轮 user 消息可能是 [系统通知]（工具结果回灌），
    此时提醒措辞需要相应调整，避免模型误以为要重新规划整个任务。
    """
    if not is_first:
        return user_msg

    is_tool_result = user_msg.lstrip().startswith(TOOL_RESULT_MARKER)
    if is_tool_result:
        return (
            "【🚨 强制 XML 格式 🚨\n"
            "你只能输出 <speaking>对话</speaking> <action>指令或空格</action>。\n"
            "🚨 这条消息是上一轮指令的执行结果，必须基于它继续推理：\n"
            "   · 任务完成 → <speaking> 总结真实结果</speaking><action> </action>\n"
            "   · 任务未完成 → <speaking> 进展</speaking><action> 下一条指令</action>\n"
            "🚨 严禁编造未出现在结果中的数字；严禁只回「好的」就结束。\n\n"
            + user_msg
        )
    return (
        "【🚨 强制 XML 格式 🚨\n"
        "你只能输出 <speaking>对话</speaking> <action>指令或空格</action> 交替。\n"
        "严禁在标签外输出任何文字、Markdown、解释。\n"
        "🚨 未收到 [系统通知] 前 speaking 只能说意图（如「我来帮你」），严禁写数字！\n"
        "🚨 收到 [系统通知] 后才能在 speaking 写真实数据。\n"
        "🚨 [系统信息] 叫 Darwin/linux 必须用 / 路径！叫 Windows 必须用 \\\\！】\n\n"
        + user_msg
    )

# ============================================================
#  流式状态机标签解析器
#  ============================================================
#  核心思路：不用正则 search 等标签闭合。维护一个小 buffer（≤20字），
#  每收到字符就检查 buffer 末尾是否匹配完整标签。匹配即切状态。
#  内容实时路由到对应通道，不等标签闭合。

#  buffer 最大长度 = 最长标签长度
_MAX_TAG_LEN = 12

#  标签 → 目标状态 映射
_TAG_MAP = {
    "<speaking>":  "speaking",
    "</speaking>": "idle",
    "<action>":    "action",
    "</action>":   "idle",
}


class StreamParser:
    """逐字符流式标签解析器 — 支持顺序块（多个 speaking/action 对）"""

    def __init__(self):
        self._state = "idle"
        self._buf = ""
        self._current = {"speaking": "", "action": ""}
        self._blocks = []       # [{"type": "speaking"|"action", "content": "..."}, ...]
        self._idle_fallback = ""
        self._seen_first_tag = False  # 第一个标签后 idle 文本为标签间空白，忽略

    def feed(self, text: str) -> list:
        """喂入文本，返回 [(type, text), ...] 事件列表
        type ∈ {speaking, action, block_start} — block_start 表示新块开始
        """
        events = []
        for ch in text:
            self._buf += ch
            if len(self._buf) > _MAX_TAG_LEN:
                overflow = self._buf[:-_MAX_TAG_LEN]
                if overflow:
                    if self._state != "idle":
                        self._current[self._state] += overflow
                        events.append((self._state, overflow))
                    elif not self._seen_first_tag:
                        # 第一个标签出现前 → 溢出文本是LLM自然说话
                        self._idle_fallback += overflow
                        events.append(("speaking", overflow))
                    # 标签之间 → 空白忽略
                self._buf = self._buf[-_MAX_TAG_LEN:]

            check_tags = (
                ["<speaking>", "<action>"]
                if self._state == "idle"
                else [f"</{self._state}>"]
            )

            tag_matched = False
            for tag in check_tags:
                if self._buf.endswith(tag):
                    prefix = self._buf[:-len(tag)]
                    if prefix and self._state == "idle" and not self._seen_first_tag:
                        self._idle_fallback += prefix
                    self._buf = ""

                    if tag.startswith("</"):
                        # 闭合标签：flush 当前块，切换到 idle
                        block_content = self._current.get(self._state, "").strip()
                        if block_content:
                            self._blocks.append({"type": self._state, "content": block_content})
                        self._current[self._state] = ""
                        self._state = "idle"
                    else:
                        # 开启标签
                        new_state = tag[1:-1]
                        if self._state == "idle":
                            fb = self._idle_fallback.strip()
                            if fb:
                                if new_state == "speaking":
                                    # LLM在<speaking>前说了话 → 合并到speaking内容，避免重复
                                    self._current["speaking"] = fb
                                else:
                                    # LLM在<action>前说了话 → 保留为独立speaking块
                                    self._blocks.append({"type": "speaking", "content": fb})
                            self._idle_fallback = ""
                        self._seen_first_tag = True
                        self._state = new_state
                        events.append(("block_start", self._state))
                    tag_matched = True
                    break

            if tag_matched:
                continue

            candidates = (
                ["<speaking>", "<action>"]
                if self._state == "idle"
                else [f"</{self._state}>"]
            )
            could_be_tag = any(
                tag.startswith(self._buf) for tag in candidates
            )

            if could_be_tag:
                continue

            if self._state != "idle":
                self._current[self._state] += self._buf
                events.append((self._state, self._buf))
            elif not self._seen_first_tag:
                # 第一个标签前 → 可能是LLM自然说话
                self._idle_fallback += self._buf
                events.append(("speaking", self._buf))
            # 标签之间 → 空行/空白忽略
            self._buf = ""

        return events

    def get_blocks(self) -> list:
        """获取所有顺序块 [{type, content}, ...]"""
        blocks = list(self._blocks)
        # flush 残余
        for t in ("speaking", "action"):
            if self._current.get(t, "").strip():
                blocks.append({"type": t, "content": self._current[t].strip()})
        fb = self._idle_fallback.strip()
        if fb:
            blocks.append({"type": "speaking", "content": fb})
        return blocks

    def get_results(self) -> dict:
        """兼容旧接口：返回第一个 speaking + 第一个 action"""
        blocks = self.get_blocks()
        speaking = ""
        action = ""
        for b in blocks:
            if b["type"] == "speaking" and not speaking:
                speaking = b["content"]
            if b["type"] == "action" and not action:
                action = b["content"]
        return {"speaking": speaking.strip(), "action": action.strip(), "blocks": blocks}

    def reset(self):
        self.__init__()


# ============================================================
#  LLM 客户端
# ============================================================
class LLMClient:
    """统一LLM客户端，支持流式输出"""

    def __init__(self, config: dict = None):
        self._config = config or AIConfig.load_all()
        self._provider = self._config.get("provider", "ollama")
        self._stopped = False

    def stop(self):
        """停止当前流式请求"""
        self._stopped = True

    def reset_stop(self):
        """重置停止标志"""
        self._stopped = False

    @staticmethod
    def _requests():
        import requests
        return requests

    def chat(self, messages: list, system: str = SYSTEM_PROMPT) -> dict:
        """非流式调用（兼容旧接口）"""
        result = {"thinking": "", "speaking": "", "action": "", "error": None}
        for chunk in self.chat_stream(messages, system):
            if chunk.get("error"):
                result["error"] = chunk["error"]
                return result
            if chunk.get("type") == "done":
                result["thinking"] = chunk.get("thinking", "")
                result["speaking"] = chunk.get("speaking", "")
                result["action"] = chunk.get("action", "")
                return result
        return result

    def chat_stream(self, messages: list, system: str = SYSTEM_PROMPT):
        """流式调用，yield 增量 chunk"""
        full_messages = [{"role": "system", "content": system}]
        # 格式提醒挂到最后一条 user 消息上（模型注意力更集中在最近上下文），
        # 且不污染历史消息，避免多轮后被稀释
        last_user_idx = -1
        for i, msg in enumerate(messages):
            if msg.get("role") == "user":
                last_user_idx = i
        for i, msg in enumerate(messages):
            m = dict(msg)
            if i == last_user_idx:
                m["content"] = _wrap_user_msg(m["content"], is_first=True)
            full_messages.append(m)

        if self._provider == "ollama":
            yield from self._stream_ollama(full_messages)
        elif self._provider == "openai":
            yield from self._stream_openai(full_messages)
        else:
            yield {"type": "error",
                   "error": f"未知 AI 提供商: {self._provider}"}

    # -------- Ollama 流式 -------
    def _stream_ollama(self, full_messages: list):
        requests = self._requests()
        url = self._config.get("ollama_url", "http://localhost:11434").rstrip("/")
        model = self._config.get("ollama_model", "").strip()
        if not model:
            yield {"type": "error", "error": "未填写 Ollama 模型名称，请在设置中填写"}
            return

        _debug_log(f"=== Ollama 开始 === model={model}")
        last_user = ""
        for m in reversed(full_messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")[:200]
                break
        _debug_log(f"最后用户消息: {last_user}")

        payload = {
            "model": model,
            "messages": full_messages,
            "stream": True,
            "options": {"temperature": 0.7, "num_predict": 4096, "think": True},
        }
        try:
            resp = requests.post(
                f"{url}/api/chat", json=payload,
                timeout=REQUEST_TIMEOUT, stream=True
            )
            resp.raise_for_status()

            all_thinking = ""
            all_content = ""
            parser = StreamParser()
            for line in resp.iter_lines(decode_unicode=True):
                if self._stopped:
                    break
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("done"):
                    break
                # Ollama 原生 think:true 分离：thinking → 直接推送，content → 解析 speaking/action
                msg = obj.get("message", {})
                think_text = msg.get("thinking", "")
                if think_text:
                    all_thinking += think_text
                    yield {"type": "thinking", "content": think_text}
                cont_text = msg.get("content", "")
                if cont_text:
                    all_content += cont_text
                    for evt_type, evt_text in parser.feed(cont_text):
                        yield {"type": evt_type, "content": evt_text}

            _debug_log(f"原生thinking({len(all_thinking)}字符): {all_thinking[:500]}")
            _debug_log(f"原始content({len(all_content)}字符): {all_content[:500]}")

            results = parser.get_results()
            _debug_log(f"解析结果: blocks={len(results['blocks'])} speaking={len(results['speaking'])} action={len(results['action'])}")
            _debug_log(f"=== Ollama 完成 ===")
            yield {
                "type": "done",
                "thinking": all_thinking.strip(),
                "speaking": results["speaking"],
                "action": results["action"],
                "blocks": results["blocks"],
                "error": None,
            }
        except requests.exceptions.ConnectionError:
            _debug_log("错误: 无法连接 Ollama")
            yield {"type": "error", "error": "无法连接 Ollama，请确认服务已启动"}
        except requests.exceptions.Timeout:
            _debug_log("错误: Ollama 请求超时")
            yield {"type": "error", "error": "Ollama 请求超时"}
        except Exception as e:
            _debug_log(f"错误: Ollama 异常: {e}")
            yield {"type": "error", "error": f"Ollama 异常: {e}"}

    # -------- OpenAI 流式 -------
    def _stream_openai(self, full_messages: list):
        requests = self._requests()
        base_url = self._config.get("openai_url", "https://api.openai.com/v1").rstrip("/")
        api_key = self._config.get("openai_key", "").strip()
        model = self._config.get("openai_model", "gpt-4o").strip()
        if not api_key:
            yield {"type": "error", "error": "未填写 OpenAI API Key，请在设置中填写"}
            return
        if not model:
            yield {"type": "error", "error": "未填写 OpenAI 模型名称，请在设置中填写"}
            return

        _debug_log(f"=== OpenAI 开始 === model={model}")

        payload = {
            "model": model,
            "messages": full_messages,
            "temperature": 0.7,
            "max_tokens": 4096,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(
                f"{base_url}/chat/completions",
                json=payload, headers=headers,
                timeout=REQUEST_TIMEOUT, stream=True
            )
            resp.raise_for_status()

            all_raw = ""
            parser = StreamParser()
            for line in resp.iter_lines(decode_unicode=True):
                if self._stopped:
                    break
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = obj.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    all_raw += content
                    for evt_type, evt_text in parser.feed(content):
                        yield {"type": evt_type, "content": evt_text}

            _debug_log(f"原始输出({len(all_raw)}字符): {all_raw[:2000]}")

            results = parser.get_results()
            _debug_log(f"解析结果: blocks={len(results['blocks'])} speaking={len(results['speaking'])} action={len(results['action'])}")
            _debug_log(f"=== OpenAI 完成 ===")
            yield {
                "type": "done",
                "thinking": "",
                "speaking": results["speaking"],
                "action": results["action"],
                "blocks": results["blocks"],
                "error": None,
            }
        except requests.exceptions.HTTPError as e:
            _debug_log(f"错误: API 请求失败 ({resp.status_code})")
            yield {"type": "error",
                   "error": f"API 请求失败 ({resp.status_code}): {resp.text[:200]}"}
        except requests.exceptions.ConnectionError:
            _debug_log(f"错误: 无法连接 OpenAI API")
            yield {"type": "error", "error": f"无法连接 API 地址: {base_url}"}
        except Exception as e:
            _debug_log(f"错误: OpenAI API 异常: {e}")
            yield {"type": "error", "error": f"API 异常: {e}"}

    # -------- 公共方法 -------
    def check_connection(self) -> bool:
        if self._provider == "ollama":
            try:
                requests = self._requests()
                url = self._config.get("ollama_url", "http://localhost:11434").rstrip("/")
                resp = requests.get(f"{url}/api/tags", timeout=5)
                return resp.status_code == 200
            except Exception:
                return False
        return True

    def list_models(self) -> list:
        if self._provider == "ollama":
            try:
                requests = self._requests()
                url = self._config.get("ollama_url", "http://localhost:11434").rstrip("/")
                resp = requests.get(f"{url}/api/tags", timeout=5)
                resp.raise_for_status()
                return [m.get("name", "") for m in resp.json().get("models", [])]
            except Exception:
                return []
        return [self._config.get("openai_model", "")]