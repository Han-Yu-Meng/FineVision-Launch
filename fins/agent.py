import subprocess
import os
import time
import requests
import sys
import socket

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

    def add_config(self, config_path: str):
        """添加一个 YAML 配置文件路径"""
        if os.path.exists(config_path):
            self.config_files.append(config_path)
            print(f"{self.prefix} Added config: {config_path}")
        else:
            print(f"{self.prefix} Error: Config file not found: {config_path}")
            sys.exit(1)

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

    def _apply_parameters(self, config_path: str):
        """上传单个 YAML 文件内容"""
        try:
            with open(config_path, 'r') as f:
                yaml_content = f.read()
            
            url = f"http://127.0.0.1:{self.port}/apply_parameters"
            payload = {"content": yaml_content}
            r = requests.post(url, json=payload, timeout=2)
            r.raise_for_status()
            print(f"{self.prefix} Applied config: {os.path.basename(config_path)}")
        except Exception as e:
            print(f"{self.prefix} Error: Failed to apply {config_path}: {e}")
            sys.exit(1)

    def _check_port_available(self, ip, port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((ip, port))
                return True
            except OSError:
                return False

    def launch(self, ld: 'LaunchDescription'):
        """启动 Agent 并依次推送配置和数据流"""
        if not os.path.exists(self.bin):
            print(f"{self.prefix} Error: Agent executable not found at {self.bin}")
            sys.exit(1)

        if not self._check_port_available(self.ip, self.port):
            print(f"{self.prefix} Warning: Port {self.port} is already in use. Please choose a different port or terminate the process using it.")
            sys.exit(1)

        # 1. 启动 Agent 进程
        print(f"{self.prefix} Starting FINS Agent [{self.name}] on port {self.port}...")
        cmd = [
            self.bin, "--name", self.name, "--port", str(self.port),
            "--ip", self.ip, "--load-all", "--log-level", self._log_level
        ]
        if self._enable_perf_monitor:
            cmd.append("--perf")
        self.proc = subprocess.Popen(cmd)

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
            if self.proc.poll() is not None:
                print(f"{self.prefix} Error: Agent process exited prematurely.")
                sys.exit(1)
        else:
            print(f"{self.prefix} Error: Timeout waiting for Agent.")
            self.stop()
            sys.exit(1)

        # 3. 依次应用所有添加的配置文件
        if self.config_files:
            print(f"{self.prefix} Applying {len(self.config_files)} configurations...")
            for cfg in self.config_files:
                self._apply_parameters(cfg)

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
                if self.proc and self.proc.poll() is not None:
                    break
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self):
        if self.proc:
            print(f"\n{self.prefix} Stopping Agent [{self.name}]...")
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
            self._is_running = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()