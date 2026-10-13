"""Five explicit fixtures for offline teaching; these are not an intent model."""


def pre_launch(mode: str, **parameters) -> dict:
    return {"action": "create_task", "task": {
        "task_type": "放飞前出任务", "parameters": {"mode": mode, **parameters},
    }}


def formation(kind: str) -> dict:
    return {"action": "create_task", "task": {
        "task_type": "编队飞行任务", "parameters": {"formation_type": kind},
    }}


EXAMPLE = "新增放飞前出任务，模式为原地起飞"
MOCK_CASES = {
    EXAMPLE: pre_launch("原地起飞"),
    "新增放飞前出任务，在指定位置展开，位置为东经120.1度、北纬30.2度":
        pre_launch("在指定位置展开", position={"longitude": 120.1, "latitude": 30.2}),
    "新增放飞前出任务，在指定区域展开，区域顶点为120.1,30.2；120.2,30.2；120.2,30.3":
        pre_launch("在指定区域展开", area={"vertices": [
            {"longitude": 120.1, "latitude": 30.2},
            {"longitude": 120.2, "latitude": 30.2},
            {"longitude": 120.2, "latitude": 30.3},
        ]}),
    "新增C型编队飞行任务": formation("C型"),
    "新增U型编队飞行任务": formation("U型"),
}
