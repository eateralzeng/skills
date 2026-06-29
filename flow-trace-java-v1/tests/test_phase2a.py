"""phase2a 单元测试（ISSUE-2a-30）。

三层覆盖：
  1. 纯函数（build_node_id / _node_layer / _normalize_calls_order / _create_node_from_call /
     _backfill_di_via_lookup / _resolve_rmb_topic_constants / _classify_zero_call / _edges_to_calls 等）
  2. TreeGraph 操作（_remove_subtree_with_refcount 引用计数）
  3. mode 集成（do_merge 调用链不丢「决策 2 核心」+ do_reconcile_apply 幂等「ISSUE-2a-36」）

运行：cd <skill_dir> && python3 -m pytest tests/test_phase2a.py -v
"""
import sys, os, json
from types import SimpleNamespace

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL, 'scripts'))

from phase2a_tree_expand import (  # noqa: E402
    build_node_id, _full_class_from_node_id, _package_from_node_id,
    _node_layer, _normalize_calls_order, _create_node_from_call,
    _backfill_di_via_lookup, _resolve_rmb_topic_constants,
    _classify_zero_call, _edges_to_calls, _remove_subtree_with_refcount,
    _load_json, _load_subagent_results,
    do_merge, do_reconcile_apply,
)
from _tree_graph import TreeGraph  # noqa: E402


def _save(p, d):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def _load(p):
    with open(p) as f:
        return json.load(f)


def _tg(nodes, edges, root="r"):
    """快速构造 TreeGraph。"""
    return TreeGraph({"entryId": "t", "rootNodeId": root,
                      "nodes": {k: (v if isinstance(v, dict) else {"nodeId": k}) for k, v in nodes.items()},
                      "edges": edges})


# ════════════ 第一层：纯函数 ════════════

def test_build_node_id_standard():
    assert build_node_id("m/src/main/java/com/a/Svc.java", "handle") == "m:com.a.Svc:handle"


def test_build_node_id_no_filepath_fallback():
    assert build_node_id("", "work", "com.X") == "com.X:work"


def test_build_node_id_nothing():
    assert build_node_id("", "work") == "unknown:work"


def test_full_class_from_node_id():
    assert _full_class_from_node_id("m:com.a.Svc:handle") == "com.a.Svc"


def test_package_from_node_id():
    assert _package_from_node_id("m:com.a.Svc:handle") == "com.a"
    assert _package_from_node_id("m:X:work") == ""


def test_node_layer_root_zero():
    assert _node_layer(_tg({"r": {}}, []), "r") == 0


def test_node_layer_multi_parents_min():
    tg = _tg({"r": {}, "a": {}, "b": {}, "x": {}},
             [{"from": "a", "to": "x", "layer": 3}, {"from": "b", "to": "x", "layer": 5}])
    assert _node_layer(tg, "x") == 3  # min(3,5)


def test_normalize_calls_order_sorts():
    calls = [{"sourceLine": 30}, {"sourceLine": 10}, {"sourceLine": 20}]
    assert [c["sourceLine"] for c in _normalize_calls_order(calls, "")] == [10, 20, 30]


def test_normalize_calls_order_empty():
    assert _normalize_calls_order([], "") == []


def test_normalize_calls_order_no_sourceline_keeps_order():
    calls = [{"targetMethod": "a"}, {"targetMethod": "b"}]
    assert _normalize_calls_order(calls, "") == calls


def test_normalize_calls_order_out_of_range_no_crash():
    out = _normalize_calls_order([{"sourceLine": 999}], "l1\nl2")
    assert len(out) == 1  # 越界告警但不崩


def test_create_node_from_call_standard():
    call = {"targetClass": "X", "targetMethod": "work",
            "targetFilePath": "m/src/main/java/X.java", "isEndpoint": True}
    existing = set()
    node = _create_node_from_call(call, existing)
    assert node["method"] == "work" and node["terminal"] is True
    assert node["shortId"].startswith("n_") and node["shortId"] in existing


def test_create_node_from_call_dispatch():
    call = {"targetClass": "Strategy", "targetMethod": "doWork",
            "targetFilePath": "m/Strategy.java", "isEndpoint": True,
            "endpointType": "DISPATCH", "patternRef": "com.Strategy"}
    node = _create_node_from_call(call, set())
    assert node["patternRef"] == "com.Strategy" and node["endpointType"] == "DISPATCH"


def test_backfill_di_fills_terminal():
    tg = _tg({"r": {}, "m": {"class": "com.OrderMapper", "method": "insert", "terminal": True}}, [])
    filled = _backfill_di_via_lookup(tg, {"OrderMapper.insert": {"table": "t_order", "operation": "INSERT"}})
    assert filled == 1
    assert tg.nodes["m"]["domainInteraction"] == {"type": "DATABASE", "operation": "INSERT", "table": "t_order"}


def test_backfill_di_skips_non_terminal():
    tg = _tg({"r": {}, "m": {"class": "com.OrderMapper", "method": "insert", "terminal": False}}, [])
    assert _backfill_di_via_lookup(tg, {"OrderMapper.insert": {"table": "t", "operation": "INSERT"}}) == 0


def test_resolve_rmb_topic_constant_ref():
    tg = _tg({"r": {}, "s": {"domainInteraction": {
        "type": "EXTERNAL", "protocol": "RMB",
        "routingKeys": {"topic": "CbrcApi.SZ_TOPIC"}, "target": "CbrcApi.SZ_TOPIC"}}}, [])
    resolved = _resolve_rmb_topic_constants(tg, {"SZ_TOPIC": "sz-court-req"})
    assert resolved == 1
    assert tg.nodes["s"]["domainInteraction"]["routingKeys"]["topic"] == "sz-court-req"


def test_resolve_rmb_topic_literal_not_resolved():
    tg = _tg({"r": {}, "s": {"domainInteraction": {
        "type": "EXTERNAL", "protocol": "RMB", "routingKeys": {"topic": "sz-court-req"}}}}, [])
    assert _resolve_rmb_topic_constants(tg, {"SZ_TOPIC": "sz-court-req"}) == 0


def test_classify_zero_call():
    assert _classify_zero_call("processOrder") == "MEDIUM"
    assert _classify_zero_call("buildDto") == "LOW"
    assert _classify_zero_call("zzz") == "SKIP"


def test_edges_to_calls():
    tg = _tg({"r": {}, "x": {"class": "X", "method": "work", "filePath": "f",
                             "package": "p", "terminal": True}},
             [{"from": "r", "to": "x", "callType": "DIRECT", "condition": "if(x)", "sourceLine": 5}])
    calls = _edges_to_calls(tg.children("r"), tg)
    assert len(calls) == 1 and calls[0]["targetClass"] == "X" and calls[0]["condition"] == "if(x)"


# ════════════ 第二层：TreeGraph 操作（引用计数删子树）════════════

def test_remove_subtree_shared_keeps_node():
    """X 被 A、B 共享，删 A 子树 → X 仍有 B→X → 保留（DAG 单实例核心）。"""
    tg = _tg({"r": {}, "a": {}, "b": {}, "x": {}},
             [{"from": "a", "to": "x", "layer": 1}, {"from": "b", "to": "x", "layer": 1}])
    _remove_subtree_with_refcount(tg, "a")
    assert "x" in tg.nodes
    assert not tg.has_edge("a", "x") and tg.has_edge("b", "x")


def test_remove_subtree_unshared_deletes_node():
    tg = _tg({"r": {}, "a": {}, "x": {}}, [{"from": "a", "to": "x", "layer": 1}])
    _remove_subtree_with_refcount(tg, "a")
    assert "x" not in tg.nodes


# ════════════ 第三层：mode 集成 ════════════

def test_do_merge_preserves_multi_parent_edge(tmp_path):
    """决策 2 核心：parent_A、parent_B 都调用 X → X 单实例 + A→X/B→X 两条 edge 都在（调用链不丢）。"""
    cache = tmp_path / "cache"
    tree = {
        "entryId": "test-001", "rootNodeId": "m:R:handle", "version": "2.0",
        "nodes": {
            "m:R:handle": {"nodeId": "m:R:handle", "class": "R", "method": "handle", "filePath": "m/r.java", "terminal": False, "shortId": "n_r"},
            "m:A:do": {"nodeId": "m:A:do", "class": "A", "method": "do", "filePath": "m/a.java", "terminal": False, "shortId": "n_a"},
            "m:B:do": {"nodeId": "m:B:do", "class": "B", "method": "do", "filePath": "m/b.java", "terminal": False, "shortId": "n_b"},
        },
        "edges": [
            {"id": "e0", "from": "m:R:handle", "to": "m:A:do", "layer": 1, "callType": "DIRECT", "condition": "始终执行", "sourceLine": 1, "truncated": False},
            {"id": "e1", "from": "m:R:handle", "to": "m:B:do", "layer": 1, "callType": "DIRECT", "condition": "始终执行", "sourceLine": 2, "truncated": False},
        ],
    }
    progress = {"entryId": "test-001", "maxDepth": 20, "maxNodes": 500, "maxFanout": 50,
                "pendingNodes": ["m:A:do", "m:B:do"], "expandedNodes": ["m:R:handle"], "totalNodes": 3, "totalEdges": 2}
    x_call = {"targetClass": "X", "targetMethod": "work", "targetFilePath": "m/src/main/java/X.java",
              "callType": "DIRECT", "condition": "始终执行", "sourceLine": 1, "isEndpoint": True}
    results = {"results": [{"nodeId": "m:A:do", "calls": [x_call]}, {"nodeId": "m:B:do", "calls": [x_call]}]}
    _save(str(cache / "phase2a/test-001-tree.json"), tree)
    _save(str(cache / "phase2a/test-001-progress.json"), progress)
    _save(str(cache / "phase2a/tmp/_r.json"), results)

    do_merge(SimpleNamespace(cache_dir=str(cache), entry_id="test-001",
                             results=str(cache / "phase2a/tmp/_r.json"), project_dir=""))

    tg = TreeGraph(_load(str(cache / "phase2a/test-001-tree.json")))
    x_id = build_node_id("m/src/main/java/X.java", "work", "X")
    assert x_id in tg.nodes                          # X 单实例
    assert tg.has_edge("m:A:do", x_id)               # A→X
    assert tg.has_edge("m:B:do", x_id)               # B→X（决策 2 核心：调用链不丢）


def test_do_reconcile_apply_idempotent_multi_parent(tmp_path, capsys):
    """ISSUE-2a-36：同 nodeId 多 parent 场景，reconcile-apply 幂等（第二次不翻倍）。"""
    cache = tmp_path / "cache"
    x_id = build_node_id("m/src/main/java/X.java", "work", "X")
    tree = {
        "entryId": "test", "rootNodeId": "m:C:handle", "version": "2.0",
        "nodes": {
            "m:C:handle": {"nodeId": "m:C:handle", "class": "C", "method": "handle", "filePath": "m/c.java", "terminal": False, "shortId": "n_c"},
            "m:B:do": {"nodeId": "m:B:do", "class": "B", "method": "do", "filePath": "m/b.java", "terminal": False, "shortId": "n_b"},
            x_id: {"nodeId": x_id, "class": "X", "method": "work", "filePath": "m/x.java", "terminal": True, "shortId": "n_x"},
        },
        "edges": [
            {"id": "e0", "from": "m:C:handle", "to": "m:B:do", "layer": 1, "callType": "DIRECT", "condition": "始终执行", "sourceLine": 1, "truncated": False},
            # 注：B→X 缺失（reconcile 要修复），X 已作为某隐式 parent 的子存在
        ],
    }
    progress = {"entryId": "test", "maxDepth": 20, "maxNodes": 500, "maxFanout": 50,
                "pendingNodes": [], "expandedNodes": ["m:C:handle", "m:B:do", x_id], "totalNodes": 3, "totalEdges": 1}
    _save(str(cache / "phase2a/test-tree.json"), tree)
    _save(str(cache / "phase2a/test-progress.json"), progress)
    _save(str(cache / "phase2a/tmp/_reconcile-report.json"),
          {"inconsistencies": [{"nodeId": "m:B:do", "bestEntry": None}], "zeroCallSuspicious": []})
    _save(str(cache / "phase2a/tmp/_reconcile-result-0.json"),
          {"results": [{"nodeId": "m:B:do", "calls": [
              {"targetClass": "X", "targetMethod": "work", "targetFilePath": "m/src/main/java/X.java",
               "callType": "DIRECT", "condition": "始终执行", "sourceLine": 1}]}]})

    args = SimpleNamespace(cache_dir=str(cache), entry_id="test", report=None)
    do_reconcile_apply(args)
    tg1 = TreeGraph(_load(str(cache / "phase2a/test-tree.json")))
    edges_after_1 = len(tg1.edges)
    assert tg1.has_edge("m:B:do", x_id)  # 第一次：B→X 建

    capsys.readouterr()
    do_reconcile_apply(args)  # 第二次
    tg2 = TreeGraph(_load(str(cache / "phase2a/test-tree.json")))
    assert len(tg2.edges) == edges_after_1  # 幂等：边数不翻倍（ISSUE-2a-36）


# ════════════ 补充：异常输入容错（ISSUE-2a-32 / 2a-34）═════════════

def test_load_json_corrupt_raises_with_path(tmp_path, capsys):
    """ISSUE-2a-32: 损坏 JSON 抛 JSONDecodeError + stderr 含文件路径（定位友好）。"""
    import pytest as _pytest
    p = tmp_path / "bad.json"
    p.write_text("{invalid json")
    with _pytest.raises(json.JSONDecodeError):
        _load_json(str(p))
    assert str(p) in capsys.readouterr().err


def test_load_subagent_results_standard_dict(tmp_path):
    """ISSUE-2a-34: 标准 {results: [...]}"""
    p = tmp_path / "r.json"
    p.write_text('{"results": [{"nodeId": "x"}]}')
    assert _load_subagent_results(str(p)) == [{"nodeId": "x"}]


def test_load_subagent_results_bare_list(tmp_path):
    """ISSUE-2a-34: 裸数组 [...] 兼容（LLM 偶发不包 results）"""
    p = tmp_path / "bare.json"
    p.write_text('[{"nodeId": "x"}]')
    assert _load_subagent_results(str(p)) == [{"nodeId": "x"}]


def test_load_subagent_results_anomaly_returns_empty(tmp_path, capsys):
    """ISSUE-2a-34: 异常格式（字符串）→ 空 results + 告警，不崩溃"""
    p = tmp_path / "str.json"
    p.write_text('"not an object"')
    assert _load_subagent_results(str(p)) == []
    assert "格式异常" in capsys.readouterr().err
