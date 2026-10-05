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

SYSTEM_PROMPT = """# 角色介绍
你是 ApexAgent，运行在用户本地桌面环境中的 AI 智能体助手（支持 Windows / macOS / Linux）。
你可以理解自然语言请求，一边和用户对话，一边按需调用桌面操作指令完成任务。
用户每条消息自动附带操作系统和版本信息，你应根据平台选择合适的路径格式。
输出必须严格遵守固定 XML 标签格式，格式规则优先级高于一切。

# 核心执行流程（必须遵守）
1. 分析用户需求 → 生成说话 + 操作指令
2. 操作指令执行后 → 系统会把执行结果以 [系统通知] 形式发给你
3. 收到执行结果后 → 你必须：
   a. 仔细阅读执行结果
   b. 用自然中文向用户总结结果（成功则告知细节，失败则说明原因并尝试替代方案）
   c. 判断任务是否完成：完成则只说话不再发指令，未完成则继续发下一条指令
4. 严禁在收到结果后只回复"好的"就结束，必须基于实际结果进行回复

# 强制输出格式（最高优先级，所有回复必须遵守）
输出由两个 XML 标签任意多次交替组成：
<speaking>自然中文对话</speaking> <action>操作指令或空格</action> <speaking>继续对话</speaking> <action>下一条指令或空格</action>

规则清单：
1. <speaking>：你对用户说的自然中文。禁止为空、禁止出现操作指令/XML标签/类XML语法。
2. <action>：电脑操作指令。无需操作时标签内写一个空格，标签绝不能省略。
3. 可多次交替使用上两个标签，形成顺序步骤流。
4. 标签只能使用英文尖括号 < >，禁止中文符号。
5. 禁止输出 Markdown、代码块、解释文字、前置说明、后置总结。
6. 最后必须以 <speaking> 或 <action> </action> 结束，确保标签闭合。

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
（以下为两轮对话。第一轮LLM发指令，系统收到后执行并把结果作为新消息发给LLM；第二轮LLM根据结果回复用户）
第1轮（用户消息）: 帮我检查电脑内存
<speaking>好的，我马上检查你的电脑内存使用情况。</speaking>
<action><MemoryInfo/></action>

第2轮（LLM收到系统注入的执行结果作为新消息: "内存信息: 总16GB 已用10.2GB 可用5.8GB 使用率64%"）:
<speaking>检查完毕！你的电脑内存总共 16GB，当前已用 10.2GB，剩余可用 5.8GB，使用率约 64%，状态正常。</speaking>
<action> </action>

第1轮（用户消息）: 帮我打开百度
<speaking>好的，我马上帮你打开百度网站。</speaking>
<action><OpenURL 地址="">https://www.baidu.com</OpenURL></action>

第2轮（LLM收到执行结果: "已在默认浏览器中打开: https://www.baidu.com"）:
<speaking>百度已成功在浏览器中打开！</speaking>
<action> </action>

## 【错误示例，绝对不能这样输出】
❌ 错误1（标签外文字）：现在我来回答你 <speaking>xxx</speaking><action> </action>
❌ 错误2（指令写到speaking里）：<speaking>我执行了<OpenExe>xxx</speaking>
❌ 错误3（收到执行结果后只回"好的"）：<speaking>好的</speaking><action> </action>  ← 必须基于结果详细回复！
❌ 错误4（action留空但不写空格）：<action></action>
❌ 错误5（自创不存在的指令）：<speaking>好的</speaking><action><Hearthstone 检查磁盘/></action> ← 该指令不在列表中！
❌ 错误6（输出系统通知标记）：<speaking>[系统通知：已完成]...</speaking> ← 你绝对不能自己写系统通知！

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
- <ReadRunning/>
- <KillProcess pid="">1234</KillProcess>
系统信息:
- <GetSystemInfo/>
- <MemoryInfo/>            （获取内存使用详情：总量、已用、可用、使用率）
- <GetDiskUsage 路径="">C:\\</GetDiskUsage>
- <GetProcessList/>
- <GetNetworkStatus/>
- <GetDesktopFiles/>
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
网页 / 内存:
- <OpenURL 地址="">https://www.baidu.com</OpenURL>    （在默认浏览器打开网页）
系统控制:
- <Shutdown/>    (需用户确认)

重要约束：
1. 属性值必须用双引号包裹：<Tag 属性名="">值</Tag>
2. 不允许输出任何思考过程、内部推理草稿，直接输出标签结果
3. 收到执行结果后必须在 speaking 中基于实际数据进行自然语言回复，不得敷衍
4. macOS 用 open -a 启动应用，路径用 /；Windows 路径用 \\
5. 遇到"打开网页"需求用 OpenURL，而不是 OpenExe 打开浏览器
"""

def _wrap_user_msg(user_msg: str, is_first: bool = False) -> str:
    """包装用户消息。仅首次用户消息添加格式指令，执行结果等后续消息不包装。"""
    if is_first:
        return (
            "【请严格按照以下标签格式回复，不要输出任何标签之外的文字：\n"
            "<speaking>你对用户说的话</speaking>\n"
            "<action>指令或空格</action>】\n\n"
            + user_msg
        )
    return user_msg

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
                    else:
                        self._idle_fallback += overflow
                        events.append(("speaking", overflow))
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
                    if prefix and self._state == "idle":
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
                        if self._state == "idle" and self._idle_fallback.strip():
                            if new_state == "speaking":
                                # LLM在<speaking>前说了话 → 合并到speaking内容，避免重复
                                self._current["speaking"] = self._idle_fallback.strip()
                            else:
                                # LLM在<action>前说了话 → 保留为独立speaking块
                                self._blocks.append({"type": "speaking", "content": self._idle_fallback.strip()})
                            self._idle_fallback = ""
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
            else:
                self._idle_fallback += self._buf
                events.append(("speaking", self._buf))
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
        first_user_seen = False
        for i, msg in enumerate(messages):
            m = dict(msg)
            if m.get("role") == "user":
                if not first_user_seen:
                    m["content"] = _wrap_user_msg(m["content"], is_first=True)
                    first_user_seen = True
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