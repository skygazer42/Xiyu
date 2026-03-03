"""LLM 角色系统 - 不同场景的系统提示词"""
from src.core.llm.roles.base import Role, RoleRegistry, get_role
from src.core.llm.roles.default import DefaultRole
from src.core.llm.roles.translator import TranslatorRole
from src.core.llm.roles.code import CodeRole
from src.core.llm.roles.corrector import CorrectorRole
from src.core.llm.roles.meeting import MeetingRole
from src.core.llm.roles.policy_polish_strict import PolicyPolishStrictRole
from src.core.llm.roles.policy_polish_balanced import PolicyPolishBalancedRole
from src.core.llm.roles.policy_polish_aggressive import PolicyPolishAggressiveRole
from src.core.llm.roles.policy_meeting import PolicyMeetingRole
from src.core.llm.roles.policy_meeting_aggressive import PolicyMeetingAggressiveRole
from src.core.llm.roles.policy_meeting_v2 import PolicyMeetingV2Role

__all__ = [
    'Role',
    'RoleRegistry',
    'get_role',
    'DefaultRole',
    'TranslatorRole',
    'CodeRole',
    'CorrectorRole',
    'MeetingRole',
    'PolicyPolishStrictRole',
    'PolicyPolishBalancedRole',
    'PolicyPolishAggressiveRole',
    'PolicyMeetingRole',
    'PolicyMeetingAggressiveRole',
    'PolicyMeetingV2Role',
]
