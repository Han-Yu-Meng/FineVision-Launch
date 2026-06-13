import subprocess
import os
import time
import requests
import sys
import socket
import atexit
import signal
import psutil
from fins.core import LaunchDescription, override_yaml

class Agent:
    def __init__(self, name="agent_default", port=9090, ip="0.0.0.0"):
        self.name = name
        self.port = port
        self.ip = ip
        self.bin = os.path.expanduser("~/.fins/install/agent")
        self.proc = None
        self._is_running = False
        self._enable_perf_monitor = False
        self._log_level = "1"  # Default to INFO
        self.prefix = "[FineVision-Launch]"
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

    def enable_performance_monitor(self):
        """启用性能监控"""
        self._enable_perf_monitor = True

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
        print(f"{self.prefix} Starting FINS Agent [{self.name}] on port {self.port}...")
        cmd = [
            self.bin, "--name", self.name, "--port", str(self.port),
            "--ip", self.ip, "--load-all", "--log-level", self._log_level
        ]
        if self._enable_perf_monitor:
            cmd.append("--perf")
        
        # 使用进程组 (Process Group) 启动，确保能杀死所有子进程
        if os.name == 'posix':
            self.proc = subprocess.Popen(cmd, preexec_fn=os.setpgrp)
        else:
            self.proc = subprocess.Popen(cmd)
        
        print(f"{self.prefix} Agent process started with PID: {self.proc.pid}")

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