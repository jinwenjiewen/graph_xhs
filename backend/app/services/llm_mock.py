"""用于本地工作流开发的确定性 LLM 模拟实现。"""

from __future__ import annotations


class MockLLMService:
    """用于替代生产 LLM 客户端的轻量确定性模拟服务。"""

    async def plan_topics(self, topic_direction: str) -> list[str]:
        """返回固定选题列表，便于稳定测试 API 行为。"""
        # 模拟服务刻意忽略输入；固定输出能使接口和工作流测试具备确定性。
        _ = topic_direction
        return ["LangGraph入门", "AI Agent实战", "Python高并发"]

    async def write_draft(self, selected_topic: str, review_feedback: str = "") -> str:
        """生成内容固定且可预测的技术文章草稿。"""
        # 仅在存在有效反馈时标记为重写稿，方便调用方和测试区分两种流程。
        revised_prefix = "【根据意见已修改】\n\n" if review_feedback.strip() else ""
        body = f"""# {selected_topic}\n\n在教育培训公司的内容运营中，技术文章的价值不止是介绍一个概念，更重要的是把抽象能力转化为读者可以理解、复用和验证的方法。本文以“{selected_topic}”为线索，说明如何将需求拆分为清晰的输入、可观察的处理步骤与可复盘的输出。\n\n首先，任何自动化流程都应先明确边界。内容团队需要知道目标读者是谁、文章准备解决什么问题，以及哪些结论必须由人工确认。将这些信息作为结构化输入保存，能够避免模型在后续生成中偏离主题。对于一篇公众号文章而言，标题、场景、核心观点和行动建议应形成一条连贯的叙事线，而不是彼此独立的段落。\n\n其次，生成过程需要具备状态。一个可靠的工作流会记录当前选题、已有草稿、修改意见和处理进度。当编辑提出“增加案例”或“语言更适合初学者”时，系统不应从零开始丢失上下文，而应带着反馈重新组织材料。这也是人工审核真正发挥作用的地方：人负责判断方向和质量，自动化负责重复性整理与扩写。\n\n再次，技术表达应当可验证。文中可以用一个小型示例说明：先收集主题，再生成初稿，随后由编辑审稿；通过后提炼配图要点并生成图片素材，驳回则回到写作环节。每一步都留下可查询记录，运营人员便能快速定位文章停在哪个阶段，也能比较不同反馈带来的修改结果。\n\n最后，内容运营不是一次性输出。发布后的阅读、收藏和转发数据会反过来帮助团队优化选题与写作模板。把这些经验沉淀为规则，配合稳定的人工把关机制，就能在效率与专业性之间取得平衡。希望这套方法能帮助你把“{selected_topic}”真正落到日常内容生产中。"""
        return revised_prefix + body

    async def extract_visual_points(self, article_content: str) -> list[str]:
        """为本地演示从文章标题构造带主题的确定性配图文案。"""
        article = article_content.strip()
        if not article:
            raise ValueError("无法从空文章中提炼配图要点")

        title = next(
            (
                line.removeprefix("#").strip()
                for line in article.splitlines()
                if line.lstrip().startswith("#") and line.removeprefix("#").strip()
            ),
            "文章核心主题",
        )
        return [
            f"围绕“{title}”的首图，突出文章主题和读者使用场景，干净的小红书插画风格",
            f"围绕“{title}”开篇读者痛点的场景图，人物与环境具体、画面有代入感",
            f"围绕“{title}”核心方法的步骤示意图，层次清晰、主体明确、简洁信息图风格",
            f"围绕“{title}”实践动作或案例的应用图，突出关键操作与使用效果，现代插画风格",
            f"围绕“{title}”行动建议的收尾画面，传达完成与复盘感，温暖现代插画风格",
        ]
