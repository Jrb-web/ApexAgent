"""
ApexAgent 闭环推理引擎 — 思考 → 说话 → 行动 → 循环
每轮 LLM 调用采用流式输出，实时反馈 thinking / speaking chunk。
"""

import threading
import xml.etree.ElementTree as ET
from .llm import LLMClient
from .executor import ActionExecutor


MAX_ITERATIONS = 8


class ApexAgent:
    """Agent 核心引擎，支持多轮推理闭环"""

    def __init__(self):
        self._llm = LLMClient()
        self._executor = ActionExecutor()

    def run_async(
        self,
        user_message: str,
        conversation_messages: list,
        on_chunk=None,
        on_complete=None,
    ):
        """异步运行 Agent 推理循环

        on_chunk(type, text)  — 流式回调: type ∈ {thinking, speaking, action}
        on_complete(result)   — 完成回调: dict{thinking, speaking, success, error, messages}
        """
        thread = threading.Thread(
            target=self._run_sync,
            args=(user_message, conversation_messages, on_chunk, on_complete),
            daemon=True,
        )
        thread.start()

    def run(self, user_message: str, conversation_messages: list):
        """同步运行，返回最终结果 dict"""
        result_holder = {}

        def on_complete(r):
            result_holder["result"] = r

        self._run_sync(user_message, conversation_messages, None, on_complete)
        return result_holder.get("result", {})

    # ================================================================
    #  同步核心
    # ================================================================
    def _run_sync(self, user_message, conversation_messages, on_chunk, on_complete):
        try:
            result = self._agent_loop(user_message, conversation_messages, on_chunk)
            if on_complete:
                on_complete(result)
        except Exception as e:
            if on_complete:
                on_complete({"thinking": "", "speaking": "", "success": False,
                             "error": str(e), "messages": conversation_messages})

    def _agent_loop(self, user_message, conv_msgs, on_chunk):
        messages = list(conv_msgs) if conv_msgs else []
        all_thinking = ""
        all_speaking = ""

        for iteration in range(MAX_ITERATIONS):
            chunk_data = {"thinking": "", "speaking": "", "action": "", "error": None}

            for chunk in self._llm.chat_stream(messages):
                if chunk.get("error"):
                    chunk_data["error"] = chunk["error"]
                    break
                t = chunk.get("type", "")
                if t in ("thinking", "speaking", "action"):
                    chunk_data[t] += chunk.get("content", "")
                    if on_chunk:
                        on_chunk(t, chunk.get("content", ""))
                elif t == "done":
                    chunk_data["thinking"] = chunk.get("thinking", "")
                    chunk_data["speaking"] = chunk.get("speaking", "")
                    chunk_data["action"] = chunk.get("action", "")

            if chunk_data["error"]:
                return {"thinking": all_thinking, "speaking": all_speaking,
                        "success": False, "error": chunk_data["error"],
                        "messages": messages}

            thinking = chunk_data.get("thinking", "")
            speaking = chunk_data.get("speaking", "")
            action = chunk_data.get("action", "")

            if thinking:
                all_thinking += thinking + "\n"
            if speaking:
                all_speaking += speaking + "\n"

            # 无 action → 结束
            if not action or not action.strip():
                messages.append({"role": "assistant",
                                 "content": speaking or thinking or "任务已完成。"})
                return {"thinking": all_thinking, "speaking": all_speaking,
                        "success": True, "error": None, "messages": messages,
                        "action": "", "exec_result": None}

            # 校验 action：XML 格式错误就跳过执行
            if not self._is_valid_action(action):
                if on_chunk:
                    on_chunk("speaking",
                             "\n\n⚠️ 模型生成的指令格式错误，已跳过执行。\n")
                messages.append({"role": "assistant",
                                 "content": speaking or thinking or "任务已完成。"})
                return {"thinking": all_thinking, "speaking": all_speaking,
                        "success": True, "error": None, "messages": messages,
                        "action": action, "exec_result": None}

            # 有 action → 执行
            assistant_content = speaking or thinking
            messages.append({"role": "assistant", "content": assistant_content})

            exec_result = self._executor.execute(action)
            if not exec_result["success"]:
                # 执行失败也结束，不继续循环（防止连锁污染）
                if on_chunk:
                    on_chunk("speaking",
                             f"\n\n❌ 指令执行失败: {exec_result.get('error', '未知错误')}\n")
                return {"thinking": all_thinking, "speaking": all_speaking,
                        "success": False,
                        "error": f"指令执行失败: {exec_result.get('error', '')}",
                        "messages": messages,
                        "action": action, "exec_result": exec_result}

            result_text = (
                f"[系统通知] 你上一条指令已执行完毕。以下是执行结果，请根据结果决定下一步：\n"
                f"{exec_result}"
            )
            if on_chunk:
                on_chunk("speaking", f"\n\n🔧 执行指令...\n{exec_result}\n")

            messages.append({"role": "user", "content": result_text})

        # 超过最大轮次
        return {"thinking": all_thinking, "speaking": all_speaking,
                "success": False, "error": f"推理轮次超过上限({MAX_ITERATIONS})",
                "messages": messages, "action": "", "exec_result": None}

    def _is_valid_action(self, action: str) -> bool:
        """校验 action 是否为有效 XML 格式"""
        if not action or not action.strip():
            return True
        try:
            ET.fromstring(f"<root>{action}</root>")
            return True
        except ET.ParseError:
            return False