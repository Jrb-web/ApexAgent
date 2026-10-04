"""
ApexAgent LLM 客户端 — 流式输出 + 多提供商
- ollama: 本地 Ollama 服务
- openai: OpenAI 兼容 API
输出格式: <thinking>内部思考</thinking><speaking>外部回复</speaking><action>指令</action>
"""

import json
from .storage import AIConfig


REQUEST_TIMEOUT = 180

SYSTEM_PROMPT = """你是 ApexAgent，一个运行在桌面上的 AI 智能体助手。

你必须严格按照以下 XML 标签格式输出回复，不要输出任何标签之外的文字：

<thinking>你的真实内部推理过程——分析用户意图、制定计划、判断下一步行动。写具体内容。</thinking>
<speaking>你对用户说的话。用自然流畅的中文回答。</speaking>
<action>需要执行的操作指令，不需要操作时留空</action>

【关键约束 - 违反将导致系统崩溃】
- 三个标签缺一不可，顺序固定：thinking → speaking → action
- 所有文字必须放在标签内部，标签外不得出现任何字符
- thinking 必须写真实分析，禁止写"分析用户意图"之类的模板文字
- speaking 必须写对用户说的话，禁止空白
- action 不需要操作时写一个空格即可，不可省略标签
- 绝对禁止输出 markdown 格式
- 绝对禁止使用  response 标签或任何其他标签格式
- 标签必须用英文尖括号 < > ，禁止用中文冒号：或括号（）
- 【最高优先级】绝对禁止在 thinking 和 speaking 中提及、列举、展示任何 XML 指令或标签（如 <ReadFile>、<RunCommand> 等）。XML 指令只能出现在 action 标签内。thinking 只写分析推理，speaking 只写自然语言回复给用户，不得包含任何类XML格式内容

类XML指令列表（放在 action 标签内）:
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
"""

def _wrap_user_msg(user_msg: str, is_first: bool = False) -> str:
    """包装用户消息。仅首次用户消息添加格式指令，执行结果等后续消息不包装。"""
    if is_first:
        return (
            "【请严格按照以下标签格式回复，不要输出任何标签之外的文字：\n"
            "<thinking>你的思考过程</thinking>\n"
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
    "<thinking>":  "thinking",
    "</thinking>": "idle",
    "<speaking>":  "speaking",
    "</speaking>": "idle",
    "<action>":    "action",
    "</action>":   "idle",
}


class StreamParser:
    """逐字符流式标签解析器"""

    def __init__(self):
        self._state = "idle"
        self._buf = ""
        self._full = {"thinking": "", "speaking": "", "action": ""}
        # idle 中的非标签内容：模型不输出标签时，全部当 speaking 回退
        self._idle_fallback = ""

    def feed(self, text: str) -> list:
        """喂入文本，返回 [(type, text), ...] 事件列表"""
        events = []
        for ch in text:
            self._buf += ch
            if len(self._buf) > _MAX_TAG_LEN:
                # 溢出部分一定不是标签的一部分（最长标签 11 字符），先发送
                overflow = self._buf[:-_MAX_TAG_LEN]
                if overflow:
                    if self._state != "idle":
                        self._full[self._state] += overflow
                        events.append((self._state, overflow))
                    else:
                        self._idle_fallback += overflow
                        events.append(("speaking", overflow))
                self._buf = self._buf[-_MAX_TAG_LEN:]

            # idle 状态：检查所有开始标签；非 idle：只检查当前状态的结束标签
            check_tags = (
                ["<thinking>", "<speaking>", "<action>"]
                if self._state == "idle"
                else [f"</{self._state}>"]
            )

            # 检查 buffer 末尾是否命中完整标签
            tag_matched = False
            for tag in check_tags:
                if self._buf.endswith(tag):
                    # 标签前的残留字符（例：idle 时 \n\n<th...中的 \n\n）
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

            # 未命中 → 检查 buffer 是否可能是某标签前缀
            candidates = (
                ["<thinking>", "<speaking>", "<action>"]
                if self._state == "idle"
                else [f"</{self._state}>"]
            )
            could_be_tag = any(
                tag.startswith(self._buf) for tag in candidates
            )

            if could_be_tag:
                continue

            # buffer 不是标签前缀 → 路由到对应通道
            if self._state != "idle":
                self._full[self._state] += self._buf
                events.append((self._state, self._buf))
            else:
                # idle 中的非标签字符：回退为 speaking 并实时发出
                self._idle_fallback += self._buf
                events.append(("speaking", self._buf))
            self._buf = ""

        return events

    def get_results(self) -> dict:
        """获取最终解析结果"""
        speaking = self._full.get("speaking", "")
        # idle 回退 + 残留 buffer
        fallback = self._idle_fallback + self._buf
        if fallback:
            speaking += fallback
        return {
            "thinking": self._full.get("thinking", "").strip(),
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

        payload = {
            "model": model,
            "messages": full_messages,
            "stream": True,
            "options": {"temperature": 0.7, "num_predict": 4096},
        }
        try:
            resp = requests.post(
                f"{url}/api/chat", json=payload,
                timeout=REQUEST_TIMEOUT, stream=True
            )
            resp.raise_for_status()

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
                content = obj.get("message", {}).get("content", "")
                if content:
                    for evt_type, evt_text in parser.feed(content):
                        yield {"type": evt_type, "content": evt_text}

            # 结束
            results = parser.get_results()
            yield {
                "type": "done",
                "thinking": results["thinking"],
                "speaking": results["speaking"],
                "action": results["action"],
                "error": None,
            }
        except requests.exceptions.ConnectionError:
            yield {"type": "error", "error": "无法连接 Ollama，请确认服务已启动"}
        except requests.exceptions.Timeout:
            yield {"type": "error", "error": "Ollama 请求超时"}
        except Exception as e:
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
                    for evt_type, evt_text in parser.feed(content):
                        yield {"type": evt_type, "content": evt_text}

            results = parser.get_results()
            yield {
                "type": "done",
                "thinking": results["thinking"],
                "speaking": results["speaking"],
                "action": results["action"],
                "error": None,
            }
        except requests.exceptions.HTTPError as e:
            yield {"type": "error",
                   "error": f"API 请求失败 ({resp.status_code}): {resp.text[:200]}"}
        except requests.exceptions.ConnectionError:
            yield {"type": "error", "error": f"无法连接 API 地址: {base_url}"}
        except Exception as e:
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