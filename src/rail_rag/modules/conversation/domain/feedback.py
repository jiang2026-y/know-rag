"""回答反馈领域模型：定义用户可提交的赞踩反应。

定义 FeedbackReaction；该类型仅允许 LIKE、DISLIKE 两种用户评价，避免客户端创建
未受产品确认的新反馈标签。
"""

from typing import Literal


FeedbackReaction = Literal["LIKE", "DISLIKE"]
