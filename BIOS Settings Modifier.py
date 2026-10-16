"""
BIOS Settings Modifier
----------------------
A desktop utility for batch-updating option selections in text NVRAM configuration
scripts exported by AMISCE (SCEWIN).

This tool edits exported text script files (such as nvram.txt) and saves the result
to a new file. It does not directly write to motherboard firmware or execute SCEWIN.
"""

from __future__ import annotations

import datetime
import os
import re
import tkinter as tk
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple


# ============================================================================
# Exceptions and Data Models
# ============================================================================


class SettingsFileError(Exception):
    """Raised when a settings configuration file cannot be read or decoded."""


class NVRAMFileError(Exception):
    """Raised when an NVRAM export file cannot be read, validated, or saved."""


class NVRAMFormatError(NVRAMFileError):
    """Raised when an NVRAM file is binary, empty, or not a valid SCEWIN text export."""


class EntryStatus(str, Enum):
    VALID = "Valid"
    DUPLICATE_SAME_VALUE = "Duplicate (Ignored)"
    DUPLICATE_CONFLICT = "Conflict"
    MALFORMED = "Invalid Syntax"


class InstanceOutcome(str, Enum):
    MODIFIED = "Modified"
    ALREADY_SET = "Already Set"
    VALUE_UNAVAILABLE = "Value Unavailable"


@dataclass(frozen=True)
class SettingEntry:
    """Represents a single parsed setting from a configuration file."""

    line_number: int
    name: str
    raw_value: str
    value_int: int

    @property
    def normalized_hex(self) -> str:
        return f"0x{self.value_int:X}"


@dataclass(frozen=True)
class ConfigIssue:
    """Represents a syntax error, conflicting duplicate, or redundant duplicate."""

    line_number: int
    raw_line: str
    message: str
    is_error: bool
    setting_name: str = ""
    raw_value: str = ""
    status: EntryStatus = EntryStatus.MALFORMED


@dataclass
class SettingsParseResult:
    """Result of parsing a settings configuration file."""

    active_settings: List[SettingEntry] = field(default_factory=list)
    issues: List[ConfigIssue] = field(default_factory=list)
    preview_rows: List[Tuple[int, str, str, EntryStatus]] = field(default_factory=list)

    @property
    def error_issues(self) -> List[ConfigIssue]:
        return [issue for issue in self.issues if issue.is_error]

    @property
    def warning_issues(self) -> List[ConfigIssue]:
        return [issue for issue in self.issues if not issue.is_error]

    @property
    def has_errors(self) -> bool:
        return len(self.error_issues) > 0

    @property
    def is_ready(self) -> bool:
        return not self.has_errors and len(self.active_settings) > 0


@dataclass(frozen=True)
class InstanceResult:
    """Outcome of processing a single Setup Question block in an NVRAM script."""

    instance_index: int
    outcome: InstanceOutcome
    available_options: Tuple[str, ...] = ()
    detail: str = ""


@dataclass
class SettingTransformReport:
    """Aggregated outcome for a single requested setting across all NVRAM instances."""

    setting: SettingEntry
    instance_results: List[InstanceResult] = field(default_factory=list)

    @property
    def instances_found(self) -> int:
        return len(self.instance_results)

    @property
    def instances_modified(self) -> int:
        return sum(1 for r in self.instance_results if r.outcome == InstanceOutcome.MODIFIED)

    @property
    def instances_already_set(self) -> int:
        return sum(1 for r in self.instance_results if r.outcome == InstanceOutcome.ALREADY_SET)

    @property
    def instances_value_unavailable(self) -> int:
        return sum(
            1 for r in self.instance_results if r.outcome == InstanceOutcome.VALUE_UNAVAILABLE
        )


@dataclass
class NVRAMTransformResult:
    """Complete result of transforming an NVRAM export text."""

    modified_content: str
    content_changed: bool
    reports: List[SettingTransformReport] = field(default_factory=list)

    @property
    def total_settings_requested(self) -> int:
        return len(self.reports)

    @property
    def settings_modified_count(self) -> int:
        return sum(1 for r in self.reports if r.instances_modified > 0)

    @property
    def settings_already_set_count(self) -> int:
        return sum(
            1
            for r in self.reports
            if r.instances_found > 0
            and r.instances_modified == 0
            and r.instances_already_set > 0
            and r.instances_value_unavailable == 0
        )

    @property
    def settings_not_found_count(self) -> int:
        return sum(1 for r in self.reports if r.instances_found == 0)

    @property
    def settings_with_unavailable_values_count(self) -> int:
        return sum(1 for r in self.reports if r.instances_value_unavailable > 0)

    @property
    def total_instances_found(self) -> int:
        return sum(r.instances_found for r in self.reports)

    @property
    def total_instances_modified(self) -> int:
        return sum(r.instances_modified for r in self.reports)

    @property
    def total_instances_already_set(self) -> int:
        return sum(r.instances_already_set for r in self.reports)

    @property
    def total_instances_value_unavailable(self) -> int:
        return sum(r.instances_value_unavailable for r in self.reports)


@dataclass(frozen=True)
class LoadedTextFile:
    """Holds validated text content and original encoding metadata."""

    content: str
    encoding: str


# ============================================================================
# Core Parsing and NVRAM Transformation Logic
# ============================================================================

SETTING_LINE_PATTERN = re.compile(r'^\s*"([^"]*)"\s+(0[xX][0-9a-fA-F]+)\s*$')
SETUP_QUESTION_PATTERN = re.compile(
    r"^[ \t]*Setup Question[ \t]*=[ \t]*(.*?)(?:[ \t]*//[^\r\n]*)?$",
    re.MULTILINE,
)
OPTIONS_SECTION_PATTERN = re.compile(
    r"^[ \t]*Options[ \t]*=[ \t]*[^\r\n]*(?:\r?\n[ \t]+\*?\[[0-9A-Fa-f]+\][^\r\n]*)*",
    re.MULTILINE,
)
OPTION_TOKEN_PATTERN = re.compile(
    r"(?P<prefix>^[ \t]*Options[ \t]*=[ \t]*|^[ \t]+|(?<=\s))(?P<star>\*?)\[(?P<hex>[0-9A-Fa-f]+)\]"
)


def _contains_disallowed_binary_bytes(raw_bytes: bytes) -> bool:
    """
    Returns True if raw bytes (without a UTF-16 BOM) contain null bytes or
    non-whitespace ASCII control characters indicative of a binary file.
    """
    if b"\x00" in raw_bytes:
        return True
    for byte in raw_bytes:
        if byte < 0x20 and byte not in (0x09, 0x0A, 0x0C, 0x0D):
            return True
    return False


def decode_text_bytes(raw_bytes: bytes, file_label: str = "File") -> LoadedTextFile:
    """
    Safely decodes text bytes while rejecting binary files.
    Supports UTF-8 (with or without BOM), UTF-16 (with BOM), and CP1252 text.
    """
    if not raw_bytes:
        raise NVRAMFormatError(f"{file_label} is empty.")

    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        try:
            text = raw_bytes.decode("utf-8-sig")
            if "\x00" in text:
                raise NVRAMFormatError(f"{file_label} appears to be a binary file.")
            return LoadedTextFile(content=text, encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise NVRAMFormatError(f"{file_label} contains invalid UTF-8 data: {exc}") from exc

    if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = raw_bytes.decode("utf-16")
            if "\x00" in text:
                raise NVRAMFormatError(f"{file_label} appears to be a binary file.")
            return LoadedTextFile(content=text, encoding="utf-16")
        except UnicodeDecodeError as exc:
            raise NVRAMFormatError(f"{file_label} contains invalid UTF-16 data: {exc}") from exc

    if _contains_disallowed_binary_bytes(raw_bytes):
        raise NVRAMFormatError(
            f"{file_label} appears to be a binary file. "
            "Only text files are supported."
        )

    for encoding in ("utf-8", "cp1252"):
        try:
            return LoadedTextFile(content=raw_bytes.decode(encoding), encoding=encoding)
        except UnicodeDecodeError:
            continue

    raise NVRAMFormatError(f"{file_label} could not be decoded as a supported text file.")


def parse_settings_text(content: str) -> SettingsParseResult:
    """
    Parses settings configuration text using the format:
        "SettingName" 0xValue

    Blank lines and full-line comments starting with '#' or '//' are ignored.
    Malformed lines and conflicting duplicate entries are flagged as errors.
    Identical duplicate entries are deduplicated and flagged as warnings.
    """
    result = SettingsParseResult()
    seen_by_name: Dict[str, SettingEntry] = {}
    conflicted_names: set[str] = set()

    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue

        match = SETTING_LINE_PATTERN.match(raw_line)
        if not match:
            issue = ConfigIssue(
                line_number=line_number,
                raw_line=raw_line,
                message=(
                    f"Line {line_number}: Invalid syntax '{stripped}'. "
                    'Expected format: "SettingName" 0xValue'
                ),
                is_error=True,
                setting_name=stripped,
                raw_value="",
                status=EntryStatus.MALFORMED,
            )
            result.issues.append(issue)
            result.preview_rows.append((line_number, stripped, "", EntryStatus.MALFORMED))
            continue

        setting_name, raw_value = match.group(1), match.group(2)
        if not setting_name.strip():
            issue = ConfigIssue(
                line_number=line_number,
                raw_line=raw_line,
                message=f"Line {line_number}: Setting name cannot be empty.",
                is_error=True,
                setting_name='""',
                raw_value=raw_value,
                status=EntryStatus.MALFORMED,
            )
            result.issues.append(issue)
            result.preview_rows.append((line_number, '""', raw_value, EntryStatus.MALFORMED))
            continue

        value_int = int(raw_value, 16)
        entry = SettingEntry(
            line_number=line_number,
            name=setting_name,
            raw_value=raw_value,
            value_int=value_int,
        )

        if setting_name in seen_by_name:
            first_entry = seen_by_name[setting_name]
            if first_entry.value_int == entry.value_int and setting_name not in conflicted_names:
                issue = ConfigIssue(
                    line_number=line_number,
                    raw_line=raw_line,
                    message=(
                        f'Line {line_number}: Duplicate setting "{setting_name}" with same '
                        f"value {raw_value} (already defined on line {first_entry.line_number}). "
                        "Duplicate line ignored."
                    ),
                    is_error=False,
                    setting_name=setting_name,
                    raw_value=raw_value,
                    status=EntryStatus.DUPLICATE_SAME_VALUE,
                )
                result.issues.append(issue)
                result.preview_rows.append(
                    (line_number, setting_name, raw_value, EntryStatus.DUPLICATE_SAME_VALUE)
                )
            else:
                conflicted_names.add(setting_name)
                issue = ConfigIssue(
                    line_number=line_number,
                    raw_line=raw_line,
                    message=(
                        f'Line {line_number}: Conflicting duplicate setting "{setting_name}" '
                        f"(line {first_entry.line_number} specifies {first_entry.raw_value}, "
                        f"line {line_number} specifies {raw_value})."
                    ),
                    is_error=True,
                    setting_name=setting_name,
                    raw_value=raw_value,
                    status=EntryStatus.DUPLICATE_CONFLICT,
                )
                result.issues.append(issue)
                result.preview_rows.append(
                    (line_number, setting_name, raw_value, EntryStatus.DUPLICATE_CONFLICT)
                )
                for idx, (r_line, r_name, r_val, r_status) in enumerate(result.preview_rows):
                    if r_name == setting_name and r_status == EntryStatus.VALID:
                        result.preview_rows[idx] = (
                            r_line,
                            r_name,
                            r_val,
                            EntryStatus.DUPLICATE_CONFLICT,
                        )
        else:
            seen_by_name[setting_name] = entry
            result.active_settings.append(entry)
            result.preview_rows.append((line_number, setting_name, raw_value, EntryStatus.VALID))

    if conflicted_names:
        result.active_settings = [
            entry for entry in result.active_settings if entry.name not in conflicted_names
        ]

    return result


def load_settings_file(filepath: str | os.PathLike[str]) -> SettingsParseResult:
    """Reads and parses a settings configuration file from disk."""
    path = Path(filepath)
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise SettingsFileError(
            f"Could not read settings file '{path.name}': {exc.strerror or exc}"
        ) from exc

    if not raw_bytes or not raw_bytes.strip():
        return SettingsParseResult()

    try:
        loaded = decode_text_bytes(raw_bytes, file_label=f"Settings file '{path.name}'")
    except NVRAMFormatError as exc:
        raise SettingsFileError(str(exc)) from exc

    return parse_settings_text(loaded.content)


def validate_nvram_text(content: str) -> None:
    """Verifies that decoded text contains at least one SCEWIN Setup Question block."""
    if not content or not content.strip():
        raise NVRAMFormatError("The NVRAM file is empty.")
    if "\x00" in content:
        raise NVRAMFormatError(
            "Binary files are not supported. Please select a text NVRAM export file from SCEWIN."
        )
    if not SETUP_QUESTION_PATTERN.search(content):
        raise NVRAMFormatError(
            "The selected file does not contain any SCEWIN 'Setup Question' blocks. "
            "Please select a valid text NVRAM script exported by SCEWIN (such as nvram.txt)."
        )


def read_nvram_file(filepath: str | os.PathLike[str]) -> LoadedTextFile:
    """Reads and validates a text SCEWIN NVRAM export file from disk."""
    path = Path(filepath)
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise NVRAMFileError(
            f"Could not read NVRAM file '{path.name}': {exc.strerror or exc}"
        ) from exc

    loaded = decode_text_bytes(raw_bytes, file_label=f"NVRAM file '{path.name}'")
    validate_nvram_text(loaded.content)
    return loaded


def save_nvram_file(
    filepath: str | os.PathLike[str],
    content: str,
    encoding: str = "utf-8",
) -> None:
    """Writes modified NVRAM text to disk while preserving original line endings."""
    path = Path(filepath)
    try:
        with open(path, "w", encoding=encoding, newline="") as handle:
            handle.write(content)
    except OSError as exc:
        raise NVRAMFileError(
            f"Could not save file '{path.name}': {exc.strerror or exc}"
        ) from exc


def _split_line_comment(line: str) -> Tuple[str, str, str]:
    """
    Splits a single line into (code_part, comment_part, line_ending) so that
    comments and line endings are preserved verbatim.
    """
    if line.endswith("\r\n"):
        ending = "\r\n"
        body = line[:-2]
    elif line.endswith("\n") or line.endswith("\r"):
        ending = line[-1]
        body = line[:-1]
    else:
        ending = ""
        body = line

    comment_idx = body.find("//")
    if comment_idx != -1:
        return body[:comment_idx], body[comment_idx:], ending
    return body, "", ending


def _update_options_section(
    options_text: str,
    target_value_int: int,
) -> Tuple[str, InstanceOutcome, Tuple[str, ...]]:
    """
    Inspects and updates an `Options = ...` section within a Setup Question block.
    Returns (new_options_text, outcome, tuple_of_available_hex_strings).
    """
    lines = options_text.splitlines(keepends=True)
    available_hex: List[str] = []
    target_found = False

    for line in lines:
        code_part, _, _ = _split_line_comment(line)
        for match in OPTION_TOKEN_PATTERN.finditer(code_part):
            hex_str = match.group("hex")
            available_hex.append(f"0x{hex_str.upper()}")
            if int(hex_str, 16) == target_value_int:
                target_found = True

    if not target_found:
        return options_text, InstanceOutcome.VALUE_UNAVAILABLE, tuple(available_hex)

    target_starred = False
    rebuilt_lines: List[str] = []

    for line in lines:
        code_part, comment_part, ending = _split_line_comment(line)

        def _replace_token(match: re.Match[str]) -> str:
            nonlocal target_starred
            prefix = match.group("prefix")
            hex_str = match.group("hex")
            if not target_starred and int(hex_str, 16) == target_value_int:
                target_starred = True
                return f"{prefix}*[{hex_str}]"
            return f"{prefix}[{hex_str}]"

        new_code_part = OPTION_TOKEN_PATTERN.sub(_replace_token, code_part)
        rebuilt_lines.append(f"{new_code_part}{comment_part}{ending}")

    new_options_text = "".join(rebuilt_lines)
    if new_options_text == options_text:
        return options_text, InstanceOutcome.ALREADY_SET, tuple(available_hex)
    return new_options_text, InstanceOutcome.MODIFIED, tuple(available_hex)


def _transform_question_block(
    block_text: str,
    target_value_int: int,
    instance_index: int,
) -> Tuple[str, InstanceResult]:
    """Transforms a single Setup Question block for the requested integer value."""
    options_match = OPTIONS_SECTION_PATTERN.search(block_text)
    if not options_match:
        return (
            block_text,
            InstanceResult(
                instance_index=instance_index,
                outcome=InstanceOutcome.VALUE_UNAVAILABLE,
                available_options=(),
                detail="No selectable Options section present in this block.",
            ),
        )

    options_start, options_end = options_match.span()
    original_options = block_text[options_start:options_end]
    updated_options, outcome, available_options = _update_options_section(
        original_options, target_value_int
    )

    if outcome == InstanceOutcome.MODIFIED:
        new_block_text = (
            block_text[:options_start] + updated_options + block_text[options_end:]
        )
        detail = f"Updated selection to 0x{target_value_int:X}."
    elif outcome == InstanceOutcome.ALREADY_SET:
        new_block_text = block_text
        detail = f"Already set to 0x{target_value_int:X}."
    else:
        new_block_text = block_text
        opts_formatted = ", ".join(available_options) if available_options else "none"
        detail = f"Requested value 0x{target_value_int:X} not in available options ({opts_formatted})."

    return (
        new_block_text,
        InstanceResult(
            instance_index=instance_index,
            outcome=outcome,
            available_options=available_options,
            detail=detail,
        ),
    )


def transform_nvram_text(
    nvram_content: str,
    settings: List[SettingEntry],
) -> NVRAMTransformResult:
    """
    Processes a validated SCEWIN NVRAM text export against the requested settings.
    - Matches exact 'Setup Question = <Name>' blocks (never substring-matching unrelated names).
    - Updates all repeated instances of each matched setting in the NVRAM export.
    - Leaves unrelated content and blocks without the requested option untouched.
    """
    validate_nvram_text(nvram_content)

    question_matches = list(SETUP_QUESTION_PATTERN.finditer(nvram_content))
    if not question_matches:
        raise NVRAMFormatError("No 'Setup Question' entries found in the NVRAM file.")

    preamble = nvram_content[: question_matches[0].start()]
    blocks: List[Tuple[str, str]] = []
    for idx, q_match in enumerate(question_matches):
        question_name = q_match.group(1).strip()
        block_start = q_match.start()
        block_end = (
            question_matches[idx + 1].start()
            if idx + 1 < len(question_matches)
            else len(nvram_content)
        )
        blocks.append((question_name, nvram_content[block_start:block_end]))

    positions_by_name: Dict[str, List[int]] = {}
    for block_idx, (question_name, _) in enumerate(blocks):
        positions_by_name.setdefault(question_name, []).append(block_idx)

    reports: List[SettingTransformReport] = []
    for setting in settings:
        report = SettingTransformReport(setting=setting)
        matching_indices = positions_by_name.get(setting.name, [])

        for instance_num, block_idx in enumerate(matching_indices, start=1):
            question_name, block_text = blocks[block_idx]
            new_block_text, instance_result = _transform_question_block(
                block_text=block_text,
                target_value_int=setting.value_int,
                instance_index=instance_num,
            )
            blocks[block_idx] = (question_name, new_block_text)
            report.instance_results.append(instance_result)

        reports.append(report)

    modified_content = preamble + "".join(block_text for _, block_text in blocks)
    return NVRAMTransformResult(
        modified_content=modified_content,
        content_changed=(modified_content != nvram_content),
        reports=reports,
    )


# ============================================================================
# Desktop Graphical User Interface (Tkinter / ttk - Black Theme)
# ============================================================================


class BIOSSettingsModifierApp:
    """Professional dark-themed Tkinter desktop interface for BIOS Settings Modifier."""

    # Restrained black / dark technical utility palette
    COLOR_BG = "#0a0a0c"
    COLOR_SURFACE = "#121216"
    COLOR_INPUT_BG = "#070709"
    COLOR_CONTROL_BG = "#222228"
    COLOR_CONTROL_ACTIVE = "#2e2e36"
    COLOR_BORDER = "#2a2a32"
    COLOR_TEXT = "#f4f4f6"
    COLOR_TEXT_MUTED = "#9ca3af"
    COLOR_PRIMARY = "#2563eb"
    COLOR_PRIMARY_ACTIVE = "#1d4ed8"
    COLOR_PRIMARY_DISABLED = "#1e2028"
    COLOR_OK = "#4ade80"
    COLOR_WARN = "#fbbf24"
    COLOR_ERROR = "#f87171"
    COLOR_SELECTION_BG = "#1e3a8a"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("BIOS Settings Modifier")
        self.root.geometry("860x720")
        self.root.minsize(720, 580)
        self.root.configure(bg=self.COLOR_BG)

        self.settings_file_path: str = ""
        self.nvram_file_path: str = ""
        self.parse_result: Optional[SettingsParseResult] = None
        self.loaded_nvram: Optional[LoadedTextFile] = None

        self.settings_path_var = tk.StringVar(value="No file selected")
        self.nvram_path_var = tk.StringVar(value="No file selected")
        self.preview_summary_var = tk.StringVar(value="0 active settings loaded")
        self.status_var = tk.StringVar(value="Status: Waiting for file selection")
        self.results_summary_var = tk.StringVar(
            value="Modified: 0 settings (0 instances) | Already Set: 0 | Not Found: 0 | Unavailable Value: 0"
        )

        self._configure_styles()
        self._build_layout()
        self._update_action_state()

    def _configure_styles(self) -> None:
        self.style = ttk.Style(self.root)
        if "clam" in self.style.theme_names():
            self.style.theme_use("clam")

        self.style.configure("App.TFrame", background=self.COLOR_BG)
        self.style.configure("Surface.TFrame", background=self.COLOR_SURFACE)

        self.style.configure(
            "Section.TLabelframe",
            background=self.COLOR_SURFACE,
            bordercolor=self.COLOR_BORDER,
            lightcolor=self.COLOR_BORDER,
            darkcolor=self.COLOR_BORDER,
            relief="solid",
            borderwidth=1,
        )
        self.style.configure(
            "Section.TLabelframe.Label",
            background=self.COLOR_BG,
            foreground=self.COLOR_TEXT,
            font=("Segoe UI", 10, "bold"),
        )

        self.style.configure(
            "HeaderTitle.TLabel",
            background=self.COLOR_BG,
            foreground=self.COLOR_TEXT,
            font=("Segoe UI", 14, "bold"),
        )
        self.style.configure(
            "HeaderSubtitle.TLabel",
            background=self.COLOR_BG,
            foreground=self.COLOR_TEXT_MUTED,
            font=("Segoe UI", 9),
        )
        self.style.configure(
            "Field.TLabel",
            background=self.COLOR_SURFACE,
            foreground=self.COLOR_TEXT,
            font=("Segoe UI", 9, "bold"),
        )
        self.style.configure(
            "MutedSurface.TLabel",
            background=self.COLOR_SURFACE,
            foreground=self.COLOR_TEXT_MUTED,
            font=("Segoe UI", 9),
        )
        self.style.configure(
            "Status.TLabel",
            background=self.COLOR_BG,
            foreground=self.COLOR_TEXT,
            font=("Segoe UI", 9, "bold"),
        )
        self.style.configure(
            "Summary.TLabel",
            background=self.COLOR_SURFACE,
            foreground=self.COLOR_TEXT,
            font=("Segoe UI", 9),
        )

        self.style.configure(
            "Primary.TButton",
            background=self.COLOR_PRIMARY,
            foreground="#ffffff",
            bordercolor=self.COLOR_PRIMARY,
            lightcolor=self.COLOR_PRIMARY,
            darkcolor=self.COLOR_PRIMARY,
            focuscolor="none",
            font=("Segoe UI", 9, "bold"),
            padding=(14, 6),
            borderwidth=1,
        )
        self.style.map(
            "Primary.TButton",
            background=[
                ("disabled", self.COLOR_PRIMARY_DISABLED),
                ("pressed", self.COLOR_PRIMARY_ACTIVE),
                ("active", self.COLOR_PRIMARY_ACTIVE),
            ],
            foreground=[
                ("disabled", "#6b7280"),
                ("!disabled", "#ffffff"),
            ],
            bordercolor=[
                ("disabled", self.COLOR_BORDER),
                ("!disabled", self.COLOR_PRIMARY),
            ],
        )

        self.style.configure(
            "Secondary.TButton",
            background=self.COLOR_CONTROL_BG,
            foreground=self.COLOR_TEXT,
            bordercolor=self.COLOR_BORDER,
            lightcolor=self.COLOR_BORDER,
            darkcolor=self.COLOR_BORDER,
            focuscolor="none",
            font=("Segoe UI", 9),
            padding=(10, 5),
            borderwidth=1,
        )
        self.style.map(
            "Secondary.TButton",
            background=[
                ("disabled", self.COLOR_PRIMARY_DISABLED),
                ("active", self.COLOR_CONTROL_ACTIVE),
                ("pressed", "#18181c"),
            ],
            foreground=[
                ("disabled", "#6b7280"),
                ("!disabled", self.COLOR_TEXT),
            ],
        )

        self.style.configure(
            "Path.TEntry",
            fieldbackground=self.COLOR_INPUT_BG,
            foreground=self.COLOR_TEXT,
            bordercolor=self.COLOR_BORDER,
            lightcolor=self.COLOR_BORDER,
            darkcolor=self.COLOR_BORDER,
            insertcolor=self.COLOR_TEXT,
            padding=4,
        )
        self.style.map(
            "Path.TEntry",
            fieldbackground=[
                ("readonly", self.COLOR_INPUT_BG),
                ("disabled", self.COLOR_BG),
            ],
            foreground=[
                ("readonly", self.COLOR_TEXT),
                ("disabled", self.COLOR_TEXT_MUTED),
            ],
        )

        self.style.configure(
            "Preview.Treeview",
            background=self.COLOR_INPUT_BG,
            fieldbackground=self.COLOR_INPUT_BG,
            foreground=self.COLOR_TEXT,
            bordercolor=self.COLOR_BORDER,
            lightcolor=self.COLOR_BORDER,
            darkcolor=self.COLOR_BORDER,
            rowheight=24,
            font=("Segoe UI", 9),
            borderwidth=1,
        )
        self.style.map(
            "Preview.Treeview",
            background=[("selected", self.COLOR_SELECTION_BG)],
            foreground=[("selected", "#ffffff")],
        )
        self.style.configure(
            "Preview.Treeview.Heading",
            background=self.COLOR_CONTROL_BG,
            foreground=self.COLOR_TEXT,
            bordercolor=self.COLOR_BORDER,
            lightcolor=self.COLOR_CONTROL_BG,
            darkcolor=self.COLOR_CONTROL_BG,
            font=("Segoe UI", 9, "bold"),
            padding=(6, 4),
        )
        self.style.map(
            "Preview.Treeview.Heading",
            background=[("active", self.COLOR_CONTROL_ACTIVE)],
        )

        self.style.configure(
            "Vertical.TScrollbar",
            background=self.COLOR_CONTROL_BG,
            troughcolor=self.COLOR_INPUT_BG,
            bordercolor=self.COLOR_BORDER,
            lightcolor=self.COLOR_CONTROL_BG,
            darkcolor=self.COLOR_CONTROL_BG,
            arrowcolor=self.COLOR_TEXT_MUTED,
        )
        self.style.map(
            "Vertical.TScrollbar",
            background=[("active", self.COLOR_CONTROL_ACTIVE)],
        )

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        container = ttk.Frame(self.root, style="App.TFrame", padding=16)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(2, weight=3)
        container.rowconfigure(4, weight=2)

        # 1. Header
        header_frame = ttk.Frame(container, style="App.TFrame")
        header_frame.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(
            header_frame,
            text="BIOS Settings Modifier",
            style="HeaderTitle.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            header_frame,
            text=(
                "Prepares a modified SCEWIN NVRAM script file from a settings list. "
                "Does not write directly to motherboard firmware."
            ),
            style="HeaderSubtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        # 2. File Selection Section
        file_section = ttk.LabelFrame(
            container,
            text=" 1. File Selection ",
            style="Section.TLabelframe",
            padding=12,
        )
        file_section.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        file_section.columnconfigure(1, weight=1)

        ttk.Label(file_section, text="Settings File:", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=(0, 8)
        )
        self.settings_entry = ttk.Entry(
            file_section,
            textvariable=self.settings_path_var,
            state="readonly",
            style="Path.TEntry",
            font=("Segoe UI", 9),
        )
        self.settings_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=(0, 8))
        self.browse_settings_btn = ttk.Button(
            file_section,
            text="Browse Settings...",
            command=self.browse_settings,
            style="Secondary.TButton",
            width=18,
        )
        self.browse_settings_btn.grid(row=0, column=2, sticky="e", pady=(0, 8))

        ttk.Label(file_section, text="NVRAM Export:", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 10)
        )
        self.nvram_entry = ttk.Entry(
            file_section,
            textvariable=self.nvram_path_var,
            state="readonly",
            style="Path.TEntry",
            font=("Segoe UI", 9),
        )
        self.nvram_entry.grid(row=1, column=1, sticky="ew", padx=(0, 10))
        self.browse_nvram_btn = ttk.Button(
            file_section,
            text="Browse NVRAM...",
            command=self.browse_nvram,
            style="Secondary.TButton",
            width=18,
        )
        self.browse_nvram_btn.grid(row=1, column=2, sticky="e")

        # 3. Settings Preview Section
        preview_section = ttk.LabelFrame(
            container,
            text=" 2. Requested Settings Preview ",
            style="Section.TLabelframe",
            padding=12,
        )
        preview_section.grid(row=2, column=0, sticky="nsew", pady=(0, 12))
        preview_section.columnconfigure(0, weight=1)
        preview_section.rowconfigure(1, weight=1)

        preview_top = ttk.Frame(preview_section, style="Surface.TFrame")
        preview_top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(
            preview_top,
            text="Loaded configuration entries (duplicate lines with identical values are deduplicated):",
            style="MutedSurface.TLabel",
        ).pack(side="left")
        ttk.Label(
            preview_top,
            textvariable=self.preview_summary_var,
            style="Field.TLabel",
        ).pack(side="right")

        columns = ("line", "name", "value", "status")
        self.tree = ttk.Treeview(
            preview_section,
            columns=columns,
            show="headings",
            style="Preview.Treeview",
            height=7,
        )
        self.tree.heading("line", text="Line", anchor="w")
        self.tree.heading("name", text="Setting Name", anchor="w")
        self.tree.heading("value", text="Requested Value", anchor="w")
        self.tree.heading("status", text="Validation Status", anchor="w")

        self.tree.column("line", width=60, minwidth=50, stretch=False, anchor="w")
        self.tree.column("name", width=420, minwidth=200, stretch=True, anchor="w")
        self.tree.column("value", width=130, minwidth=90, stretch=False, anchor="w")
        self.tree.column("status", width=160, minwidth=120, stretch=False, anchor="w")

        self.tree.tag_configure("valid", foreground=self.COLOR_TEXT)
        self.tree.tag_configure("warning", foreground=self.COLOR_WARN)
        self.tree.tag_configure("error", foreground=self.COLOR_ERROR)

        tree_scroll_y = ttk.Scrollbar(
            preview_section,
            orient="vertical",
            command=self.tree.yview,
            style="Vertical.TScrollbar",
        )
        self.tree.configure(yscrollcommand=tree_scroll_y.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        tree_scroll_y.grid(row=1, column=1, sticky="ns")

        # 4. Processing Controls & Summary Section
        control_frame = ttk.Frame(container, style="App.TFrame")
        control_frame.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        control_frame.columnconfigure(1, weight=1)

        self.process_button = ttk.Button(
            control_frame,
            text="Process and Save Modified NVRAM...",
            command=self.process_nvram,
            style="Primary.TButton",
            state="disabled",
        )
        self.process_button.grid(row=0, column=0, sticky="w", padx=(0, 12))

        self.status_label = ttk.Label(
            control_frame,
            textvariable=self.status_var,
            style="Status.TLabel",
        )
        self.status_label.grid(row=0, column=1, sticky="e")

        # 5. Process Log and Results Summary Section
        log_section = ttk.LabelFrame(
            container,
            text=" 3. Processing Results and Event Log ",
            style="Section.TLabelframe",
            padding=12,
        )
        log_section.grid(row=4, column=0, sticky="nsew")
        log_section.columnconfigure(0, weight=1)
        log_section.rowconfigure(1, weight=1)

        log_header = ttk.Frame(log_section, style="Surface.TFrame")
        log_header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(
            log_header,
            textvariable=self.results_summary_var,
            style="Summary.TLabel",
        ).pack(side="left")
        self.clear_log_btn = ttk.Button(
            log_header,
            text="Clear Log",
            command=self.clear_log,
            style="Secondary.TButton",
        )
        self.clear_log_btn.pack(side="right")

        self.log_text = tk.Text(
            log_section,
            height=8,
            bg=self.COLOR_INPUT_BG,
            fg=self.COLOR_TEXT,
            insertbackground=self.COLOR_TEXT,
            selectbackground=self.COLOR_SELECTION_BG,
            selectforeground="#ffffff",
            font=("Consolas", 9),
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=self.COLOR_BORDER,
            highlightcolor=self.COLOR_BORDER,
            wrap="word",
            state="disabled",
        )
        self.log_text.tag_configure("INFO", foreground=self.COLOR_TEXT)
        self.log_text.tag_configure("OK", foreground=self.COLOR_OK)
        self.log_text.tag_configure("WARNING", foreground=self.COLOR_WARN)
        self.log_text.tag_configure("ERROR", foreground=self.COLOR_ERROR)

        log_scroll_y = ttk.Scrollbar(
            log_section,
            orient="vertical",
            command=self.log_text.yview,
            style="Vertical.TScrollbar",
        )
        self.log_text.configure(yscrollcommand=log_scroll_y.set)
        self.log_text.grid(row=1, column=0, sticky="nsew")
        log_scroll_y.grid(row=1, column=1, sticky="ns")

    # ------------------------------------------------------------------------
    # Logging and Status Helpers
    # ------------------------------------------------------------------------

    def log(self, message: str, level: str = "INFO") -> None:
        """Appends a timestamped entry to the log without forcing re-entrant root.update()."""
        normalized_level = level.upper()
        if normalized_level not in ("INFO", "OK", "WARNING", "ERROR"):
            normalized_level = "INFO"

        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] [{normalized_level}] {message}\n"

        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, line, normalized_level)
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    def _set_status(self, message: str, level: str = "INFO") -> None:
        self.status_var.set(f"Status: {message}")
        color_map = {
            "INFO": self.COLOR_TEXT,
            "OK": self.COLOR_OK,
            "WARNING": self.COLOR_WARN,
            "ERROR": self.COLOR_ERROR,
        }
        self.status_label.configure(
            foreground=color_map.get(level.upper(), self.COLOR_TEXT)
        )

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.browse_settings_btn.configure(state=state)
        self.browse_nvram_btn.configure(state=state)
        self.clear_log_btn.configure(state=state)
        if enabled:
            self._update_action_state()
        else:
            self.process_button.configure(state="disabled")

    def _update_action_state(self) -> None:
        settings_ok = self.parse_result is not None and self.parse_result.is_ready
        nvram_ok = self.loaded_nvram is not None and bool(self.nvram_file_path)

        if settings_ok and nvram_ok:
            self.process_button.configure(state="normal")
            self._set_status("Ready to process NVRAM export", "OK")
        elif self.parse_result is not None and self.parse_result.has_errors:
            self.process_button.configure(state="disabled")
            self._set_status("Resolve configuration file errors before processing", "ERROR")
        elif not self.settings_file_path and not self.nvram_file_path:
            self.process_button.configure(state="disabled")
            self._set_status("Select a settings file and an NVRAM export file", "INFO")
        elif not settings_ok:
            self.process_button.configure(state="disabled")
            self._set_status("Select a valid settings configuration file", "WARNING")
        else:
            self.process_button.configure(state="disabled")
            self._set_status("Select a valid SCEWIN NVRAM export file", "WARNING")

    # ------------------------------------------------------------------------
    # File Selection and Validation Workflows
    # ------------------------------------------------------------------------

    def browse_settings(self) -> None:
        filename = filedialog.askopenfilename(
            title="Select Settings Configuration File",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not filename:
            return

        self.settings_file_path = filename
        self.settings_path_var.set(filename)
        self._load_and_preview_settings(filename)
        self._update_action_state()

    def _load_and_preview_settings(self, filepath: str) -> None:
        for item_id in self.tree.get_children():
            self.tree.delete(item_id)
        self.parse_result = None

        try:
            result = load_settings_file(filepath)
        except SettingsFileError as exc:
            self.preview_summary_var.set("Failed to load settings")
            self.log(str(exc), "ERROR")
            self._set_status("Settings file could not be loaded", "ERROR")
            messagebox.showerror("Settings File Error", str(exc))
            return

        self.parse_result = result

        for line_num, name, raw_val, status in result.preview_rows:
            if status == EntryStatus.VALID:
                tag = "valid"
            elif status == EntryStatus.DUPLICATE_SAME_VALUE:
                tag = "warning"
            else:
                tag = "error"
            self.tree.insert(
                "",
                "end",
                values=(line_num, name, raw_val, status.value),
                tags=(tag,),
            )

        active_count = len(result.active_settings)
        warn_count = len(result.warning_issues)
        err_count = len(result.error_issues)

        summary_parts = [f"{active_count} active"]
        if warn_count:
            summary_parts.append(f"{warn_count} duplicate warning(s)")
        if err_count:
            summary_parts.append(f"{err_count} error(s)")
        self.preview_summary_var.set(", ".join(summary_parts))

        file_name = os.path.basename(filepath)
        if err_count > 0:
            self.log(
                f"Loaded '{file_name}' with {err_count} configuration error(s) "
                f"and {warn_count} warning(s). Processing is disabled until resolved.",
                "ERROR",
            )
            for issue in result.issues:
                self.log(issue.message, "ERROR" if issue.is_error else "WARNING")

            error_preview = "\n".join(issue.message for issue in result.error_issues[:6])
            if err_count > 6:
                error_preview += f"\n...and {err_count - 6} more error(s). See Process Log."
            messagebox.showerror(
                "Configuration Validation Failed",
                f"The settings file '{file_name}' contains {err_count} error(s) that must "
                f"be fixed before processing:\n\n{error_preview}",
            )
        elif active_count == 0:
            self.log(
                f"Settings file '{file_name}' contains no valid configuration entries.",
                "WARNING",
            )
            messagebox.showwarning(
                "Empty Settings File",
                f"No setting entries were found in '{file_name}'.\n\n"
                'Expected format per line: "SettingName" 0xValue',
            )
        else:
            if warn_count > 0:
                self.log(
                    f"Loaded {active_count} unique setting(s) from '{file_name}' "
                    f"({warn_count} duplicate line(s) with identical values were deduplicated).",
                    "WARNING",
                )
                for issue in result.warning_issues:
                    self.log(issue.message, "WARNING")
            else:
                self.log(
                    f"Loaded {active_count} setting(s) from '{file_name}'.",
                    "OK",
                )

    def browse_nvram(self) -> None:
        filename = filedialog.askopenfilename(
            title="Select Exported SCEWIN NVRAM Script File",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not filename:
            return

        self.loaded_nvram = None
        self.nvram_file_path = ""

        try:
            loaded = read_nvram_file(filename)
        except NVRAMFileError as exc:
            self.nvram_path_var.set("Invalid file selected")
            self.log(str(exc), "ERROR")
            self._update_action_state()
            messagebox.showerror("Invalid NVRAM File", str(exc))
            return

        self.nvram_file_path = filename
        self.loaded_nvram = loaded
        self.nvram_path_var.set(filename)
        self.log(
            f"Loaded NVRAM export '{os.path.basename(filename)}' "
            f"(encoding: {loaded.encoding}, {len(loaded.content):,} characters).",
            "OK",
        )
        self._update_action_state()

    # ------------------------------------------------------------------------
    # NVRAM Transformation and Save-As Workflow
    # ------------------------------------------------------------------------

    def _suggest_save_filename(self) -> str:
        source_path = Path(self.nvram_file_path)
        stem = source_path.stem or "nvram"
        suffix = source_path.suffix or ".txt"
        return f"{stem}_modified{suffix}"

    def process_nvram(self) -> None:
        if not self.parse_result or not self.parse_result.is_ready or not self.nvram_file_path:
            self._update_action_state()
            return

        self._set_controls_enabled(False)
        self._set_status("Processing NVRAM export...", "WARNING")
        self.root.update_idletasks()

        try:
            loaded_nvram = read_nvram_file(self.nvram_file_path)
            self.loaded_nvram = loaded_nvram

            self.log(
                f"Starting processing of '{os.path.basename(self.nvram_file_path)}' "
                f"with {len(self.parse_result.active_settings)} active setting(s)...",
                "INFO",
            )

            transform_result = transform_nvram_text(
                nvram_content=loaded_nvram.content,
                settings=self.parse_result.active_settings,
            )

            self._log_transform_details(transform_result)
            self._update_results_summary_banner(transform_result)

            if not transform_result.content_changed:
                self._handle_no_modifications(transform_result)
                return

            self._prompt_and_save_output(transform_result, loaded_nvram.encoding)

        except NVRAMFileError as exc:
            self.log(f"Processing failed: {exc}", "ERROR")
            self._set_status("Processing failed", "ERROR")
            messagebox.showerror("NVRAM Processing Error", str(exc))
        except Exception as exc:
            self.log(f"Unexpected error during processing: {exc}", "ERROR")
            self._set_status("Processing failed", "ERROR")
            messagebox.showerror(
                "Unexpected Error",
                f"An unexpected error occurred while processing the NVRAM file:\n\n{exc}",
            )
        finally:
            self._set_controls_enabled(True)

    def _log_transform_details(self, result: NVRAMTransformResult) -> None:
        for report in result.reports:
            setting = report.setting
            if report.instances_found == 0:
                self.log(
                    f'Setting not found in NVRAM: "{setting.name}" (requested {setting.raw_value}).',
                    "WARNING",
                )
                continue

            for inst in report.instance_results:
                if inst.outcome == InstanceOutcome.VALUE_UNAVAILABLE:
                    self.log(
                        f'"{setting.name}" [instance {inst.instance_index}/{report.instances_found}]: '
                        f"{inst.detail}",
                        "WARNING",
                    )

        self.log(
            f"Transformation summary: {result.settings_modified_count}/{result.total_settings_requested} "
            f"setting(s) modified across {result.total_instances_modified} NVRAM instance(s); "
            f"{result.total_instances_already_set} instance(s) already in requested state; "
            f"{result.settings_not_found_count} setting(s) not found; "
            f"{result.total_instances_value_unavailable} instance(s) missing requested value.",
            "OK" if result.total_instances_modified > 0 else "WARNING",
        )

    def _update_results_summary_banner(self, result: NVRAMTransformResult) -> None:
        self.results_summary_var.set(
            f"Modified: {result.settings_modified_count} settings "
            f"({result.total_instances_modified} instances) | "
            f"Already Set: {result.total_instances_already_set} instances | "
            f"Not Found: {result.settings_not_found_count} settings | "
            f"Unavailable Value: {result.total_instances_value_unavailable} instances"
        )

    def _handle_no_modifications(self, result: NVRAMTransformResult) -> None:
        if (
            result.total_instances_already_set > 0
            and result.settings_not_found_count == 0
            and result.total_instances_value_unavailable == 0
        ):
            msg = (
                "No file modifications were needed because all matched settings in the NVRAM "
                f"export ({result.total_instances_already_set} instance(s)) are already set "
                "to the requested values."
            )
            self.log(msg, "OK")
            self._set_status("Completed - All matched settings already in requested state", "OK")
            messagebox.showinfo("No Modifications Needed", msg)
        else:
            msg = (
                "No changes were made to the NVRAM export.\n\n"
                f"- Instances already set to requested value: {result.total_instances_already_set}\n"
                f"- Settings not found in NVRAM file: {result.settings_not_found_count}\n"
                f"- Instances where requested value was unavailable: {result.total_instances_value_unavailable}\n\n"
                "Review the Process Log for details on individual settings."
            )
            self.log("No changes were written because no NVRAM instances were modified.", "WARNING")
            self._set_status("Completed - No modifications made", "WARNING")
            messagebox.showwarning("No Changes Made", msg)

    def _prompt_and_save_output(self, result: NVRAMTransformResult, encoding: str) -> None:
        initial_dir = os.path.dirname(self.nvram_file_path) or "."
        save_path = filedialog.asksaveasfilename(
            title="Save Modified NVRAM Export File",
            initialdir=initial_dir,
            initialfile=self._suggest_save_filename(),
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )

        if not save_path:
            self.log("Save operation cancelled by user. No output file was written.", "WARNING")
            self._set_status("Save cancelled - Output file not written", "WARNING")
            return

        if os.path.abspath(save_path) == os.path.abspath(self.nvram_file_path):
            confirm = messagebox.askyesno(
                "Confirm Overwrite",
                "You selected the original NVRAM export file as the save destination.\n\n"
                "It is strongly recommended to keep a backup of your unmodified NVRAM file.\n"
                "Do you want to overwrite the original file?",
            )
            if not confirm:
                self.log("Overwrite cancelled by user. No output file was written.", "WARNING")
                self._set_status("Save cancelled - Original file preserved", "WARNING")
                return

        save_nvram_file(save_path, result.modified_content, encoding=encoding)
        saved_name = os.path.basename(save_path)
        self.log(
            f"Saved modified NVRAM script to '{saved_name}'. "
            "Note: This file has been prepared on disk and has not been applied to BIOS firmware.",
            "OK",
        )
        self._set_status(f"Saved modified NVRAM file to '{saved_name}'", "OK")

        messagebox.showinfo(
            "Modified NVRAM File Saved",
            f"The modified NVRAM script was saved successfully.\n\n"
            f"Output file: {saved_name}\n"
            f"Settings modified: {result.settings_modified_count} "
            f"({result.total_instances_modified} instance(s))\n"
            f"Instances already set: {result.total_instances_already_set}\n"
            f"Settings not found: {result.settings_not_found_count}\n"
            f"Unavailable option values: {result.total_instances_value_unavailable}\n\n"
            "Important: Saving this file does not change your computer's BIOS settings. "
            "Review the file before importing it with SCEWIN.",
        )


def main() -> None:
    root = tk.Tk()
    BIOSSettingsModifierApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
