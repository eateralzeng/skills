"""TreeGraph: 共享调用图抽象层 + shortId 生成器。

为 phase2a（调用树展开）及下游 phase3/4/5/6 提供统一的调用图访问接口。

持久化格式：tree.json 中 edges 为扁平 list（人类友好、JSON 简洁、去重直观）。
内存访问：加载 tree.json 时构建 _outgoing / _incoming 双向索引，O(1) 查找。

设计来源（见 phase2a-design.md）：
  - 决策 6：gen_short_id（SHA256 前缀 + 冲突延长）
  - 决策 9：TreeGraph（list 持久化 + 内存索引）
"""
import hashlib


def gen_short_id(node_id: str, existing_ids: set, min_length: int = 8) -> str:
    """生成稳定的 short ID（SHA256 哈希前缀，冲突时自动延长）。

    哈希基于 nodeId，同一 nodeId 在所有 entry 中 shortId 相同（跨 entry 一致性）。
    冲突时逐步延长前缀长度（8 → 9 → ...）直至唯一。

    Args:
        node_id: 节点全限定 nodeId（module:Class:method）
        existing_ids: 已存在的 shortId 集合，用于冲突检测
        min_length: 哈希前缀最小长度，默认 8

    Returns:
        形如 "n_<hex>" 的 shortId
    """
    length = min_length
    while True:
        h = hashlib.sha256(node_id.encode()).hexdigest()[:length]
        sid = f"n_{h}"
        if sid not in existing_ids:
            return sid
        length += 1


class TreeGraph:
    """加载 tree.json 后封装的调用图结构，提供高效访问接口。

    持久化时 edges 仍为 list（简洁），加载时构建索引（O(E)）。
    下游 phase 统一通过本接口访问，不直接操作 edges list（决策 9 下游消费者约定）。
    """

    def __init__(self, tree_data: dict):
        self.entry_id = tree_data["entryId"]
        self.root_node_id = tree_data["rootNodeId"]
        self.nodes = tree_data["nodes"]          # dict[nodeId, node]
        self.edges = tree_data["edges"]          # list of edge dicts
        self._outgoing = {}                      # from -> [edge]（出边索引，保序）
        self._incoming = {}                      # to -> [edge]（入边索引，保序）
        self._build_indexes()

    def _build_indexes(self):
        """遍历 edges 构建双向索引。

        setdefault + append 天然保序 —— edges list 的出现顺序即调用顺序，
        phase6 DFS 渲染依赖此顺序（决策 9 / 决策 13 calls 保序）。
        """
        for edge in self.edges:
            self._outgoing.setdefault(edge["from"], []).append(edge)
            self._incoming.setdefault(edge["to"], []).append(edge)

    def children(self, node_id: str) -> list:
        """node 的所有出边（被 node 调用的方法）。O(1)。"""
        return self._outgoing.get(node_id, [])

    def parents(self, node_id: str) -> list:
        """node 的所有入边（调用 node 的方法）。O(1)。

        DAG 下一个 node 可能有多条入边（多 parent 调用）——
        phase3 反向 BFS 从此处取所有入边回溯到 root。
        """
        return self._incoming.get(node_id, [])

    def add_edge(self, edge: dict) -> bool:
        """添加 edge，按 (from, to) 去重。返回是否实际添加。

        去重规则是修复"调用链丢失"的核心（决策 2）：node 按 nodeId 单实例去重，
        但 parent_2 → A 的调用 edge 必须照常创建 —— 仅当 (parent_2, A) 尚不存在时添加。
        """
        for existing in self._outgoing.get(edge["from"], []):
            if existing["to"] == edge["to"]:
                return False  # (from, to) 已存在，不重复添加
        self.edges.append(edge)
        self._outgoing.setdefault(edge["from"], []).append(edge)
        self._incoming.setdefault(edge["to"], []).append(edge)
        return True

    def get_node(self, node_id: str) -> dict:
        """取节点静态信息（class / method / terminal / domainInteraction 等）。"""
        return self.nodes.get(node_id)

    def has_edge(self, from_id: str, to_id: str) -> bool:
        """判断 (from_id, to_id) 边是否存在。"""
        return any(e["to"] == to_id for e in self._outgoing.get(from_id, []))
