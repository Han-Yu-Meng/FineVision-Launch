import subprocess
import os
import time
import requests
import sys
import socket
import atexit
import signal
import threading
import psutil
from fins.core import LaunchDescription, override_yaml

class Agent:
    def __init__(self, name="agent_default", port=None, ip="0.0.0.0"):
        self.name = name
        self.ip = ip
        self.prefix = "[FineVision-Launch]"

        # 若没有指定 port，则随机选择一个未被占用的端口
        if port is None:
            self.port = self._find_random_port(ip)
            print(f"{self.prefix} No port specified, auto-selected port: {self.port}")
        else:
            self.port = port
        self.bin = os.path.expanduser("~/.fins/install/agent")
        self.proc = None
        self._is_running = False
        self._enable_timeline_monitor = False
        self._debug_mode = False
        self._debug_full_bt = False
        self._log_level = "1"  # Default to INFO
        # 存储待加载的配置文件路径列表
        self.config_files = []
        
        # 注册退出钩子，确保 Python 脚本退出时杀死 Agent 进程
        atexit.register(self.stop)
        
        # 处理 SIGTERM 信号，确保 kill 命令等也能触发正常退出
        # 注意：signal 只能在主线程注册
        try:
            signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
        except ValueError:
            # 如果不在主线程，忽略此设置
            pass

    def add_config(self, config_path: str, overrides: dict = None):
        """
        添加一个 YAML 配置文件路径，可选择性地覆盖其中的参数。

        :param config_path: YAML 配置文件路径
        :param overrides:   可选的参数字典，使用 dot-notation 路径覆盖 YAML 中的值，
                            例如 ``{"camera.width": 1920, "camera.height": 1080}``
        """
        if os.path.exists(config_path):
            self.config_files.append((config_path, overrides or {}))
            msg = f"{self.prefix} Added config: {config_path}"
            if overrides:
                msg += f" (with {len(overrides)} override(s))"
            print(msg)
        else:
            print(f"{self.prefix} Error: Config file not found: {config_path}")
            sys.exit(1)

    def add_config_dir(self, config_dir: str):
        """添加一个目录下的所有 YAML 配置文件"""
        if not os.path.isdir(config_dir):
            print(f"{self.prefix} Error: Config directory not found: {config_dir}")
            sys.exit(1)
        
        # 遍历目录，获取所有 .yaml 和 .yml 文件
        files = sorted(os.listdir(config_dir))
        added_any = False
        for f in files:
            if f.endswith((".yaml", ".yml")):
                full_path = os.path.join(config_dir, f)
                # 仅添加文件，排除子目录
                if os.path.isfile(full_path):
                    self.add_config(full_path)
                    added_any = True
        
        if not added_any:
            print(f"{self.prefix} Warning: No YAML files found in directory: {config_dir}")

    def enable_timeline_monitor(self):
        """启用时间线监控"""
        self._enable_timeline_monitor = True

    def enable_debugging(self, full_backtrace=False):
        """
        启用 GDB 调试模式。

        使用 GDB 启动 Agent 进程。如果进程因信号（如 SIGSEGV、SIGABRT）意外退出，
        GDB 会自动打印 backtrace 调用栈、信号信息和寄存器状态，方便定位崩溃位置。

        注意：
        - 需要系统安装 GDB（>= 7.0）
        - GDB 下运行可能影响进程的时序和内存布局
        - 正常退出时 GDB 也会打印 "[Inferior exited normally]" 信息

        :param full_backtrace: 如果为 True，显示完整调用栈（含局部变量）
        """
        # 检查 GDB 是否可用
        try:
            subprocess.run(["gdb", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            print(f"{self.prefix} Error: GDB is not installed or not found in PATH.")
            print(f"{self.prefix} Install it with: sudo apt install gdb")
            sys.exit(1)

        self._debug_mode = True
        self._debug_full_bt = full_backtrace
        bt_type = "full" if full_backtrace else "normal"
        print(f"{self.prefix} Debug mode enabled - will launch with GDB ({bt_type} backtrace on crash)")

    def log_level(self, level: str):
        """设置日志级别 (DEBUG, INFO, WARN, ERROR, OFF)"""
        mapping = {
            "DEBUG": "0",
            "INFO": "1",
            "WARN": "2",
            "ERROR": "3",
            "OFF": "4"
        }
        upper_level = level.upper()
        if upper_level in mapping:
            self._log_level = mapping[upper_level]
            print(f"{self.prefix} Log level set to: {upper_level} ({self._log_level})")
        else:
            print(f"{self.prefix} Error: Invalid log level: {level}. Valid levels: DEBUG, INFO, WARN, ERROR, OFF")
            sys.exit(1)

    def _check_plugin_status(self):
        try:
            r = requests.get(f"http://127.0.0.1:{self.port}/plugin_status", timeout=1)
            if r.status_code == 200:
                return r.json()
        except:
            return None
        return None

    def _apply_parameters(self, config_path: str, overrides: dict = None):
        """读取 YAML 文件，应用 overrides，上传到 Agent"""
        overrides = overrides or {}
        try:
            with open(config_path, 'r') as f:
                yaml_content = f.read()

            if overrides:
                yaml_content = override_yaml(yaml_content, overrides)

            url = f"http://127.0.0.1:{self.port}/apply_parameters"
            payload = {"content": yaml_content}
            r = requests.post(url, json=payload, timeout=2)
            r.raise_for_status()
            msg = f"{self.prefix} Applied config: {os.path.basename(config_path)}"
            if overrides:
                msg += f" (with {len(overrides)} override(s))"
            print(msg)
        except Exception as e:
            print(f"{self.prefix} Error: Failed to apply {config_path}: {e}")
            sys.exit(1)

    def _find_random_port(self, ip):
        """随机选择一个未被占用的端口"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((ip, 0))
            return s.getsockname()[1]

    def _check_port_available(self, ip, port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                # 允许重用处于 TIME_WAIT 状态的端口
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((ip, port))
                return True
            except OSError:
                return False

    def _kill_process_on_port(self, port):
        """查找并杀死占用指定端口的进程"""
        found_processes = []
        access_denied_count = 0
        
        try:
            # 遍历所有进程，查找占用端口的连接
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    connections = proc.connections(kind='inet')
                    for conn in connections:
                        if conn.laddr.port == port:
                            found_processes.append(proc)
                            break
                except psutil.AccessDenied:
                    # 记录因权限不足无法查看连接的进程数
                    access_denied_count += 1
                except psutil.NoSuchProcess:
                    continue
            
            if not found_processes:
                if access_denied_count > 0:
                    print(f"{self.prefix} Warning: Port {port} is occupied, but some processes could not be inspected due to insufficient privileges. Try running with sudo/root.")
                else:
                    print(f"{self.prefix} Info: Port {port} is occupied, but no active process owns it. It might be in TIME_WAIT or kernel cleanup state.")
                return False

            for proc in found_processes:
                pid = proc.info['pid']
                name = proc.info['name']
                print(f"{self.prefix} Found process '{name}' (PID: {pid}) using port {port}")
                try:
                    user_input = input(f"{self.prefix} Do you want to kill this process? (y/n): ").lower()
                except EOFError:
                    return False
                
                if user_input == 'y':
                    try:
                        proc.terminate()
                        try:
                            proc.wait(timeout=3)
                        except psutil.TimeoutExpired:
                            proc.kill()
                        print(f"{self.prefix} Process {pid} terminated. Waiting for port to release...")
                        return True
                    except psutil.NoSuchProcess:
                        print(f"{self.prefix} Process {pid} already terminated.")
                        return True
                    except psutil.AccessDenied:
                        print(f"{self.prefix} Error: Access denied to kill process {pid}. You may need higher privileges.")
                        return False
                else:
                    return False
            return True
        except Exception as e:
            print(f"{self.prefix} Error while trying to kill process: {e}")
        return False

    def _get_exit_message(self, exit_code):
        """获取进程退出码的描述信息"""
        if exit_code is None:
            return "Unknown"
        if exit_code < 0:
            sig_num = -exit_code
            try:
                sig_name = signal.Signals(sig_num).name
                return f"Crashed with signal {sig_num} ({sig_name})"
            except (ValueError, AttributeError):
                return f"Crashed with signal {sig_num}"
        return f"Exited with code {exit_code}"

    def _log_reader_loop(self, pipe):
        """后台线程：读取 Agent 进程的 stdout/stderr 并实时过滤不需要的调用栈帧"""
        blacklist = [
            "httplib::",
            "fins::AgentServer",
            "fins::NodeLib",
            "std::",
            "__invoke",
            "_Function_handler",
            "pthread",
            "clone3",
            "libstdc++.so",
            "/usr/include/c++/"
        ]

        skip_current_frame = False

        try:
            for line in iter(pipe.readline, ''):
                stripped = line.strip()

                # 检测是否为 GDB 调用栈帧的头部，例如 "#0  0x...", "#12 0x..."
                is_frame_header = False
                if stripped.startswith('#'):
                    parts = stripped.split()
                    if len(parts) > 0 and parts[0][1:].isdigit():
                        is_frame_header = True

                if is_frame_header:
                    # 如果匹配到黑名单中的任意关键字，则标记当前帧需要被过滤
                    if any(kw in line for kw in blacklist):
                        skip_current_frame = True
                    else:
                        skip_current_frame = False
                elif stripped.startswith('[Switching to Thread') or stripped.startswith('Thread '):
                    # 线程切换等辅助信息不属于特定帧，不自动继承上文的 skip 状态
                    skip_current_frame = False

                # 如果判定为需要过滤的帧（包括该帧下的局部变量），则跳过打印
                if skip_current_frame:
                    continue

                # 正常输出未被过滤的行
                sys.stdout.write(line)
                sys.stdout.flush()
        except Exception as e:
            print(f"\n{self.prefix} Log reader encountered an error: {e}")
        finally:
            pipe.close()

    def launch(self, *groups: 'Group'):
        """启动 Agent 并依次推送配置和数据流"""
        ld = LaunchDescription(groups=list(groups))
        
        if not os.path.exists(self.bin):
            print(f"{self.prefix} Error: Agent executable not found at {self.bin}")
            sys.exit(1)

        # 检查端口可用性
        if not self._check_port_available(self.ip, self.port):
            print(f"{self.prefix} Warning: Port {self.port} is already in use.")
            
            # 尝试识别并杀死占用该端口的进程
            self._kill_process_on_port(self.port)
            
            # 循环探测等待，确保给系统内核清理 lingering socket（如 TIME_WAIT 状态）的时间
            max_wait_time = 5.0  # 最大等待时间，单位：秒
            wait_interval = 0.5  # 探测间隔，单位：秒
            elapsed = 0.0
            
            print(f"{self.prefix} Checking/Waiting for port {self.port} to become available...")
            while elapsed < max_wait_time:
                if self._check_port_available(self.ip, self.port):
                    break
                time.sleep(wait_interval)
                elapsed += wait_interval
                
            if not self._check_port_available(self.ip, self.port):
                print(f"{self.prefix} Error: Port {self.port} is still in use after waiting {max_wait_time}s.")
                sys.exit(1)
            else:
                print(f"{self.prefix} Port {self.port} is now available.")

        # 1. 启动 Agent 进程
        cmd = [
            self.bin, "--name", self.name, "--port", str(self.port),
            "--ip", self.ip, "--load-all", "--log-level", self._log_level
        ]
        if self._enable_timeline_monitor:
            cmd.append("--perf")

        # 如果启用了调试模式，使用 GDB 启动
        if self._debug_mode:
            gdb_ex_commands = ["set pagination off", "run"]
            if self._debug_full_bt:
                gdb_ex_commands.append("bt full")
            else:
                gdb_ex_commands.append("bt")

            gdb_cmd = ["gdb", "-batch"]
            for ex in gdb_ex_commands:
                gdb_cmd.extend(["-ex", ex])
            gdb_cmd.extend(["--return-child-result", "--args"])
            gdb_cmd.extend(cmd)
            cmd = gdb_cmd
            print(f"{self.prefix} Starting FINS Agent [{self.name}] on port {self.port} (GDB debug mode)...")
        else:
            print(f"{self.prefix} Starting FINS Agent [{self.name}] on port {self.port}...")

        # 使用进程组 (Process Group) 启动，同时重定向输出以便过滤
        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1
        }
        if os.name == 'posix':
            popen_kwargs["preexec_fn"] = os.setpgrp

        self.proc = subprocess.Popen(cmd, **popen_kwargs)

        # 启动后台线程实时读取并过滤输出
        self.reader_thread = threading.Thread(
            target=self._log_reader_loop,
            args=(self.proc.stdout,),
            daemon=True
        )
        self.reader_thread.start()

        pid_label = "GDB PID" if self._debug_mode else "PID"
        print(f"{self.prefix} Agent process started with {pid_label}: {self.proc.pid}")

        # 2. 等待插件加载完成
        print(f"{self.prefix} Waiting for Agent to load plugins...")
        timeout = 30
        start_t = time.time()
        while time.time() - start_t < timeout:
            status = self._check_plugin_status()
            if status and status.get("state") == "COMPLETE":
                print(f"{self.prefix} Plugins loaded.")
                break
            elif status and status.get("state") == "ERROR":
                print(f"{self.prefix} Error: Plugin loading failed.")
                self.stop()
                sys.exit(1)
            
            time.sleep(0.5)
            exit_code = self.proc.poll()
            if exit_code is not None:
                print(f"{self.prefix} Error: Agent process exited prematurely. {self._get_exit_message(exit_code)}")
                sys.exit(1)
        else:
            print(f"{self.prefix} Error: Timeout waiting for Agent.")
            self.stop()
            sys.exit(1)

        # 3. 依次应用所有添加的配置文件
        if self.config_files:
            print(f"{self.prefix} Applying {len(self.config_files)} configurations...")
            for cfg, overrides in self.config_files:
                self._apply_parameters(cfg, overrides)

        # 4. 推送 Dataflow 配置
        print(f"{self.prefix} Sending dataflow configuration...")
        try:
            url = f"http://127.0.0.1:{self.port}/load_dataflow"
            r = requests.post(url, data=ld.to_fins_json())
            r.raise_for_status()
            
            # 5. 启动执行
            requests.post(f"http://127.0.0.1:{self.port}/set_status", json={"state": "RUNNING"})
            self._is_running = True
            print(f"{self.prefix} System is now RUNNING.")
        except Exception as e:
            print(f"{self.prefix} Error: Failed to load dataflow: {e}")
            self.stop()
            sys.exit(1)

    def spin(self):
        if not self._is_running:
            return
        print(f"{self.prefix} Press Ctrl+C to terminate.")
        try:
            while self._is_running:
                time.sleep(1)
                exit_code = self.proc.poll()
                if exit_code is not None:
                    if exit_code != 0:
                        print(f"{self.prefix} Error: Agent process died. {self._get_exit_message(exit_code)}")
                    break
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self):
        # 避免在 stop 过程中再次触发 atexit 导致的死循环
        if hasattr(self, '_stopping') and self._stopping:
            return
        self._stopping = True

        if self.proc:
            print(f"\n{self.prefix} Stopping Agent [{self.name}]...")
            
            # 如果是 POSIX 系统，尝试杀死整个进程组
            if os.name == 'posix':
                try:
                    # 检查进程是否还在运行
                    if self.proc.poll() is None:
                        os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                        # 给一点时间优雅退出
                        time.sleep(0.5)
                except (ProcessLookupError, OSError):
                    # 进程或进程组已不存在，忽略
                    pass
                except Exception as e:
                    print(f"{self.prefix} Warning: Failed to kill process group: {e}")
            
            # 常规终止
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                # 强力杀死
                if os.name == 'posix':
                    try:
                        os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                    except:
                        pass
                self.proc.kill()
            
            self.proc = None
            self._is_running = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()