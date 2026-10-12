"""Recover known TeX commands decoded as JSON control escapes, only in math."""
import re

_SEGMENTS = re.compile(r"```.*?```|`[^`]*`|\$\$.*?\$\$|(?<![\\$])\$(?!\$).*?(?<!\\)\$|\\\(.*?\\\)|\\\[.*?\\\]", re.S)
_COMMANDS = {"\b": "(?:eta|egin|ig|igg)", "\f": "(?:rac)", "\t": "(?:ext|heta|imes|au)",
             "\r": "(?:ight)", "\n": "(?:abla|u|eq)"}


def repair_math(value):
    def segment(match):
        text = match.group()
        if text.startswith("`"):
            return text
        for control, suffix in _COMMANDS.items():
            text = re.sub(re.escape(control) + "(?=" + suffix + "(?![A-Za-z]))",
                          lambda _: "\\" + {"\b": "b", "\f": "f", "\t": "t", "\r": "r", "\n": "n"}[control], text)
        return text
    return _SEGMENTS.sub(segment, value)
