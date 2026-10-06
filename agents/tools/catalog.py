"""The built-in tool catalog served by `GET /api/tools` and the studio tool picker."""

from __future__ import annotations

from agents.tools.ask_peer import AskPeer
from agents.tools.base import BaseTool
from agents.tools.board import AssignTask, BoardMessage
from agents.tools.code import CodeExec, CodeOutline
from agents.tools.code_batch import CodeOutlineBatch
from agents.tools.data import DataCsv, DataJson, DataXml, DataYaml
from agents.tools.decompose_task import DecomposeTask
from agents.tools.desk_push import DeskPushTool
from agents.tools.desk_search import DeskSearchTool
from agents.tools.doc import DocHtml, DocMarkdown
from agents.tools.fs import (
    FsCreate,
    FsDelete,
    FsEdit,
    FsExists,
    FsGrep,
    FsList,
    FsRead,
    FsReadRange,
    FsRename,
    FsWrite,
    FsWriteAppend,
)
from agents.tools.fs_batch import FsEditBatch, FsWriteBatch
from agents.tools.git import GitChangedFiles, GitDiff, GitLog, GitRead, GitStatus
from agents.tools.llm_dispatch import (
    LlmDispatchTool,
    ProtocolCleanupTool,
    SummariseSubtaskTool,
)
from agents.tools.math_tool import MathEval
from agents.tools.recall_subtask import RecallSubtask
from agents.tools.text import TextDiff, TextEstimateTokens
from agents.tools.time_tool import TimeDiff, TimeNow
from agents.tools.web import WebFetch, WebSearch

LOCAL_TOOL_CLASSES: tuple[type[BaseTool], ...] = (
    FsRead, FsList, FsExists, FsReadRange, FsGrep,
    FsCreate, FsWrite, FsWriteAppend, FsEdit, FsDelete, FsRename,
    FsWriteBatch, FsEditBatch,
    GitDiff, GitChangedFiles, GitLog, GitRead, GitStatus,
    BoardMessage, AssignTask, DecomposeTask, AskPeer,
    RecallSubtask,
    CodeExec, CodeOutline, CodeOutlineBatch,
    DataCsv, DataJson, DataXml, DataYaml,
    DocHtml, DocMarkdown,
    MathEval,
    TextDiff, TextEstimateTokens,
    TimeDiff, TimeNow,
    WebFetch, WebSearch,
    DeskSearchTool, DeskPushTool,
    LlmDispatchTool, SummariseSubtaskTool, ProtocolCleanupTool,
)
