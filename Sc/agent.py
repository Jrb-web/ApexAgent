"""
ApexAgent 闭环推理引擎 — 思考 → 说话 → 行动 → 循环

闭环协议（每轮严格按此顺序追加消息，保证 user/assistant 严格交替）：

    [user]      用户原始提问
    [assistant] <speaking>…</speaking><action>…</action>   ← 本轮模型完整输出（含指令）
    [user]      [系统通知] 指令执行结果                    ← 由本引擎注入，等价于一次全新的用户轮
    [assistant] …                                          ← 模型基于结果继续推理
    …… 直到某一轮不再输出 action，任务结束

关键点：执行结果必须作为一条 **独立的 user 消息** 注入，且必须排在对应
assistant 消息 **之后**。否则模型会看到"结果先于回复"，误判任务已完成，
从而无法进入下一轮推理。
"""

import threading
import re
import json
import datetime
import platform
import xml.etree.ElementTree as ET
from .llm import LLMClient, TOOL_RESULT_MARKER
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


def _blocks_to_assistant_content(blocks: list) -> str:
    """把本轮 blocks 还原成模型原始的 <speaking>/<action> 结构。

    必须原样带回 action —— 否则下一轮模型看不到"我上一轮到底发了什么指令"，
    闭环就断了，模型只能靠猜。
    """
    parts = []
    for b in blocks:
        t = b.get("type", "")
        c = (b.get("content") or "").strip()
        if not c:
            continue
        if t == "action":
            parts.append(f"<action>{c}</action>")
        else:
            parts.append(f"<speaking>{_clean_speaking(c)}</speaking>")
    return "".join(parts)


def _format_tool_results(exec_pairs: list) -> str:
    """把本轮所有指令的执行结果汇总成一条 user 消息。

    exec_pairs: [(action_str, exec_result_dict), ...]
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"{TOOL_RESULT_MARKER} 以下是上一轮指令的真实执行结果（非用户发言）。",
        f"时间: {now} | 系统: {platform.system()} {platform.release()} "
        f"| 架构: {platform.machine()}",
        "",
    ]
    for idx, (act, res) in enumerate(exec_pairs, 1):
        tag = _first_tag_name(act) or "未知指令"
        lines.append(f"### 第 {idx} 条指令执行结果")
        lines.append(f"指令: {act.strip()[:300]}")
        if res.get("error"):
            lines.append(f"状态: 失败（{res['error']}）")
        else:
            lines.append("状态: 已执行")
        for r in res.get("results", []) or []:
            if isinstance(r, dict):
                name = r.get("instruction", "")
                out = r.get("output", "") or ""
                err = r.get("error", "") or ""
                flag = "成功" if r.get("success", not err) else "失败"
                body = out if out else err
                if not body:
                    body = "（无文本输出）"
                lines.append(f"- {name} [{flag}]: {body}")
            else:
                lines.append(f"- {str(r)}")
        if not res.get("results"):
            lines.append("- （无文本输出）")
        lines.append("")

    lines.append(
        "请基于以上真实执行结果继续推理：\n"
        "1. 任务已完成 → 只输出 <speaking> 面向用户的总结</speaking>"
        "<action> </action>\n"
        "2. 任务未完成 → 输出 <speaking> 进展说明</speaking> "
        "<action>下一条指令</action>\n"
        "3. 严禁编造未出现在上述结果中的数据"
    )
    return "\n".join(lines)


def _first_tag_name(action: str) -> str:
    m = re.search(r'<([A-Za-z][A-Za-z0-9_()]*)\s*/?>', action or "")
    return m.group(1) if m else ""


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
            print(f"[agent] ═══ 轮次 {iteration+1}/{MAX_ITERATIONS} 开始 ═══", flush=True)
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
                print(f"[agent] 轮次{iteration+1} 无 action → 任务结束", flush=True)
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
                final_assistant = (_blocks_to_assistant_content(blocks)
                                   or _clean_speaking(speaking) or thinking or "任务已完成。")
                messages.append({"role": "assistant", "content": final_assistant})
                return {"thinking": all_thinking, "speaking": all_speaking,
                        "success": True, "error": None, "messages": messages,
                        "action": last_action, "exec_result": all_exec_results,
                        "blocks": all_blocks}

            # ---------------------------------------------------------
            # 关键顺序：先落 assistant（含 action），再执行，再注入 user 结果
            # ---------------------------------------------------------
            assistant_content = _blocks_to_assistant_content(blocks)
            if not assistant_content.strip():
                assistant_content = _clean_speaking(speaking) or thinking or "（本轮无有效输出）"
            messages.append({"role": "assistant", "content": assistant_content})

            # 顺序执行本轮 blocks 中的 action（去重：相同指令只执行一次）
            seen_actions = set()
            exec_pairs = []          # [(action, result), ...] 本轮执行记录
            for bi, blk in enumerate(blocks):
                if blk["type"] == "action" and blk["content"].strip():
                    act = blk["content"].strip()
                    if act in seen_actions:
                        continue
                    seen_actions.add(act)
                    if self._stop_event.is_set():
                        # 用户已停止 → 不再执行新指令
                        continue
                    print(f"[agent] 轮次{iteration+1} 第{bi+1}条action: {repr(act[:200])}", flush=True)
                    if not self._is_valid_action(act):
                        if on_chunk:
                            on_chunk("speaking",
                                     "\n\n⚠️ 模型生成的指令格式错误，已跳过执行。\n")
                        # 格式错误也要回灌，让模型知道失败并重试
                        exec_pairs.append((act, {"success": False, "results": [],
                                                  "error": "指令格式非法，未执行"}))
                        continue

                    exec_result = self._executor.execute(act)
                    all_exec_results.append(exec_result)
                    exec_pairs.append((act, exec_result))

                    # 实时把执行结果推给 UI（不等整个任务结束才刷新）
                    if on_chunk:
                        try:
                            on_chunk("exec_result", json.dumps({
                                "action": act,
                                "result": exec_result,
                            }, ensure_ascii=False))
                        except Exception:
                            pass

            # 一次性把本轮全部执行结果，作为一条全新的 user 消息注入上下文
            if exec_pairs:
                tool_msg = _format_tool_results(exec_pairs)
                messages.append({"role": "user", "content": tool_msg})
                print(f"[agent] 轮次{iteration+1} 执行结果已作为新一轮 user 消息注入 → LLM 继续推理", flush=True)

            print(f"[agent] 轮次{iteration+1} 完成，已执行 {len(exec_pairs)} 条指令，继续下一轮推理...", flush=True)
            # continue → 下一轮 LLM 调用，messages 严格保持 user/assistant 交替

        return {"thinking": all_thinking,
                "speaking": "\n".join(_clean_speaking(b["content"]) for b in all_blocks if b["type"] == "speaking" and b["content"].strip()),
                "success": False, "error": f"推理轮次超过上限({MAX_ITERATIONS})",
                "messages": messages, "action": last_action,
                "exec_result": all_exec_results, "blocks": all_blocks}

    def _is_valid_action(self, action: str) -> bool:
        """校验 action 是否包含类XML指令标签。

        注意：不能只判断能否被 ET 解析 —— 纯文本（如"我来帮你看看"）包进
        <root> 后同样是合法 XML（退化成文本节点），会让垃圾指令直接进入执行器。
        必须确认解析结果里至少存在一个真实的子元素标签。
        """
        if not action or not action.strip():
            return True

        for candidate in (action, _xml_escape_attrs(action)):
            try:
                root = ET.fromstring(f"<root>{candidate}</root>")
            except ET.ParseError:
                continue
            # 至少要有一个真实元素（标签名存在）
            if len(list(root)) > 0:
                return True

        # 正则回退：至少有一个类XML标签
        tags = re.findall(r'</?[A-Za-z][A-Za-z0-9_()]*[^>]*>', action)
        if len(tags) >= 1:
            return True

        print(f"[_is_valid_action] 模型输出无法解析: {repr(action[:300])}")
        return False