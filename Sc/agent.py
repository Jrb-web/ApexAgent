"""
ApexAgent 核心 Agent 引擎
实现 Thinking/Action 分离 + 闭环多轮迭代推理
"""

import json
import threading
from typing import Callable, Optional

from .llm import LLMClient
from .executor import ActionExecutor


MAX_ITERATIONS = 8


class ApexAgent:
    """
    Agent 引擎：管理对话上下文、调用 LLM、解析 action、执行指令、闭环迭代
    """

    def __init__(self):
        self._llm = LLMClient()
        self._executor = ActionExecutor()

    def run(
        self,
        user_message: str,
        conversation_messages: list,
        on_thinking: Callable[[str], None] = None,
        on_action: Callable[[str], None] = None,
    ) -> dict:
        """
        执行 Agent 闭环推理

        参数:
            user_message: 用户最新输入
            conversation_messages: 现有对话历史 (不含 system prompt)
            on_thinking: thinking 回调，用于前端实时显示
            on_action: action 回调，用于调试/日志

        返回:
            {
                "thinking": "最终总结文字",
                "messages": [...],      # 完整对话历史（含中间轮次）
                "success": True/False,
                "error": "错误信息" 或 None
            }
        """
        messages = list(conversation_messages)
        final_thinking = ""
        iteration = 0

        while iteration < MAX_ITERATIONS:
            iteration += 1

            result = self._llm.chat(messages)

            if result.get("error"):
                return {
                    "thinking": f"发生错误: {result['error']}",
                    "messages": messages,
                    "success": False,
                    "error": result["error"]
                }

            thinking = result.get("thinking", "").strip()
            action = result.get("action", "").strip()

            if on_thinking and thinking:
                on_thinking(thinking)
            if on_action and action:
                on_action(action)

            if not action:
                final_thinking = thinking
                break

            # 有 action，执行
            exec_result = self._executor.execute(action)

            # 构造执行结果反馈
            feedback = self._build_feedback(exec_result)

            # 把模型输出和执行结果写入上下文
            messages.append({
                "role": "assistant",
                "content": json.dumps(result, ensure_ascii=False)
            })
            messages.append({
                "role": "user",
                "content": feedback
            })

            final_thinking = thinking

        if iteration >= MAX_ITERATIONS and not final_thinking:
            final_thinking = "已达到最大推理轮次，任务可能未完全完成。"

        return {
            "thinking": final_thinking,
            "messages": messages,
            "success": True,
            "error": None
        }

    def run_async(
        self,
        user_message: str,
        conversation_messages: list,
        on_thinking: Callable[[str], None] = None,
        on_action: Callable[[str], None] = None,
        on_complete: Callable[[dict], None] = None,
    ):
        """异步执行 Agent 闭环推理（在子线程中运行）"""
        def _run():
            result = self.run(
                user_message=user_message,
                conversation_messages=conversation_messages,
                on_thinking=on_thinking,
                on_action=on_action,
            )
            if on_complete:
                on_complete(result)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return thread

    def check_ollama(self) -> bool:
        """检查 Ollama 是否可用"""
        return self._llm.check_connection()

    def get_models(self) -> list:
        """获取可用模型列表"""
        return self._llm.list_models()

    def _build_feedback(self, exec_result: dict) -> str:
        """构造执行结果反馈信息，回传模型上下文"""
        success = exec_result.get("success", False)
        results = exec_result.get("results", [])
        error = exec_result.get("error", "")

        if error:
            return f"[系统反馈] 指令执行出错: {error}\n请分析错误原因并尝试其他方法。"

        if not results:
            if success:
                return "[系统反馈] 指令执行成功。请根据此结果继续下一步思考。"
            return "[系统反馈] 指令执行完成。请继续。"

        feedback_parts = ["[系统反馈] 指令执行结果如下:"]
        for r in results:
            instruction = r.get("instruction", "")
            output = r.get("output", "")
            status = "成功" if r.get("success", True) else "失败"
            feedback_parts.append(f"- [{status}] {instruction}")
            if output:
                output_str = str(output)
                if len(output_str) > 2000:
                    output_str = output_str[:2000] + "...(截断)"
                feedback_parts.append(f"  结果: {output_str}")

        feedback_parts.append("\n请根据以上执行结果继续思考下一步操作。如果任务已完成，action 设为空字符串。")
        return "\n".join(feedback_parts)