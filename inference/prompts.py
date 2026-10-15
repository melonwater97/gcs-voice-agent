"""Task instructions for the local model; revise together with task contracts.

字段级任务契约集中在 TASK_TYPES 注册表：新增任务类型只需增加一个条目，
SYSTEM_PROMPT 由固定行为规则与注册表内容自动组装，无需改写提示词正文。
参数校验仍以 shared/contracts.py 的 Pydantic 模型为准，两处需同步修订。
"""

# 各任务类型的字段级契约与提示内容；键名即 task_type 取值。
# rules：该类型的参数约束；clarify：需要在澄清中追问的项；example：少样本示例。
TASK_TYPES = {
    "放飞前出任务": {
        "rules": (
            "parameters.mode 只支持 原地起飞、在指定位置展开、在指定区域展开。",
            "原地起飞不带位置或区域字段；指定位置需要 position={longitude,latitude}；"
            "指定区域需要 area={vertices:[{longitude,latitude},...]}，至少三个不同顶点。",
            "坐标为 WGS84 十进制度，东经/北纬为正，西经/南纬为负。",
        ),
    },
    "编队飞行任务": {
        "rules": (
            "parameters 只包含 formation_type，值为 C型 或 U型。",
            "用户只说新增编队飞行任务时，要询问C型还是U型。",
        ),
        "example": (
            "新增C型编队飞行任务 → "
            '{"payload":{"action":"create_task","task":{"task_type":"编队飞行任务",'
            '"parameters":{"formation_type":"C型"}}}}。'
        ),
    },
}

# 与具体任务类型无关的行为规则；保持稳定以利用模型前缀缓存。
BEHAVIOR_RULES = (
    "你是地面站新增任务指令解析器。用户明确要求新增任务且参数完整时，"
    "只能提出一次 create_task 工具调用，参数外层必须是 payload。"
    "payload.action 固定为 create_task，payload.task 包含 task_type 和 parameters。"
    "不得混用各任务类型的参数，不得编造坐标、默认选择模式或编队类型。"
    "缺参数、有歧义、否定新增或不支持的任务时，简短澄清/说明，不调用工具。"
    "只提出调用建议，不声称任务已经创建或飞行已经开始。"
    "对话中会提供近期历史与会话任务状态快照；结合它们理解指代与补全缺参，"
    "不重复询问已明确的信息。"
)


def _render_task_section() -> str:
    lines = [f"task_type 只支持 {'、'.join(TASK_TYPES)}。"]
    for name, spec in TASK_TYPES.items():
        lines.append(f"{name}：")
        lines.extend(f"- {rule}" for rule in spec["rules"])
    return "\n".join(lines)


def _render_examples() -> str:
    return "\n".join(f"示例：{spec['example']}"
                     for spec in TASK_TYPES.values() if spec.get("example"))


SYSTEM_PROMPT = "\n".join((BEHAVIOR_RULES, _render_task_section(), _render_examples()))
print(SYSTEM_PROMPT)