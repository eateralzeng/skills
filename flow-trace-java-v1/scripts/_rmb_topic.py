"""共享：RMB `@RmbTopic` 提取 + 常量解析（单一事实）。

phase1a_entry_scan（接收端 rmbTopic）+ phase2a rmb-topic-backfill（发送端 routingKeys.topic）
共用本模块，保证两端用同一套正则 + 常量解析口径，避免漂移（PRINCIPLES 配置-逻辑分离 + 单点事实）。
"""
import re

RMBTOPIC_RE = re.compile(r'@RmbTopic\s*\(([^)]+)\)')
METHOD_RE = re.compile(r'(?:public\s+)?\S+\s+(\w+)\s*\(')


def resolve_const(expr, constants):
    """常量引用解析：取表达式末段短名查 constants 表。未命中按原样标注。"""
    name = expr.strip().split('.')[-1]
    return constants.get(name, f"[常量未解析:{expr}]")


def parse_rmbtopic_params(params, constants):
    """解析 @RmbTopic(...) 括号内参数 → {topic, topicMode, transCode}（topic 已 resolve_const）。"""
    topic_lit = re.search(r'topic\s*=\s*"([^"]*)"', params)
    topic_ref = re.search(r'topic\s*=\s*([A-Za-z_][\w.]*)', params)
    if topic_lit:
        topic = topic_lit.group(1)
    elif topic_ref:
        topic = resolve_const(topic_ref.group(1), constants)
    else:
        topic = ''
    mode_m = re.search(r'topicMode\s*=\s*(?:TopicMode\.)?(\w+)', params)
    topic_mode = mode_m.group(1) if mode_m else ''
    trans_m = re.search(r'transCode\s*=\s*([A-Za-z_][\w.]*)', params)
    trans_code = resolve_const(trans_m.group(1), constants) if trans_m else None
    return {"topic": topic, "topicMode": topic_mode, "transCode": trans_code}


def extract_rmb_topic_by_method(content, constants):
    """从源码内容提取 {method_name: {topic, topicMode, transCode}}。

    每个 `@RmbTopic` 关联其后 500 字符内的方法声明名（与 phase1a 接收端同款关联逻辑）。
    发送端（RMB client 接口，一个方法一个 @RmbTopic）据 node.method 查本表取 topic。
    """
    result = {}
    for m in RMBTOPIC_RE.finditer(content):
        parsed = parse_rmbtopic_params(m.group(1), constants)
        rest = content[m.end():m.end() + 500]
        mm = METHOD_RE.search(rest)
        method_name = mm.group(1) if mm else None
        if method_name and method_name not in result:
            result[method_name] = parsed
    return result
