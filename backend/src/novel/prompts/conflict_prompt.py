# -*- coding: utf-8 -*-
"""
conflict 抽取专属 Prompt + JSON Schema（子任务 05）。

职责：抽冲突核心——conflict_title/type/side_a/side_b/start/end_chapter/current_status。
side_a/side_b 用实体或势力名（persist 期保留原名或解析，story_conflict 无 FK）。
"""
from pydantic import BaseModel, Field

from novel.prompts.base import BASE_SYSTEM_PROMPT

_CONFLICT_TYPES = ("对抗", "资源争夺", "价值观冲突", "欲望冲突")
_CONFLICT_STATUS = ("升级", "胶着", "解决")

# ========== 3-shot 跨题材极简示例（2正1错，全部自包含） ==========
# 原则：
#   - 每个示例的【正确输出】全部可由【输入文本】直接推出，不依赖示例外的原著上下文；
#   - 两个正例分别取材《凡人修仙传·第一章》（玄幻古代修仙）与《超神机械师·001 初生》（科幻现代超能）；
#   - 一个反例以「错误→正确」极简格式集中展示全部常见冲突抽取错误（日常当冲突/类型误判/编造/代词/状态误判/单事件当冲突/重复）。
_CONFLICT_FEW_SHOT = """\
【示例 1（正例·玄幻古代修仙）】
【输入文本】
韩父听到三叔说，参加七玄门考核每月能有一两银子拿、还能成为体面人，心里很想去；可他又不舍得十岁的韩立小小年纪离家远行，一时犹豫不决，最后咬咬牙答应了下来。
【正确输出】
{"conflicts": [
  {"conflict_title": "韩父对韩立赴考的取舍", "conflict_type": "欲望冲突", "conflict_desc": "既想韩立进七玄门得银两成体面人，又不舍孩子年幼离家", "side_a": "韩父", "side_b": "韩父", "start_chapter": null, "end_chapter": 0, "current_status": "解决"}
]}

【示例 2（正例·科幻现代超能）】
【输入文本】
韩萧撞开房门想要逃跑，十几个穿着黑色作战制服的警卫挥舞电棍围了上来，他被电击后押进了小黑屋。"试验体活过来了！"白大褂呼叫警卫马上控制试验体。
【正确输出】
{"conflicts": [
  {"conflict_title": "韩萧逃离与萌芽组织的追捕", "conflict_type": "对抗", "conflict_desc": "韩萧试图逃跑，萌芽组织的警卫将其电击制服并关押", "side_a": "韩萧", "side_b": "萌芽组织", "start_chapter": null, "end_chapter": null, "current_status": "升级"}
]}

【示例 3（反例·常见错误汇总，禁止照做）】
【输入文本】
韩父心里犹豫不决：既想让韩立去七玄门得那每月一两银子，又不舍得孩子离家。三叔则在一旁不停劝说。
【常见错误 → 正确做法（以下内容禁止照做）】
- 把劝说当对抗：生成"韩父 对抗 三叔" ✗ 三叔只是劝说，双方无对立，冲突在韩父内心
- 类型误判：把"韩父 vs 内心不舍"标成 资源争夺 ✗ 是利益与情感的目标取舍，应 欲望冲突
- 状态误判：把"犹豫不决"标成 升级 ✗ 冲突无激化，更接近 胶着
- 单事件当冲突："三叔来访劝韩父" ✗ 事件不是冲突，冲突须有对立双方
- 编造冲突："韩父与韩母的矛盾" ✗ 原文没提
- side 用代词："他" ✗ 归并到已明确的实体
- 重复输出：同一冲突多条 ✗ 每冲突只 1 条
【正确输出】
{"conflicts": [
  {"conflict_title": "韩父对韩立赴考的取舍", "conflict_type": "欲望冲突", "conflict_desc": "既想韩立得银两成体面人，又不舍孩子离家", "side_a": "韩父", "side_b": "韩父", "start_chapter": null, "end_chapter": null, "current_status": "胶着"}
]}
"""


class ConflictItem(BaseModel):
    """单条冲突。"""

    conflict_title: str = Field(min_length=1, description="冲突名")
    source_fragment: str = Field(default="", description="支撑本条抽取的原文片段（verbatim；可选，空则跳过锚定校验）")
    conflict_type: str = Field(default="对抗", description=f"冲突类型：{'/'.join(_CONFLICT_TYPES)}")
    conflict_desc: str = Field(default="", description="冲突描述")
    side_a: str = Field(default="", description="冲突方A（实体或势力名）")
    side_b: str = Field(default="", description="冲突方B（实体或势力名）")
    start_chapter: int | None = Field(default=None, ge=0, description="起始章节（由流水线决定，缺省=当前章）")
    end_chapter: int | None = Field(default=None, description="结束章节（未结束为 null）")
    current_status: str = Field(default="升级", description=f"当前状态：{'/'.join(_CONFLICT_STATUS)}")


class ConflictOutput(BaseModel):
    """conflict 抽取的整体输出契约。"""

    conflicts: list[ConflictItem] = Field(default_factory=list)


def build_prompt(scene_text: str, *, few_shot: bool = True) -> dict:
    """组装 conflict 抽取的 LLM 消息。

    :param scene_text: 章节场景原文（缩窗重试时传入截断后的前半文本）
    :param few_shot: 是否追加 3-shot 跨题材示例。默认开启（llm_runner 单参调用保持兼容）；
        对成本极度敏感时可首跑 few_shot=False、仅在缩窗重试时开启 few_shot=True。
    :return: {"system_prompt", "prompt"} 供 llm_runner / BaseLLMAdapter.invoke 使用
    """
    parts = [
        "任务：从以下文本中提取**冲突核心**。\n",
        "输出 JSON 结构：",
        '{"conflicts": [{"conflict_title": 冲突名, "conflict_type": 类型, "conflict_desc": 描述, '
        '"side_a": 冲突方A, "side_b": 冲突方B, "start_chapter": 起章, "end_chapter": 止章或null, '
        '"current_status": "升级"}]}\n',
        "要求：\n",
        "  1. conflict_type 选自 对抗/资源争夺/价值观冲突/欲望冲突；current_status 选自 升级/胶着/解决；\n",
        "  2. side_a/side_b 用文本中出现的实体或势力名；\n",
        "  3. 原文没提的字段填空串/null；只输出 JSON。\n\n",
        "抽取规则补充：\n",
        "冲突类型通用说明：\n",
        "- 对抗：武力、立场的直接对立\n",
        "- 资源争夺：对物品、权力、地盘、机会的争夺\n",
        "- 价值观冲突：理念、信仰、立场的分歧\n",
        "- 欲望冲突：情感、利益、目标的矛盾\n",
        "- 补充：现实题材可包含情感冲突、利益纠纷、观念对立，就近归类到对应类型\n\n",
        "冲突判定边界：\n",
        "1. 冲突须有对立双方（人或势力；内心冲突可 side_a=side_b=同一人）；单方事件、日常小摩擦不是冲突。\n",
        "2. current_status：升级=冲突激化，胶着=僵持无进展，解决=本段明确化解。\n",
        "3. end_chapter 仅在冲突明确结束时填写，未结束填 null。\n\n",
    ]
    if few_shot:
        parts.append("【Few-shot 示例】（输出格式参考；每个示例的【正确输出】均可直接由【输入文本】推出）：\n\n")
        parts.append(_CONFLICT_FEW_SHOT)
        parts.append("\n\n")
    parts.append(f"文本内容：\n{scene_text}")
    return {"system_prompt": BASE_SYSTEM_PROMPT, "prompt": "".join(parts)}
