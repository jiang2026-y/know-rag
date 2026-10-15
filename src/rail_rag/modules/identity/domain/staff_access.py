"""员工访问领域模型：表达可修改的员工权限字段与可信访问状态。

定义 StaffAccessUpdate 和 StaffAccessState，分别用于传递普通员工状态、部门的更新命令及其更新结果。
"""

from dataclasses import dataclass
from typing import Literal


StaffStatus = Literal["ON_JOB", "OFF_JOB"]


@dataclass(frozen=True)
class StaffAccessUpdate:
    """管理员提交的普通员工状态和部门增量。"""

    status: StaffStatus | None = None
    dept_id: int | None = None

    def has_changes(self) -> bool:
        """返回更新命令是否至少包含一个字段。"""
        return self.status is not None or self.dept_id is not None


@dataclass(frozen=True)
class StaffAccessState:
    """MySQL 中已确认的员工访问状态。"""

    emp_id: str
    status: StaffStatus
    job: str
    dept_id: int | None
