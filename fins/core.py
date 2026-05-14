import json
import uuid
from typing import Dict, List, Any, Optional
from contextlib import contextmanager

_CURRENT_DEFAULT_SOURCE = "common"

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
                 remappings: Dict[str, str] = None):
        """
        :param package: 对应 C++ 的 package_name
        :param name: 对应 C++ 的类名
        :param source: 插件源，如果不填则使用 DefaultSource 上下文中的值
        """
        # 如果未指定 source，则使用上下文中的默认值
        self.source = source if source is not None else _CURRENT_DEFAULT_SOURCE
        self.package = package
        self.name = name
        self.version = version
        self.parameters = parameters or {}
        self.remappings = remappings or {}
        
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

        # 建立全局 Topic 路由表
        topic_bus: Dict[str, str] = {}
        for node in all_nodes:
            for port, topic in node.remappings.items():
                if topic not in topic_bus:
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
                "inputs": {}
            }

            for port, topic in node.remappings.items():
                if topic in topic_bus and topic_bus[topic] != f"{node.id}/{port}":
                    node_cfg["inputs"][port] = {
                        "connect": topic_bus[topic]
                    }
            
            fins_nodes.append(node_cfg)

        return json.dumps({"nodes": fins_nodes}, indent=2)