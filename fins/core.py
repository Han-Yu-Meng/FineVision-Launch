import json
import uuid
import yaml
from typing import Dict, List, Any, Optional
from contextlib import contextmanager

_CURRENT_DEFAULT_SOURCE = "common"


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

@contextmanager
def DefaultSource(source_name: str):
    """
    上下文管理器，用于简化 Node 的编写。
    在此作用域下创建的 Node，如果不指定 source，则自动使用该值。
    """
    global _CURRENT_DEFAULT_SOURCE
    old_source = _CURRENT_DEFAULT_SOURCE
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