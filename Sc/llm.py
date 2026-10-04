"""
ApexAgent LLM 客户端 — 支持多提供商
- ollama: 本地 Ollama 服务
- openai: OpenAI 兼容 API（支持任意兼容接口，如 deepseek、moonshot 等）
配置位于 ini/config.ini 的 [AI] 段落，用户自行填写。
"""

import json
import re
from .storage import AIConfig


REQUEST_TIMEOUT = 120

SYSTEM_PROMPT = """你是 ApexAgent，一个运行在 Windows 桌面上的 AI 智能体助手。你可以感知电脑环境、执行操作。

你必须严格按照以下 JSON 格式回复，不要输出任何其他内容：

{
  "thinking": "这里是你的思考过程，用自然语言描述你正在分析什么、打算做什么",
  "action": "这里是你要执行的类XML指令，如果没有需要执行的操作，留空字符串"
}

类XML指令格式示例：
- <ReadFile 路径>C:\\test.txt</ReadFile>
- <ReadFolder 路径>C:\\Users</ReadFolder>
- <SearchFile 文件夹名>下载</SearchFile>
- <RemoveFile 路径>C:\\temp.txt</RemoveFile>
- <OpenExe 程序路径>C:\\Windows\\notepad.exe</OpenExe>
- <ReadRunning/>
- <KillProcess pid="1234"/>
- <GetDesktopFiles/>
- <GetActiveWindow/>
- <GetDiskUsage 路径>C:\\</GetDiskUsage>
- <GetSystemInfo/>
- <GetProcessList/>
- <GetNetworkStatus/>
- <CloseWindow 窗口标题>记事本</CloseWindow>
- <ResizeWindow 标题 宽 高>记事本</ResizeWindow>
- <FocusWindow 标题>记事本</FocusWindow>
- <ClipboardRead/>
- <ClipboardWrite 文本>hello</ClipboardWrite>
- <Screenshot 范围 保存路径>全屏 C:\\screen.png</Screenshot>
- <MouseMove x y>0.5 0.5</MouseMove>
- <MouseClick x y>0.5 0.5</MouseClick>
- <MouseDrag x1 y1 x2 y2>0.1 0.1 0.5 0.5</MouseDrag>
- <RegistryRead 路径>HKEY_CURRENT_USER\\Software</RegistryRead>
- <RegistryWrite 路径 key value>HKEY_CURRENT_USER\\Software key value</RegistryWrite>
- <ServiceControl 服务名 操作>Spooler stop</ServiceControl>
- <RunCommand cmd="dir"/>
- <Shutdown/>
- <while API名称 次数>ReadFile 3</while>
- <for(次数,数组) API名称>for(3,C:\\a.txt C:\\b.txt C:\\c.txt) ReadFile</for>
- <If condition="">condition</If>
- <NameChange 路径>新名称</NameChange>
- <DiffJsonFile 路径 替换值>C:\\config.json {"key":"value"}</DiffJsonFile>

重要规则：
1. thinking 是纯自然语言，给用户看的，不要在里面放任何指令
2. action 只放类XML指令，如果不需要执行任何操作，action 必须是空字符串 ""
3. 如果之前的执行结果显示成功或获取到了数据，根据结果继续思考下一步
4. 如果之前的执行结果出错，分析错误原因并尝试其他方法
5. 当你认为任务已经完全完成时，action 设为空字符串，在 thinking 中总结结果
6. 每条 action 可以包含多个XML指令
7. 必须输出合法的 JSON，不要有任何多余的文字
"""


class LLMClient:
    """统一LLM客户端，根据配置自动选择 ollama 或 openai 提供商"""

    def __init__(self, config: dict = None):
        self._config = config or AIConfig.load_all()
        self._provider = self._config.get("provider", "ollama")

    @staticmethod
    def _requests():
        import requests
        return requests

    def chat(self, messages: list, system: str = SYSTEM_PROMPT) -> dict:
        if self._provider == "ollama":
            return self._chat_ollama(messages, system)
        elif self._provider == "openai":
            return self._chat_openai(messages, system)
        else:
            return {"thinking": "", "action": "",
                    "error": f"未知 AI 提供商: {self._provider}，请在设置中选择 ollama 或 openai"}

    # -------- Ollama -------
    def _chat_ollama(self, messages: list, system: str) -> dict:
        requests = self._requests()
        url = self._config.get("ollama_url", "http://localhost:11434").rstrip("/")
        model = self._config.get("ollama_model", "").strip()
        if not model:
            return {"thinking": "", "action": "",
                    "error": "未填写 Ollama 模型名称，请在设置中填写"}

        full_messages = [{"role": "system", "content": system}] + messages
        payload = {
            "model": model,
            "messages": full_messages,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.7, "num_predict": 4096},
        }
        try:
            resp = requests.post(f"{url}/api/chat", json=payload, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            content = resp.json().get("message", {}).get("content", "")
            return self._parse_response(content)
        except requests.exceptions.ConnectionError:
            return {"thinking": "", "action": "",
                    "error": "无法连接 Ollama，请确认服务已启动"}
        except requests.exceptions.Timeout:
            return {"thinking": "", "action": "",
                    "error": "Ollama 请求超时"}
        except Exception as e:
            return {"thinking": "", "action": "", "error": f"Ollama 异常: {e}"}

    def _check_ollama_connection(self) -> bool:
        try:
            requests = self._requests()
            url = self._config.get("ollama_url", "http://localhost:11434").rstrip("/")
            resp = requests.get(f"{url}/api/tags", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def _list_ollama_models(self) -> list:
        try:
            requests = self._requests()
            url = self._config.get("ollama_url", "http://localhost:11434").rstrip("/")
            resp = requests.get(f"{url}/api/tags", timeout=5)
            resp.raise_for_status()
            return [m.get("name", "") for m in resp.json().get("models", [])]
        except Exception:
            return []

    # -------- OpenAI 兼容 -------
    def _chat_openai(self, messages: list, system: str) -> dict:
        requests = self._requests()
        base_url = self._config.get("openai_url", "https://api.openai.com/v1").rstrip("/")
        api_key = self._config.get("openai_key", "").strip()
        model = self._config.get("openai_model", "gpt-4o").strip()
        if not api_key:
            return {"thinking": "", "action": "",
                    "error": "未填写 OpenAI API Key，请在设置中填写"}
        if not model:
            return {"thinking": "", "action": "",
                    "error": "未填写 OpenAI 模型名称，请在设置中填写"}

        full_messages = [{"role": "system", "content": system}] + messages
        payload = {
            "model": model,
            "messages": full_messages,
            "temperature": 0.7,
            "max_tokens": 4096,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(
                f"{base_url}/chat/completions",
                json=payload, headers=headers, timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return self._parse_response(content)
        except requests.exceptions.HTTPError as e:
            return {"thinking": "", "action": "",
                    "error": f"API 请求失败 ({resp.status_code}): {resp.text[:200]}"}
        except requests.exceptions.ConnectionError:
            return {"thinking": "", "action": "",
                    "error": f"无法连接 API 地址: {base_url}"}
        except Exception as e:
            return {"thinking": "", "action": "", "error": f"API 异常: {e}"}

    # -------- 解析 -------
    def _parse_response(self, content: str) -> dict:
        if not content:
            return {"thinking": "", "action": "", "error": "模型返回为空"}
        content = content.strip()
        try:
            result = json.loads(content)
            if isinstance(result, dict):
                return {"thinking": result.get("thinking", ""),
                        "action": result.get("action", ""), "error": None}
        except json.JSONDecodeError:
            pass
        m = re.search(r'\{[\s\S]*\}', content)
        if m:
            try:
                result = json.loads(m.group(0))
                if isinstance(result, dict):
                    return {"thinking": result.get("thinking", ""),
                            "action": result.get("action", ""), "error": None}
            except json.JSONDecodeError:
                pass
        return {"thinking": content, "action": "", "error": None}

    # -------- 公共方法 -------
    def check_connection(self) -> bool:
        if self._provider == "ollama":
            return self._check_ollama_connection()
        return True

    def list_models(self) -> list:
        if self._provider == "ollama":
            return self._list_ollama_models()
        return [self._config.get("openai_model", "")]