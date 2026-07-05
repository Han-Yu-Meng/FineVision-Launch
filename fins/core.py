import json
import uuid
import yaml
import inspect
import os
from typing import Dict, List, Any, Optional
from contextlib import contextmanager

_CURRENT_DEFAULT_SOURCE = "common"

# Directory of the fins package itself, used to skip library frames
# during stack inspection (instead of a fragile '/fins/' substring check
# that also matches /home/fins/ paths).
_FINS_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def override_yaml(yaml_content: str, overrides: Dict[str, Any]) -> str:
    """
    Apply dot-notation overrides to a YAML string and return the modified YAML.

    Nested keys are navigated via ``.`` separator; intermediate dicts are
    auto-created when a path segment does not already exist.

    :param yaml_content: raw YAML string
    :param overrides:    dict mapping dot-notation paths to new values,
                         e.g. ``{"camera.width": 1920, "camera.height": 1080}``
    :return:             modified YAML string
    """
    config = yaml.safe_load(yaml_content) or {}
    for path, value in overrides.items():
        keys = path.split(".")
        target = config
        for key in keys[:-1]:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
            target = target[key]
        target[keys[-1]] = value
    return yaml.dump(config, default_flow_style=False, allow_unicode=True)


def _load_workspace_config():
    """Load local_packages from ~/.fins/config.yaml, sorted by path length descending."""
    config_path = os.path.expanduser('~/.fins/config.yaml')
    if not os.path.exists(config_path):
        return []
    with open(config_path, 'r') as f:
        cfg = yaml.safe_load(f)
    packages = cfg.get('local_packages', []) if cfg else []
    return sorted(packages, key=lambda x: len(x.get('path', '')), reverse=True)


def get_workspace_paths() -> List[str]:
    """
    Return all registered workspace root paths from ~/.fins/config.yaml.

    Used by Agent.launch() to inject --workspace arguments.
    """
    pkgs = _load_workspace_config()
    return [pkg['path'] for pkg in pkgs if 'path' in pkg]


def get_current_workspace_path() -> str:
    """
    Auto-detect the workspace path from the calling script's location.
    Only returns the single workspace that contains the user's launch script,
    NOT all registered workspaces.

    Falls back to ~/.fins/install/ if no workspace matches.
    """
    pkgs = _load_workspace_config()
    if not pkgs:
        return os.path.expanduser('~/.fins/install')

    for frame_info in inspect.stack():
        frame_file = frame_info.filename
        if 'site-packages' in frame_file:
            continue
        abs_frame = os.path.abspath(frame_file)
        if abs_frame.startswith(_FINS_PACKAGE_DIR):
            continue
        script_dir = os.path.dirname(abs_frame)
        for pkg in pkgs:
            pkg_path = os.path.abspath(os.path.expanduser(pkg.get('path', '')))
            if script_dir.startswith(pkg_path):
                return pkg_path

    return os.path.expanduser('~/.fins/install')


def _detect_workspace_name() -> str:
    """
    Auto-detect the workspace name by walking up the call stack to find
    the user's launch script, then matching its path against registered
    workspaces in ~/.fins/config.yaml.
    """
    pkgs = _load_workspace_config()
    if not pkgs:
        return 'colcon_ws'

    for frame_info in inspect.stack():
        frame_file = frame_info.filename
        # Skip files inside the fins launch package itself
        if 'site-packages' in frame_file:
            continue
        abs_frame = os.path.abspath(frame_file)
        if abs_frame.startswith(_FINS_PACKAGE_DIR):
            continue
        script_dir = os.path.dirname(abs_frame)
        for pkg in pkgs:
            pkg_path = os.path.abspath(os.path.expanduser(pkg.get('path', '')))
            if script_dir.startswith(pkg_path):
                return pkg.get('name', 'colcon_ws')

    return 'colcon_ws'


@contextmanager
def DefaultSource(source_name: str = None):
    """
    上下文管理器，用于简化 Node 的编写。
    在此作用域下创建的 Node，如果不指定 source，则自动使用该值。

    如果不传参数，则自动从调用脚本的路径推导所在的工作区名称。

    :param source_name: 工作区名称；为 None 时自动检测
    """
    global _CURRENT_DEFAULT_SOURCE
    old_source = _CURRENT_DEFAULT_SOURCE
    if source_name is None:
        source_name = _detect_workspace_name()
    _CURRENT_DEFAULT_SOURCE = source_name
    try:
        yield
    finally:
        _CURRENT_DEFAULT_SOURCE = old_source

class Node:
    def __init__(self, 
                 package: str, 
                 name: str, 
                 source: str = None, 
                 version: str = "default",
                 parameters: Dict[str, Any] = None,
                 inputs: Dict[str, str] = None,
                 outputs: Dict[str, str] = None,
                 servers: Dict[str, str] = None,
                 clients: Dict[str, str] = None):
        """
        :param package: 对应 C++ 的 package_name
        :param name: 对应 C++ 的类名
        :param source: 插件源，如果不填则使用 DefaultSource 上下文中的值
        :param inputs: 输入端口映射 { "port_name": "topic_name" }
        :param outputs: 输出端口映射 { "port_name": "topic_name" }
        :param servers: 服务端映射 { "server_name": "service_name" }
        :param clients: 客户端映射 { "client_name": "service_name" }
        """
        # 如果未指定 source，则使用上下文中的默认值
        self.source = source if source is not None else _CURRENT_DEFAULT_SOURCE
        self.package = package
        self.name = name
        self.version = version
        self.parameters = parameters or {}
        self.inputs = inputs or {}
        self.outputs = outputs or {}
        self.servers = servers or {}
        self.clients = clients or {}
        
        # 生成唯一 ID
        unique_suffix = str(uuid.uuid4())[:4]
        self.id = f"{self.name}_{unique_suffix}"

class Group:
    def __init__(self, nodes: List[Node] = None):
        self.nodes = nodes or []

    def add_node(self, node: Node):
        self.nodes.append(node)

class LaunchDescription:
    def __init__(self, groups: List[Group] = None):
        self.groups = groups or []

    def to_fins_json(self) -> str:
        all_nodes: List[Node] = []
        for g in self.groups:
            all_nodes.extend(g.nodes)

        topic_bus: Dict[str, str] = {}
        for node in all_nodes:
            for port, topic in node.outputs.items():
                if topic in topic_bus:
                    pass
                topic_bus[topic] = f"{node.id}/{port}"

        fins_nodes = []
        for node in all_nodes:
            node_cfg = {
                "id": node.id,
                "name": node.name,
                "package_name": node.package,
                "source": node.source,
                "version": node.version,
                "parameters": [
                    {"name": k, "value": v} for k, v in node.parameters.items()
                ],
                "inputs": {},
                "servers": [
                    {"name": k, "topic": v} for k, v in node.servers.items()
                ],
                "clients": [
                    {"name": k, "topic": v} for k, v in node.clients.items()
                ]
            }

            for port, topic in node.inputs.items():
                if topic in topic_bus:
                    node_cfg["inputs"][port] = {
                        "connect": topic_bus[topic]
                    }
                else:
                    pass
            
            fins_nodes.append(node_cfg)

        return json.dumps({"nodes": fins_nodes}, indent=2)