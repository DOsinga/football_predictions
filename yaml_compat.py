"""YAML 1.2 boolean spec — only `true|True|TRUE|false|False|FALSE`.

PyYAML defaults to YAML 1.1 which treats `NO`/`YES`/`ON`/`OFF` (and lowercase
variants) as booleans, which breaks team codes like NO (Norway). This module
exposes drop-in `safe_load` / `safe_dump` that use the YAML 1.2 rule.
"""
import re
import yaml


def _strip_legacy_bool(loader_or_dumper):
    for ch in list(loader_or_dumper.yaml_implicit_resolvers):
        loader_or_dumper.yaml_implicit_resolvers[ch] = [
            (tag, regex)
            for tag, regex in loader_or_dumper.yaml_implicit_resolvers[ch]
            if tag != "tag:yaml.org,2002:bool"
        ]
    loader_or_dumper.add_implicit_resolver(
        "tag:yaml.org,2002:bool",
        re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
        list("tTfF"),
    )


class SafeLoader(yaml.SafeLoader):
    pass


class SafeDumper(yaml.SafeDumper):
    pass


_strip_legacy_bool(SafeLoader)
_strip_legacy_bool(SafeDumper)


def safe_load(stream):
    return yaml.load(stream, Loader=SafeLoader)


def safe_dump(data, **kwargs):
    return yaml.dump(data, Dumper=SafeDumper, **kwargs)
