"""
ApexAgent 闭环推理引擎 — 思考 → 说话 → 行动 → 循环
每轮 LLM 调用采用流式输出，实时反馈 thinking / speaking chunk。
"""

import threading
import re
import xml.etree.ElementTree as ET
from .llm import LLMClient
from .executor import ActionExecutor, _xml_escape_attrs

# 正则：匹配 XML/HTML 尖括号标签，用于清洗 speaking 中的残留标签
_SPEAKING_TAG_RE = re.compile(r'</?[a-zA-Z][^>]*/?>')


def _clean_speaking(text: str) -> str:
    """清洗 speaking 文本：去掉所有尖括号标签（如残留的 </action></speaking>）"""
    if not text:
        return text
    cleaned = _SPEAKING_TAG_RE.sub('', text)
    # 去掉标签残留产生的多余空行
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


MAX_ITERATIONS = 8


class ApexAgent:
    """Agent 核心引擎，支持多轮推理闭环"""

    def __init__(self):
        self._llm = LLMClient()
        self._executor = ActionExecutor()
        self._stop_event = threading.Event()

    def stop(self):
        """停止当前推理"""
        self._stop_event.set()
        self._llm.stop()

    def reset_stop(self):
        """重置停止标志（新对话前调用）"""
        self._stop_event.clear()
        self._llm.reset_stop()

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
        # 把当前用户消息（含系统信息）加入消息列表
        if user_message:
            messages.append({"role": "user", "content": user_message})
        all_thinking = ""
        all_blocks = []         # 跨轮次累积所有块
        all_exec_results = []   # 跨轮次累积所有执行结果
        last_action = ""        # 最后一轮 action 字符串

        for iteration in range(MAX_ITERATIONS):
            if self._stop_event.is_set():
                speaking_parts = [_clean_speaking(b["content"]) for b in all_blocks if b["type"] == "speaking" and b["content"].strip()]
                return {"thinking": all_thinking,
                        "speaking": "\n".join(speaking_parts) or "用户手动停止",
                        "success": False, "error": "用户手动停止",
                        "messages": messages, "action": last_action,
                        "exec_result": all_exec_results, "blocks": all_blocks}

            # 第2轮起：通知 UI 正在规划下一步（微光提示）
            if iteration > 0 and on_chunk:
                on_chunk("planning", "")

            chunk_data = {"thinking": "", "speaking": "", "action": "", "error": None,
                          "blocks": [], "_block": None, "_block_content": ""}

            for chunk in self._llm.chat_stream(messages):
                if chunk.get("error"):
                    chunk_data["error"] = chunk["error"]
                    break
                t = chunk.get("type", "")
                if t == "block_start":
                    # 新块开始：flush 旧块，标记块类型
                    blk_type = chunk.get("content", "")
                    if chunk_data["_block"] and chunk_data["_block_content"].strip():
                        chunk_data["blocks"].append(
                            {"type": chunk_data["_block"], "content": chunk_data["_block_content"].strip()})
                    chunk_data["_block"] = blk_type
                    chunk_data["_block_content"] = ""
                    if on_chunk:
                        on_chunk(t, blk_type)
                elif t in ("thinking", "speaking", "action"):
                    # 类型切换检测（当 block_start 没发出时作为 fallback）
                    if chunk_data["_block"] is not None and t != chunk_data["_block"]:
                        if chunk_data["_block_content"].strip():
                            chunk_data["blocks"].append(
                                {"type": chunk_data["_block"], "content": chunk_data["_block_content"].strip()})
                        chunk_data["_block"] = t
                        chunk_data["_block_content"] = chunk.get("content", "")
                    else:
                        chunk_data["_block"] = t
                        chunk_data["_block_content"] += chunk.get("content", "")
                    chunk_data[t] += chunk.get("content", "")
                    if on_chunk:
                        on_chunk(t, chunk.get("content", ""))
                elif t == "done":
                    chunk_data["thinking"] = chunk.get("thinking", "")
                    chunk_data["speaking"] = chunk.get("speaking", "")
                    chunk_data["action"] = chunk.get("action", "")
                    chunk_data["blocks"] = chunk.get("blocks", [])
                    # parser 已有完整 blocks，清除 _block 避免 flush 追加重复
                    chunk_data["_block"] = None
                    chunk_data["_block_content"] = ""

            # flush 最后一个块
            if chunk_data["_block"] and chunk_data["_block_content"].strip():
                chunk_data["blocks"].append(
                    {"type": chunk_data["_block"], "content": chunk_data["_block_content"].strip()})

            if chunk_data["error"]:
                return {"thinking": all_thinking, "speaking": "",
                        "success": False, "error": chunk_data["error"],
                        "messages": messages, "blocks": all_blocks,
                        "action": last_action, "exec_result": all_exec_results}

            thinking = chunk_data.get("thinking", "")
            speaking = chunk_data.get("speaking", "")
            action = chunk_data.get("action", "")
            blocks = chunk_data.get("blocks", [])

            if thinking:
                all_thinking += thinking + "\n"
            if action:
                last_action = action

            # 累积本轮的 blocks（清洗 speaking 中的 XML 标签残留）
            for b in blocks:
                bb = dict(b)
                if bb["type"] == "speaking":
                    bb["content"] = _clean_speaking(bb["content"])
                all_blocks.append(bb)

            # 如果没有 action 相关块 → 任务结束
            has_action = any(b["type"] == "action" and b["content"].strip() for b in blocks)
            if not has_action:
                # 从所有 speaking block 拼出最终文本
                # 去重：A包含B或B包含A → 保留较长的
                raw_parts = []
                for b in all_blocks:
                    if b["type"] == "speaking" and b["content"].strip():
                        raw_parts.append(_clean_speaking(b["content"]))
                speaking_parts = []
                for p in raw_parts:
                    if not p:
                        continue
                    duplicate = False
                    for i, existing in enumerate(speaking_parts):
                        if p in existing:
                            duplicate = True
                            break
                        if existing in p:
                            speaking_parts[i] = p
                            duplicate = True
                            break
                    if not duplicate:
                        speaking_parts.append(p)
                all_speaking = "\n".join(speaking_parts)
                messages.append({"role": "assistant",
                                 "content": speaking or thinking or "任务已完成。"})
                return {"thinking": all_thinking, "speaking": all_speaking,
                        "success": True, "error": None, "messages": messages,
                        "action": last_action, "exec_result": all_exec_results,
                        "blocks": all_blocks}

            # 顺序执行本轮 blocks 中的 action（去重：相同指令只执行一次）
            seen_actions = set()
            for bi, blk in enumerate(blocks):
                if blk["type"] == "action" and blk["content"].strip():
                    act = blk["content"]
                    if act in seen_actions:
                        continue
                    seen_actions.add(act)
                    if self._stop_event.is_set():
                        # 用户已停止 → 不再执行新指令
                        continue
                    print(f"[agent] 轮次{iteration+1} 第{bi+1}条action: {repr(act[:200])}")
                    if not self._is_valid_action(act):
                        if on_chunk:
                            on_chunk("speaking",
                                     "\n\n⚠️ 模型生成的指令格式错误，已跳过执行。\n")
                        continue

                    exec_result = self._executor.execute(act)
                    all_exec_results.append(exec_result)

                    results = exec_result.get("results", [])
                    if results:
                        outputs = []
                        for r in results:
                            if isinstance(r, dict):
                                inst = r.get("instruction", "")
                                out = r.get("output", "")
                                outputs.append(f"[{inst}]: {out}")
                            else:
                                outputs.append(str(r))
                        clean_summary = "\n".join(outputs)
                    else:
                        clean_summary = "（无文本输出）"

                    result_text = (
                        f"[系统通知] 上一条指令已执行完毕，结果：\n{clean_summary}"
                    )
                    messages.append({"role": "user", "content": result_text})

            # 保存本轮 assistant 回复，继续循环让 LLM 看到结果后决策下一步
            # 用本轮所有 speaking block 拼接（不用 chunk_data["speaking"]，那是第一个）
            assistant_content = "\n".join(
                _clean_speaking(b["content"]) for b in blocks if b["type"] == "speaking" and b["content"].strip()
            ) or thinking or ""
            if assistant_content.strip():
                messages.append({"role": "assistant", "content": assistant_content})

            print(f"[agent] 轮次{iteration+1} 完成，已执行 {len(seen_actions)} 条指令，继续下一轮推理...")
            # continue → 下一轮 LLM 调用，messages 含执行结果

        return {"thinking": all_thinking,
                "speaking": "\n".join(_clean_speaking(b["content"]) for b in all_blocks if b["type"] == "speaking" and b["content"].strip()),
                "success": False, "error": f"推理轮次超过上限({MAX_ITERATIONS})",
                "messages": messages, "action": last_action,
                "exec_result": all_exec_results, "blocks": all_blocks}

    def _is_valid_action(self, action: str) -> bool:
        """校验 action 是否包含类XML指令标签（先标准化属性再解析）"""
        if not action or not action.strip():
            return True
        # 先尝试直接XML解析
        try:
            ET.fromstring(f"<root>{action}</root>")
            return True
        except ET.ParseError:
            pass
        # 再尝试标准化属性后再解析（兼容 <OpenExe 程序路径>xxx 等非标准格式）
        try:
            normalized = _xml_escape_attrs(action)
            ET.fromstring(f"<root>{normalized}</root>")
            return True
        except ET.ParseError:
            pass
        # 正则回退：至少有一个类XML标签
        tags = re.findall(r'</?[A-Za-z][A-Za-z0-9_()]*[^>]*>', action)
        if len(tags) >= 1:
            return True
        print(f"[_is_valid_action] 模型输出无法解析: {repr(action[:300])}")
        return False