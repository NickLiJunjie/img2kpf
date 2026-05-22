from __future__ import annotations

import argparse
import json
import re
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


NAME_REF_ANN_SID = 598


def read_varuint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    start = offset
    while True:
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if byte & 0x80:
            return value, offset
        if offset - start > 10:
            raise ValueError(f"varuint too long at offset {start}")


@dataclass
class IonValue:
    type_name: str
    value: Any
    annotations: list[int] | None = None

    def to_python(self) -> Any:
        payload: Any
        if isinstance(self.value, list):
            payload = [
                item.to_python() if isinstance(item, IonValue) else item
                for item in self.value
            ]
        elif isinstance(self.value, dict):
            payload = {
                key: item.to_python() if isinstance(item, IonValue) else item
                for key, item in self.value.items()
            }
        else:
            payload = self.value

        if self.annotations:
            return {
                "annotations": self.annotations,
                "type": self.type_name,
                "value": payload,
            }
        return payload


class IonParser:
    def __init__(self, data: bytes):
        self.data = data

    def parse_stream(self, offset: int = 0) -> tuple[IonValue, int]:
        if self.data[offset : offset + 4] == b"\xE0\x01\x00\xEA":
            offset += 4
        return self.parse_value(offset)

    def parse_value(self, offset: int) -> tuple[IonValue, int]:
        td = self.data[offset]
        offset += 1
        type_code = td >> 4
        length_code = td & 0x0F

        if type_code == 0 and length_code == 0x0F:
            return IonValue("null", None), offset

        if length_code == 0x0E:
            length, offset = read_varuint(self.data, offset)
        else:
            length = length_code

        if type_code == 0:
            raw = self.data[offset : offset + length]
            return IonValue("nop", raw.hex()), offset + length

        if type_code == 1:
            if length_code == 0:
                return IonValue("bool", False), offset
            if length_code == 1:
                return IonValue("bool", True), offset
            if length_code == 0x0F:
                return IonValue("null.bool", None), offset
            raise ValueError(f"unsupported bool encoding: {td:#x}")

        if type_code in (2, 3):
            magnitude = int.from_bytes(self.data[offset : offset + length], "big") if length else 0
            value = -magnitude if type_code == 3 else magnitude
            return IonValue("int", value), offset + length

        if type_code == 4:
            raw = self.data[offset : offset + length]
            if length == 0:
                return IonValue("float", 0.0), offset + length
            if length == 4:
                return IonValue("float", struct.unpack(">f", raw)[0]), offset + length
            if length == 8:
                return IonValue("float", struct.unpack(">d", raw)[0]), offset + length
            return IonValue("float_bytes", raw.hex()), offset + length

        if type_code == 5:
            raw = self.data[offset : offset + length]
            return IonValue("decimal_bytes", raw.hex()), offset + length

        if type_code == 6:
            raw = self.data[offset : offset + length]
            return IonValue("timestamp_bytes", raw.hex()), offset + length

        if type_code == 7:
            sid = int.from_bytes(self.data[offset : offset + length], "big") if length else 0
            return IonValue("symbol", sid), offset + length

        if type_code == 8:
            text = self.data[offset : offset + length].decode("utf-8", errors="replace")
            return IonValue("string", text), offset + length

        if type_code == 9:
            raw = self.data[offset : offset + length]
            return IonValue("clob", raw.hex()), offset + length

        if type_code == 10:
            raw = self.data[offset : offset + length]
            return IonValue("blob", raw.hex()), offset + length

        if type_code in (11, 12):
            end = offset + length
            items: list[IonValue] = []
            while offset < end:
                item, offset = self.parse_value(offset)
                items.append(item)
            return IonValue("list" if type_code == 11 else "sexp", items), offset

        if type_code == 13:
            end = offset + length
            fields: list[dict[str, Any]] = []
            while offset < end:
                field_sid, offset = read_varuint(self.data, offset)
                value, offset = self.parse_value(offset)
                fields.append({"field_sid": field_sid, "value": value})
            return IonValue("struct", fields), offset

        if type_code == 14:
            end = offset + length
            ann_length, offset = read_varuint(self.data, offset)
            ann_end = offset + ann_length
            annotations: list[int] = []
            while offset < ann_end:
                sid, offset = read_varuint(self.data, offset)
                annotations.append(sid)
            wrapped, _ = self.parse_value(offset)
            wrapped.annotations = annotations
            return wrapped, end

        raise ValueError(f"unsupported Ion type code: {type_code}")


def extract_edges(raw_text: str) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    token = (
        r"(?:"
        r"rsrc[0-9A-Z]{1,4}|"
        r"[cdeilt][0-9A-Z]{1,4}(?:-spm|-ad)?|"
        r"root_entity|document_data|book_navigation|metadata|location_map|book_metadata|"
        r"yj\.[A-Za-z0-9_\.]+|"
        r"eidbucket_[A-Za-z0-9_]+|"
        r"\$ion_symbol_table"
        r")"
    )
    edge_re = re.compile(rf"({token})child({token})")

    children: dict[str, set[str]] = defaultdict(set)
    parents: dict[str, set[str]] = defaultdict(set)
    for parent, child in edge_re.findall(raw_text):
        children[parent].add(child)
        parents[child].add(parent)
    return children, parents


def descendants(children: dict[str, set[str]], root: str) -> set[str]:
    seen: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(children.get(node, ()))
    return seen


def find_blob_offset(raw: bytes, name: str) -> int | None:
    marker = (name + "blob").encode("ascii")
    start = 0
    while True:
        index = raw.find(marker, start)
        if index == -1:
            return None
        ion_start = index + len(marker)
        if raw[ion_start : ion_start + 4] == b"\xE0\x01\x00\xEA":
            return ion_start
        start = index + 1


def parse_named_blob(raw: bytes, name: str) -> IonValue | None:
    blob_offset = find_blob_offset(raw, name)
    if blob_offset is None:
        return None
    parser = IonParser(raw[blob_offset:])
    value, _ = parser.parse_stream()
    return value


def parse_named_blob_safe(raw: bytes, name: str) -> IonValue | None:
    try:
        return parse_named_blob(raw, name)
    except Exception:
        return None


def read_typed_header(data: bytes, offset: int) -> tuple[int, int, int, int]:
    td = data[offset]
    offset += 1
    type_code = td >> 4
    length_code = td & 0x0F
    if length_code == 0x0E:
        length, offset = read_varuint(data, offset)
    else:
        length = length_code
    return type_code, length, offset, offset + length


def unwrap_annotated_struct(data: bytes) -> tuple[list[int], bytes]:
    offset = 0
    if data[offset : offset + 4] == b"\xE0\x01\x00\xEA":
        offset += 4

    type_code, _, payload_offset, wrapper_end = read_typed_header(data, offset)
    if type_code != 14:
        raise ValueError("top-level Ion value is not an annotation wrapper")

    ann_length, ann_offset = read_varuint(data, payload_offset)
    ann_end = ann_offset + ann_length
    annotations: list[int] = []
    while ann_offset < ann_end:
        sid, ann_offset = read_varuint(data, ann_offset)
        annotations.append(sid)

    struct_type, _, struct_offset, struct_end = read_typed_header(data, ann_offset)
    if struct_type != 13:
        raise ValueError("wrapped Ion value is not a struct")
    if struct_end > wrapper_end:
        raise ValueError("wrapped struct overruns annotation wrapper")
    return annotations, data[struct_offset:struct_end]


def parse_annotated_name_ref_bytes(data: bytes, offset: int) -> tuple[str | None, int]:
    type_code, _, payload_offset, wrapper_end = read_typed_header(data, offset)
    if type_code != 14:
        return None, wrapper_end

    ann_length, ann_offset = read_varuint(data, payload_offset)
    ann_end = ann_offset + ann_length
    annotations: list[int] = []
    while ann_offset < ann_end:
        sid, ann_offset = read_varuint(data, ann_offset)
        annotations.append(sid)

    wrapped_type, wrapped_length, wrapped_offset, wrapped_end = read_typed_header(data, ann_offset)
    if wrapped_type != 8:
        return None, wrapper_end

    value = data[wrapped_offset:wrapped_end].decode("utf-8", errors="replace")
    if annotations != [NAME_REF_ANN_SID]:
        return None, wrapper_end
    return value, wrapper_end


def parse_int_bytes(data: bytes, offset: int) -> tuple[int | None, int]:
    type_code, length, payload_offset, end_offset = read_typed_header(data, offset)
    if type_code not in (2, 3):
        return None, end_offset
    magnitude = int.from_bytes(data[payload_offset:end_offset], "big") if length else 0
    return (-magnitude if type_code == 3 else magnitude), end_offset


def name_ref(value: IonValue | Any) -> str | None:
    if not isinstance(value, IonValue):
        return None
    if value.annotations == [NAME_REF_ANN_SID] and value.type_name == "string":
        return value.value
    return None


def struct_fields(value: IonValue | None) -> dict[int, list[IonValue]]:
    if value is None or value.type_name != "struct":
        return {}
    fields: dict[int, list[IonValue]] = defaultdict(list)
    for entry in value.value:
        fields[entry["field_sid"]].append(entry["value"])
    return fields


def parse_document_data(value: IonValue | None) -> list[str]:
    if value is None:
        return []
    fields = struct_fields(value)
    sections: list[str] = []
    for group in fields.get(169, []):
        if group.type_name != "list":
            continue
        for group_item in group.value:
            group_fields = struct_fields(group_item)
            for section_list in group_fields.get(170, []):
                if section_list.type_name != "list":
                    continue
                for section_ref in section_list.value:
                    section_name = name_ref(section_ref)
                    if section_name is not None:
                        sections.append(section_name)
    return sections


def parse_section_pid_count_map(value: IonValue | None) -> dict[str, int]:
    if value is None:
        return {}
    fields = struct_fields(value)
    results: dict[str, int] = {}
    for mapping_list in fields.get(181, []):
        if mapping_list.type_name != "list":
            continue
        for mapping_item in mapping_list.value:
            item_fields = struct_fields(mapping_item)
            section_refs = item_fields.get(174, [])
            counts = item_fields.get(144, [])
            if not section_refs or not counts:
                continue
            section_name = name_ref(section_refs[0])
            count_value = counts[0].value if isinstance(counts[0], IonValue) else None
            if section_name is not None and isinstance(count_value, int):
                results[section_name] = count_value
    return results


def parse_pid_count_map_blob(raw: bytes) -> dict[str, int]:
    annotations, payload = unwrap_annotated_struct(raw)
    if annotations != [611]:
        return {}
    results: dict[str, int] = {}

    offset = 0
    while offset < len(payload):
        field_sid, offset = read_varuint(payload, offset)
        if field_sid != 181:
            _, _, value_offset, value_end = read_typed_header(payload, offset)
            offset = value_end
            continue

        type_code, _, list_offset, list_end = read_typed_header(payload, offset)
        if type_code != 11:
            return {}

        item_offset = list_offset
        while item_offset < list_end:
            item_type, _, item_payload_offset, item_end = read_typed_header(payload, item_offset)
            if item_type != 13:
                break
            inner_offset = item_payload_offset
            section_name: str | None = None
            count_value: int | None = None
            while inner_offset < item_end:
                item_field_sid, inner_offset = read_varuint(payload, inner_offset)
                if item_field_sid == 174:
                    section_name, inner_offset = parse_annotated_name_ref_bytes(payload, inner_offset)
                elif item_field_sid == 144:
                    count_value, inner_offset = parse_int_bytes(payload, inner_offset)
                else:
                    _, _, _, inner_offset = read_typed_header(payload, inner_offset)
            item_offset = item_end
            if section_name is not None and count_value is not None:
                results[section_name] = count_value
        return results

    return results


def parse_eidbucket(value: IonValue | None) -> dict[str, Any] | None:
    if value is None:
        return None
    fields = struct_fields(value)
    bucket_index = None
    if fields.get(602):
        bucket_index_value = fields[602][0]
        if isinstance(bucket_index_value, IonValue) and isinstance(bucket_index_value.value, int):
            bucket_index = bucket_index_value.value

    entries: list[dict[str, str]] = []
    for mapping_list in fields.get(181, []):
        if mapping_list.type_name != "list":
            continue
        for mapping_item in mapping_list.value:
            item_fields = struct_fields(mapping_item)
            eid_refs = item_fields.get(185, [])
            section_refs = item_fields.get(174, [])
            if not eid_refs or not section_refs:
                continue
            eid = name_ref(eid_refs[0])
            section = name_ref(section_refs[0])
            if eid is None or section is None:
                continue
            entries.append({"eid": eid, "section": section})

    return {"bucket": bucket_index, "entries": entries}


def parse_eidbucket_blob(raw: bytes) -> dict[str, Any] | None:
    try:
        annotations, payload = unwrap_annotated_struct(raw)
    except Exception:
        return None

    if annotations != [610]:
        return None

    try:
        bucket_index: int | None = None
        entries: list[dict[str, str]] = []

        offset = 0
        while offset < len(payload):
            field_sid, offset = read_varuint(payload, offset)
            if field_sid == 602:
                parser = IonParser(payload[offset:])
                value, consumed = parser.parse_value(0)
                offset += consumed
                if isinstance(value, IonValue) and isinstance(value.value, int):
                    bucket_index = value.value
                continue

            if field_sid != 181:
                _, _, _, value_end = read_typed_header(payload, offset)
                offset = value_end
                continue

            type_code, _, list_offset, list_end = read_typed_header(payload, offset)
            if type_code != 11:
                break
            item_offset = list_offset
            while item_offset < list_end:
                item_type, _, item_payload_offset, item_end = read_typed_header(payload, item_offset)
                if item_type != 13:
                    break
                inner_offset = item_payload_offset
                eid: str | None = None
                section: str | None = None
                while inner_offset < item_end:
                    item_field_sid, inner_offset = read_varuint(payload, inner_offset)
                    if item_field_sid == 185:
                        eid, inner_offset = parse_annotated_name_ref_bytes(payload, inner_offset)
                    elif item_field_sid == 174:
                        section, inner_offset = parse_annotated_name_ref_bytes(payload, inner_offset)
                    else:
                        _, _, _, inner_offset = read_typed_header(payload, inner_offset)
                item_offset = item_end
                if eid is not None and section is not None:
                    entries.append({"eid": eid, "section": section})
            break

        return {"bucket": bucket_index, "entries": entries}
    except Exception:
        return None


def parse_spm_blob(raw: bytes) -> dict[str, Any] | None:
    try:
        value, _ = IonParser(raw).parse_stream()
    except Exception:
        return None
    if value.annotations != [609]:
        return None

    fields = struct_fields(value)
    section_name = None
    if fields.get(174):
        section_name = name_ref(fields[174][0])

    positions: list[dict[str, Any]] = []
    for entry_list in fields.get(181, []):
        if entry_list.type_name != "list":
            continue
        for entry in entry_list.value:
            if entry.type_name != "list" or len(entry.value) != 2:
                continue
            index_item, name_item = entry.value
            if not isinstance(index_item, IonValue) or not isinstance(index_item.value, int):
                continue
            target_name = name_ref(name_item)
            positions.append({"index": index_item.value, "target": target_name})

    return {
        "section": section_name,
        "pid_count": len(positions),
        "positions": positions,
    }


def parse_spread(
    raw: bytes,
    children: dict[str, set[str]],
    root_section: str,
) -> dict[str, Any]:
    seen = descendants(children, root_section)
    storyline = next((child for child in children.get(root_section, ()) if child.startswith("l")), None)
    ad_object = next((child for child in children.get(root_section, ()) if child.endswith("-ad")), None)
    spm_name = f"{root_section}-spm"
    spm_blob_offset = find_blob_offset(raw, spm_name)
    spm_blob = parse_spm_blob(raw[spm_blob_offset:]) if spm_blob_offset is not None else None

    page_heads: list[str] = []
    if storyline is not None:
        page_heads = sorted(
            [child for child in children.get(storyline, ()) if child.startswith("i")],
            key=lambda item: (len(item), item),
        )

    pages: list[dict[str, Any]] = []
    for page_head in page_heads:
        page_tail = next((child for child in children.get(page_head, ()) if child.startswith("i")), None)
        resource = None
        aux = None
        external = None
        if page_tail is not None:
            external = next((child for child in children.get(page_tail, ()) if child.startswith("e")), None)
        if external is not None:
            resource = next((child for child in children.get(external, ()) if child.startswith("rsrc")), None)
            aux = next((child for child in children.get(external, ()) if child.startswith("d")), None)
        pages.append(
            {
                "head": page_head,
                "tail": page_tail,
                "external": external,
                "aux": aux,
                "resource": resource,
            }
        )

    family_counts = Counter()
    for name in seen:
        if name.startswith("c"):
            family_counts["c"] += 1
        elif name.startswith("l"):
            family_counts["l"] += 1
        elif name.startswith("i"):
            family_counts["i"] += 1
        elif name.startswith("e"):
            family_counts["e"] += 1
        elif name.startswith("d"):
            family_counts["d"] += 1
        elif name.startswith("rsrc"):
            family_counts["rsrc"] += 1

    return {
        "section": root_section,
        "spm": spm_name,
        "section_pid_count": spm_blob["pid_count"] if spm_blob is not None else None,
        "spm_positions": spm_blob["positions"] if spm_blob is not None else [],
        "storyline": storyline,
        "ad": ad_object,
        "family_counts": dict(family_counts),
        "pages": pages,
    }


def parse_book_metadata(value: IonValue | None) -> dict[str, Any]:
    if value is None:
        return {}
    results: dict[str, Any] = {}
    fields = struct_fields(value)
    for top_list in fields.get(491, []):
        if top_list.type_name != "list":
            continue
        for item in top_list.value:
            item_fields = struct_fields(item)
            name_field = item_fields.get(495, [])
            data_field = item_fields.get(258, [])
            if not name_field or not data_field:
                continue
            metadata_name = name_field[0].value if isinstance(name_field[0], IonValue) else None
            if not isinstance(metadata_name, str):
                continue
            rows: dict[str, Any] = {}
            for row_list in data_field:
                if row_list.type_name != "list":
                    continue
                for row in row_list.value:
                    row_fields = struct_fields(row)
                    keys = row_fields.get(492, [])
                    values = row_fields.get(307, [])
                    if not keys or not values:
                        continue
                    key = keys[0].value if isinstance(keys[0], IonValue) else None
                    value_item = values[0]
                    if not isinstance(key, str) or not isinstance(value_item, IonValue):
                        continue
                    rows[key] = value_item.value
            results[metadata_name] = rows
    return results


def build_summary(book_kdf_path: Path) -> dict[str, Any]:
    raw = book_kdf_path.read_bytes()
    raw_text = raw.decode("latin1", errors="ignore")
    children, parents = extract_edges(raw_text)

    symbol_table = parse_named_blob_safe(raw, "$ion_symbol_table")
    document_data = parse_named_blob_safe(raw, "document_data")
    book_metadata = parse_named_blob_safe(raw, "book_metadata")

    root_sections = parse_document_data(document_data)
    pid_counts: dict[str, int] = {}
    pid_blob_offset = find_blob_offset(raw, "yj.section_pid_count_map")
    if pid_blob_offset is not None:
        pid_counts = parse_pid_count_map_blob(raw[pid_blob_offset:])
    if not pid_counts:
        pid_counts = parse_section_pid_count_map(parse_named_blob_safe(raw, "yj.section_pid_count_map"))

    spreads = [
        parse_spread(raw=raw, children=children, root_section=section)
        for section in root_sections
    ]

    bucket_summaries: list[dict[str, Any]] = []
    for name in sorted(children.get("root_entity", ())):
        if not name.startswith("eidbucket_"):
            continue
        blob_offset = find_blob_offset(raw, name)
        if blob_offset is None:
            continue
        parsed = parse_eidbucket_blob(raw[blob_offset:])
        if parsed is None:
            continue
        bucket_summaries.append(
            {
                "bucket": parsed["bucket"],
                "entry_count": len(parsed["entries"]),
                "sample_entries": parsed["entries"][:5],
            }
        )

    root_children = sorted(children.get("root_entity", ()))
    root_child_counts = Counter()
    for name in root_children:
        if name.startswith("c"):
            root_child_counts["c-spm" if name.endswith("-spm") else "c"] += 1
        else:
            root_child_counts[name] += 1

    return {
        "path": str(book_kdf_path),
        "symbol_table": symbol_table.to_python() if symbol_table else None,
        "book_metadata": parse_book_metadata(book_metadata),
        "root_entity_child_counts": dict(root_child_counts),
        "document_data_section_count": len(root_sections),
        "document_data_sections": root_sections,
        "yj_section_pid_count_entry_count": len(pid_counts),
        "yj_section_pid_count_histogram": dict(Counter(pid_counts.values())),
        "spread_pid_count_histogram": dict(Counter(spread["section_pid_count"] for spread in spreads if spread["section_pid_count"] is not None)),
        "spreads": spreads,
        "eidbuckets": bucket_summaries,
        "graph_stats": {
            "edge_count": sum(len(items) for items in children.values()),
            "node_count": len(set(children) | set(parents)),
        },
    }


def json_ready(value: Any) -> Any:
    if isinstance(value, IonValue):
        payload = json_ready(value.value)
        if value.annotations:
            return {
                "annotations": value.annotations,
                "type": value.type_name,
                "value": payload,
            }
        return payload
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [json_ready(item) for item in value]
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze Kindle Create KPF/KDF structure.")
    parser.add_argument("--kdf", type=Path, help="Path to resources/book.kdf")
    parser.add_argument("--kpf", type=Path, help="Path to .kpf/.zip template")
    parser.add_argument("--output", type=Path, help="Optional JSON output path")
    args = parser.parse_args()

    if args.kdf is None and args.kpf is None:
        parser.error("provide --kdf or --kpf")

    if args.kdf is not None and args.kpf is not None:
        parser.error("use only one of --kdf or --kpf")

    if args.kpf is not None:
        import zipfile

        with zipfile.ZipFile(args.kpf, "r") as archive:
            raw = archive.read("resources/book.kdf")
        temp_path = Path.cwd() / ".analysis" / "_temp_book.kdf"
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_bytes(raw)
        book_kdf_path = temp_path
    else:
        book_kdf_path = args.kdf

    assert book_kdf_path is not None
    summary = build_summary(book_kdf_path)
    rendered = json.dumps(json_ready(summary), ensure_ascii=False, indent=2)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
