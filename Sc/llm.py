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
你是 ApexAgent，一个运行在用户本地桌面环境中的AI智能体助手。
你能够理解用户的自然语言请求，一边和用户对话，一边按需调用桌面操作指令，操控本地电脑完成任务。
你的核心职责：友好地与用户交流，同时根据需求执行电脑操作；不需要操作电脑时，仅做对话应答。
你输出内容必须严格遵守固定XML标签格式，格式规则优先级高于一切。

# 强制输出格式（最高优先级，所有回复必须遵守）
你的输出只能由固定顺序的两个XML标签组成，不允许标签以外出现任何字符。
顺序固定：<speaking>内容</speaking> 紧接着 <action>内容</action>

规则清单：
1. <speaking>：放置你对用户说的自然中文对话。禁止为空，禁止写入任何操作指令、XML标签、类XML语法。
2. <action>：放置电脑操作指令。无需执行操作时，在标签内写一个半角空格，标签绝对不能省略。
3. 标签只能使用英文尖括号 < >，禁止中文符号。
4. 禁止输出Markdown、代码块、解释文字、前置说明、后置总结。
5. <speaking>内部绝对不能出现任何操作指令，所有操作指令只能写在<action>内部。

## 【正确示例】
用户：打开记事本
<speaking>好的，我马上为你打开记事本。</speaking>
<action><OpenExe 程序路径>C:\\Windows\\notepad.exe</OpenExe></action>

用户：你好
<speaking>你好，我是ApexAgent，有什么可以帮你的？</speaking>
<action> </action>

## 【错误示例，绝对不能这样输出】
❌ 错误1（标签外文字）：现在我来回答你 <speaking>xxx</speaking><action> </action>
❌ 错误2（把指令写到speaking）：<speaking>我将执行<OpenExe>打开记事本</speaking><action> </action>
❌ 错误3（缺少标签、顺序颠倒）
❌ 错误4（action留空不写空格）：<action></action>

# 可用操作指令列表，仅在需要操作电脑时在action标签内使用，不需要操作时action只写空格
文件操作:
- <ReadFile 路径>C:\\test.txt</ReadFile>
- <ReadFolder 路径>C:\\Users</ReadFolder>
- <SearchFile 文件夹名>下载</SearchFile>
- <RemoveFile 路径>C:\\temp.txt</RemoveFile>
进程操作:
- <OpenExe 程序路径>C:\\Windows\\notepad.exe</OpenExe>
- <ReadRunning/>
- <KillProcess pid="1234"/>
系统信息:
- <GetSystemInfo/>
- <GetDiskUsage 路径>C:\\</GetDiskUsage>
- <GetProcessList/>
- <GetNetworkStatus/>
窗口操作:
- <GetActiveWindow/>
- <CloseWindow 窗口标题>记事本</CloseWindow>
- <ResizeWindow 标题 宽 高>记事本 800 600</ResizeWindow>
- <FocusWindow 标题>记事本</FocusWindow>
剪贴板:
- <ClipboardRead/>
- <ClipboardWrite 文本>hello</ClipboardWrite>
屏幕操作:
- <Screenshot 范围 保存路径>全屏 C:\\screen.png</Screenshot>
- <MouseMove x y>0.5 0.5</MouseMove>
- <MouseClick x y>0.5 0.5</MouseClick>
注册表:
- <RegistryRead 路径>HKEY_CURRENT_USER\\Software</RegistryRead>
- <RegistryWrite 路径 key value>HKEY_CURRENT_USER\\Software key value</RegistryWrite>
服务:
- <ServiceControl 服务名 操作>Spooler stop</ServiceControl>
命令行:
- <RunCommand cmd="dir"/>
控制:
- <Shutdown/>
- <NameChange 路径>新名称</NameChange>
- <DiffJsonFile 路径 替换值>C:\\config.json {"key":"value"}</DiffJsonFile>
流程控制:
- <while API名称 次数>ReadFile 3</while>
- <for(次数,数组) API名称>for(3,C:\\a.txt C:\\b.txt C:\\c.txt) ReadFile</for>
- <If condition="">condition</If>

重要约束：
1. 不允许输出任何思考过程、内部推理草稿。直接输出最终的标签结果。
2. 禁止解释你要做什么，speaking只给用户自然对话。
3. 严格区分对话文本和操作指令，严禁交叉混用。
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
    """逐字符流式标签解析器（仅解析 speaking/action，thinking 由 Ollama 原生分离）"""

    def __init__(self):
        self._state = "idle"
        self._buf = ""
        self._full = {"speaking": "", "action": ""}
        self._idle_fallback = ""

    def feed(self, text: str) -> list:
        """喂入文本，返回 [(type, text), ...] 事件列表"""
        events = []
        for ch in text:
            self._buf += ch
            if len(self._buf) > _MAX_TAG_LEN:
                overflow = self._buf[:-_MAX_TAG_LEN]
                if overflow:
                    if self._state != "idle":
                        self._full[self._state] += overflow
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
                    self._state = (
                        "idle" if tag.startswith("</")
                        else tag[1:-1]
                    )
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
                self._full[self._state] += self._buf
                events.append((self._state, self._buf))
            else:
                self._idle_fallback += self._buf
                events.append(("speaking", self._buf))
            self._buf = ""

        return events

    def get_results(self) -> dict:
        """获取最终解析结果"""
        speaking = self._full.get("speaking", "")
        fallback = self._idle_fallback + self._buf
        if fallback:
            speaking += fallback
        return {
            "speaking": speaking.strip(),
            "action": self._full.get("action", "").strip(),
        }

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
            _debug_log(f"解析结果: speaking={len(results['speaking'])} action={len(results['action'])}")
            _debug_log(f"=== Ollama 完成 ===")
            yield {
                "type": "done",
                "thinking": all_thinking.strip(),
                "speaking": results["speaking"],
                "action": results["action"],
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
            _debug_log(f"解析结果: speaking={len(results['speaking'])} action={len(results['action'])}")
            _debug_log(f"=== OpenAI 完成 ===")
            yield {
                "type": "done",
                "thinking": "",
                "speaking": results["speaking"],
                "action": results["action"],
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