"""
ApexAgent 指令执行器
解析类XML指令并执行Windows系统操作
"""

import os
import sys
import re
import subprocess
import xml.etree.ElementTree as ET


def _xml_escape_attrs(tag_xml: str) -> str:
    """将非标准XML属性格式转为标准格式，例如：
    <OpenExe 程序路径>/Applications/Safari.app</OpenExe>
    → <OpenExe 程序路径="">/Applications/Safari.app</OpenExe>
    <RunCommand cmd="ls"/> → 不变
    """
    def _fix(m: re.Match) -> str:
        tagname = m.group(1)
        attrs_raw = m.group(2)
        self_close = m.group(3) or ""

        if not attrs_raw.strip():
            return m.group(0)

        # 提取属性 token：带引号的完整保留，裸单词加 =""
        tokens = re.findall(r'(?:"[^"]*"|\'[^\']*\'|[^\s=]+(?:\s*=\s*(?:"[^"]*"|\'[^\']*\'|\S+))?|\S+)', attrs_raw)
        fixed = []
        for t in tokens:
            t = t.strip()
            if not t:
                continue
            if '=' in t:
                fixed.append(t)
            else:
                fixed.append(f'{t}=""')
        return f"<{tagname} {' '.join(fixed)}{self_close}>" if fixed else m.group(0)

    return re.sub(
        r'<([A-Za-z][A-Za-z0-9_()]*)\s+(.*?)(/)?>',
        _fix,
        tag_xml,
        flags=re.DOTALL,
    )


class ActionExecutor:
    """
    解析 action 中的类XML指令，执行对应的Windows操作
    支持标准XML解析，自动兼容换行、缩进、嵌套标签
    """

    def execute(self, action: str) -> dict:
        """
        解析并执行 action 中的所有指令

        参数:
            action: 类XML指令字符串

        返回:
            {
                "success": True/False,
                "results": [{"instruction": "指令名", "output": "结果", "success": True/False}, ...],
                "error": "错误信息" 或 None
            }
        """
        if not action or not action.strip():
            return {"success": True, "results": [], "error": None}

        results = []
        try:
            # 预处理非标准XML属性格式（如 key>value → key=""）
            normalized = _xml_escape_attrs(action)
            wrapped = f"<root>{normalized}</root>"
            root = ET.fromstring(wrapped)

            for child in root:
                result = self._execute_instruction(child)
                results.append(result)

            return {"success": True, "results": results, "error": None}

        except ET.ParseError as e:
            return {
                "success": False,
                "results": results,
                "error": f"XML指令解析失败: {str(e)}"
            }
        except Exception as e:
            return {
                "success": False,
                "results": results,
                "error": f"指令执行异常: {str(e)}"
            }

    def _execute_instruction(self, element: ET.Element) -> dict:
        tag = element.tag
        attrs = dict(element.attrib)
        text = (element.text or "").strip()

        handler = self._HANDLERS.get(tag)
        if handler is None:
            return {
                "instruction": tag,
                "output": "",
                "success": False,
                "error": f"未知指令: {tag}"
            }

        try:
            output = handler(self, attrs, text)
            return {
                "instruction": tag,
                "output": str(output) if output else "",
                "success": True,
                "error": None
            }
        except Exception as e:
            return {
                "instruction": tag,
                "output": "",
                "success": False,
                "error": str(e)
            }

    # ================================================================
    #  文件操作 API
    # ================================================================

    def _handle_read_file(self, attrs: dict, text: str) -> str:
        path = attrs.get("路径", text)
        if not os.path.exists(path):
            return f"文件不存在: {path}"
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if len(content) > 5000:
                content = content[:5000] + "\n...(文件过长，已截断)"
            return content
        except Exception as e:
            return f"读取文件失败: {str(e)}"

    def _handle_read_folder(self, attrs: dict, text: str) -> str:
        path = attrs.get("路径", text)
        if not os.path.exists(path):
            return f"文件夹不存在: {path}"
        try:
            items = os.listdir(path)
            result_lines = [f"文件夹 {path} 内容 ({len(items)} 项):"]
            for item in sorted(items):
                item_path = os.path.join(path, item)
                item_type = "📁" if os.path.isdir(item_path) else "📄"
                try:
                    size = os.path.getsize(item_path)
                    result_lines.append(f"  {item_type} {item} ({self._format_size(size)})")
                except Exception:
                    result_lines.append(f"  {item_type} {item}")
            return "\n".join(result_lines)
        except Exception as e:
            return f"读取文件夹失败: {str(e)}"

    def _handle_search_file(self, attrs: dict, text: str) -> str:
        folder_name = attrs.get("文件夹名", text)
        search_root = attrs.get("根目录", os.path.expanduser("~"))
        results = []
        try:
            for root_dir, dirs, files in os.walk(search_root):
                for d in dirs:
                    if folder_name.lower() in d.lower():
                        results.append(os.path.join(root_dir, d))
                if len(results) > 50:
                    break
            if not results:
                return f"未找到包含 '{folder_name}' 的文件夹"
            return f"搜索 '{folder_name}' 结果:\n" + "\n".join(results[:50])
        except Exception as e:
            return f"搜索失败: {str(e)}"

    def _handle_remove_file(self, attrs: dict, text: str) -> str:
        path = attrs.get("路径", text)
        if not os.path.exists(path):
            return f"路径不存在: {path}"
        try:
            if os.path.isdir(path):
                import shutil
                shutil.rmtree(path)
                return f"已删除文件夹: {path}"
            else:
                os.remove(path)
                return f"已删除文件: {path}"
        except Exception as e:
            return f"删除失败: {str(e)}"

    def _handle_name_change(self, attrs: dict, text: str) -> str:
        path = attrs.get("路径", text)
        if not os.path.exists(path):
            return f"路径不存在: {path}"
        # 新名称通常在 text 中
        return f"重命名功能: 暂未实现完整逻辑 ({path})"

    def _handle_diff_json_file(self, attrs: dict, text: str) -> str:
        path = attrs.get("路径", text)
        return f"JSON修改功能: 暂未实现 ({path})"

    # ================================================================
    #  程序进程控制 API
    # ================================================================

    def _handle_open_exe(self, attrs: dict, text: str) -> str:
        path = attrs.get("程序路径", text)
        if not os.path.exists(path):
            if sys.platform.startswith('darwin'):
                import glob as _glob
                app_name = os.path.basename(path)
                if app_name.endswith('.app'):
                    for base in ['/Applications', '/System/Applications',
                                 os.path.expanduser('~/Applications')]:
                        test_path = os.path.join(base, app_name)
                        if os.path.exists(test_path):
                            path = test_path
                            break
                    else:
                        try:
                            result = subprocess.run(
                                ['mdfind', f'kMDItemKind=="Application"&&kMDItemFSName=="{app_name}"'],
                                capture_output=True, text=True, timeout=5
                            )
                            found = [l for l in result.stdout.strip().split('\n') if l]
                            if found and os.path.exists(found[0]):
                                path = found[0]
                        except Exception:
                            pass
                else:
                    for base in ['/Applications', '/System/Applications',
                                 os.path.expanduser('~/Applications')]:
                        for tst in [f"{app_name}.app", app_name]:
                            test_path = os.path.join(base, tst)
                            if os.path.exists(test_path):
                                path = test_path
                                break
                        else:
                            continue
                        break
                    else:
                        try:
                            result = subprocess.run(
                                ['mdfind', f'kMDItemKind=="Application"&&kMDItemDisplayName=="{app_name}*"c'],
                                capture_output=True, text=True, timeout=5
                            )
                            found = [l for l in result.stdout.strip().split('\n') if l]
                            if found and os.path.exists(found[0]):
                                path = found[0]
                        except Exception:
                            pass
            if not os.path.exists(path):
                return f"程序不存在: {text}（已搜索系统但未找到）"
        if not os.path.exists(path):
            return f"程序不存在: {path}"
        try:
            if sys.platform.startswith('win32'):
                os.startfile(path)
            elif sys.platform.startswith('darwin'):
                subprocess.run(["open", path], check=True)
            else:
                subprocess.run(["xdg-open", path], check=True)
            return f"已启动程序: {path}"
        except Exception as e:
            return f"启动程序失败: {str(e)}"

    def _handle_read_running(self, attrs: dict, text: str) -> str:
        try:
            if sys.platform.startswith('win32'):
                result = subprocess.run(
                    ["tasklist", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=10
                )
            else:
                result = subprocess.run(
                    ["ps", "aux"],
                    capture_output=True, text=True, timeout=10
                )
            lines = result.stdout.strip().split("\n")[:30]
            return "当前运行进程 (前30个):\n" + "\n".join(lines)
        except Exception as e:
            return f"获取进程列表失败: {str(e)}"

    def _handle_kill_process(self, attrs: dict, text: str) -> str:
        pid = attrs.get("pid", text)
        try:
            if sys.platform.startswith('win32'):
                result = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True, text=True, timeout=10
                )
            else:
                result = subprocess.run(
                    ["kill", "-9", str(pid)],
                    capture_output=True, text=True, timeout=10
                )
            return f"进程终止结果: {result.stdout.strip() or '成功'}"
        except Exception as e:
            return f"终止进程失败: {str(e)}"

    # ================================================================
    #  系统基础 API
    # ================================================================

    def _handle_shutdown(self, attrs: dict, text: str) -> str:
        return "关机功能需要用户确认，暂不自动执行"

    # ================================================================
    #  环境感知 API
    # ================================================================

    def _handle_get_desktop_files(self, attrs: dict, text: str) -> str:
        try:
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            if not os.path.exists(desktop):
                return "桌面路径不存在"
            items = os.listdir(desktop)
            result_lines = [f"桌面文件 ({len(items)} 项):"]
            for item in sorted(items):
                item_path = os.path.join(desktop, item)
                item_type = "📁" if os.path.isdir(item_path) else "📄"
                try:
                    size = os.path.getsize(item_path)
                    result_lines.append(f"  {item_type} {item} ({self._format_size(size)})")
                except Exception:
                    result_lines.append(f"  {item_type} {item}")
            return "\n".join(result_lines)
        except Exception as e:
            return f"获取桌面文件失败: {str(e)}"

    def _handle_get_active_window(self, attrs: dict, text: str) -> str:
        return "获取活动窗口: 暂未实现（需要Windows API）"

    def _handle_get_disk_usage(self, attrs: dict, text: str) -> str:
        path = attrs.get("路径", text)
        try:
            import shutil
            usage = shutil.disk_usage(path)
            return (
                f"磁盘 {path} 使用情况:\n"
                f"  总容量: {self._format_size(usage.total)}\n"
                f"  已用: {self._format_size(usage.used)}\n"
                f"  可用: {self._format_size(usage.free)}"
            )
        except Exception as e:
            return f"获取磁盘信息失败: {str(e)}"

    def _handle_get_system_info(self, attrs: dict, text: str) -> str:
        try:
            import platform
            info_lines = [
                f"系统: {platform.system()} {platform.release()}",
                f"版本: {platform.version()}",
                f"架构: {platform.machine()}",
                f"处理器: {platform.processor()}",
                f"主机名: {platform.node()}",
            ]
            return "\n".join(info_lines)
        except Exception as e:
            return f"获取系统信息失败: {str(e)}"

    def _handle_get_process_list(self, attrs: dict, text: str) -> str:
        return self._handle_read_running(attrs, text)

    def _handle_get_network_status(self, attrs: dict, text: str) -> str:
        try:
            if sys.platform.startswith('win32'):
                result = subprocess.run(
                    ["ipconfig"],
                    capture_output=True, text=True, timeout=10,
                    shell=True
                )
            else:
                result = subprocess.run(
                    ["ifconfig"],
                    capture_output=True, text=True, timeout=10
                )
            return result.stdout.strip()[:3000]
        except Exception as e:
            return f"获取网络信息失败: {str(e)}"

    # ================================================================
    #  窗口精细化控制 API
    # ================================================================

    def _handle_close_window(self, attrs: dict, text: str) -> str:
        title = attrs.get("窗口标题", text)
        return f"关闭窗口 '{title}': 暂未实现（需要Windows API）"

    def _handle_resize_window(self, attrs: dict, text: str) -> str:
        return "调整窗口大小: 暂未实现（需要Windows API）"

    def _handle_focus_window(self, attrs: dict, text: str) -> str:
        title = attrs.get("标题", text)
        return f"置顶窗口 '{title}': 暂未实现（需要Windows API）"

    # ================================================================
    #  剪贴板 API
    # ================================================================

    def _handle_clipboard_read(self, attrs: dict, text: str) -> str:
        try:
            if sys.platform.startswith('darwin'):
                result = subprocess.run(
                    ["pbpaste"],
                    capture_output=True, text=True, timeout=5
                )
            elif sys.platform.startswith('win32'):
                result = subprocess.run(
                    ["powershell", "-Command", "Get-Clipboard"],
                    capture_output=True, text=True, timeout=5,
                    shell=True
                )
            else:
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o"],
                    capture_output=True, text=True, timeout=5
                )
            content = result.stdout.strip()
            return content if content else "剪贴板为空"
        except Exception as e:
            return f"读取剪贴板失败: {str(e)}"

    def _handle_clipboard_write(self, attrs: dict, text: str) -> str:
        content = attrs.get("文本", text)
        try:
            if sys.platform.startswith('darwin'):
                subprocess.run(
                    ["pbcopy"],
                    input=content,
                    capture_output=True, text=True, timeout=5
                )
            elif sys.platform.startswith('win32'):
                subprocess.run(
                    ["powershell", "-Command", f"Set-Clipboard -Value '{content}'"],
                    capture_output=True, text=True, timeout=5,
                    shell=True
                )
            else:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=content,
                    capture_output=True, text=True, timeout=5
                )
            return f"已写入剪贴板: {content[:100]}"
        except Exception as e:
            return f"写入剪贴板失败: {str(e)}"

    # ================================================================
    #  屏幕视觉操控 API
    # ================================================================

    def _handle_screenshot(self, attrs: dict, text: str) -> str:
        return "截图功能: 暂未实现（需要多模态支持）"

    def _handle_mouse_move(self, attrs: dict, text: str) -> str:
        return "鼠标移动: 暂未实现（需要屏幕操控支持）"

    def _handle_mouse_click(self, attrs: dict, text: str) -> str:
        return "鼠标点击: 暂未实现（需要屏幕操控支持）"

    def _handle_mouse_drag(self, attrs: dict, text: str) -> str:
        return "鼠标拖拽: 暂未实现（需要屏幕操控支持）"

    # ================================================================
    #  高级系统底层 API
    # ================================================================

    def _handle_registry_read(self, attrs: dict, text: str) -> str:
        return "注册表读取: 暂未实现"

    def _handle_registry_write(self, attrs: dict, text: str) -> str:
        return "注册表写入: 暂未实现"

    def _handle_service_control(self, attrs: dict, text: str) -> str:
        return "服务控制: 暂未实现"

    def _handle_run_command(self, attrs: dict, text: str) -> str:
        cmd = attrs.get("cmd", text)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                shell=True, encoding="utf-8", errors="replace"
            )
            output = result.stdout.strip()
            if result.stderr:
                output += f"\n[stderr]: {result.stderr.strip()}"
            return output[:3000] if output else "命令执行完成，无输出"
        except Exception as e:
            return f"执行命令失败: {str(e)}"

    # ================================================================
    #  循环与逻辑 API
    # ================================================================

    def _handle_while(self, attrs: dict, text: str) -> str:
        return "while循环: 暂未实现"

    def _handle_for(self, attrs: dict, text: str) -> str:
        return "for循环: 暂未实现"

    def _handle_if(self, attrs: dict, text: str) -> str:
        return "If条件: 暂未实现"

    # ================================================================
    #  工具方法
    # ================================================================

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} PB"

    # 指令映射表
    _HANDLERS = {
        # 文件操作
        "ReadFile": _handle_read_file,
        "ReadFolder": _handle_read_folder,
        "SearchFile": _handle_search_file,
        "RemoveFile": _handle_remove_file,
        "NameChange": _handle_name_change,
        "DiffJsonFile": _handle_diff_json_file,
        # 程序进程控制
        "OpenExe": _handle_open_exe,
        "ReadRunning": _handle_read_running,
        "KillProcess": _handle_kill_process,
        # 系统基础
        "Shutdown": _handle_shutdown,
        # 环境感知
        "GetDesktopFiles": _handle_get_desktop_files,
        "GetActiveWindow": _handle_get_active_window,
        "GetDiskUsage": _handle_get_disk_usage,
        "GetSystemInfo": _handle_get_system_info,
        "GetProcessList": _handle_get_process_list,
        "GetNetworkStatus": _handle_get_network_status,
        # 窗口控制
        "CloseWindow": _handle_close_window,
        "ResizeWindow": _handle_resize_window,
        "FocusWindow": _handle_focus_window,
        # 剪贴板
        "ClipboardRead": _handle_clipboard_read,
        "ClipboardWrite": _handle_clipboard_write,
        # 屏幕操控
        "Screenshot": _handle_screenshot,
        "MouseMove": _handle_mouse_move,
        "MouseClick": _handle_mouse_click,
        "MouseDrag": _handle_mouse_drag,
        # 系统底层
        "RegistryRead": _handle_registry_read,
        "RegistryWrite": _handle_registry_write,
        "ServiceControl": _handle_service_control,
        "RunCommand": _handle_run_command,
        # 循环逻辑
        "while": _handle_while,
        "for": _handle_for,
        "If": _handle_if,
    }