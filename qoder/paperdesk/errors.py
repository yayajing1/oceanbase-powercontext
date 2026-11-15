"""PaperDesk 的异常类型，CLI 按类型映射退出码。"""

from __future__ import annotations

__all__ = [
    "PaperDeskError",
    "ConfigError",
    "DatabaseError",
    "DatabaseUnavailable",
    "ImportError_",
]


class PaperDeskError(Exception):
    """所有可预期错误的基类。消息应当可以直接给用户看。"""


class ConfigError(PaperDeskError):
    """环境变量或 .env 配置不合法。"""


class DatabaseError(PaperDeskError):
    """数据库操作失败（建表、写入、查询）。"""


class DatabaseUnavailable(DatabaseError):
    """连不上数据库，或目标库不存在。消息里带排查步骤。"""


class ImportError_(PaperDeskError):
    """导入文件失败（路径不存在、编码无法识别、内容无法解析）。"""
